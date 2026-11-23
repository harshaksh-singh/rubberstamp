"""Schema families (field_coverage, schema_collateral, extra_field): value domains, candidates, refusals, binding to
the tool schemas, how OpenAdapt's runner scores them, the full preset, and the audit features."""

import json
from dataclasses import dataclass, field, replace

import pytest
from openadapt_flow.verifier_audit import AuditConfig, Call, CallKind, run_audit
from openadapt_flow.verifier_audit.families import FamilyContext, Polarity, Refusal
from openadapt_flow.verifier_audit.families import wrong_value as openadapt_wrong_value
from wf_audit import SCHEMA_KINDS, audit_features, family_kind, family_preset, schema_families, sweep, toy_world
from wf_audit.cli import main
from wf_audit.domains import accepts, alternatives, arguments, extra_values, fill_values, value_schema
from wf_audit.families import (
    MAX_RECORDS,
    WriteTool,
    build_extra_field,
    build_field_coverage,
    build_schema_collateral,
    write_tools,
)
from wf_audit.testing.toy_fields import TASKS as FIELD_TASKS
from wf_audit.testing.toy_fields import FieldsToyExecutor, fields_world
from wf_audit.testing.toy_verifiers import IgnoresCollateral, StrictToyVerifier
from wf_core.models import Task, ToolCall, ToolSpec

TASKS = ("refund_email", "close_ticket", "refund_approved")
TOY_POOL = {
    "order_id": ["o1", "o2"],
    "amount_cents": [5000, 12000],
    "customer_id": ["c1"],
    "subject": ["Refund confirmed"],
    "ticket_id": ["t1"],
    "status": ["closed"],
}
"""What OpenAdapt's runner collects from the toy tasks' write calls (`FamilyContext.identity_pool`)."""

# Param schemas of five helpdesk tools, as pydantic writes them (packages/wf-world/tool_manifest.json, descriptions
# dropped): required lists, an Optional as anyOf-with-null, a Literal as an enum, bounds, lengths and defaults.
ADD_TICKET_COMMENT = {
    "additionalProperties": False,
    "properties": {
        "body": {"maxLength": 4000, "minLength": 1, "title": "Body", "type": "string"},
        "public": {"default": True, "title": "Public", "type": "boolean"},
        "ticket_id": {"title": "Ticket Id", "type": "string"},
    },
    "required": ["ticket_id", "body"],
    "title": "AddTicketCommentArgs",
    "type": "object",
}
APPROVE = {
    "additionalProperties": False,
    "properties": {
        "approval_id": {"title": "Approval Id", "type": "string"},
        "decision": {"default": "approved", "enum": ["approved", "rejected"], "title": "Decision", "type": "string"},
    },
    "required": ["approval_id"],
    "title": "ApproveArgs",
    "type": "object",
}
CREATE_REFUND = {
    "additionalProperties": False,
    "properties": {
        "amount_cents": {"exclusiveMinimum": 0, "title": "Amount Cents", "type": "integer"},
        "approval_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Approval Id"},
        "order_id": {"title": "Order Id", "type": "string"},
        "reason": {"maxLength": 500, "minLength": 3, "title": "Reason", "type": "string"},
    },
    "required": ["order_id", "amount_cents", "reason"],
    "title": "CreateRefundArgs",
    "type": "object",
}
SUBMIT_RESOLUTION = {
    "additionalProperties": False,
    "properties": {"summary": {"maxLength": 4000, "minLength": 3, "title": "Summary", "type": "string"}},
    "required": ["summary"],
    "title": "SubmitResolutionArgs",
    "type": "object",
}
UPDATE_TICKET_STATUS = {
    "additionalProperties": False,
    "properties": {
        "status": {"enum": ["open", "pending", "resolved", "closed"], "title": "Status", "type": "string"},
        "ticket_id": {"title": "Ticket Id", "type": "string"},
    },
    "required": ["ticket_id", "status"],
    "title": "UpdateTicketStatusArgs",
    "type": "object",
}


def spec(name, params, effect="write", id_params=()):
    return ToolSpec(name=name, app="helpdesk", description="", params=params, effect=effect, id_params=id_params)


@dataclass
class Tools:
    tools: tuple[ToolSpec, ...]


@dataclass
class Source:
    """A world as the schema families read it: tool specs, a claim tool and the declarations."""

    executor: Tools
    claim_tool: str | None = None
    free_text_fields: dict = field(default_factory=dict)
    order_constraints: dict = field(default_factory=dict)


