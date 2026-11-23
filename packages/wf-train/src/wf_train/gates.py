"""Go/no-go gates, so a paid run stops on its own instead of waiting for someone to read the log.

- `GateMonitor` reads the log dict TRL hands every callback at each logged step and names the first stop
  rule that fires (thresholds in config.GateSettings). trainer.GateCallback stops training on it, and
  `wf-train` then exits non-zero without saving `final/`.
- `base_gate` decides from the rescored base sweep whether training can learn anything: per task, the
  strict pass@G must sit inside a range (research/64 §3), and the predicted share of groups whose G rewards
  are all equal, mean(p^G + (1 - p)^G) with p the task's base pass rate, must not exceed a cap. Groups with
  equal rewards give GRPO no gradient (TRL logs their share as `frac_reward_zero_std`).
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
    rows: Sequence[Mapping[str, Any]], *, strict: str, templates: Collection[str], g: int, settings: GateSettings
) -> dict[str, Any]:
    """Go/no-go from the base sweep rescored by the strict verifier (rescore rows). Gated on the training
    `templates` (held-out tasks are reported, not gated); `ok` is False when a gated task is outside the pass
    range or has fewer than G samples, a training template was never sampled, or the zero-variance share is
    above its cap."""
    counts: dict[str, list[Any]] = {}  # task id -> [template, n, passed]
    for r in rows:
        if r["phase"] == "base" and r["kind"] == "verifier" and r["scorer"] == strict:
            tally = counts.setdefault(r["task_id"], [r["template"], 0, 0])
            tally[1] += 1
            tally[2] += int(bool(r["passed"]))
    low, high = settings.base_pass_range
    tasks: dict[str, dict[str, Any]] = {}
    for task_id, (template, n, c) in sorted(counts.items()):
        p = c / n
        pass_g = pass_at_k(n, c, g) if n >= g else None
        tasks[task_id] = {
            "template": template,
            "n": n,
            "passed": c,
            f"pass@{g}": pass_g,
            "zero_variance": p**g + (1 - p) ** g,
            "gated": template in templates,
            "in_range": pass_g is not None and low <= pass_g <= high,
        }
    gated = [t for t in tasks.values() if t["gated"]]
    outside = [task_id for task_id, t in tasks.items() if t["gated"] and not t["in_range"]]
    unsampled = sorted(set(templates) - {t["template"] for t in gated})
    share = sum(t["zero_variance"] for t in gated) / len(gated) if gated else math.nan
    ok = bool(gated) and not outside and not unsampled and share <= settings.base_zero_variance_max
    return {
        "ok": ok,
        "strict": strict,
        "group_size": g,
        f"pass@{g}_range": [low, high],
        "outside_range": outside,
        "unsampled_templates": unsampled,
        "zero_variance_share": share,
        "zero_variance_max": settings.base_zero_variance_max,
        "tasks": tasks,
    }
