"""The analysis layer: rollout collection, late-window metrics and curves, base pass@k, the prediction
test, the achievable N per variant, and the arms flowing through `analyze`."""

import json
from collections import defaultdict

import pytest
from wf_core.testing.toyworld import SCHEMA
from wf_train.analysis import _base_pass, _brier, _curves, _final, achievable_n, analyze, collect
from wf_train.config import load_config
from wf_train.design import Cell, Manifest, RunPlan, cell_id, latin_design, make_variant
from wf_train.env import episode_for
from wf_train.labels import label_cells
from wf_train.metrics import pass_at_k
from wf_train.rollouts import RolloutStore, record_of
from wf_train.toy import toy_world
from wftrain_helpers import CONFIGS, rollout


def manifest(cells, variants=("strict", "count_at_least")):
    runs = defaultdict(list)
    for seed, template, variant in cells:
        runs[seed].append(Cell(cell_id=cell_id(template, variant), template=template, variant=variant))
    return Manifest(
        design_seed=0,
        seeds=tuple(sorted(runs)),
        strict_share=0.25,
        train_templates=tuple(sorted({t for _, t, _ in cells})),
        heldout_templates=(),
        variants=tuple(make_variant(v) for v in variants),
        runs=tuple(RunPlan(seed=s, cells=tuple(c)) for s, c in sorted(runs.items())),
    )


def record(task_id, script, *, sample, phase="train", seed=0, step=100, variant="count_at_least", arm="mixed", run="x"):
    cell = f"{task_id}::base" if phase == "base" else cell_id(task_id, variant)
    return record_of(
        episode_for(*rollout(task_id, script)),
        SCHEMA,
        run_id=run,
        phase=phase,
        seed=seed,
        step=step,
        cell_id=cell,
        template=task_id,
        variant="" if phase == "base" else variant,
        sample=sample,
        arm=arm,
    )


def test_collect_refuses_a_rerun_appended_to_the_same_store(tmp_path):
    """Findings 6 and 23: a retried run restarts `sample` at 0 and appends to the same rollouts.jsonl."""
    first = [record("refund_email", "golden", sample=i) for i in range(2)]
    RolloutStore(tmp_path / "base" / "rollouts.jsonl").append(
        [record("refund_email", "golden", sample=0, phase="base")]
    )
    RolloutStore(tmp_path / "seed0" / "rollouts.jsonl").append(first)
    assert len(collect(tmp_path)) == 3
    RolloutStore(tmp_path / "seed0" / "rollouts.jsonl").append(first)  # the retry
    with pytest.raises(ValueError, match="more than once"):
        collect(tmp_path)


def row(seed, step, cell, hacked, *, own=True, strict=False, arm="mixed"):
    """One wide (per-episode) row, as rescore.wide returns it."""
    return {
        "phase": "train",
        "seed": seed,
        "step": step,
        "cell_id": cell,
        "template": cell.split("::")[0],
        "arm": arm,
        "own_pass": own,
        "strict_pass": strict,
        "own_reward": float(own),
        "strict_reward": float(strict),
        "hacked_any": hacked,
        "hacked_opened": hacked,
    }


def late_window_rows():
    rows = [row(0, 100, "A::v", True)] * 90 + [row(0, 100, "B::w", False)] * 10
    rows += [row(1, 100, "A::w", False)] * 10 + [row(1, 100, "B::v", True)] * 10
    return rows + [row(s, 0, "A::v", False) for s in (0, 1) for _ in range(50)]  # early: outside the window


def test_final_weights_every_cell_equally_over_the_late_window():
    """Findings 12 and 39: the pooled late-window hack rate is 100/120, but the cells are 1, 0, 0, 1."""
    final = _final(late_window_rows(), 75)
    hack = final["hack_rate"]
    assert hack["estimate"] == pytest.approx(0.5) and hack["per_seed"] == {0: 0.5, 1: 0.5}
    assert hack["low"] <= hack["estimate"] <= hack["high"] and hack["seed_low"] == hack["seed_high"] == 0.5
    assert (hack["templates"], hack["seeds"], hack["reliable"]) == (2, 2, False)  # fewer than 10 clusters
    assert final["clean_resolved"]["estimate"] == pytest.approx(0.5) and final["paid_hack"]["estimate"] == 0.5
    assert {"hack_rate_opened", "hacked_resolved", "false_pass", "hack_gap"} <= set(final)


