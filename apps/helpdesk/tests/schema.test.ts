import { describe, expect, it } from "vitest";

import { buildArgs, fieldsOf, initialValues, type Field } from "@/lib/schema";

import { TOOLS } from "./fake-api";

function spec(name: string) {
  const found = TOOLS.find((tool) => tool.name === name);
  if (!found) throw new Error(`no tool ${name} in tests/fixtures/tools.json`);
  return found;
}

function field(tool: string, name: string): Field {
  const found = fieldsOf(spec(tool)).find((f) => f.name === name);
  if (!found) throw new Error(`no field ${name} on ${tool}`);
  return found;
}

describe("fieldsOf", () => {
  it("reads kinds, required and money from create_refund's schema", () => {
    const fields = fieldsOf(spec("create_refund"));
    expect(fields.map((f) => [f.name, f.kind, f.required, f.money])).toEqual([
      ["order_id", "text", true, false],
      ["amount_cents", "integer", true, true],
      ["reason", "textarea", true, false], // maxLength 500
      ["approval_id", "text", false, false], // anyOf [string, null]: optional, whatever `required` says
    ]);
  });

  it("states the schema's limits as text instead of enforcing them", () => {
    expect(field("create_refund", "amount_cents").limits).toBe("a whole number, more than 0");
    expect(field("create_refund", "reason").limits).toBe("3 to 500 characters");
    expect(field("search_customers", "name").limits).toBe("at least 2 characters");
    expect(field("list_tickets", "limit").limits).toBe("a whole number, from 1 to 100");
    expect(field("get_order", "order_id").limits).toBeUndefined();
  });

  it("unwraps nullable enums and keeps defaults", () => {
    const status = field("list_tickets", "status");
    expect(status.kind).toBe("enum");
    expect(status.required).toBe(false);
    expect(status.options).toEqual(["open", "pending", "resolved", "closed"]);
    expect(field("list_tickets", "limit").defaultValue).toBe(50);
    expect(field("add_ticket_comment", "public").kind).toBe("boolean");
  });

  it("uses a textarea for long text", () => {
    expect(field("add_ticket_comment", "body").kind).toBe("textarea"); // maxLength 4000
    expect(field("submit_resolution", "summary").kind).toBe("textarea");
    expect(field("send_email", "subject").kind).toBe("text"); // maxLength 200
  });
});

describe("initialValues", () => {
  it("fills defaults, then the given values", () => {
    const fields = fieldsOf(spec("add_ticket_comment"));
    expect(initialValues(fields)).toEqual({ ticket_id: "", body: "", public: true });
    expect(initialValues(fields, { ticket_id: "TCK-00001", public: false })).toEqual({
      ticket_id: "TCK-00001",
      body: "",
      public: false,
    });
    expect(initialValues(fieldsOf(spec("get_customer")))).toEqual({ customer_id: "", email: "" }); // default null
  });
});

describe("buildArgs", () => {
  const refund = fieldsOf(spec("create_refund"));

  it("parses numbers and leaves empty optional fields out", () => {
    expect(buildArgs(refund, { order_id: " ORD-00154 ", amount_cents: "2500", reason: "Returned", approval_id: "" })).toEqual({
      order_id: "ORD-00154",
      amount_cents: 2500,
      reason: "Returned",
    });
  });

  it("sends what the tool would refuse, as typed, so the refusal is the tool's", () => {
    expect(buildArgs(refund, { order_id: "", amount_cents: "0", reason: "ok", approval_id: "" })).toEqual({
      order_id: "",
      amount_cents: 0,
      reason: "ok",
    });
    expect(buildArgs(refund, { order_id: "ORD-1", amount_cents: "12abc", reason: "Returned", approval_id: "" })).toEqual({
      order_id: "ORD-1",
      amount_cents: "12abc",
      reason: "Returned",
    });
    expect(buildArgs(refund, { order_id: "ORD-1", amount_cents: "", reason: "", approval_id: "" })).toEqual({
      order_id: "ORD-1",
      amount_cents: "",
      reason: "",
    });
    const long = "x".repeat(600);
    expect(buildArgs(refund, { order_id: "ORD-1", amount_cents: "1", reason: long, approval_id: "" }).reason).toBe(long);
  });

  it("keeps a textarea's whitespace and sends booleans as booleans", () => {
    const comment = fieldsOf(spec("add_ticket_comment"));
    expect(buildArgs(comment, { ticket_id: "TCK-1", body: "  Hello,\n\nThanks.\n", public: false })).toEqual({
      ticket_id: "TCK-1",
      body: "  Hello,\n\nThanks.\n",
      public: false,
    });
  });

  it("leaves an optional enum out when nothing is chosen", () => {
    const list = fieldsOf(spec("list_tickets"));
    expect(buildArgs(list, { customer_id: "", status: "", priority: "urgent", limit: "50" })).toEqual({
      priority: "urgent",
      limit: 50,
    });
  });
});
