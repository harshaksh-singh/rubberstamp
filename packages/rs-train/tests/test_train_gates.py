"""Stop rules during training and the base-sweep go/no-go (finding #37)."""

import json
import math

import pytest
from rs_train.config import GateSettings
from rs_train.gates import GateMonitor, base_gate
from rs_train.rollouts import RolloutStore
from rs_train.train import main
from rs_train.trainer import GateCallback
from rstrain_helpers import WIDE, config_file
from transformers.trainer_callback import TrainerControl, TrainerState

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


def base_rows(rates: dict[str, tuple[str, int, int]], scorer: str = "strict"):
    """Tidy verifier rows of a base sweep under one grader: task -> (template, n, passed)."""
    rows = []
    for task, (template, n, passed) in rates.items():
        for i in range(n):
            rows.append(
                {"phase": "base", "kind": "verifier", "scorer": scorer, "task_id": task, "template": template,
                 "passed": i < passed}
            )  # fmt: skip
    return rows


def test_the_base_gate_checks_per_template_pass_rates_and_the_zero_variance_share():
    settings = GateSettings()
    ok = base_rows({"a": ("A", 64, 16), "b": ("B", 64, 8), "held": ("H", 64, 64)})
    report = base_gate(ok, strict="strict", templates=["A", "B"], g=8, settings=settings)
    assert report["ok"] and report["unit"] == "template" and report["pass_metric"] == "pass@1"
    assert report["templates"]["H"]["gated"] is False  # held-out templates are reported, not gated
    assert report["templates"]["A"]["pass@1"] == 0.25
    assert report["templates"]["A"]["pass@8"] == pytest.approx(1 - math.comb(48, 8) / math.comb(64, 8))  # reported
    p = 16 / 64, 8 / 64
    expected = sum(x**8 + (1 - x) ** 8 for x in p) / 2
    assert report["zero_variance_share"] == pytest.approx(expected) and expected <= 0.6
    assert report["zero_variance_by_seed"] == {0: pytest.approx(expected)}

    easy = base_rows({"a": ("A", 64, 60), "b": ("B", 64, 8)})  # strict passes 94% of `A`'s samples: solved already
    report = base_gate(easy, strict="strict", templates=["A", "B"], g=8, settings=settings)
    assert not report["ok"] and report["outside_range"] == ["A"]

    hard = base_rows({"a": ("A", 64, 1), "b": ("B", 64, 1)})  # in range, but most groups all fail
    report = base_gate(hard, strict="strict", templates=["A", "B"], g=8, settings=settings.model_copy(
        update={"base_pass_range": (0.0, 1.0)}))  # fmt: skip
    assert report["zero_variance_share"] > 0.6 and not report["ok"]

    partial = base_gate(ok, strict="strict", templates=["A", "B", "C"], g=8, settings=settings)
    assert not partial["ok"] and partial["unsampled_templates"] == ["C"]


@pytest.mark.parametrize(
    ("rate", "go"), [(0.05, False), (0.1, True), (0.2, True), (0.3, True), (0.5, True), (0.95, False)]
)
def test_the_base_gate_accepts_the_pass_rates_grpo_learns_from(rate, go):
    """Finding 10: gated on pass@8 in 10-70%, a template passed only for p of about 6-14% (pass@8 is 0.84 at
    p = 0.2), so the templates whose groups vary most were no-go. Gated on the per-sample rate, 320 samples per
    template as on main: 20-50% is a go, 95% (already solved) and 5% (mostly all-fail groups) are not."""
    n = 320
    rows = base_rows({"a": ("A", n, round(rate * n)), "b": ("B", n, round(rate * n))})
    report = base_gate(rows, strict="strict", templates=["A", "B"], g=8, settings=GateSettings())
    assert report["ok"] is go, report["outside_range"]
    zero = rate**8 + (1 - rate) ** 8
    assert report["zero_variance_share"] == pytest.approx(zero, abs=1e-9)


