/**
 * The helpdesk records as the read tools return them (packages/rs-world/src/rs_world/tools/lookups.py). The UI never
 * reads the world any other way: every record on screen comes from a recorded tool call's `result`.
 */
import type { Json, ToolCall } from "./types";

export interface Customer {
  id: string;
  name: string;
  email: string;
  phone: string;
  created_at: string;
}

export interface Account {
  id: string;
  tier: string;
  status: string;
  opened_at: string;
}

export interface OrderSummary {
  id: string;
  customer_id: string;
  placed_at: string;
  status: string;
  total_cents: number;
  refunded_cents: number;
}

export interface Order extends OrderSummary {
  subtotal_cents: number;
  shipping_cents: number;
  updated_at: string;
}

export interface OrderItem {
  id: string;
  order_id: string;
  sku: string;
  name: string;
  qty: number;
  unit_price_cents: number;
}

export interface Refund {
  id: string;
  order_id: string;
  amount_cents: number;
  reason?: string;
  approval_id: string | null;
  created_by?: string;
  created_at?: string;
}

export interface Approval {
  id: string;
  order_id: string;
  amount_cents?: number;
  reason?: string;
  status: string;
  requested_by?: string;
  decided_by?: string | null;
  created_at?: string;
  decided_at?: string | null;
}

export interface TicketSummary {
  id: string;
  customer_id: string;
  order_id: string | null;
  subject: string;
  status: string;
  priority: string;
  queue: string;
  escalated: number;
  opened_at: string;
  first_response_at: string | null;
}

export interface Ticket extends TicketSummary {
  category: string;
  resolved_at: string | null;
  updated_at: string;
}

export interface Comment {
  id: string;
  ticket_id: string;
  author: string;
  public: number;
  body: string;
  created_at: string;
}

export interface PolicySummary {
  id: string;
  description: string;
}

export interface Policy {
  id: string;
  value: Json;
  description: string;
}

export interface CustomerDetail {
  customer: Customer;
  account: Account;
}

export interface OrderDetail {
  order: Order;
  items: OrderItem[];
  refunds: Refund[];
  approvals: Approval[];
}

export interface TicketDetail {
  ticket: Ticket;
  comments: Comment[];
}

function isObject(value: Json): value is { [key: string]: Json } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The result of a successful call, read as `T` when it has the expected top-level key; otherwise null. */
export function resultOf<T>(call: ToolCall | null | undefined, key: string): T | null {
  if (!call || !call.ok || !isObject(call.result) || !(key in call.result)) return null;
  return call.result as unknown as T;
}
