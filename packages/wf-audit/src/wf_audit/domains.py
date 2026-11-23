"""Value domains of tool arguments, read from the JSON Schema in `ToolSpec.params`.

The schema families (`families.py`) ask three questions of a tool's schema: which arguments exist and which are
required, whether a value is valid for an argument, and which other valid values an argument could take.

Only the part of JSON Schema that tool argument schemas use is read: `type` (one type, or a list with "null"),
`enum`, `const`, an `anyOf`/`oneOf` with a null branch (an optional argument, as pydantic writes it), numeric
bounds, string lengths and `pattern`. An argument whose values are objects, arrays or `$ref`s has no domain here,
so the families leave it alone. `format` is an annotation in JSON Schema 2020-12, not a constraint, and is ignored.

`params` comes in two shapes:
- a full object schema, `{"type": "object", "properties": ..., "required": [...]}`, as pydantic and MCP
  `inputSchema` write it (the helpdesk tools). A tool without arguments may leave out `properties`;
- a bare `{arg: schema}` map, as the toy tools write it. Every argument of a bare map is required.
An argument whose schema is not an object (a boolean schema, a malformed spec) has no domain.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

Schema = Mapping[str, Any]

SCALARS = ("string", "integer", "number", "boolean")
_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def arguments(params: Schema) -> tuple[dict[str, Schema], frozenset[str]]:
    """(argument -> schema, required arguments) of a tool's `params`, in either shape. Arguments whose schema is not
    an object are dropped."""
    if params.get("type") == "object" or isinstance(params.get("properties"), dict):
        properties = params.get("properties") or {}
        required = frozenset(params.get("required", ()))
    else:
        properties, required = params, frozenset(params)
    args = {a: s for a, s in properties.items() if isinstance(s, Mapping)}
    return args, required & set(args)


def value_schema(schema: Any) -> Schema | None:
    """The schema of an argument's non-null values: the one non-null branch of an optional argument, or the schema
    itself, with null dropped from an enum. None when that is not a single scalar type, an `enum` or a `const`."""
    if not isinstance(schema, Mapping):
        return None
    branches = schema.get("anyOf", schema.get("oneOf"))
    if branches is not None:
        rest = [b for b in branches if not (isinstance(b, Mapping) and b.get("type") == "null")]
        return value_schema(rest[0]) if len(rest) == 1 else None
    types = schema.get("type")
    if isinstance(types, list):
        rest = [t for t in types if t != "null"]
        if len(rest) != 1:
            return None
        schema = {**schema, "type": rest[0]}
    if "enum" in schema:
        return {**schema, "enum": [m for m in schema["enum"] if m is not None]}
    if "const" in schema or schema.get("type") in SCALARS:
        return schema
    return None


def is_mutable(schema: Schema) -> bool:
    """Can a set value of this argument be replaced by another valid one? (No for a `const` or a one-member enum.)"""
    s = value_schema(schema)
    if s is None or "const" in s:
        return False
    return "enum" not in s or len(s["enum"]) > 1


def same(a: Any, b: Any) -> bool:
    """Equality with JSON semantics: True is not 1."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def accepts(schema: Any, value: Any) -> bool:
    """Is `value` valid for `schema`, as far as the keywords this module reads can tell? A boolean schema accepts
    everything or nothing, as in JSON Schema."""
    if not isinstance(schema, Mapping):
        return schema is True
    branches = schema.get("anyOf", schema.get("oneOf"))
    if branches is not None:
        return any(accepts(b, value) for b in branches)
    if "const" in schema and not same(value, schema["const"]):
        return False
    if "enum" in schema and not any(same(value, m) for m in schema["enum"]):
        return False
    types = schema.get("type")
    if types is not None:
        wanted = types if isinstance(types, list) else [types]
        ok = any(t in _TYPES and isinstance(value, _TYPES[t]) for t in wanted)
        if not ok or (isinstance(value, bool) and "boolean" not in wanted):
            return False
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return False
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            return False
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return False
    return True


def alternatives(schema: Schema, value: Any) -> list[Any]:
    """Other valid values for an argument set to `value`, best first:
    - an enum: the next member, then the rest, cycling through the declared order;
    - a boolean: its negation;
    - a number: +1, then -1 (a bound removes the side it would break);
    - any other string: "x" appended ("y" if it already ends in "x"), as OpenAdapt's `wrong_value` does, with the
      last character replaced instead when `maxLength` leaves no room."""
    s = value_schema(schema)
    if s is None or value is None or "const" in s:
        return []
    if "enum" in s:
        members = list(s["enum"])
        at = next((i for i, m in enumerate(members) if same(m, value)), -1)
        out = [m for m in members[at + 1 :] + members[: at + 1] if not same(m, value)]
    elif isinstance(value, bool):
        out = [not value]
    elif isinstance(value, int | float):
        out = [value + 1, value - 1]
    elif isinstance(value, str):
        suffix = "y" if value.endswith("x") else "x"
        fits = len(value) < s.get("maxLength", len(value) + 1)
        out = [value + suffix] if fits else [value[:-1] + ("y" if value[-1:] == "x" else "x")]
    else:
        out = []
    return [v for v in out if accepts(schema, v)]


def extra_values(schema: Schema, pool: Iterable[Any]) -> list[Any]:
    """Valid values for an optional argument a call left unset, other than its default: the `const` or the enum
    members, the negated default of a boolean (both values when it has none), a numeric default +1 and -1, then the
    values the suite's write calls give the same argument (`pool`)."""
    s = value_schema(schema)
    if s is None:
        return []
    default = schema.get("default", s.get("default"))
    if "const" in s:
        derived = [s["const"]]
    elif "enum" in s:
        derived = list(s["enum"])
    elif s.get("type") == "boolean":
        derived = [not default] if isinstance(default, bool) else [True, False]
    elif isinstance(default, int | float) and not isinstance(default, bool):
        derived = [default + 1, default - 1]
    else:
        derived = []
    return distinct(v for v in [*derived, *pool] if v is not None and not same(v, default) and accepts(schema, v))


def fill_values(schema: Schema, pool: Sequence[Any]) -> list[Any]:
    """Valid values for a required argument of a call the families add: the values the suite's write calls give
    the same argument (`pool`) first, then the `const`, the enum members, or both booleans."""
    s = value_schema(schema)
    if s is None:
        return []
    if "const" in s:
        derived = [s["const"]]
    elif "enum" in s:
        derived = list(s["enum"])
    else:
        derived = [False, True] if s.get("type") == "boolean" else []
    return distinct(v for v in [*pool, *derived] if v is not None and accepts(schema, v))


def distinct(values: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if not any(same(v, seen) for seen in out):
            out.append(v)
    return out
