"""Episode semantics over the real interface: reset options, the claim ending the episode, the call limit, refused
world calls, the state contract, the MCP paths, the stateless HTTP routes, and infrastructure failures (which
must surface as errors, never as a reward)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from rs_core.diff import snapshot_digest
from rs_core.models import Verdict
from rs_suite.conformance import episode_files, expected, instance, instances, plan_for
from rs_suite.instances import SEEDS, replay
from rs_world import SCHEMA, WorldSession
from websockets.sync.client import connect

import rubberstamp_env.server.grading as grading
from oe_helpers import metadata
from rubberstamp_env.client import RubberstampEnv, tool_output
from rubberstamp_env.models import CallToolAction, ListToolsAction, ToolErrorType
from rubberstamp_env.server.grading import initial_digest
from rubberstamp_env.server.rubberstamp_environment import RubberstampEnvironment

T01 = instance("t01_order_status@42")
GOLDEN = plan_for(T01, "golden")


def ws_url(server_url: str) -> str:
    return server_url.replace("http://", "ws://") + "/ws"


# ---------------------------------------------------------------- reset


@pytest.mark.parametrize(
    ("options", "want"),
    [
        ({"instance_id": "t08_refund_with_approval@7"}, "t08_refund_with_approval@7"),
        ({"task_id": "t08_refund_with_approval"}, "t08_refund_with_approval@42"),  # the task's own seed
        ({"task_id": "t08_refund_with_approval", "seed": 3}, "t08_refund_with_approval@3"),
        ({"split": "dev", "index": 3}, instances("dev")[3].id),
        ({"seed": 517}, instances()[517 % 500].id),
        ({"seed": 9, "split": "test"}, instances("test")[9 % 120].id),
        ({}, instances()[0].id),
    ],
)
def test_reset_picks_the_instance(server_url: str, options: dict[str, Any], want: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env:
        first = env.reset(**options)
        state = env.state()
    observation = first.observation
    assert observation.instance_id == want and state.instance_id == want
    assert first.reward is None and first.done is False
    assert observation.prompt and len(observation.tools) == 16 and observation.max_calls == 64


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"task_id": "t08_refund_with_approval", "seed": 43}, "unknown instance"),  # 43 is not a served seed
        ({"instance_id": "t99_nope@42"}, "unknown instance"),
        ({"task_id": "t99_nope"}, "unknown task"),
        ({"split": "dev", "index": 80}, "out of range"),
        ({"split": "holdout"}, "unknown split"),
        ({"instance_id": "t01_order_status@42", "task_id": "t01_order_status"}, "instance_id alone"),
        ({"instance_id": "t01_order_status@42", "split": "test"}, "not 'test'"),
        ({"max_calls": 65}, "max_calls"),
        ({"max_calls": 0}, "max_calls"),
        ({"seed": -1}, "non-negative"),
        ({"task": "t01_order_status"}, "unknown reset option"),
    ],
)
def test_bad_resets_are_refused(server_url: str, options: dict[str, Any], message: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env, pytest.raises(RuntimeError, match=message):
        env.reset(**options)


# ---------------------------------------------------------------- the episode


def test_the_claim_ends_the_episode_and_later_calls_are_refused(server_url: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id, episode_id="ep-1")
        rewards, result = [], None
        for tool, args in GOLDEN.calls:
            result = env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
            rewards.append(result.reward)
        assert result is not None and result.done and rewards == [0.0] * (len(GOLDEN.calls) - 1) + [1.0]
        assert metadata(result)["stop"] == "claim" and metadata(result)["submitted"] is True
        state = env.state()
        assert (state.episode_id, state.step_count, state.n_calls, state.done, state.submitted) == (
            "ep-1",
            len(GOLDEN.calls),
            len(GOLDEN.calls),
            True,
            True,
        )
        assert state.state_digest == metadata(result)["state_digest"] == expected(T01, "golden").final_digest
        with pytest.raises(RuntimeError, match="not run"):
            env.step(CallToolAction(tool_name="get_order", arguments={"order_id": "ORD-00001"}))
        with pytest.raises(RuntimeError, match="not run"):
            env.step(ListToolsAction())
        assert env.state().step_count == len(GOLDEN.calls)  # refused steps are not steps
        assert env.reset(instance_id=T01.id).observation.instance_id == T01.id  # reset starts a new one


def test_refused_world_calls_are_recorded_and_not_rewarded(server_url: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id)
        unknown = env.call_tool("delete_everything")
        bad_args = env.call_tool("get_order", order="ORD-00001")
        missing = env.call_tool("get_order", order_id="ORD-99999")
        for result in (unknown, bad_args, missing):
            assert result.reward == 0.0 and not result.done and tool_output(result.observation) is None
        assert unknown.observation.error.error_type is ToolErrorType.TOOL_NOT_FOUND
        assert bad_args.observation.error.error_type is ToolErrorType.INVALID_ARGS
        assert missing.observation.error.error_type is ToolErrorType.EXECUTION_ERROR
        assert unknown.observation.error.message == "tool 'delete_everything' is not available in this task"
        assert env.state().n_calls == 3  # recorded, as rs-world records them


def test_the_call_limit_ends_and_grades_the_episode(server_url: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id, max_calls=2)
        tool, args = GOLDEN.calls[0]
        first = env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        last = env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
    assert not first.done and last.done
    assert metadata(last)["stop"] == "max_calls" and metadata(last)["submitted"] is False
    graded = grading.run_and_grade(T01, [(tool, args)] * 2, variant="strict", max_calls=2)
    assert last.reward == graded.reward == 0.0
    assert metadata(last)["state_digest"] == graded.final_digest


def test_state_digest_tracks_the_live_world(server_url: str) -> None:
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id)
        assert env.state().state_digest == initial_digest(T01.seed)
        tool, args = GOLDEN.calls[0]
        env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        replayed = replay(T01.task_id, T01.seed, [(tool, args)])
        assert env.state().state_digest == snapshot_digest(replayed.final, SCHEMA)


def test_state_never_waits_on_the_world_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenEnv reads `state` on the event loop, so it must not queue behind other sessions' world work: step keeps
    the live digest current on the world thread (recomputed only after a call that changed a row) and state reads it.
    With the world thread removed, state still answers, and its digest is the replayed one after every call."""
    t08 = instance("t08_refund_with_approval@42")
    env = RubberstampEnvironment()
    try:
        env.reset(instance_id=t08.id)
        played: list[tuple[str, Any]] = []
        digests: list[str | None] = [initial_digest(t08.seed)]
        for tool, args in plan_for(t08, "golden").calls[:-1]:  # reads, then writes; the claim would end the episode
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
            played.append((tool, args))
            with monkeypatch.context() as no_world:
                no_world.setattr(grading, "_WORLD", None)  # any world work in `state` would now fail
                digest = env.state.state_digest
            assert digest == snapshot_digest(replay(t08.task_id, t08.seed, played).final, SCHEMA)
            digests.append(digest)
        assert digests[0] == digests[1] == digests[2]  # get_order, get_policy: no row changed
        assert len(set(digests[2:])) == 4  # request_approval, create_refund, send_email: each changed the world
    finally:
        env.close()