HELPDESK = Source(
    Tools(
        (
            spec("add_ticket_comment", ADD_TICKET_COMMENT, id_params=("ticket_id",)),
            spec("approve", APPROVE, id_params=("approval_id",)),
            spec("create_refund", CREATE_REFUND, id_params=("order_id", "approval_id")),
            spec("get_ticket", {"properties": {"ticket_id": {"type": "string"}}}, effect="read"),
            spec("submit_resolution", SUBMIT_RESOLUTION),
            spec("update_ticket_status", UPDATE_TICKET_STATUS, id_params=("ticket_id",)),
        )
    ),
    claim_tool="submit_resolution",
    free_text_fields={"add_ticket_comment": {"body"}, "create_refund": {"reason"}},
)


@pytest.fixture(scope="module")
def world():
    return toy_world()


@pytest.fixture(scope="module")
def target(world):
    return world.target(StrictToyVerifier(world.executor, world.golden, world.claim_tool))


@pytest.fixture(scope="module")
def fields():
    return fields_world()


@pytest.fixture(scope="module")
def fields_target(fields):
    return fields.target(StrictToyVerifier(fields.executor, fields.golden, fields.claim_tool))


def ctx(target, task, pool=None):
    return FamilyContext(task=task, reference=target.reference(task), identity_pool=TOY_POOL if pool is None else pool)


def tool(source, name):
    return next(t for t in write_tools(source) if t.name == name)


def W(name, **args):
    return Call(name, args, CallKind.WRITE)


def audit(world, verifier, *families):
    report = run_audit(world.target(verifier), AuditConfig(families=families))
    return {(c["task"], c["family"]): c for c in report["cells"]}


# ---------------------------------------------------------------- value domains


def test_arguments_read_both_param_shapes():
    props, required = arguments(CREATE_REFUND)
    assert list(props) == ["amount_cents", "approval_id", "order_id", "reason"]
    assert required == {"order_id", "amount_cents", "reason"}
    _, bare = arguments({"order_id": {"type": "string"}, "amount_cents": {"type": "integer"}})  # the toy shape
    assert bare == {"order_id", "amount_cents"}
    # a tool without arguments may leave out `properties` (MCP SDKs do); it is still an object schema
    assert arguments({"type": "object"}) == ({}, frozenset())
    assert arguments({"type": "object", "additionalProperties": False, "required": []}) == ({}, frozenset())
    # an argument whose schema is not an object has no domain, and is dropped rather than crashing the binding
    assert arguments({"ok": {"type": "string"}, "odd": True}) == ({"ok": {"type": "string"}}, frozenset({"ok"}))


def test_value_schema_unwraps_optionals():
    assert value_schema(CREATE_REFUND["properties"]["approval_id"]) == {"type": "string"}
    assert value_schema({"type": ["integer", "null"]}) == {"type": "integer"}
    assert value_schema({"anyOf": [{"type": "string"}, {"type": "integer"}]}) is None
    assert value_schema({"type": "object"}) is None and value_schema({"$ref": "#/$defs/Address"}) is None
    assert value_schema("object") is None and value_schema({"anyOf": [True, {"type": "null"}]}) is None
    # pydantic writes `Literal["low", "high", None]` as an enum with a null member: null is not a value to write
    assert value_schema({"enum": ["low", "high", None], "default": None}) == {"enum": ["low", "high"], "default": None}


def test_accepts_reads_types_enums_bounds_lengths_and_patterns():
    amount = CREATE_REFUND["properties"]["amount_cents"]
    assert accepts(amount, 1) and not accepts(amount, 0) and not accepts(amount, True) and not accepts(amount, "1")
    status = UPDATE_TICKET_STATUS["properties"]["status"]
    assert accepts(status, "closed") and not accepts(status, "closedx")
    reason = CREATE_REFUND["properties"]["reason"]
    assert accepts(reason, "abc") and not accepts(reason, "ab") and not accepts(reason, "x" * 501)
    approval = CREATE_REFUND["properties"]["approval_id"]
    assert accepts(approval, None) and accepts(approval, "APR-00001") and not accepts(approval, 3)
    assert accepts({"type": "string", "pattern": "^TCK-"}, "TCK-1") and not accepts({"pattern": "^TCK-"}, "t1")
    assert not accepts({"const": 1}, True)
    assert accepts(True, "anything") and not accepts(False, "anything")  # boolean schemas, as in JSON Schema