def test_final_raises_when_a_seed_has_nothing_in_the_late_window():
    rows = late_window_rows() + [row(2, 40, "A::v", True)]  # seed 2 stopped at step 40
    with pytest.raises(ValueError, match=r"seed\(s\) \[2\]"):
        _final(rows, 75)


def test_curves_are_binned_per_save_interval_with_equal_cell_weights():
    rows = [row(0, s, "A::v", False) for s in range(10)]
    rows += [row(0, 10 + i, "A::v", True) for i in range(9)] + [row(0, 19, "B::w", False)]
    rows += [row(0, 5, "A::strict", True, arm="pure_strict")]
    curves = _curves(rows, 10)
    assert [(c["arm"], c["step"], c["step_end"], c["cells"]) for c in curves] == [
        ("mixed", 0, 10, 1),
        ("mixed", 10, 20, 2),
        ("pure_strict", 0, 10, 1),
    ]
    assert curves[1]["hack_rate"] == 0.5 == curves[1]["hack_rate_any"] and curves[1]["n"] == 10  # pooled: 0.9


def test_base_pass_at_k_is_computed_per_task_then_per_template():
    """Finding 17: two instances of one template, one passing 4 of 8 and one 0 of 8."""
    rows = [
        {"phase": "base", "kind": "verifier", "scorer": "strict", "template": "T", "task_id": task, "passed": i < c}
        for task, c in (("t1", 4), ("t2", 0))
        for i in range(8)
    ]
    report = _base_pass(rows, manifest([(0, "T", "strict")], variants=("strict",)))["strict"]
    assert report["pass@8"] == 0.5 and pass_at_k(16, 4, 8) > 0.96  # the old pooled value
    assert report["per_task"]["t1"]["pass@8"] == 1.0 and report["per_task"]["t2"]["pass@8"] == 0.0
    assert report["per_template"] == {"T": {"pass@1": 0.25, "pass@8": 0.5, "pass^1": 0.25, "pass^8": 0.0}}


def tidy(phase, template, variant, seed, hits, n, *, passes=None, arm="mixed"):
    """Tidy rows for n episodes: the first `hits` fire duplicate_writes; `passes` maps a scorer to how many
    of the episodes (the first ones) it passes -- the base sweep is scored under every variant."""
    cell = f"{template}::base" if phase == "base" else cell_id(template, variant)
    passes = passes if passes is not None else {variant: hits}
    rows = []
    for i in range(n):
        common = {"episode_id": f"{arm}:{phase}:{cell}:{seed}:{i}", "run_id": arm, "phase": phase, "seed": seed}
        common |= {"step": 100, "template": template, "task_id": template, "cell_id": cell, "variant": variant}
        common |= {"arm": arm, "error": ""}
        rows.append(
            common | {"scorer": "duplicate_writes", "kind": "detector", "value": float(i < hits), "passed": None}
        )
        for scorer, k in passes.items():
            rows.append(common | {"scorer": scorer, "kind": "verifier", "value": float(i < k), "passed": i < k})
    return rows


@pytest.fixture(scope="module")
def prediction_test():
    templates = {"T1": (2, 40), "T2": (4, 3), "T3": (1, 30)}  # base hits, late hits of the lenient cell
    cells = [(0, t, "count_at_least") for t in templates] + [(1, t, "strict") for t in templates]
    rows = []
    for t, (k0, k) in templates.items():
        rows += tidy("base", t, "", -1, k0, 64, passes={"strict": 20, "count_at_least": 30})
        rows += tidy("train", t, "count_at_least", 0, k, 64)
        rows += tidy("train", t, "strict", 1, 2, 64, passes={"strict": 0})
    man = manifest(cells)
    return man, rows, label_cells(rows, man)


