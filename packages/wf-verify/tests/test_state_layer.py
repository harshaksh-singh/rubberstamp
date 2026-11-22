"""Truth table for the state layer (SPEC §3): expectations, exact counts, inserts matched by
content, extra fields on matched updates, allow rules, and strict-scope collateral."""

import copy

import pytest
from wf_core.diff import diff
from wf_core.models import SchemaInfo, Snapshot, TableInfo
from wf_verify import AllowRule, ExpectedChange, FieldPred, StateSpec, eq
from wf_verify.state import check_state

SCHEMA = SchemaInfo(
    version="t",
    tables={
        "items": TableInfo(pk="id", volatile=("updated_at",)),
        "notes": TableInfo(pk="id", kind="side_effect"),
    },
)

BEFORE = {
    "items": {
        "1": {"id": "1", "owner": "a", "status": "open", "qty": 1, "meta": {"x": 0}, "updated_at": "mon"},
        "2": {"id": "2", "owner": "b", "status": "open", "qty": 1, "meta": {"x": 0}, "updated_at": "mon"},
    },
    "notes": {},
}


def edit(*ops):
    """Apply ("set", table, pk, field, value) / ("put", table, pk, row) / ("del", table, pk) to BEFORE."""
    tables = copy.deepcopy(BEFORE)
    for op in ops:
        if op[0] == "set":
            _, table, pk, field, value = op
            tables[table][pk][field] = value
        elif op[0] == "put":
            _, table, pk, row = op
            tables[table][pk] = row
        else:
            _, table, pk = op
            del tables[table][pk]
    return tables


def failed(state: StateSpec, after) -> list[str]:
    d = diff(Snapshot(schema_version="t", tables=BEFORE), Snapshot(schema_version="t", tables=after), SCHEMA)
    return [c.id for c in check_state(state, d) if not c.passed]


CLOSE_1 = ExpectedChange(table="items", kind="update", where=(eq("/id", "1"),), expect=(eq("/status", "closed"),))
NOTE_A = ExpectedChange(table="notes", kind="insert", where=(eq("/owner", "a"),), expect=(eq("/text", "hi"),))
DELETE_2 = ExpectedChange(table="items", kind="delete", where=(eq("/id", "2"),))
NO_NOTE_B = ExpectedChange(table="notes", kind="insert", where=(eq("/owner", "b"),), count=0)


def S(*expected, allow=(), strict_scope=True):
    return StateSpec(expected=expected, allow=allow, strict_scope=strict_scope)


