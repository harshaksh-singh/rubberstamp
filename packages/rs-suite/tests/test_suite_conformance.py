"""The conformance contract itself (P0.6): instances, the committed instance matrix and seed digests, and that
`check` and `check_isolation` catch every kind of non-conforming driver. The all-500 runs are sharded into
test_suite_conformance_shard<k>.py."""

import asyncio
import dataclasses
import json
import tempfile
from pathlib import Path

import pytest
from rs_core.models import ToolCall
from rs_suite import VARIANTS, knob, task_ids
from rs_suite import conformance as conf
from rs_suite.conformance import (
    PLANS,
    Driver,
    Instance,
    Outcome,
    ReferenceDriver,
    check,
    check_isolation,
    expected,
    instance,
    instances,
    plan_for,
    required_reward,
    world_digests,
)
from rs_suite.instance_matrix import JSON_NAME, MARKDOWN_NAME, matrix_markdown, matrix_text
from rs_suite.instances import SEEDS, instance_task
from rs_world import WorldSession
from rs_world.oracle_kit import Plan, call
from rs_world.session import prewarm, world_path

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "results" / "suite"
DIGESTS = ROOT / "results" / "certify" / "world_digests.json"
WORLD_42_DIGEST = "4aa28cf3b609605f593ca10d34c449769616265109ae052482b523cf2b4c3bf3"  # pinned in rs-world too
T08 = Instance("t08_refund_with_approval", 7, "train")


# ---------------------------------------------------------------- instances


def test_500_instances_in_a_stable_task_then_seed_order():
    every = instances()
    assert len(every) == 500 == len({i.id for i in every})
    assert [i.id for i in every[:3]] == ["t01_order_status@42", "t01_order_status@1", "t01_order_status@2"]
    assert every[20].id == "t02_refund_window_question@42"
    assert {split: len(instances(split)) for split in ("train", "dev", "test", "heldout")} == {
        "train": 320,
        "dev": 80,
        "test": 100,  # rs-train's held-out templates (tests/integration/test_benchmark_hygiene.py)
        "heldout": 0,
    }
    assert all(i.split == "test" for i in instances("test"))
    assert instances(seeds=(7, 7, 3))[:2] == [
        Instance("t01_order_status", 7, "train"),
        Instance("t01_order_status", 3, "train"),
    ]
    assert instance("t08_refund_with_approval@7") == T08 and T08.id == "t08_refund_with_approval@7"
    with pytest.raises(KeyError):
        instance("t99_nothing@42")
    with pytest.raises(ValueError):
        plan_for(T08, "silent")


def test_the_plans_end_with_the_claim_and_the_rules_are_the_planted_ground_truth():
    for name in PLANS:
        assert plan_for(T08, name).calls[-1][0] == "submit_resolution"
    assert {v: required_reward(T08, "golden", v) for v in VARIANTS} == dict.fromkeys(VARIANTS, 1.0)
    assert {v: required_reward(T08, "noop", v) for v in VARIANTS} == dict.fromkeys(VARIANTS, 0.0)
    shortcut = {v: required_reward(T08, "shortcut", v) for v in VARIANTS}
    assert shortcut.pop("strict") == 0.0 and shortcut.pop(knob(T08.task_id).value) == 1.0
    assert set(shortcut.values()) == {None}  # the other variants are pinned by the matrix, not by a rule


# ---------------------------------------------------------------- committed results


def test_committed_instance_matrix_holds_every_rule_and_serialises_byte_for_byte():
    text = (SUITE / JSON_NAME).read_text()
    data = json.loads(text)
    assert matrix_text(data) == text, "run scripts/instance_matrix.py"
    assert matrix_markdown(data) == (SUITE / MARKDOWN_NAME).read_text(), "run scripts/instance_matrix.py"
    assert data["seeds"] == list(SEEDS) and data["plans"] == list(PLANS) and data["variants"] == list(VARIANTS)
    assert list(data["instances"]) == [i.id for i in instances()]  # every row; the shards check their values
    summary = data["summary"]
    assert summary["instances"] == 500 and summary["verdicts"] == 500 * 3 * 11
    assert summary["held"] == dict.fromkeys(summary["held"], 500) and summary["broken"] == []
    manifest = json.loads((ROOT / "packages" / "rs-world" / "tool_manifest.json").read_text())
    assert data["registry_manifest_sha256"] == manifest["sha256"]


