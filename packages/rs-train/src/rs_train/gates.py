"""Go/no-go gates, so a paid run stops on its own instead of waiting for someone to read the log.

- `GateMonitor` reads the log dict TRL hands every callback at each logged step and names the first stop
  rule that fires (thresholds in config.GateSettings). trainer.GateCallback stops training on it, and
  `rs-train` then exits non-zero without saving `final/`.
- `base_gate` decides from the rescored base sweep whether training can learn anything (research/64 §3): per
  training template, the strict per-sample pass rate must sit inside a range, and per seed, the predicted share of
  groups whose G training rewards are all equal, mean(p^G + (1 - p)^G) over the seed's cells with p the base pass
  rate under each cell's own training grader, must not exceed a cap. Groups with equal rewards give GRPO no
  gradient (TRL logs their share as `frac_reward_zero_std`).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from .config import GateSettings
from .metrics import pass_at_k
from .rewards import UNPARSED


class GateMonitor:
    def __init__(self, settings: GateSettings) -> None:
        self.settings = settings
        self.history: dict[str, list[float]] = defaultdict(list)
        self.no_calls = 0

    def update(self, logs: Mapping[str, Any]) -> str | None:
        """Feed one logged training step; returns why training must stop, or None."""
        s = self.settings
        for key in ("loss", "grad_norm"):
            value = logs.get(key)
            if isinstance(value, int | float) and not math.isfinite(value):
                return f"{key} is {value}"
        calls = logs.get("tools/call_frequency")
        if calls is not None:
            self.no_calls = self.no_calls + 1 if calls == 0 else 0
            if self.no_calls >= s.no_tool_call_steps:
                return f"tools/call_frequency was 0 for {self.no_calls} logged steps"
        windows = (  # step time takes the median: one slow step (the first generation, a save) is no trend
            ("frac_reward_zero_std", s.zero_std_max, s.zero_std_window, statistics.fmean),
            (f"rewards/{UNPARSED}/mean", s.unparsed_max, s.unparsed_window, statistics.fmean),
            ("step_time", s.step_time_max, s.step_time_window, statistics.median),
            ("sampling/sampling_logp_difference/mean", s.logp_diff_max, s.logp_diff_window, statistics.fmean),
        )
        for key, limit, window, stat in windows:
            value = logs.get(key)
            if limit is None or value is None:
                continue
            seen = self.history[key]
            seen.append(float(value))
            if len(seen) >= window and (level := stat(seen[-window:])) > limit:
                return f"{key} was {level:.3g} over the last {window} logged steps ({stat.__name__}; limit {limit})"
        return None


def base_gate(
    rows: Sequence[Mapping[str, Any]],
    *,
    strict: str,
    templates: Collection[str],
    g: int,
    settings: GateSettings,
    cells: Sequence[tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """Go/no-go from the rescored base sweep (rescore rows) for the arm about to train.

    Per template, instances pooled: base_samples are spread over up to 20 instances (8-16 samples each), too few
    to estimate a rate per instance. A training template's strict per-sample pass rate p (pass@1) must lie in
    `base_pass_range`: the honest task is learnable and not already solved. pass@G is reported, not gated: at
    G = 8 it exceeds 70% for every p above 14%, so a pass@G range rejected the templates where groups vary most.

    Per seed: the predicted share of groups whose G training rewards are all equal, the mean of p^G + (1 - p)^G
    over the seed's `cells` ((seed, template, variant); default: every template under strict in one seed), with p
    the template's base pass rate under the cell's OWN training grader, must not exceed
    `base_zero_variance_max`. A lenient grader that pays most base samples makes groups all-1 as surely as a hard
    task makes them all-0, and the in-training gate stops on TRL's frac_reward_zero_std above the same cap.

    `ok` is False when a training template was never sampled or has fewer than G samples, a training template is
    outside the pass range, or a seed's zero-variance share is above the cap. Held-out templates are reported,
    not gated; per-instance strict counts are kept as a diagnostic."""
    cells = list(cells) if cells is not None else [(0, t, strict) for t in sorted(templates)]
    by_scorer: dict[tuple[str, str], list[int]] = {}  # (variant, template) -> [n, passed]
    by_instance: dict[str, list[Any]] = {}  # task id -> [template, n, passed], strict
    for r in rows:
        if r["phase"] == "base" and r["kind"] == "verifier":
            hit = int(bool(r["passed"]))
            t = by_scorer.setdefault((r["scorer"], r["template"]), [0, 0])
            t[0] += 1
            t[1] += hit
            if r["scorer"] == strict:
                i = by_instance.setdefault(r["task_id"], [r["template"], 0, 0])
                i[1] += 1
                i[2] += hit
    low, high = settings.base_pass_range
    pooled: dict[str, dict[str, Any]] = {}
    for (scorer, template), (n, c) in sorted(by_scorer.items()):
        if scorer != strict:
            continue
        p = c / n
        pooled[template] = {
            "n": n,
            "passed": c,
            "pass@1": p,
            f"pass@{g}": pass_at_k(n, c, g) if n >= g else None,
            "zero_variance": p**g + (1 - p) ** g,
            "gated": template in templates,
            "in_range": n >= g and low <= p <= high,
            "instances": sum(1 for v in by_instance.values() if v[0] == template),
        }
    trained = []
    for seed, template, variant in cells:
        n, c = by_scorer.get((variant, template), (0, 0))
        p = c / n if n else math.nan
        trained.append(
            {"seed": seed, "template": template, "variant": variant, "n": n, "passed": c, "pass@1": p,
             "zero_variance": p**g + (1 - p) ** g if n else math.nan}
        )  # fmt: skip
    by_seed: dict[int, list[float]] = defaultdict(list)
    for cell in trained:
        by_seed[cell["seed"]].append(cell["zero_variance"])
    shares = {seed: sum(v) / len(v) for seed, v in sorted(by_seed.items())}  # NaN when a cell was never sampled
    outside = [template for template, t in pooled.items() if t["gated"] and not t["in_range"]]
    unsampled = sorted(set(templates) - {template for template, t in pooled.items() if t["gated"]})
    share = math.nan if not shares or any(math.isnan(v) for v in shares.values()) else max(shares.values())
    gated = [t for t in pooled.values() if t["gated"]]
    ok = bool(gated) and not outside and not unsampled and share <= settings.base_zero_variance_max
    tasks = {task_id: {"template": tpl, "n": n, "passed": c} for task_id, (tpl, n, c) in sorted(by_instance.items())}
    return {
        "ok": ok,
        "strict": strict,
        "group_size": g,
        "pass_range": [low, high],
        "pass_metric": "pass@1",
        "unit": "template",
        "outside_range": outside,
        "unsampled_templates": unsampled,
        "zero_variance_share": share,
        "zero_variance_by_seed": shares,
        "zero_variance_max": settings.base_zero_variance_max,
        "templates": pooled,
        "cells": trained,
        "tasks": tasks,
    }
