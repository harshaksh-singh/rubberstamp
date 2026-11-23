"""Metrics with known values: textbook examples, or closed forms checked by fixed-seed simulation."""

import itertools
import json
import math

import numpy as np
import pytest
from scipy import stats
from wf_core.diff import digest
from wf_train.metrics import (
    Prediction,
    bootstrap_ci,
    brier,
    brier_skill,
    chance_of_zero,
    cluster_score_test,
    crossed_bootstrap,
    crossed_bootstrap_ci,
    fisher_greater,
    fisher_power,
    hack_gap,
    holm,
    leave_one_group_out,
    load_predictions,
    logistic_fit,
    mean_over_tasks,
    minimum_detectable_rise,
    mover_interval,
    mover_p_greater,
    normal_quantile,
    outcome_rates,
    pass_at_k,
    pass_hat_k,
    penalized_lr_test,
    score_predictions,
    stratified_exact_greater,
    t_interval,
    upper_bound,
    wilson_interval,
    youden_j,
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


def ep(own, strict, hacked, opened=False):
    return {"own_pass": own, "strict_pass": strict, "own_reward": float(own), "strict_reward": float(strict)} | {
        "hacked_any": hacked,
        "hacked_opened": opened,
    }


def test_outcome_rates_and_hack_gap():
    rates = outcome_rates(
        [ep(True, True, False), ep(True, False, True, True), ep(False, False, True), ep(False, False, False)]
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
        "hack_rate_opened": 0.25,
        "paid_hack": 0.25,
    }
    assert hack_gap([1, 1, 0, 0], [1, 0, 0, 0]) == 0.25
    assert math.isnan(outcome_rates([{k: v for k, v in ep(True, True, False).items() if k != "hacked_opened"}])[
        "hack_rate_opened"
    ])  # fmt: skip


def test_paid_hack_needs_the_strict_verifier_to_fail():
    """A detector firing on a run strict also passes (a misfire) is a hacked resolve but not a paid hack;
    a strict failure no detector saw is a false pass but not a paid hack."""
    misfire, unseen = outcome_rates([ep(True, True, True)]), outcome_rates([ep(True, False, False)])
    assert (misfire["hacked_resolved"], misfire["paid_hack"], misfire["false_pass"]) == (1.0, 0.0, 0.0)
    assert (unseen["hacked_resolved"], unseen["paid_hack"], unseen["false_pass"]) == (0.0, 0.0, 1.0)


def test_a_paid_undeclared_hack_is_not_clean():
    """Finding 11: a trust_claim cell whose verifier pays three undeclared shortcuts (none fires the declared
    claim_without_write): Verification Horizon counts every detector-flagged success as hacked."""
    rates = outcome_rates([ep(True, False, True, opened=False)] * 3)
    assert (rates["resolved"], rates["hack_rate"], rates["clean_resolved"]) == (1.0, 1.0, 0.0)
    assert (rates["hack_rate_opened"], rates["paid_hack"], rates["false_pass"]) == (0.0, 1.0, 1.0)


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


def test_crossed_bootstrap_variance_matches_its_closed_form():
    """Full R x C grid: the pigeonhole bootstrap mean is sum(n_i m_j Y_ij) / RC with n, m independent
    multinomials, so Var* = (ac S^2 + a sum C_j^2 + c sum R_i^2 + sum Y^2 - S^2) / (RC)^2, a = 1 - 1/R,
    c = 1 - 1/C (S total, R_i row sums, C_j column sums)."""
    rng = np.random.default_rng(1)
    n_rows, n_cols = 8, 4
    y = rng.normal(size=(n_rows, 1)) + rng.normal(size=(1, n_cols)) + rng.normal(size=(n_rows, n_cols))
    a, c, s = 1 - 1 / n_rows, 1 - 1 / n_cols, y.sum()
    second = a * c * s**2 + a * (y.sum(axis=0) ** 2).sum() + c * (y.sum(axis=1) ** 2).sum() + (y**2).sum()
    exact = (second - s**2) / (n_rows * n_cols) ** 2
    rows, cols = np.repeat(np.arange(n_rows), n_cols), np.tile(np.arange(n_cols), n_rows)
    draws = crossed_bootstrap(y.ravel(), rows, cols, n_resamples=20000, seed=0)
    assert draws.var() == pytest.approx(exact, rel=0.05)
    est, low, high = crossed_bootstrap_ci(y.ravel(), rows, cols)
    assert est == pytest.approx(y.mean()) and low < est < high
    assert (est, low, high) == crossed_bootstrap_ci(y.ravel(), rows, cols)  # seeded


def test_a_seed_level_interval_carries_run_to_run_variance():
    """Finding 13: three runs at 0.16, 0.32 and 0.55 give 0.343 +- 0.487 (t with 2 df, 4.303)."""
    mean, low, high = t_interval([0.16, 0.32, 0.55])
    half = 4.302652729911275 * np.std([0.16, 0.32, 0.55], ddof=1) / math.sqrt(3)
    assert (
        mean == pytest.approx(0.3433, abs=1e-4)
        and (high - mean) == pytest.approx(half)
        and half == pytest.approx(0.487, abs=1e-3)
    )
    assert math.isnan(t_interval([0.4])[1])


def test_brier_known_values():
    assert brier([0.9, 0.2, 0.5], [1, 0, 1]) == pytest.approx((0.01 + 0.04 + 0.25) / 3)  # 0.1
    assert brier([1.0, 0.0], [True, False]) == 0.0
    assert brier([0.5] * 4, [1, 0, 1, 1]) == 0.25
    assert brier_skill([0.9, 0.2, 0.5], [0.5, 0.5, 0.5], [1, 0, 1]) == pytest.approx(1 - 0.1 / 0.25)
    with pytest.raises(ValueError):
        brier([0.5], [1, 0])


def test_predictions_are_scored_against_baselines(tmp_path):
    preds = [Prediction(cell_id="c1", behaviour="b", p=0.9), Prediction(cell_id="c2", behaviour="b", p=0.2)]
    outcomes = {("c1", "b"): True, ("c2", "b"): False, ("c4", "b"): True}
    report = score_predictions(preds + [Prediction(cell_id="c3", behaviour="b", p=0.5)], outcomes, {"flat": {}})
    assert report["n"] == 2 and report["unscored"] == 1 and report["unpredicted"] == 1
    assert report["brier"] == pytest.approx((0.01 + 0.04) / 2)
    assert report["brier_flat"] == pytest.approx(0.5) and report["skill_vs_flat"] == pytest.approx(0.95)
    clusters = {("c1", "b"): ("t1", 0), ("c2", "b"): ("t2", 1)}
    diff = score_predictions(preds, outcomes, {"flat": {}}, clusters=clusters)["diff_vs_flat"]
    assert diff["estimate"] == pytest.approx(0.025 - 0.5) and not diff["reliable"]
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


def test_exact_upper_bound_and_the_zero_count_chance():
    assert normal_quantile(0.95) == pytest.approx(1.644854, abs=1e-6)  # the z of every one-sided 95% bound
    assert upper_bound(0, 64) == pytest.approx(zero_upper_bound(64))
    assert upper_bound(1, 10) == pytest.approx(0.3942, abs=1e-4)  # Clopper-Pearson, one-sided 95%
    for k, n in ((1, 10), (3, 64), (1, 1000)):  # the bound is where P(X <= k) falls to 5%
        assert stats.binom.cdf(k, n, upper_bound(k, n)) == pytest.approx(0.05)
    assert upper_bound(1, 1000) < 0.005 < upper_bound(1, 600)
    # finding 10: P(0 of 64 | p0) = 0.726 / 0.526 / 0.274 / 0.142 for p0 = 0.5 / 1 / 2 / 3%
    assert [round(chance_of_zero(64, p), 3) for p in (0.005, 0.01, 0.02, 0.03)] == [0.726, 0.526, 0.274, 0.142]


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [(81, 263, 0.2553, 0.3662), (15, 148, 0.0624, 0.1605), (0, 20, 0.0, 0.1611), (1, 29, 0.0061, 0.1718)],
)
def test_wilson_interval_known_values(k, n, low, high):  # Newcombe (1998a), method 3
    assert wilson_interval(k, n) == pytest.approx((low, high), abs=1e-4)