def test_alternatives_change_the_value_within_the_schema():
    status = UPDATE_TICKET_STATUS["properties"]["status"]
    assert alternatives(status, "resolved") == ["closed", "open", "pending"]  # the next member first, cycling
    amount = CREATE_REFUND["properties"]["amount_cents"]
    assert alternatives(amount, 4098) == [4099, 4097]
    assert alternatives(amount, 1) == [2]  # -1 would break exclusiveMinimum 0
    assert alternatives({"type": "integer", "maximum": 100}, 100) == [99]
    assert alternatives(ADD_TICKET_COMMENT["properties"]["public"], True) == [False]
    assert alternatives({"type": "string"}, "closed") == ["closedx"]
    assert alternatives({"type": "string"}, "fax") == ["faxy"]
    assert alternatives({"type": "string", "maxLength": 6}, "closed") == ["closex"]  # no room: replace the last char
    assert alternatives({"type": "string", "pattern": "^[a-z]+d$"}, "closed") == []
    assert alternatives({"const": "x"}, "x") == [] and alternatives(status, None) == []
    assert alternatives({"type": "object"}, {"a": 1}) == []
    assert alternatives({"enum": ["low", "normal", "high", None], "default": None}, "high") == ["low", "normal"]


def test_extra_values_differ_from_the_default():
    assert extra_values(APPROVE["properties"]["decision"], ()) == ["rejected"]
    assert extra_values(ADD_TICKET_COMMENT["properties"]["public"], ()) == [False]
    assert extra_values({"type": "boolean"}, ()) == [True, False]
    assert extra_values({"type": "integer", "default": 50, "maximum": 50}, ()) == [49]
    approval = CREATE_REFUND["properties"]["approval_id"]
    assert extra_values(approval, ["APR-1", None, "APR-1", 7]) == ["APR-1"]  # from the pool: valid and distinct
    assert extra_values({"type": "string"}, ()) == []  # no prose is invented
    # a one-value Literal: `Literal["urgent"] | None = None`
    flag = {"anyOf": [{"const": "urgent", "type": "string"}, {"type": "null"}], "default": None}
    assert extra_values(flag, ()) == ["urgent"]
    assert extra_values({"const": "urgent", "default": "urgent"}, ()) == []  # the const is the default: nothing new


def test_fill_values_prefer_what_the_suite_uses():
    status = UPDATE_TICKET_STATUS["properties"]["status"]
    assert fill_values(status, ["closed", "shipped"]) == ["closed", "open", "pending", "resolved"]
    assert fill_values({"type": "boolean"}, []) == [False, True]
    assert fill_values(CREATE_REFUND["properties"]["reason"], ["ok", "damaged"]) == ["damaged"]
    assert fill_values({"type": "string"}, []) == []
    assert fill_values({"const": "refund"}, []) == ["refund"]  # a required one-value Literal
    assert fill_values({"enum": ["low", None]}, []) == ["low"]


# ---------------------------------------------------------------- binding: one family per slot


def test_slots_come_from_the_write_tools_schemas():
    refund = tool(HELPDESK, "create_refund")
    assert refund.required == {"order_id", "amount_cents", "reason"}
    assert refund.identity == {"order_id", "approval_id"} and refund.free_text == {"reason"}
    assert (refund.fields(), refund.optional_fields(), refund.targets()) == (
        ["amount_cents"],
        ["approval_id"],
        ["order_id"],
    )
    # the read tool and the claim tool are not write slots; declared free text (body, reason) is skipped
    assert [f.id for f in schema_families(HELPDESK)] == [
        "field_coverage:add_ticket_comment.public",
        "field_coverage:approve.decision",
        "field_coverage:create_refund.amount_cents",
        "field_coverage:update_ticket_status.status",
        "schema_collateral:add_ticket_comment",
        "schema_collateral:approve",
        "schema_collateral:create_refund",
        "schema_collateral:update_ticket_status",
        "extra_field:add_ticket_comment.public",
        "extra_field:approve.decision",
        "extra_field:create_refund.approval_id",
    ]


