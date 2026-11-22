"""Loophole families on the toy world: candidates, refusals, determinism, and how OpenAdapt's runner scores them."""

import json
from dataclasses import replace

import pytest
from openadapt_flow.verifier_audit import DEFAULT_FAMILIES, AuditConfig, Call, CallKind, run_audit
from openadapt_flow.verifier_audit.families import FamilyContext, FaultFamily, Polarity, Refusal, load_families
from openadapt_flow.verifier_audit.families import wrong_value as openadapt_wrong_value
from openadapt_flow.verifier_audit.model import effect_diff
from wf_audit import LOOPHOLE_IDS, family_preset, loophole_families, sweep, toy_world
from wf_audit.cli import main
from wf_audit.families import (
    build_reorder_independent,
    build_wrong_constrained_value,
    build_wrong_order,
    reorder_independent,
    wrong_constrained_value,
    wrong_order,
)
from wf_audit.testing.toy_verifiers import ExactTrajectory, StrictToyVerifier
from wf_audit.worlds import TOY_FREE_TEXT_FIELDS, TOY_ORDER_CONSTRAINTS
from wf_core.testing.toyworld import ToyExecutor

TASKS = ("refund_email", "close_ticket", "refund_approved")
APPROVAL_FIRST = TOY_ORDER_CONSTRAINTS["refund_approved"]


@pytest.fixture(scope="module")
def world():
    return toy_world()


@pytest.fixture(scope="module")
def target(world):
    return world.target(StrictToyVerifier(world.executor, world.golden, world.claim_tool))


def ctx(target, task):
    return FamilyContext(task=task, reference=target.reference(task))


def W(name, **args):
    return Call(name, args, CallKind.WRITE)


def R(name, **args):
    return Call(name, args, CallKind.READ)


def names(calls):
    return [c.name for c in calls]


def audit(world, verifier, *families):
    return {c["task"]: c for c in run_audit(world.target(verifier), AuditConfig(families=families))["cells"]}


class ApprovalFirst(StrictToyVerifier):
    """Stand-in that also checks the declared order on refund_approved."""

    id = "approval_first"

    def process_ok(self, episode):
        made = [c.tool for c in episode.trajectory.calls if c.ok]
        refund = made.index("create_refund") if "create_refund" in made else None
        if episode.task.id == "refund_approved" and refund is not None and "request_approval" not in made[:refund]:
            return False
        return super().process_ok(episode)


class NoAuditTrail(ToyExecutor):
    """The toy world without its audit table, so nothing in the end state records call order."""

    def run(self, task, calls, final_answer=None):
        episode = super().run(task, calls, final_answer)
        final = episode.final.model_copy(update={"tables": {**episode.final.tables, "audit_log": {}}})
        return episode.model_copy(update={"final": final})


class RefusesUnapprovedRefunds(ToyExecutor):
    """A world that enforces the policy itself: a refund before any approval is refused."""

    def run(self, task, calls, final_answer=None):
        approved, sent = False, []
        for call in calls:
            approved |= call.tool == "request_approval"
            if call.tool == "create_refund" and not approved:
                call = call.model_copy(update={"args": {**call.args, "amount_cents": -1}})  # the toy tool refuses it
            sent.append(call)
        return super().run(task, sent, final_answer)


# ---------------------------------------------------------------- wrong_order


def test_wrong_order_moves_b_before_a_and_keeps_everything_else(target):
    (cand,) = build_wrong_order(ctx(target, "refund_approved"), APPROVAL_FIRST)
    ref = target.reference("refund_approved")
    assert names(cand.calls) == ["create_refund", "request_approval", "submit"]
    assert sorted(cand.calls, key=lambda c: c.name) == sorted(ref, key=lambda c: c.name)  # same calls, same args
    assert cand.must_succeed == (0, 1) and not cand.ordering_only
    assert "request_approval before create_refund" in cand.description


def test_wrong_order_refusals(target):
    assert build_wrong_order(ctx(target, "refund_email"), ()) == Refusal("NO_ORDER_CONSTRAINT")
    absent = build_wrong_order(ctx(target, "refund_email"), APPROVAL_FIRST)  # no approval in this reference
    assert absent == Refusal("CONSTRAINED_TOOLS_NOT_IN_REFERENCE")
    # B already runs before A: there is nothing to move, so no candidate
    flipped = [("create_refund", "request_approval")]
    assert build_wrong_order(ctx(target, "refund_approved"), flipped) == Refusal("CONSTRAINED_TOOLS_NOT_IN_REFERENCE")


def test_wrong_order_moves_each_b_before_the_first_a_newest_first():
    ref = [W("approve", order_id="o1"), W("refund", order_id="o1"), R("get", order_id="o1"), W("refund", order_id="o2")]
    built = build_wrong_order(FamilyContext(task="t", reference=ref), [("approve", "refund")])
    assert [names(c.calls) for c in built] == [
        ["refund", "approve", "refund", "get"],  # call 3 moved first (newest)
        ["refund", "approve", "get", "refund"],
    ]
    assert [c.calls[0].args["order_id"] for c in built] == ["o2", "o1"]


