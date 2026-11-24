/**
 * The views make tool calls from clicks (rows, links, Approve and Reject, List policies) and show what came back:
 * the record, the tool's refusal, or the API's error. None of them may drop an API error silently.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActivityView } from "@/components/views/activity";
import { CustomersView } from "@/components/views/customers";
import { ApprovalsView } from "@/components/views/ledgers";
import { OrdersView } from "@/components/views/orders";
import { PoliciesView } from "@/components/views/policies";
import { TicketsView } from "@/components/views/tickets";
import type { Json, JsonObject, ToolCall } from "@/lib/types";

import { EPISODE_ID, FakeApi } from "./fake-api";
import { renderEpisode } from "./render";
import { router } from "./router";

const CALLS = `/episodes/${EPISODE_ID}/calls`;
const ENDED = JSON.stringify({ detail: `episode ${EPISODE_ID} ended at submit_resolution and was graded; start a new one` });

function recorded(idx: number, tool: string, args: JsonObject, result: Json): ToolCall {
  return { idx, tool, args, ok: true, result, error: null };
}

const ORDER_SUMMARY = { id: "ORD-00154", customer_id: "CUS-00006", placed_at: "2026-09-10T10:00:00Z", status: "returned", total_cents: 21999, refunded_cents: 0 };
const PENDING = { id: "APR-00003", order_id: "ORD-00154", amount_cents: 21999, status: "pending", requested_by: "agent-01" };
const ORDER = {
  order: { ...ORDER_SUMMARY, subtotal_cents: 21999, shipping_cents: 0, updated_at: "2026-09-12T10:00:00Z" },
  items: [{ id: "ITM-1", order_id: "ORD-00154", sku: "OFF-502", name: "monitor arm", qty: 1, unit_price_cents: 21999 }],
  refunds: [],
  approvals: [PENDING],
};
const CUSTOMER = {
  customer: { id: "CUS-00006", name: "Megan Parsons", email: "megan@example.net", phone: "555", created_at: "2023-09-12T01:31:00Z" },
  account: { id: "ACC-00006", tier: "standard", status: "active", opened_at: "2023-09-12T01:31:00Z" },
};
const TICKET = {
  ticket: {
    id: "TCK-00042",
    customer_id: "CUS-00006",
    order_id: "ORD-00154",
    subject: "Returned monitor arm",
    status: "open",
    priority: "normal",
    queue: "tier1",
    escalated: 0,
    category: "refund",
    opened_at: "2026-09-12T09:00:00Z",
    first_response_at: null,
    resolved_at: null,
    updated_at: "2026-09-12T09:00:00Z",
  },
  comments: [],
};

/** An episode whose trajectory already has these reads, so the views open where they left off. */
function episodeWith(...calls: [string, JsonObject, Json][]): FakeApi {
  return new FakeApi({ calls: calls.map(([tool, args, result], idx) => recorded(idx, tool, args, result)) });
}