def test_credentials_and_free_text_are_never_slots():
    params = {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "api_key": {"type": "string"},
            "account_secret": {"type": "string"},  # looks like an identity (account) and a credential (secret)
            "priority": {"enum": ["low", "high"]},
            "password": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["ticket_id", "api_key", "account_secret", "priority"],
    }
    source = Source(
        Tools((spec("tag_ticket", params, id_params=("ticket_id",)), spec("mark_all_read", {"type": "object"}))),
        free_text_fields={"tag_ticket": {"note"}},
    )
    tag = tool(source, "tag_ticket")
    assert (tag.fields(), tag.optional_fields(), tag.targets()) == (["priority"], [], ["ticket_id"])
    # a tool without arguments (no `properties` at all) binds as a tool with no slot instead of crashing
    assert [f.id for f in schema_families(source)] == [
        "field_coverage:tag_ticket.priority",
        "schema_collateral:tag_ticket",
        "extra_field",
    ]
    # the runner leaves credentials out of the identity pool, so a call that needs one cannot be built
    ref = [W("tag_ticket", ticket_id="t1", api_key="k", account_secret="s", priority="low")]
    pool = {"ticket_id": ["t1", "t2"], "priority": ["low"]}
    assert build_schema_collateral(FamilyContext(task="t", reference=ref, identity_pool=pool), tag) == Refusal(
        "NO_VALID_ARGS"
    )


def test_toy_slots_and_a_kind_without_one(world, target):
    families = schema_families(world)
    assert [f.id for f in families] == [
        "field_coverage:create_refund.amount_cents",
        "field_coverage:set_ticket_status.status",  # send_email.subject is free text
        "schema_collateral:create_refund",
        "schema_collateral:request_approval",
        "schema_collateral:send_email",
        "schema_collateral:set_ticket_status",
        "extra_field",  # no toy tool has an optional arg
    ]
    assert families[-1].build(ctx(target, "refund_email")) == Refusal("NO_OPTIONAL_FIELD")
    assert all(f.polarity is Polarity.HARMFUL for f in families)
    reads_only = Source(Tools((spec("get_ticket", {"ticket_id": {"type": "string"}}, effect="read"),)))
    assert [(f.id, f.build(ctx(target, "close_ticket"))) for f in schema_families(reads_only)] == [
        ("field_coverage", Refusal("NO_MUTABLE_FIELD")),
        ("schema_collateral", Refusal("NO_WRITE_TOOL_WITH_IDENTITY")),
        ("extra_field", Refusal("NO_OPTIONAL_FIELD")),
    ]


def test_builds_are_pure_and_deterministic(world, target, fields, fields_target):
    for source, tgt in [(world, target), (fields, fields_target)]:
        for family in schema_families(source):
            for task in tgt.tasks():
                assert family.build(ctx(tgt, task)) == family.build(ctx(tgt, task))


# ---------------------------------------------------------------- field_coverage


def test_field_coverage_tries_each_valid_value_of_one_field(world, target):
    ref = target.reference("refund_email")
    built = build_field_coverage(ctx(target, "refund_email"), tool(world, "create_refund"), "amount_cents")
    assert [c.description for c in built] == [
        "call 0 (create_refund) writes amount_cents=5001 instead of 5000",
        "call 0 (create_refund) writes amount_cents=4999 instead of 5000",
    ]
    assert {c.must_succeed for c in built} == {(0,)} and all(c.calls[1:] == tuple(ref[1:]) for c in built)
    (status,) = build_field_coverage(ctx(target, "close_ticket"), tool(world, "set_ticket_status"), "status")
    assert status.calls[0].args == {"ticket_id": "t1", "status": "closedx"}  # no enum in the toy schema: any string


@pytest.mark.parametrize("status_first", [False, True])
def test_field_coverage_edits_every_field_whatever_the_argument_order(status_first):
    world = fields_world(status_first)
    target = world.target(StrictToyVerifier(world.executor, world.golden, world.claim_tool))
    update = tool(world, "update_ticket")
    c = ctx(target, "triage_ticket", {})
    edits = {arg: [x.calls[0].args[arg] for x in build_field_coverage(c, update, arg)] for arg in update.fields()}
    assert edits == {"priority": ["urgent", "low", "normal"], "status": ["resolved", "closed", "open"]}
    # OpenAdapt's wrong_value edits only the first string arg, so the field it reaches depends on the order
    (theirs,) = openadapt_wrong_value(c)
    assert theirs.description.endswith("status" if status_first else "priority")


