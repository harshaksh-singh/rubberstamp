"""The API's bounds: request and argument sizes, calls per episode, live episodes and per-seed caches.

Each bound is an HTTP error for the API itself (never recorded, like a malformed request), or, for the call cap, the
end of the episode as rs-train ends a cut-off rollout. None of them changes what a call within the bounds records.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import rs_world.session as world_session
from fastapi.testclient import TestClient
from rs_api import EpisodeStore, create_app
from rs_api.body_limit import BodyLimit
from rs_api.cli import main as cli_main
from rs_api.grading import instance_spec, instance_verifier
from rs_api.models import MAX_ARGS_BYTES, MAX_BODY_BYTES, MAX_SEED, MAX_SUMMARY
from rs_api.seeds import MAX_SEEDS
from rs_api.store import MAX_CALLS, MAX_EPISODES
from rs_core.models import Episode, ToolCall, Trajectory
from rs_suite import VARIANTS
from rs_world import REGISTRY, golden, load_task
from rsapi_helpers import EXECUTOR, Api, api_verdict, expected_verdict, own_seed, reference_verifier
from starlette.types import Message, Receive, Scope, Send

UI = "http://localhost:3000"
TASK = "t06_cancel_unshipped_order"
OTHER_SEEDS = (5, 6, 8, 21)  # valid instances of TASK, none of them a seed the parity tests use


@pytest.fixture(scope="module")
def api() -> Iterator[Api]:
    with TestClient(create_app(allowed_hosts=["testserver"], ui_origins=[UI])) as client:
        yield Api(client)


def test_config_reports_the_limits(api: Api) -> None:
    assert api.ok("GET", "/config") == {
        "eval_mode": False,
        "max_episodes": MAX_EPISODES,
        "max_seeds": MAX_SEEDS,
        "max_calls": MAX_CALLS,
        "max_seed": MAX_SEED,
        "max_summary": MAX_SUMMARY,
        "max_body_bytes": MAX_BODY_BYTES,
        "max_args_bytes": MAX_ARGS_BYTES,
    }


# ---------------------------------------------------------------- request and argument sizes


def test_a_body_over_the_limit_is_413_and_not_recorded(api: Api) -> None:
    episode_id = api.start(TASK)["episode_id"]
    body = json.dumps({"tool": "nope", "args": {"x": "a" * MAX_BODY_BYTES}})
    refused = api.client.post(
        f"/episodes/{episode_id}/calls",
        content=body,
        headers={"Content-Type": "application/json", "Origin": UI},
    )
    assert refused.status_code == 413 and refused.json() == {"detail": f"request body over {MAX_BODY_BYTES} bytes"}
    assert refused.headers["access-control-allow-origin"] == UI  # the console can read why
    assert api.trajectory(episode_id)["calls"] == []


def test_a_chunked_body_over_the_limit_is_413(api: Api) -> None:
    """No Content-Length: the body is read up to the limit, then refused."""
    episode_id = api.start(TASK)["episode_id"]
    chunk = b" " * 65_536

    def chunks() -> Iterator[bytes]:
        yield b'{"tool": "get_policy", "args": {}}'
        for _ in range(MAX_BODY_BYTES // len(chunk) + 1):
            yield chunk

    refused = api.client.post(
        f"/episodes/{episode_id}/calls", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert refused.status_code == 413
    small = api.client.post(
        f"/episodes/{episode_id}/calls",
        content=iter([b'{"tool": "get_policy",', b' "args": {}}']),
        headers={"Content-Type": "application/json"},
    )
    assert small.status_code == 200 and small.json()["ok"]
    assert len(api.trajectory(episode_id)["calls"]) == 1


def run_body_limit(chunks: list[bytes], limit: int) -> tuple[int, bytes | None]:
    """BodyLimit over an app that answers with the body it read, fed `chunks` as separate ASGI messages."""
    seen: list[bytes] = []
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        seen.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    messages: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1} for i, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    asyncio.run(BodyLimit(app, limit)(scope, receive, send))
    return sent[0]["status"], (seen[0] if seen else None)


def test_body_limit_counts_a_streamed_body_across_messages() -> None:
    assert run_body_limit([b"ab", b"cd", b"e"], 5) == (200, b"abcde")
    status, reached = run_body_limit([b"ab", b"cd", b"ef"], 5)
    assert status == 413 and reached is None  # the app never ran


def test_args_over_the_limit_are_422_and_not_recorded(api: Api) -> None:
    episode_id = api.start(TASK)["episode_id"]
    too_big = {"tool": "send_email", "args": {"customer_id": "CUS-00001", "subject": "s", "body": "a" * MAX_ARGS_BYTES}}
    refused = api.ok("POST", f"/episodes/{episode_id}/calls", 422, json=too_big)
    assert "the limit is" in json.dumps(refused)
    assert all("input" not in error for error in refused["detail"]), "the oversized args are not echoed back"
    assert len(json.dumps(refused)) < 2_000
    assert api.trajectory(episode_id)["calls"] == []


def test_an_oversized_argument_within_the_limits_is_the_tools_recorded_refusal(api: Api) -> None:
    """Under the API's limits, the registry decides, exactly as over MCP or in the executor."""
    task = load_task(TASK)
    args: dict[str, Any] = {"customer_id": "CUS-00001", "subject": "s", "body": "a" * 9_000}  # the tool takes 8,000
    expected = EXECUTOR.run(task, [ToolCall(idx=0, tool="send_email", args=args)]).trajectory.calls[0]
    episode_id = api.start(TASK)["episode_id"]
    recorded = api.call(episode_id, "send_email", **args)
    assert recorded == expected.model_dump(mode="json") and recorded["ok"] is False


