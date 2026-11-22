"""Spec models reject invalid specs at construction and round-trip through JSON."""

import pytest
from pydantic import ValidationError
from wf_core.models import Episode, Snapshot, Task, Trajectory
from wf_core.testing.toyworld import SCHEMA
from wf_verify import (
    AllowRule,
    AnswerCheck,
    CallPattern,
    ExpectedChange,
    FieldPred,
    Leniency,
    ProcessRule,
    SpecVerifier,
    StateSpec,
    VerifierSpec,
    eq,
    make_lenient,
)
from wf_verify.toy_specs import TOY_SPECS

INVALID = [
    lambda: FieldPred(path="status", op="eq", value="x"),  # pointer must start with "/"
    lambda: FieldPred(path="/n", op="approx", value=5),  # approx needs tol
    lambda: FieldPred(path="/n", op="approx", value="5", tol=1),
    lambda: FieldPred(path="/n", op="approx", value=True, tol=1),
    lambda: FieldPred(path="/s", op="in", value="abc"),
    lambda: FieldPred(path="/s", op="regex", value="("),
    lambda: FieldPred(path="/s", op="regex", value=5),
    lambda: FieldPred(path="/s", op="exists", value="yes"),
    lambda: FieldPred(path="/s", op="changed", value="x"),
    lambda: FieldPred(path="/s", op="like", value="x"),
    lambda: FieldPred(path="/s", op="eq", value="x", extra=1),
    lambda: ExpectedChange(table="t", kind="delete", where=(), expect=(eq("/s", 1),)),
    lambda: ExpectedChange(table="t", kind="insert", where=(), count=-1),
    lambda: ExpectedChange(table="t", kind="upsert", where=()),
    lambda: AllowRule(table="t", fields=("",)),
    lambda: AllowRule(table="t", fields=("status",)),
    lambda: CallPattern(tool="x", args=(FieldPred(path="/a", op="changed"),)),
    lambda: ProcessRule(id="r", kind="precedes", a=CallPattern(tool="x")),
    lambda: ProcessRule(id="r", kind="never_after", a=CallPattern(tool="x")),
    lambda: ProcessRule(id="r", kind="count", a=CallPattern(tool="x"), b=CallPattern(tool="y")),
    lambda: ProcessRule(id="r", kind="count", a=CallPattern(tool="x"), min=3, max=2),
    lambda: AnswerCheck(kind="number", value="12"),
    lambda: AnswerCheck(kind="contains", value=12),
    lambda: AnswerCheck(kind="regex", value="["),
    lambda: VerifierSpec(id="s", task_id="t"),  # no layers: would accept everything
    lambda: VerifierSpec(
        id="s",
        task_id="t",
        process=(
            ProcessRule(id="dup", kind="count", a=CallPattern(tool="x")),
            ProcessRule(id="dup", kind="count", a=CallPattern(tool="y")),
        ),
    ),
]


@pytest.mark.parametrize("build", INVALID)
def test_invalid_specs_are_rejected(build):
    with pytest.raises(ValidationError):
        build()


def test_specs_are_frozen():
    spec = TOY_SPECS["close_ticket"]
    with pytest.raises(ValidationError):
        spec.id = "other"


def test_empty_state_spec_is_a_valid_do_nothing_spec():
    spec = VerifierSpec(id="s", task_id="t", state=StateSpec(expected=()))
    assert spec.state is not None and spec.state.strict_scope


@pytest.mark.parametrize("task_id", sorted(TOY_SPECS))
def test_json_roundtrip_strict_and_lenient(task_id):
    spec = TOY_SPECS[task_id]
    spec_flags = [f for f in Leniency if f is not Leniency.TRUST_CLAIM]
    for candidate in [spec, *(make_lenient(spec, f, schema=SCHEMA) for f in spec_flags)]:
        assert VerifierSpec.model_validate_json(candidate.model_dump_json()) == candidate


def test_verifier_rejects_tables_missing_from_schema():
    spec = VerifierSpec(
        id="s", task_id="t", state=StateSpec(expected=(ExpectedChange(table="nope", kind="insert", where=()),))
    )
    with pytest.raises(ValueError, match="nope"):
        SpecVerifier(spec, SCHEMA)


def test_spec_for_another_task_is_an_error_verdict_not_a_pass():
    spec = TOY_SPECS["close_ticket"]
    snap = Snapshot(schema_version="toy-1", tables={})
    ep = Episode(
        task=Task(id="other", source="toy", prompt="", tools=()),
        initial=snap,
        final=snap,
        trajectory=Trajectory(task_id="other", agent_id="x"),
    )
    verdict = SpecVerifier(spec, SCHEMA).verify(ep)
    assert not verdict.passed and verdict.reward == 0.0 and verdict.error
