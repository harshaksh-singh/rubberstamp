import type { ToolCall } from "./types";

/** Slots that hold each view's last read (see `useRead`). */
export const SLOTS = {
  ticketList: "tickets.list",
  ticket: "tickets.detail",
  customerList: "customers.list",
  customer: "customers.detail",
  orderList: "orders.list",
  order: "orders.detail",
  policyList: "policies.list",
  policy: "policies.detail",
} as const;

/** The view slot a read call feeds, if any. */
export function slotOf(call: ToolCall): string | null {
  switch (call.tool) {
    case "list_tickets":
      return SLOTS.ticketList;
    case "get_ticket":
      return SLOTS.ticket;
    case "search_customers":
      return SLOTS.customerList;
    case "get_customer":
      return SLOTS.customer;
    case "search_orders":
      return SLOTS.orderList;
    case "get_order":
      return SLOTS.order;
    case "get_policy":
      return call.args.policy_id ? SLOTS.policy : SLOTS.policyList;
    default:
      return null;
  }
}

/**
 * The views' state recovered from a recorded trajectory: each view shows the last successful read it would have
 * made. A reloaded page, or an episode an agent drove through the API, opens where it left off, with no new call.
 */
export function slotsFromCalls(calls: ToolCall[]): Record<string, unknown> {
  const slots: Record<string, unknown> = {};
  for (const call of calls) {
    const slot = call.ok ? slotOf(call) : null;
    if (slot) slots[slot] = call;
  }
  return slots;
}
