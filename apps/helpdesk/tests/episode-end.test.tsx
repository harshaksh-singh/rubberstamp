/**
 * How an episode ends in the console: once ended it never shows as open again, and reaching rs-api's call cap
 * (which records the call, then cuts the episode off and grades it) shows as ended without a 409 first.
 * Its own file: the cap test makes the console cache rs-api's /config for the rest of the module.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolForm } from "@/components/tool-form";
import { SubmitView } from "@/components/views/submit";
import { useEpisode } from "@/lib/episode";

import { FakeApi } from "./fake-api";
import { renderEpisode } from "./render";

function Status() {
  const { info, submitted } = useEpisode();
  return <p data-testid="status">{`${info?.status ?? "loading"} ${submitted ? "ended" : "live"}`}</p>;
}

describe("the end of an episode", () => {
  it("never reopens: a reload that reads 'open' after a submit keeps it submitted", async () => {
    const fake = new FakeApi();
    const { user } = await renderEpisode(
      <>
        <Status />
        <SubmitView />
      </>,
      fake,
    );
    await user.type(screen.getByRole("textbox", { name: /summary/i }), "Refunded the order.");
    await user.click(screen.getByRole("button", { name: "Submit and grade" }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("submitted ended"));

    fake.status = "open"; // a stale answer, as from a reload that started before the submit landed
    fireEvent.focus(window);

    await waitFor(() => expect(fake.count("GET", "/episodes/0123456789abcdef0123456789abcdef/trajectory")).toBe(2));
    expect(screen.getByTestId("status").textContent).toBe("submitted ended");
  });

  it("shows an episode cut off at the call cap as ended, with its grade, without another call", async () => {
    const fake = new FakeApi();
    fake.maxCalls = 2;
    const { user } = await renderEpisode(
      <>
        <Status />
        <ToolForm tool="get_policy" submitLabel="Read" />
      </>,
      fake,
    );
    await user.click(screen.getByRole("button", { name: "Read" }));
    expect(screen.getByTestId("status").textContent).toBe("open live");

    await user.click(screen.getByRole("button", { name: "Read" })); // the second call reaches the cap

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("cut_off ended"));
    expect(fake.argsSent("get_policy")).toHaveLength(2);
    expect(fake.count("GET", "/episodes/0123456789abcdef0123456789abcdef/grade")).toBeGreaterThan(0);
  });
});