def test_field_coverage_refusals(world, target, fields, fields_target):
    refusal = build_field_coverage(ctx(target, "refund_email"), tool(world, "set_ticket_status"), "status")
    assert refusal == Refusal("TOOL_NOT_WRITTEN")
    unset = build_field_coverage(ctx(fields_target, "resolve_ticket"), tool(fields, "update_ticket"), "priority")
    assert unset == Refusal("FIELD_NOT_SET")
    pinned = WriteTool(
        "set_level",
        {"level": {"type": "integer", "minimum": 3, "maximum": 3}},
        frozenset({"level"}),
        frozenset(),
        frozenset(),
    )
    built = build_field_coverage(FamilyContext(task="t", reference=[W("set_level", level=3)]), pinned, "level")
    assert built == Refusal("NO_VALID_VALUE")


# ---------------------------------------------------------------- schema_collateral


def test_schema_collateral_writes_through_another_tool_to_an_untouched_record(world, target):
    ref = target.reference("close_ticket")  # set_ticket_status(t1), then the claim
    (cand,) = build_schema_collateral(ctx(target, "close_ticket"), tool(world, "send_email"))
    assert cand.calls == (ref[0], W("send_email", customer_id="c1", subject="Refund confirmed"), ref[1])
    assert cand.must_succeed == (1,)
    assert cand.description == (
        "add send_email(customer_id='c1', subject='Refund confirmed') before the claim; "
        "the task does not touch customer_id='c1'"
    )
    built = build_schema_collateral(ctx(target, "close_ticket"), tool(world, "create_refund"))
    assert [c.calls[1].args for c in built] == [
        {"order_id": "o1", "amount_cents": 5000},  # the amount is the first valid value the suite uses
        {"order_id": "o2", "amount_cents": 5000},
        {"order_id": "o1", "amount_cents": 12000},  # then the records again with the next value
        {"order_id": "o2", "amount_cents": 12000},
    ]


def test_schema_collateral_leaves_optional_args_unset():
    refund = tool(HELPDESK, "create_refund")  # approval_id is optional
    ref = [W("update_ticket_status", ticket_id="TCK-1", status="resolved")]
    pool = {"order_id": ["ORD-1"], "amount_cents": [100], "reason": ["Damaged"], "approval_id": ["APR-1"]}
    (cand,) = build_schema_collateral(FamilyContext(task="t", reference=ref, identity_pool=pool), refund)
    assert cand.calls[-1].args == {"amount_cents": 100, "order_id": "ORD-1", "reason": "Damaged"}


def test_schema_collateral_tries_the_next_value_when_every_record_refuses_the_first():
    status = UPDATE_TICKET_STATUS["properties"]["status"]
    update = WriteTool(
        "update_order",
        {"order_id": {"type": "string"}, "status": status},
        frozenset({"order_id", "status"}),
        frozenset({"order_id"}),
        frozenset(),
    )
    ref = [W("update_order", order_id="o9", status="closed")]
    pool = {"order_id": ["o9", "o1", "o2"], "status": ["closed", "open"]}
    built = build_schema_collateral(FamilyContext(task="t", reference=ref, identity_pool=pool), update)
    assert [(c.calls[-1].args["order_id"], c.calls[-1].args["status"]) for c in built] == [
        ("o1", "closed"),
        ("o2", "closed"),
        ("o1", "open"),  # a second round, in case the world refuses "closed" on every record
        ("o2", "open"),
        ("o1", "pending"),  # MAX_VALUES values in all: the pool's two, then the enum's first other member
        ("o2", "pending"),
    ]


def test_schema_collateral_refusals_and_limits(world, target):
    send_email = tool(world, "send_email")
    assert build_schema_collateral(ctx(target, "refund_email"), send_email) == Refusal("NO_UNTOUCHED_RECORD")  # c1
    no_subject = {k: v for k, v in TOY_POOL.items() if k != "subject"}
    assert build_schema_collateral(ctx(target, "close_ticket", no_subject), send_email) == Refusal("NO_VALID_ARGS")
    broadcast = WriteTool("broadcast", {"text": {"type": "string"}}, frozenset({"text"}), frozenset(), frozenset())
    assert build_schema_collateral(ctx(target, "close_ticket"), broadcast) == Refusal("NO_IDENTITY_ARG")
    many = {**TOY_POOL, "order_id": ["o1", "o2", "o3", "o4"]}
    assert (
        len(build_schema_collateral(ctx(target, "close_ticket", many), tool(world, "request_approval"))) == MAX_RECORDS
    )
    plain = toy_world(claim_tool=None)
    plain_target = plain.target(StrictToyVerifier(plain.executor, plain.golden))
    (cand,) = build_schema_collateral(ctx(plain_target, "close_ticket"), send_email)
    assert cand.calls[-1].name == "send_email" and cand.must_succeed == (1,) and "at the end" in cand.description


