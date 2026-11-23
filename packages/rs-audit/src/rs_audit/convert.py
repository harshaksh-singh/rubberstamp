"""Conversions between rs_core data and OpenAdapt `verifier_audit` data.

- A rs_core `ToolCall` becomes an OpenAdapt `Call`; its `kind` comes from the tool's
  `ToolSpec.effect` (read -> READ, write -> WRITE) or from the configured claim tool (CLAIM).
- A rs_core `StateDiff` becomes an OpenAdapt effect: a canonical sorted multiset of JSON rows,
  one per changed row, naming the table, primary key, operation and the row itself. The diff has
  already dropped volatile fields, so two replays that differ only in timestamps give equal effects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from openadapt_flow.verifier_audit.model import Call, CallKind, Effect, canonical_effect
from rs_core.diff import StateDiff
from rs_core.models import ToolCall, ToolSpec


def kind_map(
    tools: Iterable[ToolSpec],
    claim_tool: str | None = None,
    overrides: Mapping[str, CallKind] | None = None,
) -> dict[str, CallKind]:
    """Tool name -> call kind. The claim tool and explicit overrides win over `ToolSpec.effect`."""
    kinds = {t.name: CallKind.READ if t.effect == "read" else CallKind.WRITE for t in tools}
    if claim_tool:
        kinds[claim_tool] = CallKind.CLAIM
    kinds.update(overrides or {})
    return kinds


def to_call(tc: ToolCall, kinds: Mapping[str, CallKind]) -> Call:
    if tc.tool not in kinds:
        raise KeyError(f"tool {tc.tool!r} has no ToolSpec, so its call kind is unknown")
    return Call(name=tc.tool, args=dict(tc.args), kind=kinds[tc.tool])


def to_tool_call(call: Call, idx: int) -> ToolCall:
    return ToolCall(idx=idx, tool=call.name, args=dict(call.args))


def effect_rows(d: StateDiff) -> list[dict[str, Any]]:
    """One JSON row per changed record. `row` is the record after the change (before, for a delete)."""
    rows: list[dict[str, Any]] = []
    for c in d.changes:
        row: dict[str, Any] = {
            "table": c.table,
            "pk": c.pk,
            "op": c.kind,
            "row": c.before if c.kind == "delete" else c.after,
        }
        if c.kind == "update":
            row["changed"] = [f.path for f in c.fields]
        rows.append(row)
    return rows


def effect_of(d: StateDiff) -> Effect:
    return canonical_effect(effect_rows(d))
