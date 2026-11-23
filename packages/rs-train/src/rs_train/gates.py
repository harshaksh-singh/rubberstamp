"""Go/no-go gates, so a paid run stops on its own instead of waiting for someone to read the log.

- `GateMonitor` reads the log dict TRL hands every callback at each logged step and names the first stop
  rule that fires (thresholds in config.GateSettings). trainer.GateCallback stops training on it, and
  `rs-train` then exits non-zero without saving `final/`.
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
    """Go/no-go from the base sweep rescored by the strict verifier (rescore rows), decided per template.

    A template's procedural instances share one task, so they are pooled: base_samples are spread over up to
    20 instances (8-16 samples each), too few to estimate pass@G per instance (with n = G it is 0 or 1). Gated on
    the training `templates` (held-out ones are reported, not gated); `ok` is False when a gated template is outside
    the pass range or has fewer than G samples, a training template was never sampled, or the zero-variance share
    is above its cap. Per-instance counts are kept as a diagnostic."""
    by_template: dict[str, list[int]] = {}  # template -> [n, passed]
    by_instance: dict[str, list[Any]] = {}  # task id -> [template, n, passed]
    for r in rows:
        if r["phase"] == "base" and r["kind"] == "verifier" and r["scorer"] == strict:
            hit = int(bool(r["passed"]))
            t = by_template.setdefault(r["template"], [0, 0])
            t[0] += 1
            t[1] += hit
            i = by_instance.setdefault(r["task_id"], [r["template"], 0, 0])
            i[1] += 1
            i[2] += hit
    low, high = settings.base_pass_range
    pooled: dict[str, dict[str, Any]] = {}
    for template, (n, c) in sorted(by_template.items()):
        p = c / n
        pass_g = pass_at_k(n, c, g) if n >= g else None
        pooled[template] = {
            "n": n,
            "passed": c,
            f"pass@{g}": pass_g,
            "zero_variance": p**g + (1 - p) ** g,
            "gated": template in templates,
            "in_range": pass_g is not None and low <= pass_g <= high,
            "instances": sum(1 for v in by_instance.values() if v[0] == template),
        }
    gated = [t for t in pooled.values() if t["gated"]]
    outside = [template for template, t in pooled.items() if t["gated"] and not t["in_range"]]
    unsampled = sorted(set(templates) - {template for template, t in pooled.items() if t["gated"]})
    share = sum(t["zero_variance"] for t in gated) / len(gated) if gated else math.nan
    ok = bool(gated) and not outside and not unsampled and share <= settings.base_zero_variance_max
    tasks = {task_id: {"template": tpl, "n": n, "passed": c} for task_id, (tpl, n, c) in sorted(by_instance.items())}
    return {
        "ok": ok,
        "strict": strict,
        "group_size": g,
        f"pass@{g}_range": [low, high],
        "unit": "template",
        "outside_range": outside,
        "unsampled_templates": unsampled,
        "zero_variance_share": share,
        "zero_variance_max": settings.base_zero_variance_max,
        "templates": pooled,
        "tasks": tasks,
    }
