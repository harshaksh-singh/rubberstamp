"use client";

import Link from "next/link";

import { CLAIM_TOOL, useEpisode } from "@/lib/episode";

import { EffectBadge } from "../call-view";
import { ToolForm } from "../tool-form";
import { Badge, PageHeader } from "../ui";

/** Every tool of the task, each with a form generated from its spec: the whole agent-facing surface, generically. */
export function ToolsView() {
  const { info, id } = useEpisode();
  if (!info) return null;
  const ordered = [...info.tools].sort(
    (a, b) => Number(a.effect === "write") - Number(b.effect === "write") || a.name.localeCompare(b.name),
  );
  return (
    <>
      <PageHeader
        title="All tools"
        description="The tools exactly as agents see them (name, description, JSON Schema), each with a form generated from its schema. The views call the same tools; this page reaches any of them directly."
      />
      <div className="space-y-2">
        {ordered.map((spec) => (
          <details key={spec.name} id={spec.name} className="group rounded-lg border border-line bg-panel">
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-4 py-2.5 [&::-webkit-details-marker]:hidden">
              <span aria-hidden className="text-fg-subtle transition-transform group-open:rotate-90 motion-reduce:transition-none">
                ›
              </span>
              <span className="font-mono text-[13px] font-medium text-fg">{spec.name}</span>
              <EffectBadge spec={spec} />
              <Badge>{spec.app}</Badge>
              {spec.roles.length === 1 ? <Badge tone="warn">{spec.roles[0]} only</Badge> : null}
              {spec.effect === "write" && !spec.idempotent ? <Badge>not idempotent</Badge> : null}
              <span className="basis-full truncate pl-5 text-[12.5px] text-fg-muted">{spec.description.split("\n")[0]}</span>
            </summary>
            <div className="border-t border-line px-4 py-3">
              {spec.name === CLAIM_TOOL ? (
                <p className="text-[13px] text-fg-muted">
                  {spec.description}{" "}
                  <Link href={`/episodes/${id}/submit`} className="text-accent underline">
                    Submit a resolution
                  </Link>{" "}
                  to call it and see every grader&apos;s verdict.
                </p>
              ) : (
                <ToolForm tool={spec.name} showDescription />
              )}
            </div>
          </details>
        ))}
      </div>
    </>
  );
}
