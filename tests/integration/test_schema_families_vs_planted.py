"""The schema families against planted rs-verify graders, on the toy fixture with one more ticket tool
(`rs_audit.testing.toy_fields`: `update_ticket(ticket_id, priority, status)`, priority optional).

1. `field_coverage` detects a planted status-only grader whatever the argument order. OpenAdapt's `wrong_value`
   catches it or misses it depending on which field the golden call names first.
2. `extra_field` detects a grader that ignores extra fields on an updated row. No planted rs-verify flag has that
   blind spot (every flag but trust_claim fails a matched update that changed a field it did not expect), so the
   grader here is test-local: the strict spec, plus an allow rule for any field of the expected row.

Both use the `full` preset as the planted toy audit does, with episodes that end at the claim.
"""

from dataclasses import replace

import pytest
from openadapt_flow.verifier_audit import AuditConfig, run_audit
from rs_audit import family_preset
from rs_audit.testing.toy_fields import fields_world
from rs_core.models import Episode, Verdict
from rs_core.testing.toyworld import SCHEMA
from rs_verify import AllowRule, ExpectedChange, Leniency, SpecVerifier, StateSpec, VerifierSpec, eq, lenient_verifier

TICKET = (eq("/id", "t2"),)
SPECS = {
    "triage_ticket": VerifierSpec(
        id="fields.triage_ticket.strict",
        task_id="triage_ticket",
        state=StateSpec(
            expected=(
                ExpectedChange(
                    table="tickets",
                    kind="update",
                    where=TICKET,
                    expect=(eq("/status", "pending"), eq("/priority", "high")),
                ),
            )
        ),
    ),
    "resolve_ticket": VerifierSpec(
        id="fields.resolve_ticket.strict",
        task_id="resolve_ticket",
        state=StateSpec(
            expected=(
                ExpectedChange(table="tickets", kind="update", where=TICKET, expect=(eq("/status", "resolved"),)),
            )
        ),
    ),
}


class ByTask:
    """One variant over both tasks: each episode goes to its own task's verifier."""

    def __init__(self, name, verifiers):
        self.id = name
        self.by_task = verifiers

    def verify(self, episode: Episode) -> Verdict:
        return self.by_task[episode.task.id].verify(episode)


def planted(*flags):
    return ByTask("+".join(flags) or "strict", {t: lenient_verifier(s, SCHEMA, *flags) for t, s in SPECS.items()})


def ignores_extra_fields():
    """Test-local blind spot: the expected row is still located and its expected values checked, but any other
    field of it may change too."""
    loose = AllowRule(table="tickets", kind="update", where=TICKET)
    verifiers = {}
    for t, s in SPECS.items():
        assert s.state is not None
        state = s.state.model_copy(update={"allow": (loose,)})
        verifiers[t] = SpecVerifier(s.model_copy(update={"state": state}), SCHEMA)
    return ByTask("ignores_extra_fields", verifiers)


def statuses(world, verifier):
    report = run_audit(world.target(verifier, verifier.id), AuditConfig(families=family_preset("full", world)))
    assert report["tasks_excluded"] == 0
    return {(c["task"], c["family"]): c["status"] for c in report["cells"]}


@pytest.mark.parametrize("status_first", [False, True], ids=["priority_first", "status_first"])
def test_field_coverage_detects_status_only_whatever_the_argument_order(status_first):
    world = replace(fields_world(status_first), post_claim_steps=False)
    strict = statuses(world, planted())
    lenient = statuses(world, planted(Leniency.STATUS_ONLY))
    assert "FALSE_ACCEPT" not in strict.values()
    assert lenient[("triage_ticket", "field_coverage:update_ticket.priority")] == "FALSE_ACCEPT"
    assert lenient[("triage_ticket", "field_coverage:update_ticket.status")] == "CAUGHT"
    # OpenAdapt's wrong_value (and wrong_constrained_value, its free-text-safe copy) edits the first string field
    # only: the planted blind spot shows up only when priority comes first
    blind = "CAUGHT" if status_first else "FALSE_ACCEPT"
    assert lenient[("triage_ticket", "wrong_value")] == lenient[("triage_ticket", "wrong_constrained_value")] == blind


def test_extra_field_detects_a_grader_that_ignores_extra_fields():
    world = replace(fields_world(), post_claim_steps=False)
    slot = ("resolve_ticket", "extra_field:update_ticket.priority")
    strict = statuses(world, planted())
    assert strict[slot] == "CAUGHT" and "FALSE_ACCEPT" not in strict.values()
    assert statuses(world, ignores_extra_fields())[slot] == "FALSE_ACCEPT"
    # no planted flag has this blind spot; trust_claim passes anything that claims success
    for flag in Leniency:
        assert statuses(world, planted(flag))[slot] == ("FALSE_ACCEPT" if flag is Leniency.TRUST_CLAIM else "CAUGHT")
