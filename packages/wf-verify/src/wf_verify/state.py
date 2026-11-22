"""State layer: compare the world diff against `StateSpec` (SPEC §3, "State verifier").

Given D = diff(initial, final):

1. For each `ExpectedChange` e (check `state:expect:<i>`):
   - candidates = rows of D with e's table and kind whose `where` holds
     (on the after-row for inserts, the before-row for updates/deletes);
   - hits = candidates that also satisfy every `expect` (on the after-row);
   - pass iff len(hits) == e.count (or >= e.count with count_mode "at_least"),
     and no hit is an update that changed a field that neither `allow` nor the `expect`
     of some expectation hitting that row covers.
2. A row change is *matched* if it is a hit of some expectation, and *allowed* if the
   allow rules cover it. With `strict_scope`, every change that is neither fails
   `state:scope:<table>:<pk>` (collateral damage). A located row that fails `expect`
   is not matched, so a wrong-amount duplicate is collateral, not silently ignored.
"""

from __future__ import annotations

from wf_core.diff import RowChange, StateDiff
from wf_core.models import Check

from .preds import MISSING, all_hold
from .spec import AllowRule, ExpectedChange, FieldPred, Json, StateSpec


def _row(row: dict[str, Json] | None) -> Json:
    return MISSING if row is None else row


def located(where: tuple[FieldPred, ...], change: RowChange) -> bool:
    """`where` reads the after-row of an insert and the before-row of an update or delete."""
    doc = change.after if change.kind == "insert" else change.before
    return all_hold(where, _row(doc), before=_row(change.before), after=_row(change.after))


def satisfies(expect: tuple[FieldPred, ...], change: RowChange) -> bool:
    return all_hold(expect, _row(change.after), before=_row(change.before), after=_row(change.after))


def _under(path: str, prefixes: tuple[str, ...] | list[str]) -> bool:
    """Is `path` one of `prefixes` or nested inside one of them?"""
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _rule_applies(rule: AllowRule, change: RowChange) -> bool:
    return rule.table == change.table and rule.kind in ("*", change.kind) and located(rule.where, change)


def field_allowed(path: str, change: RowChange, allow: tuple[AllowRule, ...]) -> bool:
    return any(_rule_applies(r, change) and (r.fields == "*" or _under(path, r.fields)) for r in allow)


def row_allowed(change: RowChange, allow: tuple[AllowRule, ...]) -> bool:
    """Inserts and deletes need a whole-row rule; an update needs every changed path covered."""
    if change.kind != "update":
        return any(_rule_applies(r, change) and r.fields == "*" for r in allow)
    return all(field_allowed(f.path, change, allow) for f in change.fields)


def _uncovered_fields(change: RowChange, covered: list[str], allow: tuple[AllowRule, ...]) -> list[str]:
    if change.kind != "update":
        return []
    return [f.path for f in change.fields if not _under(f.path, covered) and not field_allowed(f.path, change, allow)]


def _expect_check(i: int, e: ExpectedChange, hits: list[RowChange], n_located: int, extras: list[str]) -> Check:
    count_ok = len(hits) == e.count if e.count_mode == "exact" else len(hits) >= e.count
    need = "exactly" if e.count_mode == "exact" else "at least"
    detail = f"{e.table} {e.kind}: {len(hits)} of {n_located} located rows satisfy expect, need {need} {e.count}"
    if hits:
        detail += f" (pks {', '.join(c.pk for c in hits)})"
    if extras:
        detail += f"; unexpected changed fields {', '.join(extras)}"
    return Check(id=f"state:expect:{i}", layer="state", passed=count_ok and not extras, detail=detail)


def check_state(spec: StateSpec, d: StateDiff) -> list[Check]:
    located_rows: list[list[RowChange]] = []
    hit_rows: list[list[RowChange]] = []
    covered: dict[tuple[str, str], list[str]] = {}  # row -> expect paths of every expectation it hits
    for e in spec.expected:
        candidates = [c for c in d.changes if c.table == e.table and c.kind == e.kind and located(e.where, c)]
        hits = [c for c in candidates if satisfies(e.expect, c)]
        located_rows.append(candidates)
        hit_rows.append(hits)
        for c in hits:
            covered.setdefault((c.table, c.pk), []).extend(p.path for p in e.expect)

    checks: list[Check] = []
    for i, (e, candidates, hits) in enumerate(zip(spec.expected, located_rows, hit_rows, strict=True)):
        extras = [
            f"{c.table}/{c.pk}{path}"
            for c in hits
            for path in _uncovered_fields(c, covered[(c.table, c.pk)], spec.allow)
        ]
        checks.append(_expect_check(i, e, hits, len(candidates), extras))

    if spec.strict_scope:
        for c in d.changes:
            if (c.table, c.pk) in covered or row_allowed(c, spec.allow):  # a hit, or allowed
                continue
            changed = ", ".join(f.path for f in c.fields) if c.kind == "update" else "whole row"
            checks.append(
                Check(
                    id=f"state:scope:{c.table}:{c.pk}",
                    layer="state",
                    passed=False,
                    detail=f"{c.kind} ({changed}) is neither expected nor allowed",
                )
            )
    return checks