def test_the_zero_variance_share_uses_the_grader_each_cell_trains_on():
    """Finding 12: a mixed run rewards most cells with a lenient grader. Strict passes 30% of the base samples
    of both templates (a go under strict alone), but in seed 1 both cells are graded by a lenient variant that
    pays 97% of them: most of that run's groups are all-1, so the seed's predicted share is above the cap."""
    rates = {"a": ("A", 320, 96), "b": ("B", 320, 96)}
    rows = base_rows(rates) + base_rows({"a": ("A", 320, 310), "b": ("B", 320, 310)}, scorer="trust_claim")
    settings = GateSettings()
    assert base_gate(rows, strict="strict", templates=["A", "B"], g=8, settings=settings)["ok"]  # strict alone
    cells = [(0, "A", "strict"), (0, "B", "strict"), (1, "A", "trust_claim"), (1, "B", "trust_claim")]
    report = base_gate(rows, strict="strict", templates=["A", "B"], g=8, settings=settings, cells=cells)
    lenient = (310 / 320) ** 8 + (10 / 320) ** 8
    assert report["zero_variance_by_seed"] == {0: pytest.approx(0.7**8 + 0.3**8), 1: pytest.approx(lenient)}
    assert report["zero_variance_share"] == pytest.approx(lenient) and lenient > 0.6 and not report["ok"]
    assert report["outside_range"] == []  # the strict pass range still holds
    assert [c["pass@1"] for c in report["cells"]] == [0.3, 0.3, 310 / 320, 310 / 320]
    mixed = [(0, "A", "strict"), (0, "B", "trust_claim"), (1, "A", "trust_claim"), (1, "B", "strict")]
    half = base_gate(rows, strict="strict", templates=["A", "B"], g=8, settings=settings, cells=mixed)
    assert half["zero_variance_share"] == pytest.approx((lenient + 0.7**8 + 0.3**8) / 2) and half["ok"]


@pytest.mark.parametrize("per_instance", [8, 16])
def test_the_base_gate_pools_a_templates_procedural_instances(per_instance):
    """Final review: base samples are spread over up to 20 instances per template (8 on smoke, 16 on main). Gated
    per instance, a rate from n = 8 is 0, 1/8, ...; pooled per template, a template whose instances pass 12.5% or
    15.6% of the time is a go (pass rate in range, zero-variance share under its cap)."""
    rates = {}
    for template in ("A", "B"):
        for k in range(20):
            passed = 1 if per_instance == 8 else 2 + k % 2  # 12.5% of 160, or 15.6% of 320
            rates[f"{template.lower()}@{k}"] = (template, per_instance, passed)
    report = base_gate(base_rows(rates), strict="strict", templates=["A", "B"], g=8, settings=GateSettings())
    assert report["ok"], report["outside_range"]
    assert report["templates"]["A"]["n"] == 20 * per_instance and report["templates"]["A"]["instances"] == 20
    assert len(report["tasks"]) == 40  # instances stay visible as a diagnostic


def test_the_gate_command_exits_non_zero_on_a_no_go(tmp_path, capsys):
    """`rs-train --gate` rescoring a base sweep stored in the GPU layout. The scripted policy's base model passes
    strict on about 30% of its samples: a go; with a pass range it misses, a no-go."""
    config = config_file(tmp_path, "smoke", world=WIDE)
    dry = tmp_path / "dry"
    assert main(["--config", str(config), "--out", str(dry), "--dry-run"]) == 0
    out = tmp_path / "out"
    base = [r for r in RolloutStore(dry / "rollouts.jsonl").read() if r.phase == "base"]
    RolloutStore(out / "base" / "rollouts.jsonl").append(base)
    capsys.readouterr()
    assert main(["--config", str(config), "--out", str(out), "--gate"]) == 0
    report = json.loads((out / "base_gate.json").read_text())
    assert report["base_rollouts"] == 20 * 64 and report["ok"] and report["arm"] == "pure_strict"
    assert all(0.1 <= t["pass@1"] <= 0.7 for t in report["templates"].values())
    (tmp_path / "narrow").mkdir()
    narrow = config_file(tmp_path / "narrow", "smoke", world=WIDE, gates={"base_pass_range": [0.5, 0.7]})
    assert main(["--config", str(narrow), "--out", str(out), "--gate"]) == 1
    report = json.loads((out / "base_gate.json").read_text())
    assert not report["ok"] and report["outside_range"] and report["pass_range"] == [0.5, 0.7]
    assert main(["--config", str(config), "--out", str(tmp_path / "empty"), "--gate"]) == 1  # no sweep: no-go
    # strict JSON (jq and most parsers reject a bare NaN): a cell with no samples has rate null, not NaN
    empty = json.loads((tmp_path / "empty" / "base_gate.json").read_text(), parse_constant=_no_constants)
    assert empty["cells"] and all(c["pass@1"] is None for c in empty["cells"])


def _no_constants(name):
    raise ValueError(f"not strict JSON: {name}")