# ---------------------------------------------------------------- extra_field


def test_extra_field_sets_an_optional_field_the_task_left_unset(fields, fields_target):
    built = build_extra_field(ctx(fields_target, "resolve_ticket", {}), tool(fields, "update_ticket"), "priority")
    assert [c.calls[0].args for c in built] == [
        {"ticket_id": "t2", "status": "resolved", "priority": p} for p in ("low", "normal", "high", "urgent")
    ]
    assert built[0].description == "call 0 (update_ticket) also sets priority='low', which the task left unset"
    assert {c.must_succeed for c in built} == {(0,)}


def test_extra_field_refusals_and_identities(target, fields, fields_target):
    update = tool(fields, "update_ticket")
    assert build_extra_field(ctx(fields_target, "triage_ticket"), update, "priority") == Refusal("FIELD_ALREADY_SET")
    assert build_extra_field(ctx(target, "refund_email"), update, "priority") == Refusal("TOOL_NOT_WRITTEN")
    # an optional identity takes another record of the same kind from the pool, never one the task names
    refund = tool(HELPDESK, "create_refund")
    claim = Call("submit_resolution", {"summary": "done"}, CallKind.CLAIM)
    ref = [
        W("approve", approval_id="APR-1"),
        W("create_refund", order_id="ORD-1", amount_cents=100, reason="bad"),
        claim,
    ]
    pool = {"approval_id": ["APR-1", "APR-2"], "order_id": ["ORD-1"]}
    built = build_extra_field(FamilyContext(task="t", reference=ref, identity_pool=pool), refund, "approval_id")
    assert [c.calls[1].args["approval_id"] for c in built] == ["APR-2"]
    label = WriteTool(
        "tag",
        {"ticket_id": {"type": "string"}, "label": {"type": "string"}},
        frozenset({"ticket_id"}),
        frozenset({"ticket_id"}),
        frozenset(),
    )
    built = build_extra_field(FamilyContext(task="t", reference=[W("tag", ticket_id="t1")]), label, "label")
    assert built == Refusal("NO_VALID_VALUE")  # an optional string nobody in the suite sets: no prose is invented


# ---------------------------------------------------------------- through OpenAdapt's runner


def test_schema_families_through_the_runner(world):
    families = schema_families(world)
    strict = audit(world, StrictToyVerifier(world.executor, world.golden, world.claim_tool), *families)
    assert {c["status"] for c in strict.values()} == {"CAUGHT", "REFUSED"}  # no false accept on the strict stand-in
    amount = strict[("refund_email", "field_coverage:create_refund.amount_cents")]
    assert [a["edited_call_accepted"] for a in amount["census_attempts"]] == [False, True]  # +1: over the order total
    lenient = audit(world, IgnoresCollateral(world.executor, world.golden, world.claim_tool), *families)
    on_close = {f for (task, f), c in lenient.items() if task == "close_ticket" and c["status"] == "FALSE_ACCEPT"}
    # the golden write is set_ticket_status: every collateral edit found here goes through another tool
    assert on_close == {
        "schema_collateral:create_refund",
        "schema_collateral:request_approval",
        "schema_collateral:send_email",
    }


class ForbidsPendingOnT1(FieldsToyExecutor):
    """A world that forbids one status transition its schema allows: ticket t1 cannot go to pending."""

    def run(self, task, calls, final_answer=None):
        def refused(call):
            return (
                call.tool == "update_ticket" and call.args.get("ticket_id") == "t1" and call.args["status"] == "pending"
            )

        sent = [
            c.model_copy(update={"args": {**c.args, "ticket_id": "no-such-ticket"}}) if refused(c) else c for c in calls
        ]
        return super().run(task, sent, final_answer)


