"use client";

import Link from "next/link";

import { useEpisode } from "@/lib/episode";

import { EffectBadge } from "../call-view";
import { Badge, KeyValues, Mono, PageHeader, Panel, PanelHeader } from "../ui";

export function OverviewView() {
  const { info, id, calls } = useEpisode();
  if (!info) return null;
  const reads = info.tools.filter((tool) => tool.effect === "read");
  const writes = info.tools.filter((tool) => tool.effect === "write");
  const base = `/episodes/${id}`;

  return (
    <>
      <PageHeader
        title={info.task.title}
        description="Read the request, look up what it names, act, then submit a resolution. Every lookup and action is a recorded tool call."
      />
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Panel aria-labelledby="prompt-title">
          <PanelHeader id="prompt-title" title="Request" meta={<Badge tone="accent">the prompt an agent receives</Badge>} />
          <div className="prose-prompt px-4 py-3 text-[14px] leading-relaxed text-fg">
            {info.prompt.split(/\n{2,}/).map((paragraph, i) => (
              <p key={i}>{paragraph}</p>
            ))}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel aria-labelledby="episode-title">
            <PanelHeader id="episode-title" title="Episode" />
            <div className="px-4 py-3">
              <KeyValues
                items={[
                  ["Task", <Mono key="t">{info.task.id}</Mono>],
                  ["Category", info.task.category.replaceAll("_", " ")],
                  ["Split", info.task.split],
                  ["World seed", `${info.seed}${info.seed === info.task.seed ? " (the task's own)" : ""}`],
                  ["Signed in as", `${info.persona.id} (${info.persona.role})`],
                  ["Status", info.status === "cut_off" ? "cut off at the call cap" : info.status],
                  ["Tool calls", String(calls.length)],
                  ["Episode", <Mono key="e" className="break-all">{info.episode_id}</Mono>],
                  ["Seeded world", <Mono key="w" className="break-all text-fg-muted">{info.world_digest}</Mono>],
                ]}
              />
            </div>
          </Panel>

          <Panel aria-labelledby="tools-title">
            <PanelHeader
              id="tools-title"
              title="Tools"
              meta={<span className="text-[12px] text-fg-subtle">{info.tools.length} in this task</span>}
              actions={
                <Link href={`${base}/tools`} className="text-[12.5px] text-accent hover:underline">
                  All tools
                </Link>
              }
            />
            <div className="grid gap-4 px-4 py-3 sm:grid-cols-2">
              {[
                ["Reads", reads],
                ["Writes", writes],
              ].map(([label, list]) => (
                <div key={label as string}>
                  <h3 className="mb-1.5 text-[12px] font-medium tracking-wide text-fg-subtle uppercase">{label as string}</h3>
                  <ul className="space-y-1">
                    {(list as typeof reads).map((tool) => (
                      <li key={tool.name} className="flex items-center gap-2">
                        <EffectBadge spec={tool} />
                        <Link href={`${base}/tools#${tool.name}`} className="font-mono text-[12.5px] text-fg hover:text-accent">
                          {tool.name}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
