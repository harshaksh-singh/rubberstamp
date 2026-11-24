import { API_BASE } from "./config";
import type {
  EpisodeInfo,
  Grade,
  Health,
  JsonObject,
  SubmitResult,
  TaskInfo,
  ToolCall,
  Trajectory,
  VariantInfo,
} from "./types";

/** An HTTP error from rs-api itself (404, 409, 422) or no answer at all (status 0). Tool refusals are not errors. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : JSON.stringify(item)))
        .join("; ");
    }
  }
  return fallback;
}

async function request<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach rs-api at ${API_BASE}. Start it with \`uv run rs-api\`.`);
  }
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(parsed, `${method} ${path} failed with HTTP ${response.status}`));
  }
  return parsed as T;
}

export const api = {
  base: API_BASE,
  health: () => request<Health>("GET", "/health"),
  tasks: () => request<TaskInfo[]>("GET", "/tasks"),
  variants: () => request<VariantInfo[]>("GET", "/variants"),
  start: (taskId: string, seed?: number) =>
    request<EpisodeInfo>("POST", "/episodes", seed === undefined ? { task_id: taskId } : { task_id: taskId, seed }),
  episode: (id: string) => request<EpisodeInfo>("GET", `/episodes/${id}`),
  trajectory: (id: string) => request<Trajectory>("GET", `/episodes/${id}/trajectory`),
  /** The only way to read or write the world: one tool call, recorded in the episode's trajectory. */
  call: (id: string, tool: string, args: JsonObject) =>
    request<ToolCall>("POST", `/episodes/${id}/calls`, { tool, args }),
  submit: (id: string, summary: string) => request<SubmitResult>("POST", `/episodes/${id}/submit`, { summary }),
  grade: (id: string) => request<Grade>("GET", `/episodes/${id}/grade`),
};