def test_the_seed_42_slice_of_the_instance_matrix_is_the_shortcut_matrix():
    rows = json.loads((SUITE / JSON_NAME).read_text())["instances"]
    shortcut_matrix = json.loads((SUITE / "shortcut_matrix.json").read_text())
    for task_id in task_ids():
        row = rows[f"{task_id}@42"]
        assert row["world_digest"] == shortcut_matrix["world_digest"] == WORLD_42_DIGEST
        for kind in ("shortcut", "noop"):
            paid = [v for v, r in zip(VARIANTS, row[kind]["rewards"], strict=True) if r == 1.0]
            assert paid == shortcut_matrix["accepted_by"][kind][task_id], (task_id, kind)


def test_world_digests_regenerate_byte_for_byte():
    assert DIGESTS.read_text() == json.dumps(world_digests(), indent=1) + "\n", "run scripts/world_digests.py"
    data = json.loads(DIGESTS.read_text())
    assert list(data["world_digests"]) == [str(s) for s in SEEDS] and len(set(data["world_digests"].values())) == 20
    assert data["world_digests"]["42"] == WORLD_42_DIGEST
    manifest = json.loads((ROOT / "packages" / "rs-world" / "tool_manifest.json").read_text())
    assert data["registry_manifest_sha256"] == manifest["sha256"]


# ---------------------------------------------------------------- check() bites


@dataclasses.dataclass
class Tampered:
    """The reference driver with its outcome edited: what a broken adapter would report."""

    edit: object
    inner: Driver = dataclasses.field(default_factory=ReferenceDriver)

    def run(self, inst, plan):
        return self.edit(self.inner.run(inst, plan))  # type: ignore[operator]


def kinds(driver, plans=PLANS):
    return {(m.plan, m.what) for m in check(driver, [T08], plans)}


def test_check_passes_the_reference_driver_and_a_strict_only_driver():
    assert isinstance(ReferenceDriver(), Driver)
    assert check(ReferenceDriver(), [T08]) == []
    assert check(ReferenceDriver(variants=("strict",)), [T08]) == []
    with pytest.raises(ValueError):
        ReferenceDriver(variants=("trust_claim",))


def test_check_catches_wrong_rewards_and_broken_rules():
    def lenient(o):  # an adapter whose reward is the task's own knob: pays the shortcut
        return dataclasses.replace(o, rewards={**o.rewards, "strict": o.rewards[knob(T08.task_id).value]})

    assert ("shortcut", "reward:strict") in kinds(Tampered(lenient))
    assert kinds(Tampered(lambda o: dataclasses.replace(o, rewards={"strict": 1.0})), ("noop",)) == {
        ("noop", "reward:strict")
    }
    assert kinds(Tampered(lambda o: dataclasses.replace(o, rewards={"trust_claim": 0.0}))) >= {("golden", "rewards")}
    assert kinds(Tampered(lambda o: dataclasses.replace(o, rewards={**o.rewards, "lenient": 1.0}))) >= {
        ("golden", "reward:lenient")
    }


@pytest.mark.parametrize("bad", [True, 1, float("nan"), 1.5, -0.0 - 1, "1.0", None])
def test_check_rejects_a_reward_that_is_not_a_finite_float_in_0_1(bad):
    driver = Tampered(lambda o: dataclasses.replace(o, rewards={**o.rewards, "strict": bad}))
    assert kinds(driver, ("golden",)) == {("golden", "reward:strict")}


def test_check_catches_another_end_state_other_calls_and_a_driver_that_raises():
    assert kinds(Tampered(lambda o: dataclasses.replace(o, final_digest="0" * 64)), ("golden",)) == {
        ("golden", "final_digest")
    }
    assert kinds(Tampered(lambda o: dataclasses.replace(o, calls=o.calls[:-1])), ("golden",)) == {("golden", "calls")}
    edited = Tampered(
        lambda o: dataclasses.replace(o, calls=(o.calls[0].model_copy(update={"result": {}}),) + o.calls[1:])
    )
    assert kinds(edited, ("golden",)) == {("golden", "calls")}

    class Broken:
        def run(self, inst, plan):
            raise ConnectionError("server gone")

    found = check(Broken(), [T08], ("golden",))
    assert [(m.what, m.got) for m in found] == [("error", "ConnectionError: server gone")]  # reported, never scored


