from hypothesis import given
from hypothesis import strategies as st
from rs_core.diff import apply, diff, normalize, snapshot_digest
from rs_core.models import SchemaInfo, Snapshot, TableInfo
from rs_core.testing.toyworld import SCHEMA, TASKS, ToyExecutor, golden_calls

leaf = st.one_of(st.none(), st.booleans(), st.integers(-5, 5), st.text(max_size=3))
value = st.recursive(leaf, lambda c: st.dictionaries(st.text(max_size=2), c, max_size=3), max_leaves=6)
row = st.dictionaries(st.sampled_from(["a", "b", "c", "ts"]), value, max_size=4)
rows = st.dictionaries(st.sampled_from(["1", "2", "3"]), row, max_size=3)
tables = st.dictionaries(st.sampled_from(["x", "y"]), rows, max_size=2)
SCH = SchemaInfo(version="t", tables={"x": TableInfo(pk="id", volatile=("ts",)), "y": TableInfo(pk="id")})


def snap(t):
    return Snapshot(schema_version="t", tables=t)


@given(tables, tables)
def test_apply_diff_roundtrip(a, b):
    sa, sb = snap(a), snap(b)
    na, nb = normalize(sa, SCH), normalize(sb, SCH)
    # tables present in one side only may be absent vs empty; compare non-empty content
    got = apply(na, diff(sa, sb, SCH))
    assert {k: v for k, v in got.tables.items() if v} == {k: v for k, v in nb.tables.items() if v}


@given(tables)
def test_diff_with_itself_is_empty(a):
    assert diff(snap(a), snap(a), SCH).changes == ()


def test_digest_ignores_key_order_and_volatile():
    a = snap({"x": {"1": {"a": 1, "b": 2, "ts": "monday"}}})
    b = snap({"x": {"1": {"b": 2, "a": 1, "ts": "tuesday"}}})
    assert snapshot_digest(a, SCH) == snapshot_digest(b, SCH)
    assert diff(a, b, SCH).changes == ()


def test_nested_field_paths():
    a = snap({"y": {"1": {"addr": {"city": "Pune", "zip": "1"}}}})
    b = snap({"y": {"1": {"addr": {"city": "Delhi", "zip": "1"}}}})
    (change,) = diff(a, b, SCH).changes
    assert change.kind == "update"
    assert [f.path for f in change.fields] == ["/addr/city"]


def test_toy_golden_is_deterministic_and_changes_expected_rows():
    ex = ToyExecutor()
    task = TASKS["refund_approved"]
    eps = [ex.run(task, golden_calls(task.id)) for _ in range(3)]
    digests = {snapshot_digest(e.final, SCHEMA) for e in eps}
    assert len(digests) == 1
    d = diff(eps[0].initial, eps[0].final, SCHEMA)
    assert {(c.table, c.kind) for c in d.changes} == {
        ("approvals", "insert"),
        ("refunds", "insert"),
        ("orders", "update"),
        ("audit_log", "insert"),
    }


def test_toy_errors_are_recorded_not_raised():
    from rs_core.models import ToolCall

    ex = ToyExecutor()
    ep = ex.run(
        TASKS["close_ticket"],
        [ToolCall(idx=0, tool="set_ticket_status", args={"ticket_id": "nope", "status": "closed"})],
    )
    assert ep.trajectory.calls[0].ok is False
    assert diff(ep.initial, ep.final, SCHEMA).changes == ()


def test_volatile_timestamp_not_a_change_but_status_is():
    ex = ToyExecutor()
    ep = ex.run(TASKS["close_ticket"], golden_calls("close_ticket"))
    (change,) = diff(ep.initial, ep.final, SCHEMA).changes
    assert [f.path for f in change.fields] == ["/status"]
