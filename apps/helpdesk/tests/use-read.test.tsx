/** useRead: a result shown through setCall (an inline form's onDone) clears the failure of an earlier read. */
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRead } from "@/lib/episode";
import type { ToolCall } from "@/lib/types";

import { EPISODE_ID, FakeApi } from "./fake-api";
import { renderEpisode } from "./render";

const SHOWN: ToolCall = { idx: 0, tool: "get_order", args: { order_id: "ORD-00154" }, ok: true, result: {}, error: null };

function Reader() {
  const { read, setCall, failure } = useRead("orders.detail", "get_order");
  return (
    <>
      <button type="button" onClick={() => void read({ order_id: "ORD-00154" })}>Read</button>
      <button type="button" onClick={() => setCall(SHOWN)}>Show</button>
      <p data-testid="failure">{failure ?? "none"}</p>
    </>
  );
}

describe("useRead", () => {
  it("clears a failed read when a result is shown through setCall", async () => {
    const fake = new FakeApi();
    fake.fail("POST", `/episodes/${EPISODE_ID}/calls`, { status: 500, body: JSON.stringify({ detail: "boom" }) });
    const { user } = await renderEpisode(<Reader />, fake);

    await user.click(screen.getByRole("button", { name: "Read" }));
    await waitFor(() => expect(screen.getByTestId("failure").textContent).not.toBe("none"));

    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByTestId("failure").textContent).toBe("none");
  });
});
