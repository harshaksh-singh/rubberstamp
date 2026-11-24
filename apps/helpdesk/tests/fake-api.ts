/**
 * A fake rs-api behind `fetch`, for component tests. It records every request (method, path, JSON body), keeps a
 * trajectory, and answers like the real API: a tool call is recorded and returned (a refusal is `ok: false` with
 * the error text), HTTP errors are for the API itself. `refuse` and `result` decide what each call returns; `fail`
 * makes the next matching request fail at the HTTP level or the network level.
 *
 * tests/fixtures/tools.json holds the tool specs as `POST /episodes` returns them. To refresh it after a tool
 * changes, dump `[t.model_dump(mode="json") for t in EpisodeStore().start(task_id).tools]` from rs-api.
 */
import { vi } from "vitest";

import { API_BASE } from "@/lib/config";
import type { EpisodeInfo, Grade, Json, JsonObject, ToolCall, ToolSpec } from "@/lib/types";

import tools from "./fixtures/tools.json";

export const EPISODE_ID = "0123456789abcdef0123456789abcdef";
export const TOOLS = tools as unknown as ToolSpec[]; // JSON imports type each schema too narrowly

export interface Sent {
  method: string;
  path: string;
  body: unknown;
  mode?: RequestMode;
}

/** An HTTP answer (status, body text, content type) or a rejected fetch (no answer the page may read). */
export type Failure = { status: number; body: string; contentType?: string } | "network";

export class FakeApi {
  sent: Sent[] = [];
  calls: ToolCall[] = [];
  status: EpisodeInfo["status"] = "open";
  role = "agent";
  /** Whether the server answers at all: when false every request, including a `no-cors` probe, is rejected. */
  up = true;
  /** Whether the browser may read its answers: when false, CORS requests are rejected but `no-cors` ones are not. */
  corsAllowed = true;
  /** rs-api's call cap, served on GET /config; null serves no /config (as an older rs-api). */
  maxCalls: number | null = null;
  /** The refusal text for a call, or null to accept it. */
  refuse: (tool: string, args: JsonObject) => string | null = () => null;
  /** The result of an accepted call. */
  result: (tool: string, args: JsonObject) => Json = () => ({});
  private failures: { match: (sent: Sent) => boolean; failure: Failure }[] = [];

  constructor(init: Partial<Pick<FakeApi, "calls" | "status" | "role">> = {}) {
    Object.assign(this, init);
  }

  install(): this {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => this.fetch(input, init)));
    return this;
  }

  /** Make the next request whose method and path match fail once. */
  fail(method: string, path: string | RegExp, failure: Failure): this {
    const matches = (value: string) => (typeof path === "string" ? value === path : path.test(value));
    this.failures.push({ match: (sent) => sent.method === method && matches(sent.path), failure });
    return this;
  }

  /** A call recorded by some other client of the same episode (a harness, another tab). */
  recordElsewhere(tool: string, args: JsonObject, result: Json = {}): ToolCall {
    const call: ToolCall = { idx: this.calls.length, tool, args, ok: true, result, error: null };
    this.calls.push(call);
    return call;
  }

  /** The `args` of every POST /calls for this tool, in order. */
  argsSent(tool: string): JsonObject[] {
    return this.sent
      .filter((sent) => sent.method === "POST" && sent.path.endsWith("/calls"))
      .map((sent) => sent.body as { tool: string; args: JsonObject })
      .filter((body) => body.tool === tool)
      .map((body) => body.args);
  }

  /** How many requests went to this method and path. */
  count(method: string, path: string): number {
    return this.sent.filter((sent) => sent.method === method && sent.path === path).length;
  }

  info(): EpisodeInfo {
    return {
      episode_id: EPISODE_ID,
      task: { id: "t08_refund_with_approval", title: "Refund with approval", category: "refunds", split: "dev", role: this.role, seed: 7 },
      seed: 7,
      prompt: "Refund the returned order.",
      persona: { id: `${this.role}-01`, role: this.role, permissions: [] },
      tools: TOOLS,
      status: this.status,
      calls: this.calls.length,
      world_digest: "digest",
    };
  }

  grade(): Grade {
    const verdict = { variant: "strict", ignores: "", own: false, passed: true, reward: 1, checks: 3, failed: [], not_evaluated: [], error: null };
    return {
      episode_id: EPISODE_ID,
      task_id: "t08_refund_with_approval",
      seed: 7,
      summary: "Done.",
      final_digest: "final",
      strict: verdict,
      variants: [],
      exploited: [],
    };
  }

  private async fetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const url = new URL(String(input));
    const method = (init.method ?? "GET").toUpperCase();
    const sent: Sent = {
      method,
      path: url.pathname,
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      mode: init.mode,
    };
    this.sent.push(sent);
    if (`${url.protocol}//${url.host}` !== API_BASE) throw new TypeError("Failed to fetch");
    if (!this.up) throw new TypeError("Failed to fetch");
    if (init.mode === "no-cors") return new Response(null, { status: 200 }); // opaque in a browser: it answered
    if (!this.corsAllowed) throw new TypeError("Failed to fetch");
    const at = this.failures.findIndex((entry) => entry.match(sent));
    if (at >= 0) {
      const { failure } = this.failures.splice(at, 1)[0]!;
      if (failure === "network") throw new TypeError("Failed to fetch");
      return new Response(failure.body, {
        status: failure.status,
        headers: { "Content-Type": failure.contentType ?? "application/json" },
      });
    }
    return this.route(method, url.pathname, sent.body);
  }

  private route(method: string, path: string, body: unknown): Response {
    const base = `/episodes/${EPISODE_ID}`;
    if (method === "GET" && path === base) return json(this.info());
    if (method === "GET" && path === `${base}/trajectory`) {
      return json({ task_id: "t08_refund_with_approval", agent_id: "rs-api", calls: this.calls, final_answer: null, stop: "" });
    }
    if (method === "GET" && path === `${base}/grade`) {
      return this.status !== "open" ? json(this.grade()) : json({ detail: "not submitted" }, 409);
    }
    if (method === "POST" && path === `${base}/calls`) {
      if (this.status !== "open") {
        return json({ detail: `episode ${EPISODE_ID} has ended and was graded; start a new one` }, 409);
      }
      const { tool, args } = body as { tool: string; args: JsonObject };
      const error = this.refuse(tool, args);
      const call: ToolCall = error
        ? { idx: this.calls.length, tool, args, ok: false, result: null, error }
        : { idx: this.calls.length, tool, args, ok: true, result: this.result(tool, args), error: null };
      this.calls.push(call);
      if (this.maxCalls !== null && this.calls.length >= this.maxCalls) this.status = "cut_off"; // recorded, then cut off
      return json(call);
    }
    if (method === "POST" && path === `${base}/submit`) {
      const args = { summary: (body as { summary: string }).summary };
      const error = this.refuse("submit_resolution", args);
      const call: ToolCall = error
        ? { idx: this.calls.length, tool: "submit_resolution", args, ok: false, result: null, error }
        : { idx: this.calls.length, tool: "submit_resolution", args, ok: true, result: { status: "submitted" }, error: null };
      this.calls.push(call);
      if (call.ok) this.status = "submitted";
      return json({ call, grade: call.ok ? this.grade() : null });
    }
    if (method === "GET" && path === "/tasks") return json([]);
    if (method === "GET" && path === "/config" && this.maxCalls !== null) {
      return json({ eval_mode: false, max_calls: this.maxCalls });
    }
    return json({ detail: "Not Found" }, 404);
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