def test_no_call_a_tool_accepts_is_over_the_size_limits() -> None:
    """The largest arguments any tool takes, at 6 bytes per character (a JSON \\u escape) plus room for ids, fit
    under MAX_ARGS_BYTES; the longest summary /submit takes fits under MAX_BODY_BYTES."""
    for spec in REGISTRY.specs():
        params = spec.params.get("properties", {})
        bounded = sum(6 * p["maxLength"] + len(name) + 6 for name, p in params.items() if "maxLength" in p)
        other = 256 * sum(1 for p in params.values() if "maxLength" not in p)
        assert bounded + other < MAX_ARGS_BYTES, spec.name
    assert 6 * MAX_SUMMARY + 64 < MAX_BODY_BYTES


# ---------------------------------------------------------------- calls per episode


def cut_off_api(max_calls: int) -> TestClient:
    return TestClient(create_app(EpisodeStore(max_calls=max_calls), allowed_hosts=["testserver"]))


def test_the_call_that_reaches_the_cap_ends_and_grades_the_episode() -> None:
    task = load_task(TASK)
    work = golden(TASK).calls[:-1]
    assert len(work) >= 2
    with cut_off_api(2) as client:
        api = Api(client)
        episode_id = api.start(TASK)["episode_id"]
        first, second = (api.call(episode_id, tool, **args) for tool, args in work[:2])
        assert first["ok"] and second["ok"]  # the call that reaches the cap is recorded as usual
        assert api.ok("GET", f"/episodes/{episode_id}")["status"] == "cut_off"
        refused = api.ok("POST", f"/episodes/{episode_id}/calls", 409, json={"tool": "get_policy", "args": {}})
        assert "--max-calls" in refused["detail"]
        api.ok("POST", f"/episodes/{episode_id}/submit", 409, json={"summary": "Cancelled the order."})

        trajectory = api.trajectory(episode_id)
        assert trajectory["stop"] == "max_steps" and trajectory["final_answer"] is None
        assert len(trajectory["calls"]) == 2
        grade = api.ok("GET", f"/episodes/{episode_id}/grade")
        assert grade["summary"] is None and not grade["strict"]["passed"]

    # graded as the same calls replayed and cut off, by every grader
    calls = [ToolCall(idx=i, tool=tool, args=args) for i, (tool, args) in enumerate(work[:2])]
    replayed = EXECUTOR.run(task, calls, None)
    reference = Episode(
        task=replayed.task,
        initial=replayed.initial,
        final=replayed.final,
        trajectory=Trajectory(task_id=TASK, agent_id="replay", calls=replayed.trajectory.calls, stop="max_steps"),
    )
    by_name = {"strict": grade["strict"], **{v["variant"]: v for v in grade["variants"]}}
    for variant in VARIANTS:
        expected = expected_verdict(reference_verifier(TASK, own_seed(TASK), variant).verify(reference))
        assert api_verdict(by_name[variant]) == expected, variant


