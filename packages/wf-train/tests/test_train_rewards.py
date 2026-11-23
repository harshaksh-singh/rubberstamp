"""Reward functions (verifiers as rewards) and the verifier-independent detectors."""

import pytest
from trl.trainer.utils import get_callable_name
from wf_core.models import ToolCall
from wf_core.testing.toyworld import SCHEMA, TASKS, TOOLS, ToyExecutor, golden_calls
from wf_train.config import load_config
from wf_train.design import make_variant
from wf_train.detectors import DETECTOR_NAMES, Precedes, detect, profile_from_golden
from wf_train.env import episode_for
from wf_train.rewards import (
    CUT_OFF,
    UNPARSED,
    CellVerifierReward,
    DetectorReward,
    detector_rewards,
    stop_rewards,
    verifier_reward,
)
from wf_train.scripted import Script
from wf_train.toy import SCRIPTS, toy_world
from wf_train.train import build_manifest, reward_functions
from wftrain_helpers import CONFIGS, BrokenStub, StatusOnlyStub, StrictStub, new_env, rollout, trl_call, wide_toy_world

# (task, script) -> (strict stub reward, status-only stub reward)
STUB_CASES = {
    ("refund_email", "golden"): (1.0, 1.0),
    ("refund_email", "partial_write"): (0.0, 1.0),  # 1-cent refund: the status still flips
    ("refund_email", "side_effect_missing"): (0.0, 1.0),
    ("close_ticket", "collateral_edits"): (0.0, 1.0),
    ("close_ticket", "claim_without_write"): (0.0, 0.0),
    ("refund_email", "noop"): (0.0, 0.0),
}


def test_verifier_reward_strict_vs_lenient_stub_on_golden_and_shortcuts():
    envs, completions = zip(*(rollout(task, script) for task, script in STUB_CASES), strict=True)
    strict, lenient = verifier_reward(StrictStub(), "strict"), verifier_reward(StatusOnlyStub())
    assert get_callable_name(strict) == "strict" and get_callable_name(lenient) == "stub.status_only"
    got = list(zip(trl_call(strict, envs, completions), trl_call(lenient, envs, completions), strict=True))
    assert got == list(STUB_CASES.values())


def test_a_harness_error_is_left_out_rather_than_scored_zero():
    env, completion = rollout("refund_email", "golden")
    assert trl_call(verifier_reward(BrokenStub()), [env], [completion]) == [None]


def test_reward_functions_need_the_environments():
    with pytest.raises(ValueError, match="environment_factory"):
        verifier_reward(StrictStub())(completions=[[]])


EXPECTED = {
    (task, name): ({name} if name in DETECTOR_NAMES else set()) for task, scripts in SCRIPTS.items() for name in scripts
}


@pytest.mark.parametrize(("task_id", "script"), sorted(EXPECTED))
def test_each_script_fires_exactly_its_own_detector_and_golden_fires_none(task_id, script):
    env, completion = rollout(task_id, script)
    episode = episode_for(env, completion)
    fired = {name for name, hit in detect(episode, toy_world().profiles[task_id], SCHEMA, TOOLS).items() if hit}
    assert fired == EXPECTED[(task_id, script)]


def test_the_status_only_shortcut_of_wf_verify_shows_two_behaviours():
    """A 1-cent refund and no email is both a partial write and a missing side effect."""
    script = Script(name="status_only", calls=(("create_refund", {"order_id": "o1", "amount_cents": 1}),))
    env, completion = rollout("refund_email", script)
    hits = detect(episode_for(env, completion), toy_world().profiles["refund_email"], SCHEMA, TOOLS)
    assert {name for name, hit in hits.items() if hit} == {"partial_write", "side_effect_missing"}


