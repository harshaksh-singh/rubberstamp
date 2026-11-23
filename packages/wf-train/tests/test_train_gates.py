"""Stop rules during training and the base-sweep go/no-go (finding #37)."""

import json
import math

import pytest
from transformers.trainer_callback import TrainerControl, TrainerState
from wf_train.config import GateSettings
from wf_train.gates import GateMonitor, base_gate
from wf_train.rollouts import RolloutStore
from wf_train.train import main
from wf_train.trainer import GateCallback
from wftrain_helpers import WIDE, config_file

HEALTHY = {
    "loss": 0.01,
    "grad_norm": 0.5,
    "frac_reward_zero_std": 0.3,
    "tools/call_frequency": 2.5,
    "rewards/unparsed_tool_call/mean": 0.05,
    "step_time": 90.0,
    "sampling/sampling_logp_difference/mean": 0.02,
}


def feed(monitor, logs, times):
    reasons = [monitor.update(logs) for _ in range(times)]
    return reasons


def test_healthy_steps_never_stop_training():
    monitor = GateMonitor(GateSettings())
    assert feed(monitor, HEALTHY, 50) == [None] * 50
    assert monitor.update({"train_runtime": 3.0, "eval_loss": float("nan")}) is None  # not a training step


@pytest.mark.parametrize(
    ("key", "value", "steps"),
    [
        ("frac_reward_zero_std", 0.65, 10),  # research/64: above 0.6, SFT first
        ("tools/call_frequency", 0.0, 5),
        ("rewards/unparsed_tool_call/mean", 0.25, 5),  # research/64: parse failures under 20%
        ("step_time", 200.0, 3),
        ("sampling/sampling_logp_difference/mean", 0.8, 3),
    ],
)
def test_each_rule_fires_once_its_window_is_full(key, value, steps):
    monitor = GateMonitor(GateSettings())
    reasons = feed(monitor, HEALTHY | {key: value}, steps)
    assert reasons[:-1] == [None] * (steps - 1) and key in reasons[-1]


def test_a_window_averages_and_zero_tool_calls_must_be_consecutive():
    monitor = GateMonitor(GateSettings())
    for value in [0.9] * 5 + [0.2] * 5:  # mean 0.55 over 10 steps: under the 0.6 limit
        assert monitor.update(HEALTHY | {"frac_reward_zero_std": value}) is None
    monitor = GateMonitor(GateSettings())
    for seconds in (600.0, 90.0, 95.0, 100.0):  # a slow first step alone is not over budget (median)
        assert monitor.update(HEALTHY | {"step_time": seconds}) is None
    monitor = GateMonitor(GateSettings())
    for calls in (0, 0, 0, 0, 1, 0, 0, 0, 0):
        assert monitor.update(HEALTHY | {"tools/call_frequency": calls}) is None
    assert "tools/call_frequency" in monitor.update(HEALTHY | {"tools/call_frequency": 0})


@pytest.mark.parametrize("key", ["loss", "grad_norm"])
def test_a_nan_loss_or_gradient_stops_at_once(key):
    assert f"{key} is nan" == GateMonitor(GateSettings()).update(HEALTHY | {key: math.nan})


def test_unset_limits_and_missing_metrics_are_skipped():
    monitor = GateMonitor(GateSettings(step_time_max=None, logp_diff_max=None))
    assert feed(monitor, HEALTHY | {"step_time": 1e6, "sampling/sampling_logp_difference/mean": 9.0}, 20) == [None] * 20
    assert feed(GateMonitor(GateSettings()), {"loss": 0.1, "frac_reward_zero_std": None}, 20) == [None] * 20


def test_the_callback_stops_training_and_remembers_why():
    gate = GateCallback(GateMonitor(GateSettings(no_tool_call_steps=2)))
    state, control = TrainerState(global_step=7), TrainerControl()
    gate.on_log(None, state, control, logs=HEALTHY | {"tools/call_frequency": 0})
    assert not control.should_training_stop and gate.reason is None
    gate.on_log(None, state, control, logs=HEALTHY | {"tools/call_frequency": 0})
    assert control.should_training_stop and gate.reason.startswith("step 7: tools/call_frequency was 0")


def base_rows(rates: dict[str, tuple[str, int, int]]):
    """Tidy strict rows of a base sweep: task -> (template, n, passed)."""
    rows = []
    for task, (template, n, passed) in rates.items():
        for i in range(n):
            rows.append(
                {"phase": "base", "kind": "verifier", "scorer": "strict", "task_id": task, "template": template,
                 "passed": i < passed}
            )  # fmt: skip
    return rows


def test_the_base_gate_checks_per_task_pass_at_g_and_the_zero_variance_share():
    settings = GateSettings()
    ok = base_rows({"a": ("A", 64, 8), "b": ("B", 64, 4), "held": ("H", 64, 64)})
    report = base_gate(ok, strict="strict", templates=["A", "B"], g=8, settings=settings)
    assert report["ok"] and report["tasks"]["held"]["gated"] is False  # held-out tasks are not gated
    assert report["tasks"]["a"]["pass@8"] == pytest.approx(1 - math.comb(56, 8) / math.comb(64, 8))
    p = 8 / 64, 4 / 64
    expected = sum(x**8 + (1 - x) ** 8 for x in p) / 2
    assert report["zero_variance_share"] == pytest.approx(expected) and expected <= 0.6

    easy = base_rows({"a": ("A", 64, 60), "b": ("B", 64, 4)})  # pass@8 of `a` is 1.0: out of range
    report = base_gate(easy, strict="strict", templates=["A", "B"], g=8, settings=settings)
    assert not report["ok"] and report["outside_range"] == ["a"]

    hard = base_rows({"a": ("A", 64, 1), "b": ("B", 64, 1)})  # in range, but most groups all fail
    report = base_gate(hard, strict="strict", templates=["A", "B"], g=8, settings=settings.model_copy(
        update={"base_pass_range": (0.0, 1.0)}))  # fmt: skip
    assert report["zero_variance_share"] > 0.6 and not report["ok"]

    partial = base_gate(ok, strict="strict", templates=["A", "B", "C"], g=8, settings=settings)
    assert not partial["ok"] and partial["unsampled_templates"] == ["C"]


def test_the_gate_command_exits_non_zero_on_a_no_go(tmp_path, capsys):
    """`wf-train --gate` rescoring a base sweep stored in the GPU layout: the scripted policy's base model
    solves the toy tasks too often (pass@8 above 70%), so it is a no-go."""
    config = config_file(tmp_path, "smoke", world=WIDE)
    dry = tmp_path / "dry"
    assert main(["--config", str(config), "--out", str(dry), "--dry-run"]) == 0
    out = tmp_path / "out"
    base = [r for r in RolloutStore(dry / "rollouts.jsonl").read() if r.phase == "base"]
    RolloutStore(out / "base" / "rollouts.jsonl").append(base)
    capsys.readouterr()
    assert main(["--config", str(config), "--out", str(out), "--gate"]) == 1
    report = json.loads((out / "base_gate.json").read_text())
    assert report["base_rollouts"] == 20 * 64 and not report["ok"] and report["outside_range"]
    assert main(["--config", str(config), "--out", str(tmp_path / "empty"), "--gate"]) == 1  # no sweep: no-go
