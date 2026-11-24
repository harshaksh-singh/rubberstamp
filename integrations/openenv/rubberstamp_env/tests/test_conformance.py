"""The conformance proof: `rs_suite.conformance.check` on all 500 instances x (golden, shortcut, noop) through the
OpenEnv server's WebSocket, `check_isolation` with 64 concurrent clients against the shipped session cap of 64, and
RFC 008's semantic checks (openenv's own graders) on every measured reward.

Shard k holds the 25 tasks on `SEEDS[k]` (instances are listed task by task, 20 seeds each), so 20 shards cover the
500 instances; run them in parallel with `pytest -n 4`.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from contextlib import ExitStack
from typing import cast

import pytest
from openenv.validation.types import CheckStatus
from rs_suite.conformance import PLANS, Driver, check, check_isolation, instances
from rs_suite.instances import SEEDS
from websockets.sync.client import connect

from oe_helpers import serve
from rubberstamp_env.client import RubberstampEnv
from rubberstamp_env.driver import OpenEnvDriver
from rubberstamp_env.quality import played_evidence, runtime_plan_evidence, semantic_checks
from rubberstamp_env.server.app import DEFAULT_MAX_CONCURRENT_ENVS, build_app

ALL = instances()
SHARDS = len(SEEDS)


def test_the_shards_cover_every_instance_once() -> None:
    shards = [ALL[k::SHARDS] for k in range(SHARDS)]
    assert len(ALL) == 500
    assert sorted(i.id for shard in shards for i in shard) == sorted(i.id for i in ALL)
    assert all({i.seed for i in shard} == {SEEDS[k]} and len(shard) == 25 for k, shard in enumerate(shards))


@pytest.mark.parametrize("k", range(SHARDS), ids=[f"seed{seed}" for seed in SEEDS])
def test_conformance_shard(server_url: str, k: int) -> None:
    shard = ALL[k::SHARDS]
    driver = OpenEnvDriver(server_url)
    mismatches = check(cast(Driver, driver), shard, PLANS)  # an async `run`, which check() accepts
    assert mismatches == [], "\n".join(map(str, mismatches[:20]))
    assert len(driver.played) == len(shard) * len(PLANS)
    assert driver.refused == 0  # no plan calls past its claim (the refusal itself is tested in test_episodes.py)
    for run in driver.played:
        assert run.metadata["reward_variant"] == "strict"
        assert run.metadata["metrics"]["zero_weight"] is True
    # RFC 008 on this shard: golden = oracle at 1.0, no-op = floor, shortcuts = canaries at or below the floor
    results = semantic_checks(played_evidence(driver.played))
    assert [(r.check_id, r.status) for r in results] == [
        ("semantic.oracle_max", CheckStatus.PASS),
        ("semantic.floor_gap", CheckStatus.PASS),
        ("semantic.canary_floor", CheckStatus.PASS),
    ], [r.evidence for r in results]


def test_isolation_with_64_concurrent_clients() -> None:
    """On a fresh server with the shipped defaults (at most 64 sessions): every concurrent outcome equals its solo
    one, no session is refused for capacity, and the episode temp files return to their baseline (check_isolation
    counts rs-episode-* files in this process's private temp dir). Then 64 open sessions fill it: a 65th is refused,
    so the 64 clients ran against the shipped cap, not a larger one."""
    assert "MAX_CONCURRENT_ENVS" not in os.environ and DEFAULT_MAX_CONCURRENT_ENVS == 64
    insts = ALL[::10]  # every task on 2 seeds (42 and 10): 50 instances, 150 episodes
    with serve(build_app()) as url:
        started = time.perf_counter()
        found = asyncio.run(check_isolation(lambda: cast(Driver, OpenEnvDriver(url)), insts, concurrency=64))
        elapsed = time.perf_counter() - started
        assert found == [], "\n".join(map(str, found[:20]))
        print(f"\nisolation: {len(insts) * len(PLANS)} episodes at concurrency 64 in {elapsed:.1f}s")
        ws_url = url.replace("http://", "ws://") + "/ws"
        with ExitStack() as held:
            for _ in range(DEFAULT_MAX_CONCURRENT_ENVS):
                ws = held.enter_context(connect(ws_url))
                ws.send(json.dumps({"type": "state"}))
                assert json.loads(ws.recv())["type"] == "state"  # the session exists
            with connect(ws_url) as extra:
                refused = json.loads(extra.recv())
            assert refused["type"] == "error" and refused["data"]["code"] == "CAPACITY_REACHED", refused


def test_shipped_oracle_floor_and_canaries_pass_rfc008(server_url: str) -> None:
    """validation/runtime.json, floor.json and canaries/*.json, played as OpenEnv's runtime collector plays a plan."""
    measured = runtime_plan_evidence(server_url)
    assert measured.oracle == (1,) and measured.floor == 0 and len(measured.canaries) == 8
    results = semantic_checks(measured)
    assert all(r.status is CheckStatus.PASS for r in results), [(r.check_id, r.evidence) for r in results]


def test_reset_round_trip_latency(server_url: str) -> None:
    """Warm reset round trips over one WebSocket session (recorded; the bound is only a sanity check)."""
    samples = []
    with RubberstampEnv(base_url=server_url).sync() as env:
        for i in range(200):
            inst = ALL[i % len(ALL)]
            started = time.perf_counter_ns()
            env.reset(instance_id=inst.id)
            samples.append((time.perf_counter_ns() - started) / 1e6)
    samples.sort()
    p50, p95 = statistics.median(samples), samples[int(0.95 * len(samples)) - 1]
    print(f"\nreset round trip over /ws: p50 {p50:.2f} ms, p95 {p95:.2f} ms (n={len(samples)})")
    assert p95 < 250