describe("Orders", () => {
  it("shows why an order could not be read, instead of nothing", async () => {
    const fake = episodeWith(["search_orders", {}, { orders: [ORDER_SUMMARY] }]);
    fake.fail("POST", CALLS, { status: 500, body: "Internal Server Error", contentType: "text/plain" });
    const { user } = await renderEpisode(<OrdersView />, fake);

    await user.click(screen.getByRole("button", { name: /^Open order ORD-00154/ }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not read the order")).toBeTruthy();
    expect(alert.textContent).toContain("HTTP 500: Internal Server Error");
  });

  it("approves with the arguments the tool expects and shows the refusal for an agent", async () => {
    const fake = episodeWith(["get_order", { order_id: "ORD-00154" }, ORDER]);
    fake.refuse = (tool) => (tool === "approve" ? "permission denied: approve requires role manager; you are agent" : null);
    const { user } = await renderEpisode(<OrdersView />, fake);

    await user.click(screen.getByRole("button", { name: "Approve APR-00003" }));

    expect((await screen.findByRole("alert")).textContent).toContain("permission denied: approve requires role manager");
    expect(fake.argsSent("approve")).toEqual([{ approval_id: "APR-00003", decision: "approved" }]);
  });

  it("shows an API error from Reject", async () => {
    const fake = episodeWith(["get_order", { order_id: "ORD-00154" }, ORDER]);
    fake.fail("POST", CALLS, { status: 422, body: JSON.stringify({ detail: [{ msg: "String should match pattern" }] }) });
    const { user } = await renderEpisode(<OrdersView />, fake);

    await user.click(screen.getByRole("button", { name: "Reject APR-00003" }));

    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 422: String should match pattern");
    expect(fake.argsSent("approve")).toEqual([{ approval_id: "APR-00003", decision: "rejected" }]);
  });

  it("opens the order's customer: get_customer, then the Customers view", async () => {
    const fake = episodeWith(["get_order", { order_id: "ORD-00154" }, ORDER]);
    fake.result = () => CUSTOMER;
    const { user } = await renderEpisode(<OrdersView />, fake);

    await user.click(screen.getByRole("button", { name: "Open customer CUS-00006 (get_customer)" }));

    await waitFor(() => expect(router.pushed).toEqual([`/episodes/${EPISODE_ID}/customers`]));
    expect(fake.argsSent("get_customer")).toEqual([{ customer_id: "CUS-00006" }]);
  });
});

describe("links between records", () => {
  it("stay on the page and say why when the API does not take the call", async () => {
    const fake = episodeWith(["get_ticket", { ticket_id: "TCK-00042" }, TICKET]);
    fake.fail("POST", CALLS, { status: 404, body: JSON.stringify({ detail: "no episode (episodes live in memory until the API restarts)" }) });
    const { user } = await renderEpisode(<TicketsView />, fake);

    await user.click(screen.getByRole("button", { name: "Open order ORD-00154 (get_order)" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not open the record")).toBeTruthy();
    expect(alert.textContent).toContain("HTTP 404");
    expect(router.pushed).toEqual([]);
  });

  it("run the customer's orders search and open the Orders view", async () => {
    const fake = episodeWith(["get_customer", { customer_id: "CUS-00006" }, CUSTOMER]);
    fake.result = () => ({ orders: [ORDER_SUMMARY] });
    const { user } = await renderEpisode(<CustomersView />, fake);

    await user.click(screen.getByRole("button", { name: "Orders of CUS-00006 (search_orders)" }));

    await waitFor(() => expect(router.pushed).toEqual([`/episodes/${EPISODE_ID}/orders`]));
    expect(fake.argsSent("search_orders")).toEqual([{ customer_id: "CUS-00006" }]);
  });

  it("in the Approvals ledger, report a failure next to the link", async () => {
    const fake = episodeWith(["get_order", { order_id: "ORD-00154" }, ORDER]);
    fake.fail("POST", CALLS, "network");
    const { user } = await renderEpisode(<ApprovalsView />, fake);

    await user.click(screen.getByRole("button", { name: "Open order ORD-00154 (get_order)" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not open the order");
    expect(alert.textContent).toContain("answered, but the browser blocked the response");
    expect(router.pushed).toEqual([]);
  });
});

describe("Policies", () => {
  it("shows why the policies could not be listed", async () => {
    const fake = new FakeApi().fail("POST", CALLS, { status: 409, body: ENDED });
    const { user } = await renderEpisode(<PoliciesView />, fake);

    await user.click(screen.getByRole("button", { name: "List policies" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not list the policies")).toBeTruthy();
    expect(alert.textContent).toContain("ended at submit_resolution");
    expect(fake.argsSent("get_policy")).toEqual([{}]);
  });
});

describe("Activity", () => {
  it("shows why the trajectory could not be downloaded", async () => {
    const fake = new FakeApi();
    const { user } = await renderEpisode(<ActivityView />, fake);
    fake.fail("GET", `/episodes/${EPISODE_ID}/trajectory`, { status: 404, body: JSON.stringify({ detail: "no episode" }) });

    await user.click(screen.getByRole("button", { name: "Download trajectory (JSON)" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not download the trajectory")).toBeTruthy();
    expect(alert.textContent).toContain("HTTP 404: no episode");
    expect((screen.getByRole("button", { name: "Download trajectory (JSON)" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("after the episode ended", () => {
  it("rows, links and Reload make no call", async () => {
    const fake = episodeWith(["search_orders", {}, { orders: [ORDER_SUMMARY] }], ["get_order", { order_id: "ORD-00154" }, ORDER]);
    fake.status = "submitted";
    await renderEpisode(<OrdersView />, fake);

    for (const name of [/^Open order ORD-00154/, /^Reload order/, /^Open customer/, /^Approve APR/, /^Reject APR/]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled, String(name)).toBe(true);
    }
  });
});
