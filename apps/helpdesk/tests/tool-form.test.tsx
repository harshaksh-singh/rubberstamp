/**
 * The UI half of UI/API parity: a form generated from a tool's schema sends the arguments an API or MCP agent would
 * send for the same intent, including arguments the tool will refuse, so the refusal is recorded like an agent's.
 */
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolForm } from "@/components/tool-form";

import { EPISODE_ID, FakeApi } from "./fake-api";
import { renderEpisode } from "./render";

const CALLS = `/episodes/${EPISODE_ID}/calls`;

function refundRules(tool: string, args: Record<string, unknown>): string | null {
  if (tool !== "create_refund") return null;
  const problems: string[] = [];
  if (typeof args.amount_cents !== "number" || args.amount_cents <= 0) problems.push("amount_cents: Input should be greater than 0");
  if (typeof args.reason !== "string" || args.reason.length < 3) problems.push("reason: String should have at least 3 characters");
  if (typeof args.reason === "string" && args.reason.length > 500) problems.push("reason: String should have at most 500 characters");
  return problems.length ? `invalid arguments for create_refund: ${problems.join("; ")}` : null;
}

function refundForm() {
  return <ToolForm tool="create_refund" fixed={{ order_id: "ORD-00154" }} submitLabel="Issue refund" />;
}

describe("ToolForm", () => {
  it("sends the arguments the API expects, built from the tool's schema", async () => {
    const fake = new FakeApi();
    fake.result = (_, args) => ({ id: "REF-00001", order_id: args.order_id!, amount_cents: args.amount_cents!, approval_id: null });
    const { user } = await renderEpisode(refundForm(), fake);

    await user.type(screen.getByLabelText(/^Amount/), "2500");
    expect(screen.getByText("= $25.00")).toBeTruthy();
    await user.type(screen.getByLabelText(/^Reason/), "Returned to the warehouse");
    await user.click(screen.getByRole("button", { name: "Issue refund" }));

    const done = await screen.findByRole("status");
    expect(done.textContent).toContain("Done #0 create_refund");
    expect(fake.argsSent("create_refund")).toEqual([
      { order_id: "ORD-00154", amount_cents: 2500, reason: "Returned to the warehouse" }, // empty approval_id left out
    ]);
  });

  it("lets input the tool refuses reach the tool, and shows the recorded refusal", async () => {
    const fake = new FakeApi();
    fake.refuse = refundRules;
    const { user } = await renderEpisode(refundForm(), fake);

    await user.type(screen.getByLabelText(/^Amount/), "0");
    await user.type(screen.getByLabelText(/^Reason/), "ok");
    await user.click(screen.getByRole("button", { name: "Issue refund" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Refused #0 create_refund");
    expect(alert.textContent).toContain("amount_cents: Input should be greater than 0");
    expect(fake.argsSent("create_refund")).toEqual([{ order_id: "ORD-00154", amount_cents: 0, reason: "ok" }]);
    expect(fake.calls.map((call) => call.ok)).toEqual([false]);
  });

  it("sends an empty required field and non-numeric text as typed", async () => {
    const fake = new FakeApi();
    fake.refuse = refundRules;
    const { user } = await renderEpisode(<ToolForm tool="create_refund" submitLabel="Issue refund" />, fake);

    await user.type(screen.getByLabelText(/^Amount/), "12abc");
    await user.click(screen.getByRole("button", { name: "Issue refund" }));

    await screen.findByRole("alert");
    expect(fake.argsSent("create_refund")).toEqual([{ order_id: "", amount_cents: "12abc", reason: "" }]);
  });

  it("does not cut text at the schema's maxLength: the tool decides", async () => {
    const fake = new FakeApi();
    fake.refuse = refundRules;
    const { user } = await renderEpisode(refundForm(), fake);
    const reason = "x".repeat(600);

    await user.type(screen.getByLabelText(/^Amount/), "100");
    await user.click(screen.getByLabelText(/^Reason/));
    await user.paste(reason);
    await user.click(screen.getByRole("button", { name: "Issue refund" }));

    expect((await screen.findByRole("alert")).textContent).toContain("reason: String should have at most 500 characters");
    expect(fake.argsSent("create_refund")[0]?.reason).toBe(reason);
  });

  it("leaves validation to the tool: no browser constraint can block or change a call", async () => {
    await renderEpisode(refundForm());
    const form = screen.getByRole("button", { name: "Issue refund" }).closest("form")!;
    expect(form.noValidate).toBe(true);
    for (const control of form.querySelectorAll("input, textarea")) {
      for (const attribute of ["maxlength", "minlength", "min", "max", "pattern"]) {
        expect(control.hasAttribute(attribute), `${control.id} has ${attribute}`).toBe(false);
      }
    }
    expect(screen.getByText(/3 to 500 characters/)).toBeTruthy(); // the limit is stated instead
  });

  it("shows an API error, which is not a refusal, as an alert", async () => {
    const fake = new FakeApi().fail("POST", CALLS, { status: 422, body: JSON.stringify({ detail: [{ msg: "String should match pattern" }] }) });
    const { user } = await renderEpisode(<ToolForm tool="get_order" submitLabel="Open" />, fake);

    await user.type(screen.getByLabelText(/^Order ID/), "ORD-00154");
    await user.click(screen.getByRole("button", { name: "Open" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("The API did not take this call")).toBeTruthy();
    expect(alert.textContent).toContain("HTTP 422: String should match pattern");
  });

  it("shows a server error whose body is not JSON with its status", async () => {
    const fake = new FakeApi().fail("POST", CALLS, { status: 500, body: "Internal Server Error", contentType: "text/plain" });
    const { user } = await renderEpisode(<ToolForm tool="get_policy" submitLabel="Read" />, fake);

    await user.click(screen.getByRole("button", { name: "Read" }));

    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 500: Internal Server Error");
  });
});
