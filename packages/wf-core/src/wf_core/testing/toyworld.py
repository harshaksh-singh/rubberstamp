"""A tiny dict-backed world used as the shared test fixture until the real app lands.

Tables: customers, orders, refunds, approvals, tickets, emails (side effect), audit_log (audit).
Three toy tasks with golden tool calls: refund + email, close ticket, refund after approval.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from ..models import Episode, SchemaInfo, Snapshot, TableInfo, Task, ToolCall, ToolSpec, Trajectory

SCHEMA = SchemaInfo(
    version="toy-1",
    tables={
        "customers": TableInfo(pk="id"),
        "orders": TableInfo(pk="id", fk={"customer_id": "customers"}),
        "refunds": TableInfo(pk="id", fk={"order_id": "orders"}),
        "approvals": TableInfo(pk="id", fk={"order_id": "orders"}),
        "tickets": TableInfo(pk="id", fk={"customer_id": "customers"}, volatile=("updated_at",)),
        "emails": TableInfo(pk="id", kind="side_effect", fk={"customer_id": "customers"}),
        "audit_log": TableInfo(pk="id", kind="audit"),
    },
)

INITIAL: dict[str, dict[str, dict[str, Any]]] = {
    "customers": {
        "c1": {"id": "c1", "name": "Asha Rao", "tier": "gold"},
        "c2": {"id": "c2", "name": "Ben Ortiz", "tier": "standard"},
    },
    "orders": {
        "o1": {"id": "o1", "customer_id": "c1", "amount_cents": 5000, "status": "delivered"},
        "o2": {"id": "o2", "customer_id": "c1", "amount_cents": 12000, "status": "delivered"},
        "o3": {"id": "o3", "customer_id": "c2", "amount_cents": 3000, "status": "delivered"},
    },
    "refunds": {},
    "approvals": {},
    "tickets": {
        "t1": {"id": "t1", "customer_id": "c2", "status": "open", "updated_at": "2026-11-01"},
        "t2": {"id": "t2", "customer_id": "c1", "status": "open", "updated_at": "2026-11-02"},
    },
    "emails": {},
    "audit_log": {},
}

Tables = dict[str, dict[str, dict[str, Any]]]


def _next_id(rows: dict[str, Any], prefix: str) -> str:
    return f"{prefix}{len(rows) + 1}"


def _audit(t: Tables, action: str, ref: str) -> None:
    t["audit_log"][_next_id(t["audit_log"], "a")] = {"action": action, "ref": ref}


def get_order(t: Tables, order_id: str) -> dict[str, Any]:
    if order_id not in t["orders"]:
        raise KeyError(f"no order {order_id}")
    return t["orders"][order_id]


def create_refund(t: Tables, order_id: str, amount_cents: int) -> dict[str, Any]:
    order = get_order(t, order_id)
    if amount_cents <= 0 or amount_cents > order["amount_cents"]:
        raise ValueError("invalid refund amount")
    rid = _next_id(t["refunds"], "r")
    t["refunds"][rid] = {"id": rid, "order_id": order_id, "amount_cents": amount_cents}
    order["status"] = "refunded"
    _audit(t, "refund", rid)
    return {"refund_id": rid}


def request_approval(t: Tables, order_id: str) -> dict[str, Any]:
    get_order(t, order_id)
    aid = _next_id(t["approvals"], "ap")
    t["approvals"][aid] = {"id": aid, "order_id": order_id, "status": "approved"}
    _audit(t, "approval", aid)
    return {"approval_id": aid}


def send_email(t: Tables, customer_id: str, subject: str) -> dict[str, Any]:
    if customer_id not in t["customers"]:
        raise KeyError(f"no customer {customer_id}")
    eid = _next_id(t["emails"], "e")
    t["emails"][eid] = {"id": eid, "customer_id": customer_id, "subject": subject}
    return {"email_id": eid}


def set_ticket_status(t: Tables, ticket_id: str, status: str) -> dict[str, Any]:
    if ticket_id not in t["tickets"]:
        raise KeyError(f"no ticket {ticket_id}")
    t["tickets"][ticket_id]["status"] = status
    t["tickets"][ticket_id]["updated_at"] = "2026-11-22"
    return {"ok": True}


TOOL_FNS: dict[str, Callable[..., Any]] = {
    "get_order": get_order,
    "create_refund": create_refund,
    "request_approval": request_approval,
    "send_email": send_email,
    "set_ticket_status": set_ticket_status,
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_order",
        app="toy",
        description="Read an order",
        effect="read",
        params={"order_id": {"type": "string"}},
        id_params=("order_id",),
    ),
    ToolSpec(
        name="create_refund",
        app="toy",
        description="Refund an order",
        effect="write",
        idempotent=False,
        params={"order_id": {"type": "string"}, "amount_cents": {"type": "integer"}},
        id_params=("order_id",),
    ),
    ToolSpec(
        name="request_approval",
        app="toy",
        description="Get manager approval",
        effect="write",
        idempotent=False,
        params={"order_id": {"type": "string"}},
        id_params=("order_id",),
    ),
    ToolSpec(
        name="send_email",
        app="toy",
        description="Email a customer",
        effect="write",
        idempotent=False,
        params={"customer_id": {"type": "string"}, "subject": {"type": "string"}},
        id_params=("customer_id",),
    ),
    ToolSpec(
        name="set_ticket_status",
        app="toy",
        description="Change a ticket's status",
        effect="write",
        params={"ticket_id": {"type": "string"}, "status": {"type": "string"}},
        id_params=("ticket_id",),
    ),
)


def _task(tid: str, prompt: str) -> Task:
    return Task(id=tid, source="toy", prompt=prompt, tools=tuple(TOOL_FNS))


TASKS: dict[str, Task] = {
    "refund_email": _task("refund_email", "Refund order o1 in full and email the customer a confirmation."),
    "close_ticket": _task("close_ticket", "Close ticket t1."),
    "refund_approved": _task(
        "refund_approved", "Refund order o2 in full; refunds over $100 need manager approval first."
    ),
}

GOLDEN_CALLS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "refund_email": [
        ("create_refund", {"order_id": "o1", "amount_cents": 5000}),
        ("send_email", {"customer_id": "c1", "subject": "Refund confirmed"}),
    ],
    "close_ticket": [("set_ticket_status", {"ticket_id": "t1", "status": "closed"})],
    "refund_approved": [
        ("request_approval", {"order_id": "o2"}),
        ("create_refund", {"order_id": "o2", "amount_cents": 12000}),
    ],
}


class ToyExecutor:
    schema = SCHEMA
    tools = TOOLS

    def initial(self, task: Task) -> Snapshot:
        return Snapshot(schema_version=SCHEMA.version, tables=copy.deepcopy(INITIAL))

    def run(self, task: Task, calls: list[ToolCall], final_answer: str | None = None) -> Episode:
        tables: Tables = copy.deepcopy(INITIAL)
        recorded: list[ToolCall] = []
        for i, call in enumerate(calls):
            fn = TOOL_FNS.get(call.tool)
            try:
                if fn is None:
                    raise KeyError(f"unknown tool {call.tool}")
                result = fn(tables, **call.args)
                recorded.append(ToolCall(idx=i, tool=call.tool, args=call.args, result=copy.deepcopy(result)))
            except (KeyError, ValueError, TypeError) as exc:
                recorded.append(ToolCall(idx=i, tool=call.tool, args=call.args, ok=False, error=str(exc)))
        return Episode(
            task=task,
            initial=self.initial(task),
            final=Snapshot(schema_version=SCHEMA.version, tables=tables),
            trajectory=Trajectory(task_id=task.id, agent_id="replay", calls=tuple(recorded), final_answer=final_answer),
        )


def golden_calls(task_id: str) -> list[ToolCall]:
    return [ToolCall(idx=i, tool=name, args=args) for i, (name, args) in enumerate(GOLDEN_CALLS[task_id])]
