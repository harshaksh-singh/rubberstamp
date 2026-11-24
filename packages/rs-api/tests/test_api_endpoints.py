"""The API surface: task listing, episode lifecycle, request validation, CORS, hosts, the store and the CLI."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rs_api import EpisodeStore, create_app, title_of
from rs_api.cli import main as cli_main
from rs_api.cli import ui_origins
from rs_suite import VARIANTS, task_ids
from rs_verify import CATALOGUE, Leniency
from rs_world import REGISTRY, golden, load_task
from rs_world.tasks import task_file
from rsapi_helpers import Api

TASKS = task_ids()
UI = "http://localhost:3000"


@pytest.fixture(scope="module")
def api() -> Iterator[Api]:
    with TestClient(create_app(allowed_hosts=["testserver"])) as client:
        yield Api(client)


# ---------------------------------------------------------------- tasks and graders


def test_tasks_lists_every_task_generically(api: Api) -> None:
    listed = api.ok("GET", "/tasks")
    assert [t["id"] for t in listed] == TASKS
    for task in listed:
        spec = task_file(task["id"])
        assert task == {
            "id": spec.id,
            "title": title_of(spec.id),
            "category": spec.category,
            "split": spec.split,
            "role": spec.persona.role,
            "seed": spec.seed,
        }


@pytest.mark.parametrize(
    ("task_id", "title"),
    [
        ("t08_refund_with_approval", "Refund with approval"),
        ("t20_refund_not_received", "Refund not received"),
        ("t05_sla_breach_escalation", "SLA breach escalation"),
        ("t30_check_sla", "Check SLA"),
        ("t101_x", "X"),
        ("custom_task", "Custom task"),
        ("t_only", "T only"),
    ],
)
def test_title_of(task_id: str, title: str) -> None:
    assert title_of(task_id) == title


def test_variants_are_strict_and_every_flag(api: Api) -> None:
    listed = api.ok("GET", "/variants")
    assert [v["variant"] for v in listed] == list(VARIANTS)
    assert listed[0] == {"variant": "strict", "ignores": ""}
    for v in listed[1:]:
        assert v["ignores"] == CATALOGUE[Leniency(v["variant"])].ignores


def test_health(api: Api) -> None:
    health = api.ok("GET", "/health")
    assert health["status"] == "ok" and health["tasks"] == len(TASKS)


# ---------------------------------------------------------------- episodes


def test_start_returns_the_rendered_prompt_and_the_tool_specs(api: Api) -> None:
    task_id = "t13_manager_approves_refund"
    info = api.start(task_id)
    task = load_task(task_id)
    assert info["prompt"] == task.prompt
    assert task.persona is not None and info["persona"] == task.persona.model_dump(mode="json")
    assert info["tools"] == [s.model_dump(mode="json") for s in REGISTRY.specs() if s.name in task.tools]
    assert info["status"] == "open" and info["calls"] == 0
    assert info["task"]["id"] == task_id and info["seed"] == task_file(task_id).seed
    assert api.ok("GET", f"/episodes/{info['episode_id']}") == info


def test_another_seed_is_another_instance(api: Api) -> None:
    task_id = "t08_refund_with_approval"
    base, other = api.start(task_id), api.start(task_id, 7)
    assert other["seed"] == 7 and other["prompt"] == load_task(task_id, 7).prompt
    assert other["world_digest"] != base["world_digest"]


def test_episodes_are_isolated(api: Api) -> None:
    task_id = "t06_cancel_unshipped_order"
    first, second = api.start(task_id), api.start(task_id)
    write = next(args for tool, args in golden(task_id).calls if tool == "update_order_status")
    assert api.call(first["episode_id"], "update_order_status", **write)["ok"]
    seen = api.call(second["episode_id"], "get_order", order_id=write["order_id"])
    assert seen["result"]["order"]["status"] != write["status"]


def test_unknown_task_and_episode_are_404(api: Api) -> None:
    assert "no task" in api.ok("POST", "/episodes", 404, json={"task_id": "t99_missing"})["detail"]
    missing = "0" * 32
    for method, path in [
        ("GET", f"/episodes/{missing}"),
        ("GET", f"/episodes/{missing}/trajectory"),
        ("GET", f"/episodes/{missing}/grade"),
        ("DELETE", f"/episodes/{missing}"),
    ]:
        api.ok(method, path, 404)
    api.ok("POST", f"/episodes/{missing}/calls", 404, json={"tool": "get_policy", "args": {}})
    api.ok("POST", f"/episodes/{missing}/submit", 404, json={"summary": "No action taken."})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"task_id": "t01_order_status", "seed": -1},
        {"task_id": "t01_order_status", "seed": 10_000},
        {"task_id": "t01_order_status", "seed": "seven"},
        {"task_id": "../etc/passwd"},
        {"task_id": "t01_order_status", "persona": "manager"},
    ],
)
def test_malformed_start_is_422(api: Api, body: dict[str, object]) -> None:
    api.ok("POST", "/episodes", 422, json=body)


def test_malformed_call_and_submit_are_422_and_not_recorded(api: Api) -> None:
    episode_id = api.start(TASKS[0])["episode_id"]
    for body in [{}, {"tool": ""}, {"tool": "get_policy", "args": []}, {"tool": "get_policy", "extra": 1}]:
        api.ok("POST", f"/episodes/{episode_id}/calls", 422, json=body)
    api.ok("POST", f"/episodes/{episode_id}/submit", 422, json={"summary": "x" * 20_001})
    api.ok("POST", f"/episodes/{episode_id}/submit", 422, json={})
    api.ok("GET", "/episodes/not-an-id", 422)
    assert api.trajectory(episode_id)["calls"] == []


def test_the_episode_ends_at_the_claim(api: Api) -> None:
    episode_id = api.start(TASKS[0])["episode_id"]
    api.ok("GET", f"/episodes/{episode_id}/grade", 409)  # nothing to grade yet
    api.submit(episode_id, "No action taken.")
    assert api.ok("GET", f"/episodes/{episode_id}")["status"] == "submitted"
    refused = api.ok("POST", f"/episodes/{episode_id}/calls", 409, json={"tool": "get_policy", "args": {}})
    assert "ended" in refused["detail"]
    api.ok("POST", f"/episodes/{episode_id}/submit", 409, json={"summary": "Again."})
    assert len(api.trajectory(episode_id)["calls"]) == 1  # the refused calls never reached the world
    assert api.ok("GET", f"/episodes/{episode_id}/grade")["summary"] == "No action taken."


def test_the_claim_through_calls_also_ends_and_grades(api: Api) -> None:
    """An agent that calls submit_resolution like any other tool gets the same episode as one that uses /submit."""
    episode_id = api.start(TASKS[0])["episode_id"]
    call = api.call(episode_id, "submit_resolution", summary="No action taken.")
    assert call["ok"]
    grade = api.ok("GET", f"/episodes/{episode_id}/grade")
    assert grade["summary"] == "No action taken." and grade["strict"]["passed"] is False
    assert api.trajectory(episode_id)["final_answer"] == "No action taken."


def test_delete_closes_the_episode(api: Api) -> None:
    episode_id = api.start(TASKS[0])["episode_id"]
    api.ok("DELETE", f"/episodes/{episode_id}", 204)
    api.ok("GET", f"/episodes/{episode_id}", 404)


# ---------------------------------------------------------------- local only


def test_cors_allows_only_the_ui_origin() -> None:
    with TestClient(create_app(allowed_hosts=["testserver"], ui_origins=[UI])) as client:
        preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
        allowed = client.options("/episodes", headers={"Origin": UI, **preflight})
        assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == UI
        other = client.options("/episodes", headers={"Origin": "https://example.com", **preflight})
        assert other.status_code == 400 and "access-control-allow-origin" not in other.headers
        simple = client.get("/tasks", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in simple.headers


def test_only_loopback_host_headers_are_served() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1:8787") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health", headers={"Host": "localhost:8787"}).status_code == 200
        assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400


def test_cli_refuses_a_non_loopback_host(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["--host", "0.0.0.0"])
    assert exit_info.value.code == 2
    assert "no authentication" in capsys.readouterr().err


def test_cli_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RS_API_UI_ORIGINS", raising=False)
    assert UI in ui_origins(None)
    monkeypatch.setenv("RS_API_UI_ORIGINS", "http://localhost:3100, http://127.0.0.1:3100")
    assert ui_origins(None) == ("http://localhost:3100", "http://127.0.0.1:3100")
    assert ui_origins(["http://localhost:4000"]) == ("http://localhost:4000",)


# ---------------------------------------------------------------- the store


def session_path(store: EpisodeStore, episode_id: str) -> Path:
    session = store._get(episode_id).session
    assert session is not None
    return session.path


def test_store_evicts_the_least_recently_used_episode_and_closes_its_world() -> None:
    store = EpisodeStore(max_episodes=2)
    try:
        first = store.start(TASKS[0])
        second = store.start(TASKS[0])
        path = store._on_world(lambda: session_path(store, second.episode_id))
        store.info(first.episode_id)  # touch: the second is now the oldest
        assert path.exists()
        store.start(TASKS[0])
        assert store.count() == 2
        assert store.info(first.episode_id).episode_id == first.episode_id
        with pytest.raises(LookupError):
            store.info(second.episode_id)
        assert not path.exists()
    finally:
        store.shutdown()


def test_store_serialises_concurrent_calls_on_one_world_thread() -> None:
    store = EpisodeStore()
    try:
        episode_id = store.start(TASKS[0]).episode_id
        threads: set[str] = set()

        def read(n: int) -> int:
            threads.add(threading.current_thread().name)
            return store.call(episode_id, "get_policy", {}).idx

        with ThreadPoolExecutor(max_workers=8) as pool:
            indices = sorted(pool.map(read, range(40)))
        assert indices == list(range(40))  # one order, no lost or duplicated record
        assert len(threads) > 1  # the requests did come from several threads
        assert [c.idx for c in store.trajectory(episode_id).calls] == list(range(40))
    finally:
        store.shutdown()
