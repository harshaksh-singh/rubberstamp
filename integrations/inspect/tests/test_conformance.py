"""The conformance proof: rs_suite.conformance.check through the Inspect task on all 500 instances x 3 plans, and
check_isolation with 16 samples running at once inside Inspect.

Every (instance, plan) goes through a real Inspect eval: the registered task (dataset replaced by the jobs), the
replay model, the react agent, Inspect's tool calling, one world per sample, the scorer and the .eval log, from which
the driver reads the outcome. The 1,500 jobs run as 25 evals of 60 samples (one per task, sharded across pytest
workers); `check` then compares every outcome with the in-process replay and the planted ground truth.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from inspect_ai import Epochs
from rs_suite.conformance import PLANS, Driver, check, check_isolation, compare, expected, instances, plan_for
from rs_world.tasks import task_ids

from rs_inspect import episode as episode_module
from rs_inspect.driver import Batcher, BatchingDriver, InspectDriver, InspectRunner
from rs_inspect.episode import live_count

from helpers import episode, samples, score


@pytest.mark.conformance
@pytest.mark.parametrize("task_id", task_ids())
def test_check_passes_on_every_instance_and_plan(task_id: str) -> None:
    insts = [inst for inst in instances() if inst.task_id == task_id]
    assert len(insts) == 20
    driver = InspectDriver(InspectRunner(max_samples=16))
    driver.prefetch([(inst, plan_for(inst, name)) for inst in insts for name in PLANS])
    assert driver.runner.evals == 1  # all 60 outcomes come from one Inspect eval
    assert check(driver, insts, PLANS) == []
    assert driver.runner.evals == 1  # check used the prefetched outcomes, nothing ran outside the eval
    assert live_count() == 0


def test_the_unbatched_driver_passes_too() -> None:
    """`run` without prefetch: one eval per (instance, plan)."""
    insts = [inst for inst in instances() if inst.id in ("t13_manager_approves_refund@42", "t21_gift_order_refund@9")]
    driver = InspectDriver()
    assert check(driver, insts, PLANS) == []
    assert driver.runner.evals == len(insts) * len(PLANS)


class _PeakDict(dict):  # type: ignore[type-arg]
    peak = 0

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        _PeakDict.peak = max(_PeakDict.peak, len(self))


def test_isolation_with_16_samples_at_once(monkeypatch: pytest.MonkeyPatch, private_tempdir: Path) -> None:
    """check_isolation at concurrency 16: concurrent runs are gathered into Inspect evals with max_samples=16, so
    16 samples (16 worlds) run at the same time; every outcome must equal its solo run and every temp world must be
    gone afterwards."""
    monkeypatch.setattr(episode_module, "_LIVE", _PeakDict())
    insts = instances(seeds=(42, 7))  # 50 instances x 3 plans = 150 runs
    runner = InspectRunner(max_samples=16)
    batcher = Batcher(runner)

    def make_driver() -> Driver:
        return cast(Driver, BatchingDriver(batcher))  # an async `run`, which check_isolation awaits

    found = asyncio.run(check_isolation(make_driver, insts, 16, temp_dir=private_tempdir))
    assert found == []
    assert sum(batcher.batches) == 150 and max(batcher.batches) == 16
    assert _PeakDict.peak >= 8  # at least 8 worlds were open at once inside one eval
    assert live_count() == 0


def test_two_concurrent_runs_are_identical(tmp_path: Path) -> None:
    """The same 50 samples twice at max_samples=16: identical calls, digests and scores (and no SQLite thread
    errors, which would end samples with an error)."""
    insts = instances(seeds=(13, 5))
    jobs = [(inst, plan_for(inst, "golden" if i % 2 else "shortcut")) for i, inst in enumerate(insts)]
    runs = []
    for k in range(2):
        log, ids = InspectRunner(max_samples=16).eval_log(jobs, tmp_path / str(k))
        assert all(s.error is None for s in samples(log).values())
        runs.append(
            {
                sid: (episode(s).calls, episode(s).final_digest, score(s).value, score(s).metadata)
                for sid, s in samples(log).items()
            }
        )
    assert runs[0] == runs[1]
    assert len(runs[0]) == 50


def test_epochs_replay_each_instance_on_a_fresh_world(tmp_path: Path) -> None:
    """Inspect epochs: every epoch of a sample plays on its own fresh copy of the same seeded world."""
    insts = [inst for inst in instances() if inst.id in ("t08_refund_with_approval@42", "t20_refund_not_received@4")]
    jobs = [(inst, plan_for(inst, "golden")) for inst in insts]
    log, _ = InspectRunner().eval_log(jobs, tmp_path, epochs=Epochs(3, ["mean", "pass_at_2"]))
    assert log.samples is not None and len(log.samples) == 6
    assert len({episode(s).key for s in log.samples}) == 6  # six worlds
    for inst in insts:
        runs = [s for s in log.samples if s.id == inst.id]
        assert sorted(s.epoch for s in runs) == [1, 2, 3]
        want = expected(inst, "golden")
        for s in runs:
            assert episode(s).final_digest == want.final_digest
            assert score(s).value == 1.0
    assert log.results is not None
    reducers = {s.reducer: s.metrics["mean"].value for s in log.results.scores}
    assert reducers == {"mean": 1.0, "pass_at_2": 1.0}


def test_compare_catches_a_wrong_outcome() -> None:
    """The proof is not vacuous: an Inspect outcome under another plan's name is a mismatch."""
    inst = instances()[0]
    driver = InspectDriver()
    golden = driver.run(inst, plan_for(inst, "golden"))
    assert compare(inst, "golden", golden) == []
    assert {m.what for m in compare(inst, "noop", golden)} >= {"final_digest", "calls", "reward:strict"}
