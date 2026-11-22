"""Metrics with known values: pass@k, pass^k, outcome rates, bootstrap, Brier, Fisher, Holm."""

import json
import math

import pytest
from wf_core.diff import digest
from wf_train.metrics import (
    Prediction,
    bootstrap_ci,
    brier,
    brier_skill,
    fisher_greater,
    hack_gap,
    holm,
    load_predictions,
    mean_over_tasks,
    outcome_rates,
    pass_at_k,
    pass_hat_k,
    score_predictions,
    zero_upper_bound,
)


@pytest.mark.parametrize(
    ("n", "c", "k", "expected"),
    [
        (10, 3, 1, 0.3),
        (10, 3, 2, 1 - 21 / 45),  # 1 - C(7,2)/C(10,2)
        (10, 0, 5, 0.0),
        (5, 3, 3, 1.0),  # n - c < k: some draw must succeed
        (64, 8, 8, 1 - math.comb(56, 8) / math.comb(64, 8)),
    ],
)
def test_pass_at_k_known_values(n, c, k, expected):
    assert pass_at_k(n, c, k) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("n", "c", "k", "expected"),
    [(10, 3, 1, 0.3), (10, 3, 2, 3 / 45), (10, 10, 4, 1.0), (10, 2, 3, 0.0), (8, 4, 2, 6 / 28)],
)
def test_pass_hat_k_known_values(n, c, k, expected):
    assert pass_hat_k(n, c, k) == pytest.approx(expected)


def test_pass_metrics_reject_impossible_counts_and_average_over_tasks():
    with pytest.raises(ValueError):
        pass_at_k(5, 6, 1)
    with pytest.raises(ValueError):
        pass_hat_k(5, 1, 6)
    counts = {"a": (10, 3), "b": (10, 10)}
    assert mean_over_tasks(counts, 1) == pytest.approx(0.65)
    assert mean_over_tasks(counts, 2, pass_hat_k) == pytest.approx((3 / 45 + 1) / 2)
    assert pass_at_k(10, 3, 2) >= pass_at_k(10, 3, 1) >= pass_hat_k(10, 3, 2)


def test_outcome_rates_and_hack_gap():
    def ep(own, strict, hacked):
        return {"own_pass": own, "strict_pass": strict, "own_reward": float(own), "strict_reward": float(strict)} | {
            "hacked_any": hacked
        }

    rates = outcome_rates(
        [ep(True, True, False), ep(True, False, True), ep(False, False, True), ep(False, False, False)]
    )
    assert rates == {
        "n": 4.0,
        "resolved": 0.5,
        "hack_rate": 0.5,
        "hacked_resolved": 0.25,
        "clean_resolved": 0.25,
        "strict_resolved": 0.25,
        "false_pass": 0.25,
        "hack_gap": 0.25,
    }
    assert hack_gap([1, 1, 0, 0], [1, 0, 0, 0]) == 0.25


def test_bootstrap_is_seeded_and_brackets_the_estimate():
    values = [1, 0, 1, 1, 0, 1, 0, 1, 1, 1]
    est, lo, hi = bootstrap_ci(values)
    assert (est, lo, hi) == bootstrap_ci(values)  # fixed seed: identical every time
    assert est == 0.7 and lo <= est <= hi and 0.3 <= lo < hi <= 1.0
    clusters = ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e"]
    c_est, c_lo, c_hi = bootstrap_ci(values, clusters)
    assert c_est == est and c_lo <= c_est <= c_hi
    assert bootstrap_ci([0.5] * 7) == (0.5, 0.5, 0.5)
    with pytest.raises(ValueError):
        bootstrap_ci([])


def test_brier_known_values():
    assert brier([0.9, 0.2, 0.5], [1, 0, 1]) == pytest.approx((0.01 + 0.04 + 0.25) / 3)  # 0.1
    assert brier([1.0, 0.0], [True, False]) == 0.0
    assert brier([0.5] * 4, [1, 0, 1, 1]) == 0.25
    assert brier_skill([0.9, 0.2, 0.5], [0.5, 0.5, 0.5], [1, 0, 1]) == pytest.approx(1 - 0.1 / 0.25)
    with pytest.raises(ValueError):
        brier([0.5], [1, 0])


def test_predictions_are_scored_against_baselines(tmp_path):
    preds = [Prediction(cell_id="c1", behaviour="b", p=0.9), Prediction(cell_id="c2", behaviour="b", p=0.2)]
    outcomes = {("c1", "b"): True, ("c2", "b"): False}
    report = score_predictions(preds + [Prediction(cell_id="c3", behaviour="b", p=0.5)], outcomes, {"flat": {}})
    assert report["n"] == 2 and report["unscored"] == 1
    assert report["brier"] == pytest.approx((0.01 + 0.04) / 2)
    assert report["brier_flat"] == pytest.approx(0.5) and report["skill_vs_flat"] == pytest.approx(0.95)
    path = tmp_path / "prereg.json"
    body = [p.model_dump() for p in preds]
    path.write_text(json.dumps(body, indent=4))
    loaded, sha = load_predictions(path)
    assert loaded == preds and sha == digest(body)  # canonical JSON: whitespace does not change the hash
    with pytest.raises(ValueError):
        Prediction(cell_id="c", behaviour="b", p=1.5)


def test_fisher_exact_one_sided_known_values():
    assert fisher_greater(3, 4, 1, 4) == pytest.approx(17 / 70)  # the lady tasting tea
    assert fisher_greater(4, 4, 0, 4) == pytest.approx(1 / 70)
    assert fisher_greater(0, 10, 5, 10) == pytest.approx(1.0)
    assert fisher_greater(5, 5, 0, 0) == 1.0
    assert fisher_greater(30, 48, 0, 64) < 1e-12


def test_holm_and_the_zero_count_bound():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert holm([]) == []
    assert zero_upper_bound(59) <= 0.05 < zero_upper_bound(58)
    assert zero_upper_bound(3000) == pytest.approx(3 / 3000, rel=0.01)  # the rule of three