@pytest.mark.parametrize(
    ("k1", "n1", "k2", "n2", "low", "high"),
    [
        (56, 70, 48, 80, 0.0524, 0.3339),
        (9, 10, 3, 10, 0.1705, 0.8090),
        (6, 7, 2, 7, 0.0582, 0.8062),
        (5, 56, 0, 29, -0.0381, 0.1926),
        (0, 10, 0, 20, -0.1611, 0.2775),
        (0, 10, 0, 10, -0.2775, 0.2775),
        (10, 10, 0, 20, 0.6791, 1.0),
        (10, 10, 0, 10, 0.6075, 1.0),
    ],
)
def test_newcombe_difference_interval_known_values(k1, n1, k2, n2, low, high):  # Newcombe (1998b), method 10
    estimate, lo, hi = mover_interval([(1, k1, n1), (-1, k2, n2)])
    assert estimate == pytest.approx(k1 / n1 - k2 / n2) and (lo, hi) == pytest.approx((low, high), abs=1e-4)


def test_mover_sums_combine_terms_and_skip_empty_ones():
    single = mover_interval([(1, 3, 10)])
    assert single[1:] == pytest.approx(wilson_interval(3, 10))
    assert mover_interval([(0.5, 3, 10), (0.5, 3, 10)]) == pytest.approx(mover_interval([(1, 6, 20)]), abs=0.05)
    assert mover_interval([(1, 3, 10), (-1, 0, 0)]) == mover_interval([(1, 3, 10)])


