"""State differences between two snapshots.

Rules (also tested as properties in tests/test_diff.py):
- Volatile fields (per table) are dropped before comparing.
- Nested dicts are compared field by field; lists and scalars are compared whole.
- `apply(normalize(a), diff(a, b)) == normalize(b)`, and `diff(a, a)` is empty.
- Digests are sha256 of canonical JSON, so dict insertion order never matters.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from .models import Frozen, Json, SchemaInfo, Snapshot


def canonical_json(value: Json) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Json) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def normalize(snap: Snapshot, schema: SchemaInfo) -> Snapshot:
    """Drop volatile fields so two states that differ only in timestamps compare equal."""
    tables: dict[str, dict[str, dict[str, Json]]] = {}
    for name, rows in snap.tables.items():
        info = schema.tables.get(name)
        drop = set(info.volatile) if info else set()
        tables[name] = {pk: {k: v for k, v in row.items() if k not in drop} for pk, row in rows.items()}
    return Snapshot(schema_version=snap.schema_version, tables=tables)


def snapshot_digest(snap: Snapshot, schema: SchemaInfo) -> str:
    return digest(normalize(snap, schema).tables)


class FieldChange(Frozen):
    path: str  # JSON pointer inside the row, e.g. "/status" or "/address/city"
    before: Json = None
    after: Json = None


class RowChange(Frozen):
    table: str
    pk: str
    kind: Literal["insert", "update", "delete"]
    before: dict[str, Json] | None = None
    after: dict[str, Json] | None = None
    fields: tuple[FieldChange, ...] = ()


class StateDiff(Frozen):
    changes: tuple[RowChange, ...] = ()  # sorted by (table, pk)

    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))

    def touched(self) -> set[tuple[str, str]]:
        return {(c.table, c.pk) for c in self.changes}


_MISSING = object()


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _field_changes(before: Json, after: Json, path: str = "") -> list[FieldChange]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[FieldChange] = []
        for key in sorted(set(before) | set(after)):
            b, a = before.get(key, _MISSING), after.get(key, _MISSING)
            if b == a:
                continue
            sub = f"{path}/{_escape(key)}"
            if b is _MISSING:
                out.append(FieldChange(path=sub, before=None, after=a))
            elif a is _MISSING:
                out.append(FieldChange(path=sub, before=b, after=None))
            else:
                out.extend(_field_changes(b, a, sub))
        return out
    return [] if before == after else [FieldChange(path=path or "/", before=before, after=after)]


def diff(a: Snapshot, b: Snapshot, schema: SchemaInfo) -> StateDiff:
    na, nb = normalize(a, schema), normalize(b, schema)
    changes: list[RowChange] = []
    for table in sorted(set(na.tables) | set(nb.tables)):
        ra, rb = na.tables.get(table, {}), nb.tables.get(table, {})
        for pk in sorted(set(ra) | set(rb)):
            before, after = ra.get(pk), rb.get(pk)
            if before == after:
                continue
            if before is None:
                assert after is not None
                fields = tuple(FieldChange(path=f"/{_escape(k)}", after=v) for k, v in sorted(after.items()))
                changes.append(RowChange(table=table, pk=pk, kind="insert", after=after, fields=fields))
            elif after is None:
                changes.append(RowChange(table=table, pk=pk, kind="delete", before=before))
            else:
                fields = tuple(_field_changes(before, after))
                changes.append(RowChange(table=table, pk=pk, kind="update", before=before, after=after, fields=fields))
    return StateDiff(changes=tuple(changes))


def apply(a: Snapshot, d: StateDiff) -> Snapshot:
    """Apply a diff produced by `diff` (rows carry their full `after`)."""
    tables = {name: dict(rows) for name, rows in a.tables.items()}
    for change in d.changes:
        rows = tables.setdefault(change.table, {})
        if change.kind == "delete":
            rows.pop(change.pk, None)
        else:
            assert change.after is not None
            rows[change.pk] = dict(change.after)
    return Snapshot(schema_version=a.schema_version, tables=tables)
