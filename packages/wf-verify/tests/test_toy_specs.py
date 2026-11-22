"""Strict toy specs: goldens pass, no-ops fail, and the over-strict (pinned-PK) false reject."""

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wf_core.models import Episode, Snapshot
from wf_core.testing.toyworld import GOLDEN_CALLS, SCHEMA, TASKS
from wf_verify import ExpectedChange, SpecVerifier, eq
from wf_verify.toy_specs import REFUND_EMAIL, TOY_SPECS, toy_episode

TASK_IDS = sorted(TOY_SPECS)


def test_one_strict_spec_per_toy_task():
    assert set(TOY_SPECS) == set(TASKS)
    assert all(spec.id.endswith(".strict") for spec in TOY_SPECS.values())


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_golden_passes_strict_spec(task_id):
    verdict = SpecVerifier(TOY_SPECS[task_id], SCHEMA).verify(toy_episode(task_id, GOLDEN_CALLS[task_id]))
    assert verdict.passed and verdict.reward == 1.0, verdict.checks
    assert all(c.passed for c in verdict.checks)


@pytest.mark.parametrize("task_id", TASK_IDS)
@pytest.mark.parametrize("answer", [None, "Done, all complete."])
def test_noop_fails_strict_spec(task_id, answer):
    verdict = SpecVerifier(TOY_SPECS[task_id], SCHEMA).verify(toy_episode(task_id, [], answer))
    assert not verdict.passed and verdict.reward == 0.0
    assert any(c.id.startswith("state:expect:") and not c.passed for c in verdict.checks)


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_golden_with_extra_reads_still_passes(task_id):
    calls = [("get_order", {"order_id": "o1"}), *GOLDEN_CALLS[task_id], ("get_order", {"order_id": "o3"})]
    assert SpecVerifier(TOY_SPECS[task_id], SCHEMA).verify(toy_episode(task_id, calls)).passed


def test_wrong_record_and_wrong_amount_fail():
    v = SpecVerifier(REFUND_EMAIL, SCHEMA)
    wrong_order = toy_episode(
        "refund_email",
        [("create_refund", {"order_id": "o2", "amount_cents": 5000}), *GOLDEN_CALLS["refund_email"][1:]],
    )
    partial = toy_episode(
        "refund_email",
        [("create_refund", {"order_id": "o1", "amount_cents": 4999}), *GOLDEN_CALLS["refund_email"][1:]],
    )
    assert not v.verify(wrong_order).passed
    assert not v.verify(partial).passed


# ---------------------------------------------------------------- over-strict spec: the false-reject case


def rekey_refund(ep: Episode, new_pk: str) -> Episode:
    """A valid alternative end state: the same refund stored under another generated id
    (what SPEC §4's `alt_insert_ids` operator produces)."""
    tables = copy.deepcopy(ep.final.tables)
    row = tables["refunds"].pop("r1")
    tables["refunds"][new_pk] = {**row, "id": new_pk}
    for audit in tables["audit_log"].values():
        if audit["ref"] == "r1":
            audit["ref"] = new_pk
    return ep.model_copy(update={"final": Snapshot(schema_version=ep.final.schema_version, tables=tables)})


def pinned_pk_spec():
    """Over-strict: locates the inserted refund by its generated primary key."""
    state = REFUND_EMAIL.state
    assert state is not None
    pinned = ExpectedChange(
        table="refunds", kind="insert", where=(eq("/id", "r1"),), expect=(eq("/amount_cents", 5000),)
    )
    return REFUND_EMAIL.model_copy(
        update={
            "id": "toy.refund_email.pinned_pk",
            "state": state.model_copy(update={"expected": (pinned, *state.expected[1:])}),
        }
    )


def test_overstrict_spec_rejects_a_valid_alternative():
    golden = toy_episode("refund_email", GOLDEN_CALLS["refund_email"])
    alternative = rekey_refund(golden, "r7")
    over_strict = SpecVerifier(pinned_pk_spec(), SCHEMA)
    correct = SpecVerifier(REFUND_EMAIL, SCHEMA)

    assert over_strict.verify(golden).passed  # looks fine on the golden run...
    rejected = over_strict.verify(alternative)  # ...but rejects an equally correct world
    assert not rejected.passed
    assert {c.id for c in rejected.checks if not c.passed} == {"state:expect:0", "state:scope:refunds:r7"}
    assert correct.verify(alternative).passed  # matching inserts by content accepts it


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=8))
def test_content_matching_is_invariant_to_insert_ids(new_pk):
    golden = toy_episode("refund_email", GOLDEN_CALLS["refund_email"])
    assert SpecVerifier(REFUND_EMAIL, SCHEMA).verify(rekey_refund(golden, new_pk)).passed
