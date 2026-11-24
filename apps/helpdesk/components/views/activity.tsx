"use client";

import { useState } from "react";

import { api, messageOf } from "@/lib/api";
import { useEpisode } from "@/lib/episode";

import { TimelineItem } from "../call-view";
import { ApiFailure, Button, EmptyState, PageHeader, Panel } from "../ui";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "read", label: "Reads" },
  { key: "write", label: "Writes" },
  { key: "refused", label: "Refused" },
] as const;

type Filter = (typeof FILTERS)[number]["key"];

export function ActivityView() {
  const { id, calls, tools } = useEpisode();
  const [filter, setFilter] = useState<Filter>("all");
  const [downloading, setDownloading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const shown = calls.filter((call) => {
    const effect = tools.get(call.tool)?.effect;
    if (filter === "refused") return !call.ok;
    return filter === "all" || effect === filter;
  });

  /** The trajectory as rs-api records it (GET /trajectory), the same file an API client would save. */
  async function download() {
    setDownloading(true);
    setFailure(null);
    try {
      const trajectory = await api.trajectory(id);
      const url = URL.createObjectURL(new Blob([JSON.stringify(trajectory, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `trajectory-${trajectory.task_id}-${id.slice(0, 8)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (exc) {
      setFailure(messageOf(exc));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Activity"
        description="Every tool call of this episode, in order, as rs-api recorded it: arguments, then the result or the refusal. This is the trajectory the graders read."
        actions={
          <Button onClick={download} disabled={downloading}>
            {downloading ? "Preparing…" : "Download trajectory (JSON)"}
          </Button>
        }
      />
      {failure ? (
        <div className="mb-3">
          <ApiFailure title="Could not download the trajectory" message={failure} />
        </div>
      ) : null}
      <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "secondary" : "ghost"} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
            {f.label}
          </Button>
        ))}
      </div>
      <Panel className="px-4 py-4">
        {shown.length === 0 ? (
          <EmptyState title={calls.length === 0 ? "No tool calls yet" : "No call matches"} />
        ) : (
          <ol aria-label="Tool calls" className="space-y-3 border-l border-line">
            {shown.map((call) => (
              <TimelineItem key={call.idx} call={call} spec={tools.get(call.tool)} />
            ))}
          </ol>
        )}
      </Panel>
    </>
  );
}