def test_order_violation_uses_the_configured_pairs_and_argument_matching():
    executor, task = ToyExecutor(), TASKS["refund_approved"]
    golden = golden_calls(task.id)
    loose = profile_from_golden(executor, task, golden, [Precedes(before="request_approval", after="create_refund")])
    tight = profile_from_golden(
        executor,
        task,
        golden,
        [Precedes(before="request_approval", after="create_refund", same_args=(("order_id", "order_id"),))],
    )
    unconfigured = profile_from_golden(executor, task, golden)
    approve_o3 = ToolCall(idx=0, tool="request_approval", args={"order_id": "o3"})
    refund_o2 = ToolCall(idx=1, tool="create_refund", args={"order_id": "o2", "amount_cents": 12000})
    wrong_record = executor.run(task, [approve_o3, refund_o2])
    assert not detect(wrong_record, loose, SCHEMA, TOOLS)["order_violation"]
    assert detect(wrong_record, tight, SCHEMA, TOOLS)["order_violation"]
    swapped = executor.run(task, [refund_o2.model_copy(update={"idx": 0}), golden[0].model_copy(update={"idx": 1})])
    assert detect(swapped, tight, SCHEMA, TOOLS)["order_violation"]
    assert not detect(swapped, unconfigured, SCHEMA, TOOLS)["order_violation"]


def test_a_golden_run_with_failing_calls_cannot_be_a_profile():
    bad = [ToolCall(idx=0, tool="create_refund", args={"order_id": "o1", "amount_cents": 10**9})]
    with pytest.raises(ValueError, match="failing calls"):
        profile_from_golden(ToyExecutor(), TASKS["refund_email"], bad)


def test_detector_rewards_are_zero_one_signals():
    world = toy_world()
    funcs = detector_rewards(world.profiles, SCHEMA, TOOLS)
    assert [get_callable_name(f) for f in funcs] == list(DETECTOR_NAMES)
    envs, completions = zip(*(rollout("refund_email", s) for s in ("golden", "duplicate_writes")), strict=True)
    duplicate = funcs[DETECTOR_NAMES.index("duplicate_writes")]
    assert trl_call(duplicate, envs, completions) == [0.0, 1.0]
    assert trl_call(DetectorReward("collateral_edits", {}, SCHEMA, TOOLS), envs, completions) == [None, None]
    with pytest.raises(KeyError):
        DetectorReward("nope", world.profiles, SCHEMA, TOOLS)


def test_the_training_reward_grades_each_rollout_with_its_cells_variant():
    world = toy_world()
    variants = {v.id: v for v in map(make_variant, ["strict", "status_only", "trust_claim"])}
    envs, completions = zip(*(rollout("refund_email", "partial_write") for _ in range(3)), strict=True)
    rows = [{"task_id": "refund_email", "variant": v} for v in ("strict", "status_only", "trust_claim")]
    train = CellVerifierReward(world.verifier, variants, name="train_verifier")
    assert trl_call(train, envs, completions, rows) == [0.0, 1.0, 1.0]  # trust_claim: "Done." claims success
    logged = CellVerifierReward(world.verifier, variants, name="strict_verifier", fixed="strict")
    assert trl_call(logged, envs, completions, rows) == [0.0, 0.0, 0.0]


def test_one_reward_trains_and_the_rest_are_logged_at_weight_zero():
    cfg, world = load_config(CONFIGS / "main.yaml"), wide_toy_world()
    funcs, weights = reward_functions(cfg, world, build_manifest(cfg, world))
    names = ["train_verifier", "strict_verifier", *DETECTOR_NAMES, UNPARSED, CUT_OFF]
    assert [get_callable_name(f) for f in funcs] == names
    assert weights == [1.0] + [0.0] * (len(funcs) - 1)


def test_how_each_rollout_ended_is_logged_every_step():
    """Finding #35: TRL's tools/failure_frequency only counts calls that parsed, so unparsed calls get their
    own zero-weight reward (and a cut-off share for rollouts that ran out of iterations or tokens)."""
    call = {"type": "function", "function": {"name": "set_ticket_status", "arguments": {"ticket_id": "t1"}}}
    lasts = [
        {"role": "assistant", "content": "Ticket t1 is closed."},
        {"role": "assistant", "content": '<tool_call>\n{"name": "set_ticket_status", "arguments": {"ticket_id"'},
        {"role": "assistant", "content": "", "tool_calls": [call]},
    ]
    envs = [new_env() for _ in lasts]
    for env in envs:
        env.reset(task_id="close_ticket")
    unparsed, cut_off = stop_rewards()
    assert (get_callable_name(unparsed), get_callable_name(cut_off)) == (UNPARSED, CUT_OFF)
    assert trl_call(unparsed, envs, [[m] for m in lasts]) == [0.0, 1.0, 0.0]
    assert trl_call(cut_off, envs, [[m] for m in lasts]) == [0.0, 0.0, 1.0]
