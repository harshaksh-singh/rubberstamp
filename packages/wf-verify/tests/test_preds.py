"""Truth tables for every FieldPred op, JSON pointers, and JSON equality."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wf_verify.preds import MISSING, holds, json_equal, pointer, resolve
from wf_verify.spec import FieldPred

DOC = {
    "s": "closed",
    "n": 5,
    "f": 5.0,
    "b": True,
    "z": None,
    "l": [1, 2],
    "nested": {"a/b": {"~k": 1}},
}


def P(op, path, value=None, tol=None):
    return FieldPred(path=path, op=op, value=value, tol=tol)


VALUE_OPS = [
    # eq: JSON equality, missing is never eq, bool is not int
    (P("eq", "/s", "closed"), True),
    (P("eq", "/s", "open"), False),
    (P("eq", "/n", 5.0), True),
    (P("eq", "/b", 1), False),
    (P("eq", "/n", True), False),
    (P("eq", "/z", None), True),
    (P("eq", "/missing", None), False),
    (P("eq", "/l", [1, 2]), True),
    (P("eq", "/l", [2, 1]), False),
    (P("eq", "/nested/a~1b/~0k", 1), True),
    # ne: the complement of eq, so a missing field is "ne"
    (P("ne", "/s", "open"), True),
    (P("ne", "/s", "closed"), False),
    (P("ne", "/missing", "x"), True),
    (P("ne", "/b", 1), True),
    # approx: numbers only, within tol inclusive
    (P("approx", "/n", 5.4, tol=0.5), True),
    (P("approx", "/n", 5.5, tol=0.5), True),
    (P("approx", "/n", 5.6, tol=0.5), False),
    (P("approx", "/s", 5, tol=1), False),
    (P("approx", "/b", 1, tol=1), False),
    (P("approx", "/missing", 5, tol=1), False),
    # in
    (P("in", "/s", ["open", "closed"]), True),
    (P("in", "/s", ["open"]), False),
    (P("in", "/b", [1]), False),
    (P("in", "/missing", [None]), False),
    # regex: re.search on strings only
    (P("regex", "/s", "^clo"), True),
    (P("regex", "/s", "ose"), True),
    (P("regex", "/s", "^ose"), False),
    (P("regex", "/n", "5"), False),
    # exists: presence (a null value is present); value=False means absent
    (P("exists", "/z"), True),
    (P("exists", "/missing"), False),
    (P("exists", "/missing", False), True),
    (P("exists", "/s", False), False),
    (P("exists", "/l/1"), True),
    (P("exists", "/l/2"), False),
]


@pytest.mark.parametrize(("pred", "expected"), VALUE_OPS, ids=lambda x: repr(x) if isinstance(x, bool) else None)
def test_value_op_truth_table(pred, expected):
    assert holds(pred, DOC) is expected


CHANGE_OPS = [
    # (before, after, path, changed?)
    ({"s": "open"}, {"s": "closed"}, "/s", True),
    ({"s": "open"}, {"s": "open"}, "/s", False),
    ({"n": 1}, {"n": 1.0}, "/n", False),
    ({"b": True}, {"b": 1}, "/b", True),
    ({}, {"s": "new"}, "/s", True),
    ({"s": "old"}, {}, "/s", True),
    ({}, {}, "/s", False),
    (MISSING, {"s": "x"}, "/s", True),  # insert: no before-row
    ({"s": "x"}, MISSING, "/s", True),  # delete: no after-row
]


@pytest.mark.parametrize(("before", "after", "path", "changed"), CHANGE_OPS)
def test_changed_unchanged_truth_table(before, after, path, changed):
    doc = after
    assert holds(P("changed", path), doc, before=before, after=after) is changed
    assert holds(P("unchanged", path), doc, before=before, after=after) is (not changed)


@pytest.mark.parametrize(
    ("doc", "path", "expected"),
    [
        ({"a": 1}, "", {"a": 1}),
        ({"a": {"b": 2}}, "/a/b", 2),
        ({"a/b": 1}, "/a~1b", 1),
        ({"~": 1}, "/~0", 1),
        ({"~1": 1}, "/~01", 1),  # ~01 decodes to "~1", not "/"
        ({"l": [10, 20]}, "/l/1", 20),
        ({"l": [10, 20]}, "/l/01", MISSING),  # leading zeros are not array indices
        ({"l": [10, 20]}, "/l/-", MISSING),
        ({"a": 1}, "/a/b", MISSING),
        (None, "/a", MISSING),
    ],
)
def test_resolve(doc, path, expected):
    got = resolve(doc, path)
    if expected is MISSING:
        assert got is MISSING
    else:
        assert got == expected


def test_json_equal_distinguishes_bool_from_number():
    assert json_equal(1, 1.0)
    assert not json_equal(True, 1)
    assert not json_equal({"x": [True]}, {"x": [1]})
    assert json_equal({"x": [1, {"y": None}]}, {"x": (1, {"y": None})})
    assert not json_equal({"x": 1}, {"x": 1, "y": 2})
    assert not json_equal([1], {"0": 1})


# ---------------------------------------------------------------- properties

keys = st.text(max_size=4)
json_leaf = st.one_of(
    st.none(), st.booleans(), st.integers(-3, 3), st.floats(-3, 3, allow_nan=False), st.text(max_size=3)
)
json_value = st.recursive(
    json_leaf,
    lambda inner: st.one_of(st.lists(inner, max_size=3), st.dictionaries(keys, inner, max_size=3)),
    max_leaves=8,
)


@given(st.lists(keys, min_size=1, max_size=3), json_leaf)
def test_pointer_roundtrip(path_keys, leaf):
    doc = leaf
    for key in reversed(path_keys):
        doc = {key: doc}
    assert json_equal(resolve(doc, pointer(*path_keys)), leaf)


@given(st.dictionaries(keys, json_value, max_size=4), keys, json_value)
def test_eq_and_ne_are_complements(doc, key, value):
    path = pointer(key)
    assert holds(P("eq", path, value), doc) != holds(P("ne", path, value), doc)


@given(st.dictionaries(keys, json_value, max_size=3), st.dictionaries(keys, json_value, max_size=3), keys)
def test_changed_and_unchanged_are_complements(before, after, key):
    path = pointer(key)
    c = holds(P("changed", path), after, before=before, after=after)
    u = holds(P("unchanged", path), after, before=before, after=after)
    assert c != u


@given(json_value)
def test_json_equal_is_reflexive(value):
    assert json_equal(value, value)