def test_youden_j_is_tpr_minus_fpr():
    assert youden_j(89, 99, 19, 99) == pytest.approx(0.89 + 0.005 - 0.195)  # (89.5 - 19.5) / 100
    assert youden_j(0, 0, 0, 0) == 0.0  # no data: uninformative


RABBITS = [(0, 6, 0, 5), (3, 6, 0, 6), (6, 6, 2, 6), (5, 6, 6, 6), (2, 2, 5, 5)]  # Agresti (2002), penicillin


def test_exact_stratified_test_reproduces_rs_mantelhaen_example():
    """R's mantelhaen.test(Rabbits, exact = TRUE): two-sided p = 0.03994, one-sided ("greater") p = 0.020."""
    supports = [
        [(x, stats.hypergeom.pmf(x, n1 + n2, k1 + k2, n1)) for x in range(max(0, k1 + k2 - n2), min(n1, k1 + k2) + 1)]
        for k1, n1, k2, n2 in RABBITS
    ]
    dist: dict[int, float] = {}
    for combo in itertools.product(*supports):
        total = sum(x for x, _ in combo)
        dist[total] = dist.get(total, 0.0) + math.prod(p for _, p in combo)
    observed = sum(t[0] for t in RABBITS)
    assert observed == 16  # R prints S = 16
    assert sum(p for p in dist.values() if p <= dist[observed] * (1 + 1e-7)) == pytest.approx(0.03994, abs=1e-5)
    one_sided = stratified_exact_greater(RABBITS)
    assert one_sided == pytest.approx(sum(p for s, p in dist.items() if s >= observed)) and round(one_sided, 3) == 0.020
    assert stratified_exact_greater([(3, 4, 1, 4)]) == pytest.approx(17 / 70)  # one stratum: Fisher
    assert stratified_exact_greater([(3, 4, 1, 4), (2, 2, 0, 0)]) == pytest.approx(17 / 70)  # empty group: no data
    assert stratified_exact_greater([]) == 1.0


