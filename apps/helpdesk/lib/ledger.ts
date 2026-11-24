/**
 * Records gathered from the results of calls already made in this episode. The world has no tool that lists refunds,
 * approvals or sent email, so these views show what the episode's own calls returned (get_order's refunds and
 * approvals, and the write tools' results), each with the call it came from. They never call a tool themselves.
 */
import type { Approval, OrderDetail, Refund } from "./records";
import { resultOf } from "./records";
import type { JsonObject, ToolCall } from "./types";

export interface Sourced<T> {
  row: T;
  idx: number; // the call it was last seen in
  via: string; // that call's tool
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

/** The latest get_order result per order id. */
export function ordersSeen(calls: ToolCall[]): Map<string, Sourced<OrderDetail>> {
  const out = new Map<string, Sourced<OrderDetail>>();
  for (const call of calls) {
    const detail = call.tool === "get_order" ? resultOf<OrderDetail>(call, "order") : null;
    if (detail) out.set(detail.order.id, { row: detail, idx: call.idx, via: call.tool });
  }
  return out;
}

export function refundsSeen(calls: ToolCall[]): Sourced<Refund>[] {
  const out = new Map<string, Sourced<Refund>>();
  for (const call of calls) {
    if (!call.ok) continue;
    if (call.tool === "get_order") {
      for (const row of resultOf<OrderDetail>(call, "order")?.refunds ?? []) {
        out.set(row.id, { row, idx: call.idx, via: call.tool });
      }
    } else if (call.tool === "create_refund") {
      const result = call.result as JsonObject;
      const id = str(result.id);
      if (!id) continue;
      out.set(id, {
        row: {
          id,
          order_id: str(result.order_id) ?? "",
          amount_cents: num(result.amount_cents) ?? 0,
          approval_id: str(result.approval_id) ?? null,
          reason: str(call.args.reason),
        },
        idx: call.idx,
        via: call.tool,
      });
    }
  }
  return [...out.values()].sort((a, b) => b.row.id.localeCompare(a.row.id));
}

export function approvalsSeen(calls: ToolCall[]): Sourced<Approval>[] {
  const out = new Map<string, Sourced<Approval>>();
  for (const call of calls) {
    if (!call.ok) continue;
    if (call.tool === "get_order") {
      for (const row of resultOf<OrderDetail>(call, "order")?.approvals ?? []) {
        out.set(row.id, { row, idx: call.idx, via: call.tool });
      }
    } else if (call.tool === "request_approval" || call.tool === "approve") {
      const result = call.result as JsonObject;
      const id = str(result.id);
      if (!id) continue;
      const before = out.get(id)?.row;
      out.set(id, {
        row: {
          ...before,
          id,
          order_id: str(result.order_id) ?? before?.order_id ?? "",
          amount_cents: num(result.amount_cents) ?? before?.amount_cents,
          status: str(result.status) ?? before?.status ?? "unknown",
          decided_by: str(result.decided_by) ?? before?.decided_by,
          reason: before?.reason ?? str(call.args.reason),
        },
        idx: call.idx,
        via: call.tool,
      });
    }
  }
  const pendingFirst = (row: Approval) => (row.status === "pending" ? 0 : 1);
  return [...out.values()].sort(
    (a, b) => pendingFirst(a.row) - pendingFirst(b.row) || b.row.id.localeCompare(a.row.id),
  );
}

export interface SentEmail {
  idx: number;
  id: string;
  customer_id: string;
  to_address: string;
  subject: string;
  body: string;
}

export function emailsSent(calls: ToolCall[]): SentEmail[] {
  return calls
    .filter((call) => call.ok && call.tool === "send_email")
    .map((call) => {
      const result = call.result as JsonObject;
      return {
        idx: call.idx,
        id: str(result.id) ?? "",
        customer_id: str(call.args.customer_id) ?? "",
        to_address: str(result.to_address) ?? "",
        subject: str(call.args.subject) ?? "",
        body: str(call.args.body) ?? "",
      };
    });
}