def test_wrong_order_is_visible_only_through_sequenced_audit_rows(target):
    (cand,) = build_wrong_order(ctx(target, "refund_approved"), APPROVAL_FIRST)
    base = target.run("refund_approved", target.reference("refund_approved"), score=False)
    moved = target.run("refund_approved", cand.calls, score=False)
    rows = [json.loads(r) for side in effect_diff(base.final_effect, moved.final_effect).values() for r in side]
    assert rows and {r["table"] for r in rows} == {"audit_log"}  # a1/a2 swap their actions; nothing else changes


def test_wrong_order_through_the_runner(world):
    family = wrong_order(world.order_constraints)
    blind = audit(world, StrictToyVerifier(world.executor, world.golden, world.claim_tool), family)
    assert blind["refund_approved"]["status"] == "FALSE_ACCEPT"  # the stand-in strict checks rows, not order
    assert blind["refund_email"]["reason"] == blind["close_ticket"]["reason"] == "NO_ORDER_CONSTRAINT"
    aware = audit(world, ApprovalFirst(world.executor, world.golden, world.claim_tool), family)
    assert aware["refund_approved"]["status"] == "CAUGHT"


def test_wrong_order_is_degenerate_when_the_world_does_not_record_order():
    world = replace(toy_world(), executor=NoAuditTrail())
    verifier = ApprovalFirst(world.executor, world.golden, world.claim_tool)
    cell = audit(world, verifier, wrong_order(world.order_constraints))["refund_approved"]
    assert cell["status"] == "DEGENERATE"  # honest "cannot see order", not a fake catch
    assert cell["census_attempts"][0]["effect_differs"] is False


def test_wrong_order_is_not_the_fault_when_the_world_enforces_the_order():
    world = replace(toy_world(), executor=RefusesUnapprovedRefunds())
    verifier = StrictToyVerifier(world.executor, world.golden, world.claim_tool)
    cell = audit(world, verifier, wrong_order(world.order_constraints))["refund_approved"]
    assert cell["status"] == "CANDIDATES_EXHAUSTED"
    assert cell["census_attempts"][0]["edited_call_accepted"] is False


# ---------------------------------------------------------------- reorder_independent


def test_reorder_independent_swaps_adjacent_independent_writes(target):
    (cand,) = build_reorder_independent(ctx(target, "refund_email"), ())
    assert names(cand.calls) == ["send_email", "create_refund", "submit"]
    assert cand.must_succeed == (0, 1)


def test_reorder_independent_refusals(target):
    assert build_reorder_independent(ctx(target, "close_ticket"), ()) == Refusal("FEWER_THAN_TWO_WRITES")
    blocked = Refusal("NO_INDEPENDENT_WRITE_PAIR")
    assert build_reorder_independent(ctx(target, "refund_approved"), APPROVAL_FIRST) == blocked
    # the shared identity (order o2) alone blocks the swap, even with no declared order
    assert build_reorder_independent(ctx(target, "refund_approved"), ()) == blocked
    # a declared order alone blocks it too, whichever way round it is declared
    for pair in [("create_refund", "send_email"), ("send_email", "create_refund")]:
        assert build_reorder_independent(ctx(target, "refund_email"), [pair]) == blocked


def test_reorder_independent_is_conservative():
    def built(*calls):
        return build_reorder_independent(FamilyContext(task="t", reference=list(calls)), ())

    assert built(W("a", order_id="o1"), R("get", x_id="z"), W("b", ticket_id="t1")) == Refusal(
        "NO_INDEPENDENT_WRITE_PAIR"
    )  # not adjacent: a read sits between them
    assert built(W("a", order_id="o1"), W("broadcast", text="hello")) == Refusal("NO_INDEPENDENT_WRITE_PAIR")
    assert built(W("a", order_id="o1"), W("b", customer_id="o1")) == Refusal("NO_INDEPENDENT_WRITE_PAIR")
    (cand,) = built(W("a", order_id="o1"), W("b", ticket_id="t1"))
    assert names(cand.calls) == ["b", "a"]


def test_reorder_independent_through_the_runner(world):
    family = reorder_independent(world.order_constraints)
    good = audit(world, StrictToyVerifier(world.executor, world.golden, world.claim_tool), family)
    assert good["refund_email"]["status"] == "ACCEPTED"
    assert good["refund_email"]["effect_changed"] is False  # the same end state: a valid alternative
    assert good["close_ticket"]["reason"] == "FEWER_THAN_TWO_WRITES"
    assert good["refund_approved"]["reason"] == "NO_INDEPENDENT_WRITE_PAIR"
    picky = audit(world, ExactTrajectory(world.executor, world.golden, world.claim_tool), family)
    assert picky["refund_email"]["status"] == "FALSE_REJECT"


# ---------------------------------------------------------------- wrong_constrained_value