def test_a_claim_at_the_cap_ends_the_episode_as_a_claim() -> None:
    with cut_off_api(1) as client:
        api = Api(client)
        episode_id = api.start(TASK)["episode_id"]
        submitted = api.submit(episode_id, "No action taken.")
        assert submitted["grade"]["summary"] == "No action taken."
        assert api.ok("GET", f"/episodes/{episode_id}")["status"] == "submitted"
        assert api.trajectory(episode_id)["stop"] == "done"


def test_a_refused_claim_at_the_cap_returns_the_cut_off_grade() -> None:
    with cut_off_api(1) as client:
        api = Api(client)
        episode_id = api.start(TASK)["episode_id"]
        submitted = api.submit(episode_id, "ok")  # too short: refused, and it was the last call
        assert submitted["call"]["ok"] is False
        assert submitted["grade"] is not None and submitted["grade"]["summary"] is None
        assert api.ok("GET", f"/episodes/{episode_id}")["status"] == "cut_off"


# ---------------------------------------------------------------- live episodes


def test_an_evicted_episode_is_410_with_the_reason_and_a_deleted_one_404() -> None:
    with TestClient(create_app(EpisodeStore(max_episodes=1), allowed_hosts=["testserver"])) as client:
        api = Api(client)
        first = api.start(TASK)["episode_id"]
        second = api.start(TASK)["episode_id"]
        for method, path in [("GET", f"/episodes/{first}"), ("GET", f"/episodes/{first}/trajectory")]:
            gone = api.ok(method, path, 410)
            assert "at most 1 live episodes (--max-episodes)" in gone["detail"]
        api.ok("POST", f"/episodes/{first}/calls", 410, json={"tool": "get_policy", "args": {}})
        api.ok("DELETE", f"/episodes/{second}", 204)
        missing = api.ok("GET", f"/episodes/{second}", 404)
        assert "closed with DELETE" in missing["detail"]


# ---------------------------------------------------------------- per-seed caches


def test_seed_caches_are_bounded_and_released() -> None:
    store = EpisodeStore(max_seeds=2)

    def held() -> tuple[dict[int, Path], set[int], int, int]:
        return (
            dict(world_session._worlds),
            set(world_session._initial),
            instance_spec.cache_info().currsize,
            instance_verifier.cache_info().currsize,
        )

    try:
        world_files: dict[int, Path] = {}
        for seed in OTHER_SEEDS:
            info = store.start(TASK, seed)
            store.submit(info.episode_id, "No action taken.")
            store.close(info.episode_id)
            world_files[seed] = store._on_world(held)[0][seed]
        assert store.seeds.kept() == list(OTHER_SEEDS[-2:]) and store.seeds.released == len(OTHER_SEEDS) - 2

        worlds, snapshots, specs, verifiers = store._on_world(held)
        for seed in OTHER_SEEDS[:-2]:  # released: no world file, no snapshot
            assert seed not in worlds and seed not in snapshots and not world_files[seed].exists()
        for seed in OTHER_SEEDS[-2:]:
            assert seed in worlds and seed in snapshots and world_files[seed].exists()
        assert specs <= store.max_seeds and verifiers <= store.max_seeds * len(VARIANTS)

        store.start(TASK, own_seed(TASK))  # the task's own seed is never counted or released
        assert store.seeds.kept() == list(OTHER_SEEDS[-2:])
    finally:
        store.shutdown()


def test_a_live_episode_on_a_released_seed_still_grades_as_the_replay() -> None:
    store = EpisodeStore(max_seeds=1)
    seed = OTHER_SEEDS[3]
    try:
        plan = golden(TASK, seed)
        episode_id = store.start(TASK, seed).episode_id
        for tool, args in plan.calls[:-1]:
            store.call(episode_id, tool, args)
        store.start(TASK, OTHER_SEEDS[0])  # releases `seed` while the episode is open
        assert store.seeds.kept() == [OTHER_SEEDS[0]]
        _, grade = store.submit(episode_id, str(plan.answer))
        assert grade is not None and grade.strict.passed
        reference = EXECUTOR.run(load_task(TASK, seed), plan.tool_calls(), plan.answer)
        assert grade.strict.reward == reference_verifier(TASK, seed, "strict").verify(reference).reward
    finally:
        store.shutdown()


# ---------------------------------------------------------------- the CLI


@pytest.mark.parametrize("flag", ["--max-calls", "--max-seeds", "--max-episodes"])
def test_cli_refuses_a_bound_below_one(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_main([flag, "0"])
    assert exit_info.value.code == 2 and "at least 1" in capsys.readouterr().err
