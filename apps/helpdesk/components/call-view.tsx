"use client";

import { brief, pretty } from "@/lib/format";
import type { Json, ToolCall, ToolSpec } from "@/lib/types";

import { Badge, cn } from "./ui";

export function JsonBlock({ value, label }: { value: Json; label?: string }) {
  return (
    <pre
      aria-label={label}
      className="max-h-80 overflow-auto rounded-md border border-line bg-subtle p-2.5 font-mono text-[11.5px] leading-relaxed text-fg"
    >
      {pretty(value)}
    </pre>
  );
}

export function EffectBadge({ spec }: { spec: ToolSpec | undefined }) {
  if (!spec) return <Badge>unknown</Badge>;
  return spec.effect === "write" ? <Badge tone="accent">write</Badge> : <Badge>read</Badge>;
}

/** What one recorded call returned: the result, or the refusal text an agent would get for the same call. */
export function CallOutcome({ call }: { call: ToolCall }) {
  if (!call.ok) {
    return (
      <div role="alert" className="rounded-md border border-bad/40 bg-bad-soft px-3 py-2 text-[13px]">
        <p className="font-medium text-bad">
          Refused <span className="font-mono text-[12px]">#{call.idx} {call.tool}</span>
        </p>
        <p className="mt-0.5 font-mono text-[12px] break-words text-fg">{call.error}</p>
      </div>
    );
  }
  return (
    <div role="status" className="rounded-md border border-ok/40 bg-ok-soft px-3 py-2 text-[13px]">
      <p className="font-medium text-ok">
        Done <span className="font-mono text-[12px]">#{call.idx} {call.tool}</span>
      </p>
      <details className="mt-1">
        <summary className="cursor-pointer text-[12px] text-fg-muted">Result</summary>
        <div className="mt-1.5">
          <JsonBlock value={call.result} label={`Result of call ${call.idx}`} />
        </div>
      </details>
    </div>
  );
}

/** One call in the activity timeline: tool, effect, outcome, arguments; expands to the full record. */
export function TimelineItem({ call, spec, compact }: { call: ToolCall; spec: ToolSpec | undefined; compact?: boolean }) {
  const write = spec?.effect === "write";
  const entries = Object.entries(call.args);
  return (
    <li className="relative pl-4">
      <span
        aria-hidden
        className={cn(
          "absolute top-1.5 -left-[5.5px] size-2.5 rounded-full border-2",
          !call.ok ? "border-bad bg-bad-soft" : write ? "border-accent bg-accent" : "border-line-strong bg-panel",
        )}
      />
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-0.5 rounded px-1 py-0.5 hover:bg-muted [&::-webkit-details-marker]:hidden">
          <span className="font-mono text-[11px] text-fg-subtle">#{call.idx}</span>
          <span className="font-mono text-[12px] font-medium text-fg">{call.tool}</span>
          <EffectBadge spec={spec} />
          {call.ok ? <Badge tone="ok">ok</Badge> : <Badge tone="bad">refused</Badge>}
        </summary>
        <div className="mt-1.5 space-y-1.5 px-1 pb-1">
          <p className="text-[11px] font-medium tracking-wide text-fg-subtle uppercase">Arguments</p>
          <JsonBlock value={call.args} label={`Arguments of call ${call.idx}`} />
          <p className="text-[11px] font-medium tracking-wide text-fg-subtle uppercase">{call.ok ? "Result" : "Error"}</p>
          {call.ok ? (
            <JsonBlock value={call.result} label={`Result of call ${call.idx}`} />
          ) : (
            <p className="rounded-md border border-bad/40 bg-bad-soft p-2 font-mono text-[12px] text-fg">{call.error}</p>
          )}
        </div>
      </details>
      {entries.length > 0 && !compact ? (
        <p className="mt-0.5 truncate px-1 font-mono text-[11.5px] text-fg-muted">
          {entries.map(([key, value]) => `${key}=${brief(value, 40)}`).join("  ")}
        </p>
      ) : null}
      {entries.length > 0 && compact ? (
        <p className="mt-0.5 truncate px-1 font-mono text-[11px] text-fg-subtle">
          {entries.map(([, value]) => brief(value, 24)).join(" · ")}
        </p>
      ) : null}
      {!call.ok && compact ? <p className="mt-0.5 px-1 text-[11.5px] text-bad">{brief(call.error, 90)}</p> : null}
    </li>
  );
}