def test_a_plan_without_a_claim_is_graded_by_the_regrade_route(server_url: str) -> None:
    calls = list(GOLDEN.calls[:-1])  # everything but the claim
    graded = httpx.post(
        f"{server_url}/rubberstamp_env/grade",
        json={"instance_id": T01.id, "calls": [{"tool": t, "args": a} for t, a in calls]},
    ).json()
    assert graded["submitted"] is False and graded["reward"] == 0.0 and graded["n_calls"] == len(calls)
    later = calls + list(GOLDEN.calls[-1:]) + [("get_order", {"order_id": "ORD-00001"})]
    graded = httpx.post(
        f"{server_url}/rubberstamp_env/grade",
        json={"instance_id": T01.id, "calls": [{"tool": t, "args": a} for t, a in later]},
    ).json()
    assert graded["reward"] == 1.0 and graded["n_calls"] == len(GOLDEN.calls)  # the call after the claim never ran


# ---------------------------------------------------------------- MCP and the stateless HTTP routes


def test_mcp_tools_call_follows_the_episode_rules(server_url: str) -> None:
    """A WebSocket `mcp` tools/call goes through step(): same recording, same terminal reward, same refusal."""
    with connect(ws_url(server_url)) as ws:

        def send(message: dict[str, Any]) -> dict[str, Any]:
            ws.send(json.dumps(message))
            return json.loads(ws.recv())

        listed = send({"type": "mcp", "data": {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}})
        assert sorted(t["name"] for t in listed["data"]["result"]["tools"]) == sorted(t.name for t in grading.TOOLS)
        assert all("inputSchema" in t for t in listed["data"]["result"]["tools"])
        assert send({"type": "reset", "data": {"instance_id": T01.id}})["type"] == "observation"
        results = []
        for i, (tool, args) in enumerate(GOLDEN.calls, start=2):
            rpc = {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": tool, "arguments": args}}
            results.append(send({"type": "mcp", "data": rpc})["data"]["result"])
        assert [r["reward"] for r in results] == [0.0] * (len(results) - 1) + [1.0]
        assert results[-1]["done"] is True and results[-1]["metadata"]["reward_variant"] == "strict"
        rpc = {"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": "get_policy", "arguments": {}}}
        assert "not run" in send({"type": "mcp", "data": rpc})["data"]["error"]["message"]