def test_wrong_constrained_value_skips_free_text(target):
    built = build_wrong_constrained_value(ctx(target, "refund_email"), TOY_FREE_TEXT_FIELDS)
    assert [c.description for c in built] == [
        "call 0 (create_refund) writes amount_cents=5001 instead of 5000",
        "call 0 (create_refund) writes amount_cents=4999 instead of 5000",
    ]
    assert all(c.calls[1] == target.reference("refund_email")[1] for c in built)  # the email is never touched
    assert {c.must_succeed for c in built} == {(0,)}
    (status,) = build_wrong_constrained_value(ctx(target, "close_ticket"), TOY_FREE_TEXT_FIELDS)
    assert status.calls[0].args == {"ticket_id": "t1", "status": "closedx"}


def test_wrong_constrained_value_matches_openadapt_without_free_text(target):
    for task in TASKS:
        theirs = openadapt_wrong_value(ctx(target, task))
        ours = build_wrong_constrained_value(ctx(target, task), {})
        first_per_call = {c.must_succeed: c.calls for c in reversed(ours)}  # +1 comes before -1
        assert {c.must_succeed: c.calls for c in theirs} == first_per_call


def test_wrong_constrained_value_refuses_without_a_constrained_field():
    ref = [W("request_approval", order_id="o2"), W("send_email", customer_id="c1", subject="Refund confirmed")]
    built = build_wrong_constrained_value(FamilyContext(task="t", reference=ref), TOY_FREE_TEXT_FIELDS)
    assert built == Refusal("NO_CONSTRAINED_FIELD")


def test_wrong_constrained_value_falls_back_to_minus_one_when_plus_one_is_refused(world):
    family = wrong_constrained_value(world.free_text_fields)
    cells = audit(world, StrictToyVerifier(world.executor, world.golden, world.claim_tool), family)
    for task, amount in [("refund_email", 4999), ("refund_approved", 11999)]:
        attempts = cells[task]["census_attempts"]
        assert [a["edited_call_accepted"] for a in attempts] == [False, True]  # +1 exceeds the order total
        assert cells[task]["status"] == "CAUGHT" and f"amount_cents={amount}" in cells[task]["edit"]


# ---------------------------------------------------------------- determinism, presets, plug-in contract


def test_builds_are_pure_and_deterministic(world, target):
    for family in loophole_families(world):
        for task in TASKS:
            assert family.build(ctx(target, task)) == family.build(ctx(target, task))


def test_presets(world):
    assert family_preset("default", world) == tuple(DEFAULT_FAMILIES)
    extended = family_preset("extended", world)
    assert [f.id for f in extended] == [f.id for f in DEFAULT_FAMILIES] + list(LOOPHOLE_IDS)
    assert [f.polarity for f in extended[-3:]] == [Polarity.HARMFUL, Polarity.HARMFUL, Polarity.LEGITIMATE]
    with pytest.raises(ValueError, match="preset"):
        family_preset("everything", world)


def test_families_module_contract():
    families = load_families("wf_audit.testing.toy_families")  # what --families-module does
    assert all(isinstance(f, FaultFamily) for f in families)
    assert tuple(f.id for f in families) == LOOPHOLE_IDS


def test_declarations_are_validated(world):
    verifier = StrictToyVerifier(world.executor, world.golden, world.claim_tool)
    with pytest.raises(KeyError, match="unknown task"):
        replace(world, order_constraints={"nope": [("request_approval", "create_refund")]}).target(verifier)
    for bad in [("request_approval", "refund_typo"), ("create_refund", "create_refund")]:
        with pytest.raises(ValueError, match="distinct known tools"):
            replace(world, order_constraints={"refund_approved": [bad]}).target(verifier)
    with pytest.raises(ValueError, match="free-text"):
        replace(world, free_text_fields={"send_email": {"subjet"}}).target(verifier)
    target = world.target(verifier)
    assert target.order_constraints == {"refund_approved": (("request_approval", "create_refund"),)}
    assert target.free_text_fields == {"send_email": frozenset({"subject"})}


def test_sweep_preset_records_declarations_only_when_used(world, tmp_path):
    only = {"strict": StrictToyVerifier(world.executor, world.golden, world.claim_tool)}
    plain = json.loads((sweep(only, world, tmp_path / "d") / "run_card.json").read_text())
    assert "declarations" not in plain  # the default run card is unchanged
    out = sweep(only, world, tmp_path / "x", families="extended")
    card = json.loads((out / "run_card.json").read_text())
    assert card["families"][-3:] == list(LOOPHOLE_IDS)
    assert card["declarations"] == {
        "order_constraints": {"refund_approved": [["request_approval", "create_refund"]]},
        "free_text_fields": {"send_email": ["subject"]},
    }
    again = sweep(only, world, tmp_path / "y", families="extended")
    for name in ("scorecard.json", "SCORECARD.md", "run_card.json"):
        assert (again / name).read_text() == (out / name).read_text()
    with pytest.raises(ValueError, match="duplicate family ids"):
        sweep(only, world, tmp_path / "z", families=family_preset("extended", world) + loophole_families(world))


def test_cli_preset(tmp_path, capsys):
    out = tmp_path / "audit"
    assert main(["sweep", "--world", "toy", "--out", str(out), "--only", "strict", "--preset", "extended", "-q"]) == 0
    printed = capsys.readouterr().out
    assert "wrong_order (FA)" in printed and "reorder_independent (FR)" in printed