def test_the_reference_driver_ends_the_episode_at_the_claim():
    golden = plan_for(T08, "golden")
    late = Plan(calls=(*golden.calls, call("send_email", customer_id="CUS-00001", subject="x", body="y")), answer="")
    driver = ReferenceDriver()
    got = driver.run(T08, late)
    assert driver.refused == 1 and got == expected(T08, "golden")


def test_a_replay_mismatch_raises_and_check_reports_it(monkeypatch):
    real = conf.replay

    def drifting(task_id, seed, calls, final_answer=None, *, live_digest=None):
        return real(task_id, seed, list(calls)[:-1], final_answer, live_digest=live_digest)  # loses the claim

    monkeypatch.setattr(conf, "replay", drifting)
    found = check(ReferenceDriver(), [T08], ("golden",))
    assert [m.what for m in found] == ["error"] and found[0].got.startswith("ReplayMismatch")


# ---------------------------------------------------------------- isolation under concurrency


@pytest.fixture
def private_tmp(monkeypatch):
    """Episode temp files in a directory of their own (other xdist workers share the system temp dir). The world
    cache is filled first, so it stays where it was."""
    prewarm(SEEDS)
    with tempfile.TemporaryDirectory(prefix="rs-conformance-") as tmp:
        monkeypatch.setattr(tempfile, "tempdir", tmp)
        yield Path(tmp)


def test_32_concurrent_reference_drivers_match_their_solo_runs(private_tmp):
    insts = instances(seeds=(42, 7, 13))[::3]  # 25 instances over all three seeds
    found = asyncio.run(check_isolation(ReferenceDriver, insts, concurrency=32, temp_dir=private_tmp))
    assert found == []
    assert not list(private_tmp.glob("rs-episode-*"))


def test_check_isolation_catches_crosstalk_and_leaked_worlds(private_tmp):
    leaked: list[WorldSession] = []

    class Leaky:
        """Opens a world per episode and never closes it."""

        def run(self, inst, plan):
            leaked.append(WorldSession(world_path(inst.seed)))
            return expected(inst, "golden")

    try:
        found = asyncio.run(
            check_isolation(Leaky, [T08], 2, plans=("golden", "noop"), temp_dir=private_tmp, settle_s=0.2)
        )
    finally:
        for session in leaked:
            session.path.unlink()  # its connection belongs to a pool thread; the file is what leaked
    assert {(m.plan, m.what) for m in found} >= {("noop", "final_digest"), ("*", "temp_files")}


def test_check_isolation_awaits_async_drivers_and_closes_them(private_tmp):
    closed = []

    class AsyncDriver:
        async def run(self, inst, plan):
            await asyncio.sleep(0)
            return ReferenceDriver().run(inst, plan)

        async def aclose(self):
            closed.append(True)

    async def make():
        return AsyncDriver()

    insts = [T08, Instance("t01_order_status", 3, "train")]
    assert asyncio.run(check_isolation(make, insts, concurrency=4, temp_dir=private_tmp)) == []
    assert len(closed) == len(insts) * len(PLANS)


def test_expected_is_the_in_process_replay():
    got = expected(T08, "golden")
    assert [c.tool for c in got.calls] == [tool for tool, _ in plan_for(T08, "golden").calls]
    assert all(isinstance(c, ToolCall) and c.ok for c in got.calls)
    assert set(got.rewards) == set(VARIANTS) and instance_task(T08.task_id, T08.seed).id == T08.id
    assert isinstance(got, Outcome)


def test_check_runs_an_async_driver_to_completion():
    class AsyncReference:
        async def run(self, inst, plan):
            await asyncio.sleep(0)
            return ReferenceDriver().run(inst, plan)

    assert check(AsyncReference(), [T08]) == []


def test_check_isolation_reports_a_driver_that_cannot_start(private_tmp):
    def broken():
        raise ConnectionRefusedError("no server")

    found = asyncio.run(check_isolation(broken, [T08], 2, plans=("golden",), temp_dir=private_tmp))
    assert [(m.what, m.got) for m in found] == [("error", "ConnectionRefusedError: no server")]
