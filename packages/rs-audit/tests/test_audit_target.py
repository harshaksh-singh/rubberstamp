"""ExecutorAuditTarget over the toy world: protocol fit, conversions, effects, claims, verdicts."""

import json

import pytest
from openadapt_flow.verifier_audit import AuditTarget, Call, CallKind, Verdict
from openadapt_flow.verifier_audit.model import IncidentalCalls
from rs_audit import ExecutorAuditTarget, kind_map, to_call, to_tool_call, toy_world
from rs_audit.testing.toy_verifiers import StrictToyVerifier
from rs_core.models import ToolCall
from rs_core.models import Verdict as WfVerdict
from rs_core.testing.toyworld import TOOLS


@pytest.fixture(scope="module")
def world():
    return toy_world()


@pytest.fixture(scope="module")
def target(world):
    return world.target(StrictToyVerifier(world.executor, world.golden, world.claim_tool))


def W(name, **args):
    return Call(name, args, CallKind.WRITE)


SUBMIT = Call("submit", {"answer": "done"}, CallKind.CLAIM)


class Raising:
    id = "raising"

    def verify(self, episode):
        raise RuntimeError("boom")


class Errored:
    id = "errored"

    def verify(self, episode):
        return WfVerdict(passed=False, reward=0.0, error="judge unavailable")


def test_target_fits_openadapt_protocols_and_name_carries_variant(world, target):
    assert isinstance(target, AuditTarget)
    assert isinstance(target, IncidentalCalls)
    assert target.name == "rubberstamp/toy/strict"
    assert world.target(Raising(), variant="v2").name == "rubberstamp/toy/v2"
    assert target.tasks() == ["refund_email", "close_ticket", "refund_approved"]


def test_kinds_come_from_tool_specs_and_claim_tool():
    kinds = kind_map(TOOLS, "submit")
    assert kinds["get_order"] is CallKind.READ
    assert kinds["create_refund"] is CallKind.WRITE
    assert kinds["submit"] is CallKind.CLAIM
    assert kind_map(TOOLS, None, {"get_order": CallKind.AUTH})["get_order"] is CallKind.AUTH


def test_toolcall_call_roundtrip():
    tc = ToolCall(idx=3, tool="create_refund", args={"order_id": "o1", "amount_cents": 5000})
    call = to_call(tc, kind_map(TOOLS))
    assert (call.name, call.args, call.kind) == ("create_refund", tc.args, CallKind.WRITE)
    assert to_tool_call(call, 3) == tc
    with pytest.raises(KeyError):
        to_call(ToolCall(idx=0, tool="nope", args={}), kind_map(TOOLS))


def test_reference_appends_claim_only_when_configured(world, target):
    ref = target.reference("refund_email")
    assert [c.kind for c in ref] == [CallKind.WRITE, CallKind.WRITE, CallKind.CLAIM]
    assert ref[-1] == SUBMIT
    plain = toy_world(claim_tool=None)
    no_claim = plain.target(StrictToyVerifier(plain.executor, plain.golden))
    assert all(c.kind is CallKind.WRITE for c in no_claim.reference("refund_email"))


def test_final_effect_is_canonical_rows_without_volatile_fields(target):
    ep = target.run("close_ticket", target.reference("close_ticket"), score=False)
    (row,) = (json.loads(r) for r in ep.final_effect)
    assert row == {
        "table": "tickets",
        "pk": "t1",
        "op": "update",
        "changed": ["/status"],
        "row": {"id": "t1", "customer_id": "c2", "status": "closed"},  # updated_at is volatile
    }
    assert ep.verdict is None and ep.call_errors == {}


def test_duplicate_insert_survives_in_the_multiset(target):
    ref = target.reference("refund_email")
    dup = [ref[0], ref[1], ref[1], ref[2]]
    once = target.run("refund_email", ref, score=False).final_effect
    twice = target.run("refund_email", dup, score=False).final_effect
    assert len(twice) == len(once) + 1


def test_virtual_claim_sets_final_answer_and_stays_in_trajectory(target):
    calls = [
        W("create_refund", order_id="o1", amount_cents=5000),
        SUBMIT,
        W("send_email", customer_id="c1", subject="x"),
    ]
    episode, errors = target.replay("refund_email", calls)
    assert [c.tool for c in episode.trajectory.calls] == ["create_refund", "submit", "send_email"]
    assert [c.idx for c in episode.trajectory.calls] == [0, 1, 2]
    assert episode.trajectory.final_answer == "done"
    assert errors == {}


def test_claim_effect_is_the_prefix_effect(target):
    late = [
        W("create_refund", order_id="o1", amount_cents=5000),
        SUBMIT,
        W("send_email", customer_id="c1", subject="x"),
    ]
    ep = target.run("refund_email", late, score=False)
    assert not any('"emails"' in r for r in ep.claim_effect)
    assert any('"emails"' in r for r in ep.final_effect)
    ref = target.run("refund_email", target.reference("refund_email"), score=False)
    assert ref.claim_effect == ref.final_effect


def test_refused_calls_are_keyed_by_audited_index(target):
    calls = [
        W("create_refund", order_id="o1", amount_cents=5000),
        SUBMIT,
        W("create_refund", order_id="o9", amount_cents=1),
    ]
    ep = target.run("refund_email", calls, score=False)
    assert list(ep.call_errors) == [2]
    assert "o9" in ep.call_errors[2]


def test_verdict_mapping(world, target):
    ref = target.reference("refund_email")
    assert target.run("refund_email", ref, score=True).verdict is Verdict.PASS
    failed = target.run("refund_email", [SUBMIT], score=True)
    assert failed.verdict is Verdict.FAIL
    assert "state:expected_diff" in failed.scorer_detail["failed_checks"]
    for bad in (Raising(), Errored()):
        ep = world.target(bad).run("refund_email", ref, score=True)
        assert ep.verdict is Verdict.ERROR
        assert ep.final_effect  # the effect is still read; only the verdict errored


def test_harness_crash_is_error_even_when_unscored(world):
    class Crashing(type(world.executor)):
        def run(self, task, calls, final_answer=None):
            raise OSError("disk gone")

    target = ExecutorAuditTarget(Crashing(), Raising(), world.tasks, world.golden, claim_tool="submit")
    ep = target.run("close_ticket", target.reference("close_ticket"), score=False)
    assert ep.verdict is Verdict.ERROR and ep.final_effect == ()
    assert "disk gone" in ep.scorer_detail


def test_incidental_calls_read_the_records_the_reference_names(target):
    assert target.incidental_calls("refund_approved", target.reference("refund_approved")) == [
        Call("get_order", {"order_id": "o2"}, CallKind.READ)
    ]
    assert target.incidental_calls("close_ticket", target.reference("close_ticket")) == []  # no ticket reader


def test_instruction_and_golden_validation(world, target):
    assert target.instruction("close_ticket") == "Close ticket t1."
    with pytest.raises(KeyError):
        ExecutorAuditTarget(world.executor, Raising(), {}, world.golden)