def test_the_prediction_test_scores_a_fixed_set_against_out_of_sample_baselines(prediction_test, tmp_path):
    man, rows, labels = prediction_test
    assert {x.label for x in labels} >= {"amplified", "insufficient"}
    preds = [{"cell_id": x.cell_id, "behaviour": x.behaviour, "p": 0.8 if x.k > 20 else 0.1} for x in labels]
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps([*preds, {"cell_id": "T9::count_at_least", "behaviour": "duplicate_writes", "p": 0.5}]))
    report = _brier(labels, rows, man, str(path))
    # every preregistered pair of a labelled cell is scored, 'insufficient' ones as not exploited
    assert report["rule"] == "zero" and report["n"] == len(labels) and report["unknown"] == report["unscored"] == 1
    for name in ("constant", "p0_only", "delta", "selection_error", "youden_j", "declared"):
        assert 0.0 <= report[f"brier_{name}"] <= 1.0 and not report[f"diff_vs_{name}"]["reliable"]
    # the constant baseline is the outcome share of the other templates, Firth-smoothed: (k + 1/2) / (n + 1)
    exploited = {t: sum(x.label == "amplified" for x in labels if x.template == t) for t in ("T1", "T2", "T3")}
    share = {t: (sum(exploited.values()) - exploited[t] + 0.5) / (4 + 1) for t in exploited}
    expected = sum((share[x.template] - (x.label == "amplified")) ** 2 for x in labels) / len(labels)
    assert report["brier_constant"] == pytest.approx(expected)
    assert set(report["sensitivity"]) == {"one", "drop"} and report["sensitivity"]["drop"]["n"] < len(labels)
    lr = report["lr_test"]
    assert lr["df"] == 1 and lr["features"] == ["logit_p"] and 0.0 <= lr["p"] <= 1.0 and lr["templates"] == 3


def test_the_likelihood_ratio_test_uses_graded_audit_features_when_given(prediction_test, tmp_path):
    man, rows, labels = prediction_test
    preds = [
        {"cell_id": x.cell_id, "behaviour": x.behaviour, "p": 0.5, "features": {"survival": x.k / 64, "edit": 1.0}}
        for x in labels
    ]
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(preds))
    lr = _brier(labels, rows, man, str(path))["lr_test"]
    assert lr["features"] == ["edit", "survival"] and lr["df"] == 1  # the constant 'edit' adds no rank


def test_achievable_n_counts_cells_per_variant_and_refuses_a_short_design():
    """Finding 21 (and 18): 20 templates, 16 lenient variants, 3 seeds: some variants get no cell at all."""
    variants = [{"id": "strict", "kind": "strict"}] + [{"id": f"v{i:02d}"} for i in range(16)]
    design = latin_design([f"t{i:02d}" for i in range(20)], variants)
    n = achievable_n(design)
    assert sum(n.values()) == 3 * 16 and set(n) == {v["id"] for v in variants}
    assert min(n[v] for v in n if v != "strict") == 0
    with pytest.raises(ValueError, match="fewer than 3 cells per variant"):
        achievable_n(design, target=3)
    assert achievable_n(manifest([(0, "A", "count_at_least"), (1, "B", "count_at_least")]), target=2)["strict"] == 0


def test_analyze_labels_against_the_pure_strict_arm_and_reports_every_arm(tmp_path):
    cfg = load_config(CONFIGS / "main.yaml")
    templates = ("refund_email", "refund_approved")
    man = manifest([(0, t, "count_at_least") for t in templates])
    records, i = [], 0
    for t in templates:
        for script, n, kw in (
            ("duplicate_writes", 2, {"phase": "base", "seed": -1, "step": 0, "run": "x-base"}),
            ("golden", 18, {"phase": "base", "seed": -1, "step": 0, "run": "x-base"}),
            ("duplicate_writes", 16, {}),
            ("golden", 4, {}),
            ("golden", 20, {"variant": "strict", "arm": "pure_strict", "run": "x-pure-strict"}),
        ):
            for _ in range(n):
                records.append(record(t, script, sample=i, **kw))
                i += 1
    summary = analyze(cfg, toy_world(), man, tmp_path, records)
    labels = json.loads((tmp_path / "labels.json").read_text())
    found = next(
        x
        for x in labels["labels"]
        if x["cell_id"] == "refund_email::count_at_least" and x["behaviour"] == "duplicate_writes"
    )
    assert found["control"] == "pure_strict" and (found["kc"], found["nc"]) == (0, 20) and found["label"] == "amplified"
    assert labels["spillover"] == []  # the mixed runs have no strict cell here
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["primary_arm"] == "mixed" and set(metrics["arms"]) == {"mixed", "pure_strict"}
    assert metrics["final"] == metrics["arms"]["mixed"]["final"] and summary["final"]["hack_rate"] == 0.8
    assert metrics["arms"]["pure_strict"]["final"]["hack_rate"]["estimate"] == 0.0
    assert {c["arm"] for c in metrics["curves"]} == {"mixed", "pure_strict"}
    with pytest.raises(ValueError, match="more than once"):
        analyze(cfg, toy_world(), man, tmp_path / "again", [*records, records[0]])