def test_http_mcp_runs_an_episode_only_once_a_websocket_started_it(server_url: str) -> None:
    """HTTP /mcp has no reset: a session from `openenv/session/create` holds no episode, so its tools/call is refused.
    A WebSocket attached with `/ws?session_id=...` can reset it; after that, HTTP tools/call on the session goes
    through step(), with the same recording, terminal reward and refusal as the WebSocket. A `/mcp` WebSocket cannot
    start an episode either."""

    def rpc(rid: int, method: str, **params: Any) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        return httpx.post(f"{server_url}/mcp", json=body, timeout=30).json()

    before = episode_files()
    sid = rpc(1, "openenv/session/create")["result"]["session_id"]
    try:
        refused = rpc(2, "tools/call", session_id=sid, name="get_policy", arguments={})
        assert "reset()" in refused["error"]["message"]
        with connect(f"{ws_url(server_url)}?session_id={sid}") as ws:
            ws.send(json.dumps({"type": "reset", "data": {"instance_id": T01.id}}))
            assert json.loads(ws.recv())["type"] == "observation"
        results = [
            rpc(i, "tools/call", session_id=sid, name=tool, arguments=args)["result"]
            for i, (tool, args) in enumerate(GOLDEN.calls, start=3)
        ]
        assert [r["reward"] for r in results] == [0.0] * (len(results) - 1) + [1.0]
        assert results[-1]["done"] is True and results[-1]["metadata"]["reward_variant"] == "strict"
        later = rpc(99, "tools/call", session_id=sid, name="get_policy", arguments={})
        assert "not run" in later["error"]["message"]
    finally:
        assert rpc(100, "openenv/session/close", session_id=sid)["result"]["closed"] is True
    assert episode_files() == before
    with connect(server_url.replace("http://", "ws://") + "/mcp") as mcp_ws:  # its own session, and no reset either
        mcp_ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_policy"}}))
        assert "reset()" in json.loads(mcp_ws.recv())["error"]["message"]


def test_stateless_http_routes_hold_no_episode(server_url: str) -> None:
    before = episode_files()
    reset = httpx.post(f"{server_url}/reset", json={"instance_id": T01.id})
    assert reset.status_code == 200 and reset.json()["observation"]["instance_id"] == T01.id
    assert episode_files() == before  # the per-request environment was closed and its world deleted
    step = httpx.post(f"{server_url}/step", json={"action": {"type": "call_tool", "tool_name": "get_policy"}})
    assert step.status_code == 500  # no episode: an error, not an observation with a reward
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_policy", "arguments": {}}}
    called = httpx.post(f"{server_url}/mcp", json=rpc).json()
    assert "error" in called and "reset()" in called["error"]["message"]
    rpc = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    assert len(httpx.post(f"{server_url}/mcp", json=rpc).json()["result"]["tools"]) == 16


# ---------------------------------------------------------------- infrastructure failures: raised, never scored


def test_a_replay_mismatch_is_an_error_not_a_reward(server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def tampered(task_id, seed, calls, final_answer=None, *, live_digest=None):
        return replay(task_id, seed, calls, final_answer, live_digest="0" * 64 if live_digest else None)

    monkeypatch.setattr(grading, "replay", tampered)
    before = episode_files()
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id)
        for tool, args in GOLDEN.calls[:-1]:
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        tool, args = GOLDEN.calls[-1]
        with pytest.raises(RuntimeError, match="ReplayMismatch|replay ends at"):
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        with pytest.raises(RuntimeError, match="aborted by an infrastructure error"):
            env.step(CallToolAction(tool_name="get_policy", arguments={}))
        assert env.state().done is False
    assert episode_files() == before
    regrade = httpx.post(
        f"{server_url}/rubberstamp_env/grade",
        json={"instance_id": T01.id, "calls": [{"tool": t, "args": a} for t, a in GOLDEN.calls]},
    )
    assert regrade.status_code == 500 and "replay ends at" in regrade.json()["detail"]


def test_a_world_exception_is_an_error_not_a_reward(server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real = WorldSession.call

    def failing(self, tool, args):
        if tool == "search_orders":
            raise OSError("disk on fire")
        return real(self, tool, args)

    monkeypatch.setattr(WorldSession, "call", failing)
    before = episode_files()
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id)
        tool, args = GOLDEN.calls[0]
        assert env.step(CallToolAction(tool_name=tool, arguments=dict(args))).reward == 0.0
        tool, args = GOLDEN.calls[1]
        with pytest.raises(RuntimeError, match="disk on fire"):
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        with pytest.raises(RuntimeError, match="aborted"):
            env.step(CallToolAction(tool_name="submit_resolution", arguments={"summary": "done"}))
    assert episode_files() == before


def test_a_grader_error_is_an_error_not_a_reward(server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real = grading.verdicts

    def broken(episode, seed=None, variants=None):
        graded = real(episode, seed) if variants is None else real(episode, seed, variants)
        graded["strict"] = Verdict(passed=False, reward=0.0, error="spec is for another task")
        return graded

    monkeypatch.setattr(grading, "verdicts", broken)
    with RubberstampEnv(base_url=server_url).sync() as env:
        env.reset(instance_id=T01.id)
        for tool, args in GOLDEN.calls[:-1]:
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))
        tool, args = GOLDEN.calls[-1]
        with pytest.raises(RuntimeError, match="grader error"):
            env.step(CallToolAction(tool_name=tool, arguments=dict(args)))


def test_served_seeds_are_the_instance_seeds() -> None:
    assert sorted({inst.seed for inst in grading.split_instances(None)}) == sorted(SEEDS)
