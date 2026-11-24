"""The per-process world cache under threads (P0.4): `world_path` and `initial_snapshot` fill their caches under a
lock, so 32 threads on a cold cache build each seed exactly once and every world is the committed one."""

import json
import random
import threading
from pathlib import Path

import pytest
from rs_core.diff import snapshot_digest
from rs_world import session
from rs_world.schema import SCHEMA
from rs_world.seed import world_digest

DIGESTS = Path(__file__).resolve().parents[3] / "results" / "certify" / "world_digests.json"
SEEDS = (42, *range(1, 20))
THREADS = 32


@pytest.fixture
def cold_cache(monkeypatch):
    """An empty world cache (as in a fresh process) whose builds are counted; the warm cache comes back after."""
    monkeypatch.setattr(session, "_worlds", {})
    monkeypatch.setattr(session, "_initial", {})
    monkeypatch.setattr(session, "_seed_locks", {})
    monkeypatch.setattr(session, "_cache_dir", None)
    builds: dict[int, int] = {}
    count = threading.Lock()
    real_seed = session.seed

    def counted(seed_value, out_dir=None):
        with count:
            builds[seed_value] = builds.get(seed_value, 0) + 1
        return real_seed(seed_value, out_dir)

    monkeypatch.setattr(session, "seed", counted)
    yield builds
    cache_dir = session._cache_dir
    monkeypatch.undo()
    if cache_dir is not None:
        cache_dir.cleanup()


def hammer(fn, threads=THREADS):
    """Call fn(seed) from `threads` threads at once, each over every seed in its own order; return each result."""
    barrier = threading.Barrier(threads)
    results: list[dict[int, object]] = [{} for _ in range(threads)]
    errors: list[BaseException] = []

    def work(k):
        order = list(SEEDS)
        random.Random(k).shuffle(order)
        try:
            barrier.wait()
            for s in order:
                results[k][s] = fn(s)
        except BaseException as exc:  # surfaced in the main thread
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(k,)) for k in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    assert not errors, errors
    return results


def test_32_threads_on_a_cold_cache_build_each_seed_once(cold_cache):
    committed = json.loads(DIGESTS.read_text())["world_digests"]
    results = hammer(session.world_path)
    assert cold_cache == {s: 1 for s in SEEDS}  # each seed built exactly once
    for s in SEEDS:
        paths = {r[s] for r in results}
        assert len(paths) == 1  # every thread got the same file
        assert world_digest(paths.pop()) == committed[str(s)], s


def test_initial_snapshots_are_read_once_and_shared(cold_cache):
    committed = json.loads(DIGESTS.read_text())["world_digests"]
    results = hammer(session.initial_snapshot, threads=16)
    assert cold_cache == {s: 1 for s in SEEDS}
    for s in SEEDS:
        snapshots = {id(r[s]) for r in results}
        assert len(snapshots) == 1  # one object per seed, shared
        assert snapshot_digest(results[0][s], SCHEMA) == committed[str(s)], s


def test_prewarm_builds_every_seed_up_front(cold_cache):
    paths = session.prewarm(SEEDS[:3])
    assert set(paths) == set(SEEDS[:3]) == set(session._initial)
    assert cold_cache == {s: 1 for s in SEEDS[:3]}
    assert session.prewarm(SEEDS[:3]) == paths and cold_cache == {s: 1 for s in SEEDS[:3]}  # warm: no rebuild
