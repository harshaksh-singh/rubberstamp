/**
 * The console's trajectory is rs-api's: calls made by other clients of the episode (a harness, another tab) appear
 * too, and a submission made elsewhere ends the episode here.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolForm } from "@/components/tool-form";
import { ActivityView } from "@/components/views/activity";
import { SubmitView } from "@/components/views/submit";
import { useEpisode } from "@/lib/episode";

import { EPISODE_ID, FakeApi } from "./fake-api";
import { renderEpisode } from "./render";

function Status() {
  const { submitted, calls } = useEpisode();
  return <p data-testid="status">{`${submitted ? "submitted" : "open"} with ${calls.length} calls`}</p>;
}

function shownCalls(): string[] {
  const list = screen.queryByRole("list", { name: "Tool calls" });
  if (!list) return [];
  return within(list)
    .getAllByRole("listitem")
    .map((item) => {
      const [idx, tool] = item.querySelectorAll("summary > span"); // "#0", then the tool
      return `${idx?.textContent} ${tool?.textContent}`;
    });
}

describe("EpisodeProvider", () => {
  it("fills in calls another client made when a returned index skips ahead", async () => {
    const fake = new FakeApi();
    fake.recordElsewhere("list_tickets", {}, { tickets: [] });
    const { user } = await renderEpisode(
      <>
        <ToolForm tool="get_policy" submitLabel="Read" />
        <ActivityView />
      </>,
      fake,
    );
    expect(shownCalls()).toEqual(["#0 list_tickets"]);

    fake.recordElsewhere("search_orders", { status: "returned" }, { orders: [] }); // a harness, after the page loaded
    await user.click(screen.getByRole("button", { name: "Read" }));

    await waitFor(() => expect(shownCalls()).toEqual(["#0 list_tickets", "#1 search_orders", "#2 get_policy"]));
  });

  it("reloads the trajectory when the window regains focus", async () => {
    const fake = new FakeApi();
    await renderEpisode(<ActivityView />, fake);
    expect(screen.getByText("No tool calls yet")).toBeTruthy();

    fake.recordElsewhere("get_order", { order_id: "ORD-00154" }, {});
    fireEvent.focus(window);

    await waitFor(() => expect(shownCalls()).toEqual(["#0 get_order"]));
    expect(fake.argsSent("get_order")).toEqual([]); // reloading is not a tool call
  });

  it("learns from a 409 that the episode was submitted elsewhere, and disables its forms", async () => {
    const fake = new FakeApi();
    const { user } = await renderEpisode(
      <>
        <Status />
        <ToolForm tool="get_policy" submitLabel="Read" />
      </>,
      fake,
    );
    fake.status = "submitted"; // another tab submitted

    await user.click(screen.getByRole("button", { name: "Read" }));

    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 409");
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("submitted with 0 calls"));
    expect((screen.getByRole("button", { name: "Read" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the episode as missing once rs-api no longer has it", async () => {
    const fake = new FakeApi();
    const { user } = await renderEpisode(<ToolForm tool="get_policy" submitLabel="Read" />, fake);
    const gone = { status: 404, body: JSON.stringify({ detail: `no episode '${EPISODE_ID}'` }) };
    fake.fail("POST", `/episodes/${EPISODE_ID}/calls`, gone).fail("GET", `/episodes/${EPISODE_ID}`, gone);

    await user.click(screen.getByRole("button", { name: "Read" }));

    expect(await screen.findByText(/^Episode missing: HTTP 404/)).toBeTruthy();
  });
});

describe("SubmitView", () => {
  it("sends an empty summary to submit_resolution, so the refusal is recorded", async () => {
    const fake = new FakeApi();
    fake.refuse = (tool, args) =>
      tool === "submit_resolution" && String(args.summary).length < 3
        ? "invalid arguments for submit_resolution: summary: String should have at least 3 characters"
        : null;
    const { user } = await renderEpisode(<SubmitView />, fake);

    await user.click(screen.getByRole("button", { name: "Submit and grade" }));

    expect((await screen.findByRole("alert")).textContent).toContain("summary: String should have at least 3 characters");
    expect(fake.sent.filter((sent) => sent.path.endsWith("/submit")).map((sent) => sent.body)).toEqual([{ summary: "" }]);
  });
});
