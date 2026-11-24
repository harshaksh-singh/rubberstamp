import { describe, expect, it } from "vitest";

import { api, ApiError } from "@/lib/api";
import { API_BASE } from "@/lib/config";

import { EPISODE_ID, FakeApi } from "./fake-api";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (exc) {
    if (exc instanceof ApiError) return exc;
    throw exc;
  }
  throw new Error("expected an ApiError");
}

describe("api", () => {
  it("sends a tool call as {tool, args} and returns the recorded call", async () => {
    const fake = new FakeApi().install();
    fake.result = () => ({ orders: [] });
    const call = await api.call(EPISODE_ID, "search_orders", { status: "delivered" });
    expect(call).toMatchObject({ idx: 0, tool: "search_orders", ok: true, result: { orders: [] } });
    expect(fake.sent).toEqual([
      { method: "POST", path: `/episodes/${EPISODE_ID}/calls`, body: { tool: "search_orders", args: { status: "delivered" } }, mode: undefined },
    ]);
  });

  it("reads FastAPI's detail, a string or a list of validation errors", async () => {
    new FakeApi()
      .install()
      .fail("GET", `/episodes/${EPISODE_ID}`, { status: 404, body: JSON.stringify({ detail: "no episode 'x'" }) })
      .fail("POST", "/episodes", { status: 422, body: JSON.stringify({ detail: [{ msg: "Field required" }, { msg: "Input should be a valid integer" }] }) });
    expect(await failure(api.episode(EPISODE_ID))).toMatchObject({ status: 404, message: "HTTP 404: no episode 'x'" });
    expect(await failure(api.start("t01"))).toMatchObject({
      status: 422,
      message: "HTTP 422: Field required; Input should be a valid integer",
    });
  });

  it("keeps the status and the text of an error body that is not JSON", async () => {
    new FakeApi()
      .install()
      .fail("GET", "/tasks", { status: 500, body: "Internal Server Error", contentType: "text/plain" })
      .fail("GET", "/health", { status: 400, body: "Invalid host header", contentType: "text/plain" });
    expect(await failure(api.tasks())).toMatchObject({ status: 500, message: "HTTP 500: Internal Server Error" });
    expect(await failure(api.health())).toMatchObject({ status: 400, message: "HTTP 400: Invalid host header" });
  });

  it("says so when a successful answer is not JSON (the URL is not rs-api)", async () => {
    new FakeApi().install().fail("GET", "/tasks", { status: 200, body: "<!doctype html><title>Helpdesk</title>", contentType: "text/html" });
    const error = await failure(api.tasks());
    expect(error.status).toBe(200);
    expect(error.message).toContain(`is ${API_BASE} rs-api?`);
  });

  it("tells a server that is down from one that answered but was blocked by CORS", async () => {
    const down = new FakeApi().install();
    down.up = false;
    const unreachable = await failure(api.tasks());
    expect(unreachable.status).toBe(0);
    expect(unreachable.message).toContain(`Nothing answered at ${API_BASE}`);
    expect(unreachable.message).toContain("uv run rs-api");

    const blocked = new FakeApi().install();
    blocked.corsAllowed = false;
    const refused = await failure(api.tasks());
    expect(refused.status).toBe(0);
    expect(refused.message).toContain(`rs-api at ${API_BASE} answered, but the browser blocked`);
    expect(refused.message).toContain(window.location.origin);
    expect(refused.message).toContain("RS_API_UI_ORIGINS");
    expect(blocked.sent.map((sent) => [sent.path, sent.mode])).toEqual([
      ["/tasks", undefined],
      ["/health", "no-cors"],
    ]);
  });
});