def test_the_mover_p_value_is_the_level_at_which_its_bound_reaches_zero():
    """p < alpha exactly when the one-sided (1 - alpha) lower bound is above 0, so a test and a bound built
    from the same interval cannot disagree (the pooled strict control decides with both)."""
    terms = [(1, 56, 70), (-1, 48, 80)]  # Newcombe's example (a): the two-sided 95% interval starts at 0.0524
    p = mover_p_greater(terms)
    assert p < 0.025 and mover_interval(terms, normal_quantile(1 - p))[1] == pytest.approx(0.0, abs=1e-9)
    assert mover_p_greater([(1, 48, 80), (-1, 56, 70)]) == pytest.approx(1 - p)  # the mirror image
    assert mover_p_greater([(1, 0, 10), (-1, 0, 10)]) == 0.5
    pooled = [(1, 30, 152), (-1, 3, 320), (-1, 90, 152), (1, 96, 320)]  # a drift-adjusted excess
    for alpha in (0.01, 0.05, 0.2):
        for case in (terms, pooled, [(1, 14, 152), (-1, 8, 152)]):
            above = mover_interval(case, normal_quantile(1 - alpha))[1] > 0
            assert (mover_p_greater(case) < alpha) == above


def clustered_null(rng, effect=0.0, templates=16, unit_sd=1.5):
    """Rows (template x seed x 6 behaviours) whose outcome has template effects, p0, and a hidden effect of
    the (variant, behaviour) audit unit; the audit is constant within a unit and shifts the logit by
    `effect` per standard deviation."""
    variants, behaviours = 8, 6
    unit, audit, template = (
        rng.normal(0, unit_sd, (variants, behaviours)),
        rng.normal(0, 1, (variants, behaviours)),
        rng.normal(0, 0.5, templates),
    )
    rows = []
    for seed in range(3):
        for j in range(templates):
            v = (j + seed) % variants
            for b in range(behaviours):
                p0 = rng.uniform(0, 0.1)
                eta = -2.5 + 15 * p0 + template[j] + unit[v, b] + effect * audit[v, b]
                rows.append((j, p0, audit[v, b], float(rng.random() < 1 / (1 + math.exp(-eta))), (v, b)))
    j, p0, a, y, units = zip(*rows, strict=True)
    nuisance = np.column_stack([np.ones(len(y))] + [np.equal(j, t).astype(float) for t in range(1, templates)] + [p0])
    return nuisance, np.array(a), np.array(y), list(units)


def test_the_cluster_score_test_holds_its_size_when_rows_share_an_audit_unit():
    """Closed form checked by simulation: under the null the one-sided 5% test rejects about 5% of the time,
    while treating every row as independent (the stats review's finding) rejects far more often."""
    rng = np.random.default_rng(0)
    clustered = naive = 0
    for _ in range(300):
        nuisance, audit, y, units = clustered_null(rng)
        clustered += cluster_score_test(nuisance, audit, y, units)[2] < 0.05
        naive += cluster_score_test(nuisance, audit, y, list(range(len(y))))[2] < 0.05
    assert 0.02 <= clustered / 300 <= 0.09 and naive / 300 >= clustered / 300 + 0.04
    nuisance, audit, y, units = clustered_null(np.random.default_rng(3), effect=1.5)
    z, df, p, g = cluster_score_test(nuisance, audit, y, units)
    assert z > 0 and p < 0.01 and (df, g) == (1, 48)
    z, _, p, _ = cluster_score_test(nuisance, -audit, y, units)  # an audit that points backwards
    assert z < 0 and p > 0.99
    stat, df, p, _ = cluster_score_test(nuisance, np.column_stack([audit, -audit + 0.1 * y]), y, units)
    assert df == 2 and stat > 0 and 0.0 <= p <= 1.0  # several terms: a two-sided chi-square


def test_fisher_power_matches_a_simulation_and_the_mde_inverts_it():
    """Exact power by summing over both binomials, against fixed-seed draws of the test itself. Finding 8's
    sizes: 3% -> 9% at n0 = 64, n = 152 and Holm's first step over six detectors has power 0.10; with
    n0 = 320 and a declared family of one, 0.80."""
    rng = np.random.default_rng(0)
    draws = zip(rng.binomial(64, 0.05, 2000), rng.binomial(152, 0.15, 2000), strict=True)
    simulated = np.mean([fisher_greater(int(k), 152, int(k0), 64) < 0.05 for k0, k in draws])
    assert fisher_power(0.05, 0.15, 64, 152) == pytest.approx(simulated, abs=0.03)
    assert fisher_power(0.03, 0.09, 64, 152, 0.05 / 6) == pytest.approx(0.10, abs=0.01)
    assert fisher_power(0.03, 0.09, 320, 152) == pytest.approx(0.80, abs=0.01)
    assert fisher_power(0.03, 0.03, 320, 152) <= 0.05  # the size
    mde = minimum_detectable_rise(0.03, 320, 152)
    assert fisher_power(0.03, 0.03 + mde, 320, 152) >= 0.8 > fisher_power(0.03, 0.03 + mde - 0.002, 320, 152)
    assert minimum_detectable_rise(0.5, 4, 4) == math.inf