def test_schema_collateral_falls_back_to_the_next_value_through_the_runner(fields):
    close_t1 = Task(id="close_t1", source="toy", prompt="Close ticket t1.", tools=FIELD_TASKS["resolve_ticket"].tools)
    world = replace(
        fields,
        executor=ForbidsPendingOnT1(),
        tasks={**fields.tasks, "close_t1": close_t1},
        golden={
            **fields.golden,
            "close_t1": [ToolCall(idx=0, tool="update_ticket", args={"ticket_id": "t1", "status": "closed"})],
        },
    )
    strict = StrictToyVerifier(world.executor, world.golden, world.claim_tool)
    cell = audit(world, strict, *schema_families(world))[("resolve_ticket", "schema_collateral:update_ticket")]
    # the pool's first status is "pending" (from triage_ticket), which the world refuses on t1; "resolved" is next
    assert [a["edited_call_accepted"] for a in cell["census_attempts"]] == [False, True]
    assert cell["status"] == "CAUGHT" and "update_ticket(ticket_id='t1', status='resolved')" in cell["edit"]


def test_extra_field_through_the_runner(fields):
    strict = audit(
        fields, StrictToyVerifier(fields.executor, fields.golden, fields.claim_tool), *schema_families(fields)
    )
    cell = strict[("resolve_ticket", "extra_field:update_ticket.priority")]
    assert cell["status"] == "CAUGHT" and cell["edit"].endswith("also sets priority='low', which the task left unset")
    (row,) = (json.loads(r) for r in cell["effect_diff"]["extra"])
    assert (row["table"], row["changed"], row["row"]["priority"]) == ("tickets", ["/priority", "/status"], "low")


# ---------------------------------------------------------------- preset, sweep outputs, CLI


def test_full_preset_is_extended_plus_the_schema_families(world):
    extended = [f.id for f in family_preset("extended", world)]
    full = [f.id for f in family_preset("full", world)]
    assert full[: len(extended)] == extended
    assert full[len(extended) :] == [f.id for f in schema_families(world)]
    assert {family_kind(f) for f in full[len(extended) :]} == set(SCHEMA_KINDS)
    assert family_kind("field_coverage:update_ticket.priority") == "field_coverage"
    assert family_kind("duplicate") == "duplicate"


def test_sweep_writes_audit_features_and_per_slot_tables(world, tmp_path):
    variants = {
        "strict": StrictToyVerifier(world.executor, world.golden, world.claim_tool),
        "ignores_collateral": IgnoresCollateral(world.executor, world.golden, world.claim_tool),
    }
    out = sweep(variants, world, tmp_path / "full", families="full")
    features = json.loads((out / "audit_features.json").read_text())
    assert features == audit_features(out)  # rebuilt from raw/ the same way
    assert features["format"] == "wf-audit.audit-features/v1" and features["excluded"] == []
    polarity = features["polarity"]
    assert (polarity["field_coverage"], polarity["incidental_change"]) == ("harmful", "legitimate")
    rows = {(r["task"], r["variant"]): r["families"] for r in features["rows"]}
    assert len(rows) == 6  # every (task, variant)
    slots = {"create_refund": True, "request_approval": True, "send_email": True}
    assert rows[("close_ticket", "ignores_collateral")]["schema_collateral"] == {
        "candidates": 3,
        "survived": 3,
        "survival": 1.0,
        "errors": 0,
        "slots": slots,  # set_ticket_status is refused here: no untouched ticket
    }
    assert rows[("close_ticket", "strict")]["schema_collateral"]["slots"] == dict.fromkeys(slots, False)
    unscored = {"candidates": 0, "survived": 0, "survival": None, "errors": 0}
    assert rows[("close_ticket", "strict")]["extra_field"] == {**unscored, "slots": {}}
    # only scored cells count: a degenerate duplicate (idempotent status) and an exhausted wrong_value are not
    # candidates, so they read "never scored", not "caught"
    assert rows[("close_ticket", "strict")]["duplicate"] == unscored
    assert rows[("refund_approved", "strict")]["wrong_value"] == unscored
    # a legitimate family scores built[0] once, with no census; a legitimate candidate should survive
    assert rows[("refund_email", "strict")]["incidental_change"] == {
        **unscored,
        "candidates": 1,
        "survived": 1,
        "survival": 1.0,
    }
    md = (out / "SCORECARD.md").read_text()
    assert "## Schema families, per slot" in md and "### schema_collateral" in md
    assert "| ignores_collateral | **3/3** | **3/3** | **2/2** | **2/2** |" in md
    assert "| extra_field (FA) |" in md  # a kind without a slot stays in the main table
    run_card = json.loads((out / "run_card.json").read_text())
    assert run_card["declarations"]["free_text_fields"] == {"send_email": ["subject"]}
    assert list(run_card["tool_schemas"]) == ["create_refund", "request_approval", "send_email", "set_ticket_status"]
    plain = sweep(variants, world, tmp_path / "default")
    plain_card = json.loads((plain / "run_card.json").read_text())
    assert "per slot" not in (plain / "SCORECARD.md").read_text()
    assert "tool_schemas" not in plain_card and "declarations" not in plain_card