CASES = [
    # --- updates
    ("update done", S(CLOSE_1), edit(("set", "items", "1", "status", "closed")), []),
    ("update missing", S(CLOSE_1), edit(), ["state:expect:0"]),
    (
        "update wrong value is not a match, so it is also collateral",
        S(CLOSE_1),
        edit(("set", "items", "1", "status", "pending")),
        ["state:expect:0", "state:scope:items:1"],
    ),
    (
        "volatile field changes are invisible",
        S(CLOSE_1),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "updated_at", "tue")),
        [],
    ),
    (
        "extra changed field on the matched row",
        S(CLOSE_1),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "qty", 9)),
        ["state:expect:0"],
    ),
    (
        "extra field allowed by an allow rule",
        S(CLOSE_1, allow=(AllowRule(table="items", kind="update", fields=("/qty",)),)),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "qty", 9)),
        [],
    ),
    (
        "extra field on the matched row is checked even without strict scope",
        S(CLOSE_1, strict_scope=False),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "qty", 9)),
        ["state:expect:0"],
    ),
    (
        "two expectations on the same row cover each other's fields",
        S(CLOSE_1, ExpectedChange(table="items", kind="update", where=(eq("/id", "1"),), expect=(eq("/qty", 9),))),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "qty", 9)),
        [],
    ),
    (
        "an expect on a parent object covers nested changes",
        S(ExpectedChange(table="items", kind="update", where=(eq("/id", "1"),), expect=(eq("/meta", {"x": 1}),))),
        edit(("set", "items", "1", "meta", {"x": 1})),
        [],
    ),
    (
        "changed op in expect",
        S(
            ExpectedChange(
                table="items", kind="update", where=(eq("/id", "1"),), expect=(FieldPred(path="/qty", op="changed"),)
            )
        ),
        edit(("set", "items", "1", "qty", 2)),
        [],
    ),
    # --- collateral (strict scope)
    (
        "collateral edit on another row",
        S(CLOSE_1),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "2", "status", "closed")),
        ["state:scope:items:2"],
    ),
    (
        "collateral passes without strict scope",
        S(CLOSE_1, strict_scope=False),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "2", "status", "closed")),
        [],
    ),
    (
        "allow rule scoped by where: matching row",
        S(CLOSE_1, allow=(AllowRule(table="items", kind="update", where=(eq("/owner", "b"),)),)),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "2", "qty", 3)),
        [],
    ),
    (
        "allow rule scoped by where: other row",
        S(CLOSE_1, allow=(AllowRule(table="items", kind="update", where=(eq("/owner", "c"),)),)),
        edit(("set", "items", "1", "status", "closed"), ("set", "items", "2", "qty", 3)),
        ["state:scope:items:2"],
    ),
    (
        "allow rule with a field list does not allow inserts",
        S(CLOSE_1, allow=(AllowRule(table="notes", fields=("/text",)),)),
        edit(("set", "items", "1", "status", "closed"), ("put", "notes", "n1", {"owner": "a"})),
        ["state:scope:notes:n1"],
    ),
    (
        "allow rule with kind * and fields * allows an insert",
        S(CLOSE_1, allow=(AllowRule(table="notes"),)),
        edit(("set", "items", "1", "status", "closed"), ("put", "notes", "n1", {"owner": "a"})),
        [],
    ),
    # --- inserts: matched by content, exact count
    ("insert done", S(NOTE_A), edit(("put", "notes", "n1", {"owner": "a", "text": "hi"})), []),
    ("insert under any pk", S(NOTE_A), edit(("put", "notes", "zz-99", {"owner": "a", "text": "hi"})), []),
    ("insert missing", S(NOTE_A), edit(), ["state:expect:0"]),
    (
        "duplicate insert breaks the exact count",
        S(NOTE_A),
        edit(
            ("put", "notes", "n1", {"owner": "a", "text": "hi"}), ("put", "notes", "n2", {"owner": "a", "text": "hi"})
        ),
        ["state:expect:0"],
    ),
    (
        "duplicate insert passes count_mode at_least",
        S(NOTE_A.model_copy(update={"count_mode": "at_least"})),
        edit(
            ("put", "notes", "n1", {"owner": "a", "text": "hi"}), ("put", "notes", "n2", {"owner": "a", "text": "hi"})
        ),
        [],
    ),
    (
        "a located row that fails expect is collateral, not ignored",
        S(NOTE_A),
        edit(
            ("put", "notes", "n1", {"owner": "a", "text": "hi"}), ("put", "notes", "n2", {"owner": "a", "text": "??"})
        ),
        ["state:scope:notes:n2"],
    ),
    (
        "count=2 needs exactly two",
        S(NOTE_A.model_copy(update={"count": 2})),
        edit(("put", "notes", "n1", {"owner": "a", "text": "hi"})),
        ["state:expect:0"],
    ),
    # --- prohibitions (count=0) and deletes
    ("count=0 holds when nothing happens", S(NO_NOTE_B), edit(), []),
    (
        "count=0 violated",
        S(NO_NOTE_B, allow=(AllowRule(table="notes"),)),
        edit(("put", "notes", "n1", {"owner": "b"})),
        ["state:expect:0"],
    ),
    ("delete done", S(DELETE_2), edit(("del", "items", "2")), []),
    ("delete of the wrong row", S(DELETE_2), edit(("del", "items", "1")), ["state:expect:0", "state:scope:items:1"]),
    # --- no expectations: a "do nothing" task
    ("empty spec, nothing changed", S(), edit(), []),
    ("empty spec, something changed", S(), edit(("set", "items", "2", "qty", 5)), ["state:scope:items:2"]),
]


@pytest.mark.parametrize(("name", "state", "after", "expected_failures"), CASES, ids=[c[0] for c in CASES])
def test_state_semantics_truth_table(name, state, after, expected_failures):
    assert failed(state, after) == expected_failures


def test_check_details_explain_the_failure():
    d = diff(
        Snapshot(schema_version="t", tables=BEFORE),
        Snapshot(
            schema_version="t", tables=edit(("set", "items", "1", "status", "closed"), ("set", "items", "1", "qty", 9))
        ),
        SCHEMA,
    )
    (check,) = check_state(S(CLOSE_1), d)
    assert "1 of 1 located rows" in check.detail and "items/1/qty" in check.detail
