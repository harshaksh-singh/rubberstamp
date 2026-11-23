"""Truth table for the state layer (SPEC §3): expectations, exact counts, inserts matched by
content, extra fields on matched updates, allow rules, and strict-scope collateral; plus the
additions: "at most" tolerances, final-state counts and backed claims."""

import copy

import pytest
from rs_core.diff import diff
from rs_core.models import SchemaInfo, Snapshot, TableInfo
from rs_verify import AllowRule, BackedClaim, ExpectedChange, FieldPred, FinalCount, RowMatch, StateSpec, eq
from rs_verify.state import check_state

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


# ---------------------------------------------------------------- at_most, final counts, backed claims


def failed_with_final(state: StateSpec, after) -> list[str]:
    final = Snapshot(schema_version="t", tables=after)
    d = diff(Snapshot(schema_version="t", tables=BEFORE), final, SCHEMA)
    return [c.id for c in check_state(state, d, final) if not c.passed]


NOTE = {"id": "n1", "owner": "a", "text": "hi"}
NOTE_2 = {"id": "n2", "owner": "a", "text": "again"}
UP_TO_ONE_NOTE_A = ExpectedChange(
    table="notes", kind="insert", where=(eq("/owner", "a"),), count=1, count_mode="at_most"
)
OPEN_ITEMS = FinalCount(table="items", where=(eq("/status", "open"),), count=2)
NEW_ITEM = {"id": "3", "owner": "a", "status": "open", "qty": 1, "meta": {"x": 0}, "updated_at": "tue"}
CLAIM_CLOSED = BackedClaim(
    claim=RowMatch(table="notes", where=(FieldPred(path="/text", op="regex", value="(?i)closed"),)),
    evidence=RowMatch(table="items", kind="update", where=(eq("/id", "1"),)),
)

EXTENDED_CASES = [
    ("at_most: none is fine", S(UP_TO_ONE_NOTE_A), edit(), []),
    ("at_most: one is covered, not collateral", S(UP_TO_ONE_NOTE_A), edit(("put", "notes", "n1", NOTE)), []),
    (
        "at_most: one too many",
        S(UP_TO_ONE_NOTE_A),
        edit(("put", "notes", "n1", NOTE), ("put", "notes", "n2", NOTE_2)),
        ["state:expect:0"],
    ),
    ("final: the seeded rows satisfy an exact count", StateSpec(expected=(), final=(OPEN_ITEMS,)), edit(), []),
    (
        "final: a new matching row breaks the exact count but is not collateral",
        StateSpec(expected=(), final=(OPEN_ITEMS,)),
        edit(("put", "items", "3", NEW_ITEM)),
        ["state:final:0"],
    ),
    (
        "final at_least: a new matching row passes",
        StateSpec(expected=(), final=(OPEN_ITEMS.model_copy(update={"count_mode": "at_least"}),)),
        edit(("put", "items", "3", NEW_ITEM)),
        [],
    ),
    (
        "final: an update that leaves the count is still collateral (only inserts are covered)",
        StateSpec(expected=(), final=(OPEN_ITEMS,)),
        edit(("set", "items", "1", "qty", 5)),
        ["state:scope:items:1"],
    ),
    (
        "final: closing a seeded row lowers the count",
        StateSpec(expected=(CLOSE_1,), final=(OPEN_ITEMS,)),
        edit(("set", "items", "1", "status", "closed")),
        ["state:final:0"],
    ),
    (
        "backed: no claim needs no evidence",
        StateSpec(expected=(), backed=(CLAIM_CLOSED,), strict_scope=False),
        edit(),
        [],
    ),
    (
        "backed: a claim without the change fails",
        StateSpec(expected=(), backed=(CLAIM_CLOSED,), strict_scope=False),
        edit(("put", "notes", "n1", {"id": "n1", "owner": "a", "text": "Item closed."})),
        ["state:backed:0"],
    ),
    (
        "backed: a claim with the change passes",
        StateSpec(expected=(), backed=(CLAIM_CLOSED,), strict_scope=False),
        edit(
            ("put", "notes", "n1", {"id": "n1", "owner": "a", "text": "Item closed."}),
            ("set", "items", "1", "status", "closed"),
        ),
        [],
    ),
    (
        "backed: evidence on another record does not count",
        StateSpec(expected=(), backed=(CLAIM_CLOSED,), strict_scope=False),
        edit(
            ("put", "notes", "n1", {"id": "n1", "owner": "a", "text": "Item closed."}),
            ("set", "items", "2", "status", "closed"),
        ),
        ["state:backed:0"],
    ),
]


@pytest.mark.parametrize(
    ("name", "state", "after", "expected_failures"), EXTENDED_CASES, ids=[c[0] for c in EXTENDED_CASES]
)
def test_at_most_final_and_backed_truth_table(name, state, after, expected_failures):
    assert sorted(failed_with_final(state, after)) == sorted(expected_failures)


def test_final_count_without_a_snapshot_fails_loudly():
    d = diff(Snapshot(schema_version="t", tables=BEFORE), Snapshot(schema_version="t", tables=edit()), SCHEMA)
    (check,) = [c for c in check_state(StateSpec(expected=(), final=(OPEN_ITEMS,)), d) if c.id == "state:final:0"]
    assert not check.passed and "no final snapshot" in check.detail