class CrashesOnCollateralEmail(StrictToyVerifier):
    """Raises when close_ticket also sent an email, as a grader with a bug might."""

    id = "crashes"

    def verify(self, episode):
        if episode.task.id == "close_ticket" and any(c.tool == "send_email" for c in episode.trajectory.calls):
            raise RuntimeError("boom")
        return super().verify(episode)


class RejectsCloseTicket(StrictToyVerifier):
    """Fails close_ticket whatever happened, so OpenAdapt's control excludes the task."""

    id = "rejects_close"

    def state_ok(self, actual, expected, episode):
        return episode.task.id != "close_ticket" and super().state_ok(actual, expected, episode)


def test_audit_features_keep_errors_and_excluded_tasks_apart_from_refusals(world):
    families = schema_families(world)
    reports = {
        v.id: run_audit(world.target(v), AuditConfig(families=families))
        for v in (
            cls(world.executor, world.golden, world.claim_tool)
            for cls in (CrashesOnCollateralEmail, RejectsCloseTicket)
        )
    }
    features = audit_features(reports)
    rows = {(r["task"], r["variant"]): r["families"] for r in features["rows"]}
    collateral = rows[("close_ticket", "crashes")]["schema_collateral"]
    assert (collateral["candidates"], collateral["errors"]) == (2, 1)  # the email slot crashed: not a candidate
    assert "send_email" not in collateral["slots"]
    assert features["excluded"] == [
        {"task": "close_ticket", "variant": "rejects_close", "reason": "REFERENCE_NOT_ACCEPTED:FAIL"}
    ]
    assert ("close_ticket", "rejects_close") not in rows


def test_run_card_records_the_tool_schemas_the_schema_families_read(tmp_path):
    world = fields_world()
    (ut,) = [t for t in world.executor.tools if t.name == "update_ticket"]
    reordered = {"anyOf": [{"type": "string", "enum": ["urgent", "low", "normal", "high"]}, {"type": "null"}]}
    props = {**ut.params["properties"], "priority": {**reordered, "default": None}}
    changed = ut.model_copy(update={"params": {**ut.params, "properties": props}})

    class Reordered(FieldsToyExecutor):
        tools = (*world.executor.tools[:-1], changed)

    variants = {"strict": StrictToyVerifier(world.executor, world.golden, world.claim_tool)}
    a = json.loads((sweep(variants, world, tmp_path / "a", families="full") / "run_card.json").read_text())
    other = replace(world, executor=Reordered())
    b = json.loads((sweep(variants, other, tmp_path / "b", families="full") / "run_card.json").read_text())
    assert a["tool_schemas"]["update_ticket"] != b["tool_schemas"]["update_ticket"]
    assert {k: v for k, v in a["tool_schemas"].items() if k != "update_ticket"} == {
        k: v for k, v in b["tool_schemas"].items() if k != "update_ticket"
    }


def test_cli_full_preset_and_kind_filter(tmp_path, capsys):
    out = tmp_path / "audit"
    argv = ["sweep", "--world", "toy", "--out", str(out), "--only", "strict", "--preset", "full", "-q"]
    assert main([*argv, "--families", "field_coverage,extra_field"]) == 0
    printed = capsys.readouterr().out
    assert "### field_coverage" in printed and "| extra_field (FA) |" in printed and "duplicate" not in printed
    run_card = json.loads((out / "run_card.json").read_text())
    assert run_card["families"] == [
        "field_coverage:create_refund.amount_cents",
        "field_coverage:set_ticket_status.status",
        "extra_field",
    ]
    # schema families alone still record what they read: the free-text declaration and the tool schemas
    assert run_card["declarations"]["free_text_fields"] == {"send_email": ["subject"]}
    assert "set_ticket_status" in run_card["tool_schemas"]
