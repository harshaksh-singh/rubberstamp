"""Evaluating `FieldPred`s against JSON documents (rows or call args).

Evaluation never raises: a missing path or a type mismatch simply makes the predicate
false (except `ne`, which holds when the field is missing). Invalid predicates are rejected
earlier, when the spec is built.
"""

from __future__ import annotations

import re

from .spec import FieldPred, Json, is_array, is_number, is_object


class _Missing:
    """Marks "no value at this path" (distinct from a JSON null)."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def pointer(*keys: str | int) -> str:
    """Build a JSON pointer from raw keys: pointer("a/b", 0) == "/a~1b/0"."""
    return "".join("/" + str(k).replace("~", "~0").replace("/", "~1") for k in keys)


def resolve(doc: Json, path: str) -> Json:
    """Return the value at JSON pointer `path` in `doc`, or MISSING."""
    if path == "":
        return doc
    current = doc
    for raw in path[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if is_object(current):
            if key not in current:
                return MISSING
            current = current[key]
        elif is_array(current):
            if not key.isdigit() or (len(key) > 1 and key[0] == "0") or int(key) >= len(current):
                return MISSING
            current = current[int(key)]
        else:
            return MISSING
    return current


def json_equal(a: Json, b: Json) -> bool:
    """Equality with JSON semantics: 1 == 1.0, but True != 1 (Python says True == 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if is_object(a) and is_object(b):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if is_array(a) and is_array(b):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b, strict=True))
    if is_object(a) or is_array(a) or is_object(b) or is_array(b):
        return False
    return a == b


def holds(pred: FieldPred, doc: Json, *, before: Json = MISSING, after: Json = MISSING) -> bool:
    """Does `pred` hold?

    Value ops (eq, ne, approx, in, regex, exists) read `doc`. `changed` / `unchanged`
    compare `before` and `after` at the same path; a missing row or field counts as a value,
    so a field that appears on insert is "changed".
    """
    if pred.op in ("changed", "unchanged"):
        b, a = resolve(before, pred.path), resolve(after, pred.path)
        same = (b is MISSING and a is MISSING) or (b is not MISSING and a is not MISSING and json_equal(b, a))
        return same if pred.op == "unchanged" else not same

    actual = resolve(doc, pred.path)
    if pred.op == "exists":
        present = actual is not MISSING
        return present if pred.value is not False else not present
    if pred.op == "ne":
        return actual is MISSING or not json_equal(actual, pred.value)
    if actual is MISSING:
        return False
    if pred.op == "eq":
        return json_equal(actual, pred.value)
    if pred.op == "approx":
        assert pred.tol is not None  # guaranteed by FieldPred validation
        return is_number(actual) and abs(actual - pred.value) <= pred.tol
    if pred.op == "in":
        return any(json_equal(actual, option) for option in pred.value)
    if pred.op == "regex":
        return isinstance(actual, str) and re.search(pred.value, actual) is not None
    raise AssertionError(f"unhandled op {pred.op}")


def all_hold(preds: tuple[FieldPred, ...], doc: Json, *, before: Json = MISSING, after: Json = MISSING) -> bool:
    return all(holds(p, doc, before=before, after=after) for p in preds)
