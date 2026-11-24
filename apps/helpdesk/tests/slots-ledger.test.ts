import { describe, expect, it } from "vitest";

import { approvalsSeen, emailsSent, ordersSeen, refundsSeen } from "@/lib/ledger";
import { SLOTS, slotOf, slotsFromCalls } from "@/lib/slots";
import type { Json, JsonObject, ToolCall } from "@/lib/types";

let next = 0;
function ok(tool: string, args: JsonObject, result: Json): ToolCall {
  return { idx: next++, tool, args, ok: true, result, error: null };
}
function refused(tool: string, args: JsonObject, error = "refused"): ToolCall {
  return { idx: next++, tool, args, ok: false, result: null, error };
}

function order(id: string, refunds: Json[] = [], approvals: Json[] = []): Json {
  return {
    order: { id, customer_id: "CUS-1", status: "delivered", total_cents: 10000, refunded_cents: 0 },
    items: [],
    refunds,
    approvals,
  };
}

describe("slots", () => {
  it("maps each read to its view, and get_policy by whether it names a policy", () => {
    expect(slotOf(ok("get_order", { order_id: "ORD-1" }, {}))).toBe(SLOTS.order);
    expect(slotOf(ok("get_policy", {}, {}))).toBe(SLOTS.policyList);
    expect(slotOf(ok("get_policy", { policy_id: "refund_window_days" }, {}))).toBe(SLOTS.policy);
    expect(slotOf(ok("create_refund", {}, {}))).toBeNull();
  });

  it("restores the last successful read of each view and skips refusals", () => {
    next = 0;
    const first = ok("get_order", { order_id: "ORD-1" }, order("ORD-1"));
    const second = ok("get_order", { order_id: "ORD-2" }, order("ORD-2"));
    const missing = refused("get_order", { order_id: "ORD-404" }, "no order with id 'ORD-404'");
    const tickets = ok("list_tickets", {}, { tickets: [] });
    const write = ok("create_refund", { order_id: "ORD-2" }, { id: "REF-1" });
    expect(slotsFromCalls([first, second, missing, tickets, write])).toEqual({
      [SLOTS.order]: second,
      [SLOTS.ticketList]: tickets,
    });
  });
});

describe("ledger", () => {
  it("gathers refunds from get_order and create_refund, newest id first", () => {
    next = 0;
    const calls = [
      ok("get_order", { order_id: "ORD-1" }, order("ORD-1", [{ id: "REF-00001", order_id: "ORD-1", amount_cents: 500, approval_id: null }])),
      ok("create_refund", { order_id: "ORD-1", amount_cents: 700, reason: "Damaged" }, { id: "REF-00002", order_id: "ORD-1", amount_cents: 700, approval_id: null }),
      refused("create_refund", { order_id: "ORD-1", amount_cents: 0, reason: "ok" }),
    ];
    expect(refundsSeen(calls).map(({ row, idx, via }) => [row.id, row.amount_cents, row.reason, idx, via])).toEqual([
      ["REF-00002", 700, "Damaged", 1, "create_refund"],
      ["REF-00001", 500, undefined, 0, "get_order"],
    ]);
    expect([...ordersSeen(calls).keys()]).toEqual(["ORD-1"]);
  });

  it("updates an approval from approve and lists pending ones first", () => {
    next = 0;
    const calls = [
      ok("request_approval", { order_id: "ORD-1", amount_cents: 20000, reason: "Big" }, { id: "APR-00001", order_id: "ORD-1", amount_cents: 20000, status: "pending" }),
      ok("request_approval", { order_id: "ORD-2", amount_cents: 15000, reason: "Big" }, { id: "APR-00002", order_id: "ORD-2", amount_cents: 15000, status: "pending" }),
      ok("approve", { approval_id: "APR-00001", decision: "approved" }, { id: "APR-00001", status: "approved", decided_by: "mgr-01" }),
    ];
    expect(approvalsSeen(calls).map(({ row, via }) => [row.id, row.status, row.order_id, row.amount_cents, row.decided_by, row.reason, via])).toEqual([
      ["APR-00002", "pending", "ORD-2", 15000, undefined, "Big", "request_approval"],
      ["APR-00001", "approved", "ORD-1", 20000, "mgr-01", "Big", "approve"],
    ]);
  });

  it("lists sent email from send_email's arguments and result", () => {
    next = 0;
    const calls = [
      ok("send_email", { customer_id: "CUS-1", subject: "Your refund", body: "It is on its way." }, { id: "EML-1", to_address: "a@example.net" }),
      refused("send_email", { customer_id: "CUS-1", subject: "x", body: "short" }),
    ];
    expect(emailsSent(calls)).toEqual([
      { idx: 0, id: "EML-1", customer_id: "CUS-1", to_address: "a@example.net", subject: "Your refund", body: "It is on its way." },
    ]);
  });
});