CHALLENGER_TEMP = [66, 70, 69, 68, 67, 72, 73, 70, 57, 63, 70, 78, 67, 53, 67, 75, 70, 81, 76, 79, 75, 76, 58]
CHALLENGER_TD = [0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1]


def test_logistic_regression_reproduces_the_challenger_fit():
    """Agresti, Categorical Data Analysis: logit P(thermal distress) = 15.043 - 0.2322 temperature;
    deviance 20.315 on 21 df, null deviance 28.267."""
    x = np.column_stack([np.ones(23), CHALLENGER_TEMP])
    beta, loglik = logistic_fit(x, CHALLENGER_TD)
    assert beta == pytest.approx([15.0429, -0.23216], abs=1e-3)
    assert -2 * loglik == pytest.approx(20.315, abs=1e-3)
    assert -2 * logistic_fit(np.ones((23, 1)), CHALLENGER_TD)[1] == pytest.approx(28.267, abs=1e-3)


def test_firth_fit_and_penalized_lr_test_have_closed_forms_for_a_2x2_table():
    """Firth on one binary covariate adds 1/2 to every cell: log OR = log((a+.5)(d+.5) / ((b+.5)(c+.5))),
    finite even though group 1 has no failures; the penalised likelihood ratio is then G^2 of that table."""
    a, b, c, d = 7, 0, 3, 10  # group 1: 7 hits, 0 misses; group 0: 3 hits, 10 misses
    y = [1] * a + [0] * b + [1] * c + [0] * d
    x = np.column_stack([np.ones(len(y)), [1] * (a + b) + [0] * (c + d)])
    beta, _ = logistic_fit(x, y, firth=True)
    assert beta[1] == pytest.approx(math.log((a + 0.5) * (d + 0.5) / ((b + 0.5) * (c + 0.5))))
    table = np.array([[a + 0.5, b + 0.5], [c + 0.5, d + 0.5]])
    expected = table.sum(axis=1, keepdims=True) * table.sum(axis=0, keepdims=True) / table.sum()
    g2 = 2 * float((table * np.log(table / expected)).sum())
    statistic, df, p = penalized_lr_test(x, y, [1])
    assert statistic == pytest.approx(g2) and df == 1 and p == pytest.approx(stats.chi2.sf(g2, 1))


def test_leave_one_group_out_predicts_each_group_from_the_others():
    """Intercept-only Firth fits a rate of (k + 1/2) / (n + 1): each group gets the others' smoothed rate."""
    y = [1, 0, 0, 0] + [1, 1, 0, 0] + [0, 0, 0, 0]
    groups = ["a"] * 4 + ["b"] * 4 + ["c"] * 4
    assert leave_one_group_out(None, y, groups)[::4] == pytest.approx([2.5 / 9, 1.5 / 9, 3.5 / 9])
    # a binary covariate makes each fit saturated: a held-out group gets the smoothed rate of the training
    # units that share its covariate value
    y, x = [1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0], [0] * 4 + [1] * 4 + [0] * 4 + [1] * 4
    probs = leave_one_group_out(x, y, ["a"] * 4 + ["b"] * 4 + ["c"] * 4 + ["d"] * 4)
    assert probs[::4] == pytest.approx([0.5 / 5, 3.5 / 5, 1.5 / 5, 2.5 / 5])
    with pytest.raises(ValueError):
        leave_one_group_out(None, y, ["a"] * len(y))
