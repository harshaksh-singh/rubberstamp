"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { EpisodeProvider, useEpisode } from "@/lib/episode";
import { approvalsSeen } from "@/lib/ledger";

import { TimelineItem } from "./call-view";
import { ThemeToggle } from "./theme";
import { Badge, Button, cn, EmptyState, Mono, Notice, Spinner } from "./ui";

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const { episodeId } = useParams<{ episodeId: string }>();
  return (
    <EpisodeProvider key={episodeId} id={episodeId}>
      <Frame>{children}</Frame>
    </EpisodeProvider>
  );
}

interface NavItem {
  href: string;
  label: string;
  count?: number;
  tone?: "warn" | "accent";
}

function useNav(): NavItem[] {
  const { id, calls, submitted } = useEpisode();
  const base = `/episodes/${id}`;
  const pending = approvalsSeen(calls).filter((a) => a.row.status === "pending").length;
  return [
    { href: base, label: "Overview" },
    { href: `${base}/tickets`, label: "Tickets" },
    { href: `${base}/customers`, label: "Customers" },
    { href: `${base}/orders`, label: "Orders" },
    { href: `${base}/refunds`, label: "Refunds" },
    { href: `${base}/approvals`, label: "Approvals", count: pending || undefined, tone: "warn" },
    { href: `${base}/policies`, label: "Policies" },
    { href: `${base}/tools`, label: "All tools" },
    { href: `${base}/activity`, label: "Activity", count: calls.length || undefined },
    { href: `${base}/submit`, label: submitted ? "Verdict" : "Submit", tone: "accent" },
  ];
}

function NavLinks({ items, orientation }: { items: NavItem[]; orientation: "vertical" | "horizontal" }) {
  const pathname = usePathname();
  const vertical = orientation === "vertical";
  return (
    <ul className={cn(vertical ? "space-y-0.5" : "flex gap-1 overflow-x-auto pb-px")}>
      {items.map((item) => {
        const active = pathname === item.href;
        return (
          <li key={item.href} className="shrink-0">
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                active ? "bg-panel font-medium text-fg shadow-[0_0_0_1px_var(--line)]" : "text-fg-muted hover:bg-muted hover:text-fg",
              )}
            >
              <span>{item.label}</span>
              {item.count ? (
                <Badge tone={item.tone === "warn" ? "warn" : "neutral"}>
                  <span className="sr-only">(</span>
                  {item.count}
                  <span className="sr-only">)</span>
                </Badge>
              ) : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  const episode = useEpisode();
  const nav = useNav();
  const { info, phase, grade, pending, submitted, id } = episode;
  const onVerdict = usePathname() === `/episodes/${id}/submit`;

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[224px_minmax(0,1fr)]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-accent px-3 py-2 text-accent-fg focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-line bg-subtle lg:flex" aria-label="Console">
        <div className="px-4 pt-4 pb-3">
          <Link href="/" className="block text-[13px] font-semibold text-fg">
            Helpdesk console
          </Link>
          <p className="text-[11.5px] text-fg-subtle">RubberStamp · UI/API parity</p>
        </div>
        <nav aria-label="Views" className="flex-1 overflow-y-auto px-2">
          <NavLinks items={nav} orientation="vertical" />
        </nav>
        <div className="space-y-2 border-t border-line px-3 py-3">
          <ThemeToggle />
          <p className="truncate px-2 text-[11px] text-fg-subtle" title={api.base}>
            API <Mono className="text-[11px]">{api.base}</Mono>
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur supports-[backdrop-filter]:bg-bg/75">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 sm:px-6">
            <Link href="/" className="text-[13px] font-semibold text-fg lg:hidden">
              Helpdesk
            </Link>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="truncate text-[13px] font-medium text-fg">{info?.task.title ?? "Episode"}</span>
              {info ? (
                <>
                  <Mono className="text-fg-subtle">{info.task.id}</Mono>
                  <Badge>seed {info.seed}</Badge>
                  <Badge>{info.persona.id} · {info.persona.role}</Badge>
                </>
              ) : null}
              {submitted && grade ? (
                <Badge tone={grade.strict.passed ? "ok" : "bad"}>submitted · strict {grade.strict.passed ? "pass" : "fail"}</Badge>
              ) : phase === "ready" ? (
                <Badge tone="accent">open</Badge>
              ) : null}
              {pending > 0 ? <Spinner label="Calling" /> : null}
            </div>
            <div className="flex items-center gap-2">
              <span className="lg:hidden">
                <ThemeToggle />
              </span>
              <Link
                href={`/episodes/${id}/submit`}
                className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-[13px] font-medium text-accent-fg hover:bg-accent-hover"
              >
                {submitted ? "Verdict" : "Submit resolution"}
              </Link>
            </div>
          </div>
          <nav aria-label="Views" className="px-3 pb-2 lg:hidden">
            <NavLinks items={nav} orientation="horizontal" />
          </nav>
        </header>

        <div className="min-w-0 flex-1 xl:grid xl:grid-cols-[minmax(0,1fr)_320px]">
          <main id="main" tabIndex={-1} className="min-w-0 px-4 py-5 outline-none sm:px-6">
            {phase === "loading" ? (
              <Spinner label="Loading the episode" />
            ) : phase === "missing" ? (
              <EmptyState title="This episode is not in the API's memory">
                <p>Episodes live in rs-api&apos;s memory until it restarts. Start a new one from the task list.</p>
                <p className="mt-3">
                  <Link className="text-accent underline" href="/">
                    Choose a task
                  </Link>
                </p>
              </EmptyState>
            ) : phase === "error" ? (
              <div className="max-w-xl space-y-3">
                <Notice tone="bad" title="Could not load the episode">
                  {episode.error}
                </Notice>
                <Button onClick={() => window.location.reload()}>Try again</Button>
              </div>
            ) : (
              <>
                {submitted && !onVerdict ? (
                  <div className="mb-4">
                    <Notice tone={grade?.strict.passed ? "ok" : "warn"} title="This episode was submitted and graded">
                      It ended at submit_resolution and takes no more calls; the views show what its calls returned.{" "}
                      <Link className="text-accent underline" href={`/episodes/${id}/submit`}>
                        See the verdict
                      </Link>
                    </Notice>
                  </div>
                ) : null}
                {children}
              </>
            )}
          </main>
          <ActivityRail />
        </div>
      </div>
    </div>
  );
}

function ActivityRail() {
  const { calls, tools, phase } = useEpisode();
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = list.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [calls.length]);
  return (
    <aside
      aria-label="Activity"
      className="sticky top-[57px] hidden h-[calc(100dvh-57px)] flex-col border-l border-line bg-subtle xl:flex"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="text-[13px] font-semibold text-fg">Activity</h2>
        <span className="text-[12px] text-fg-subtle">{calls.length} tool calls</span>
      </div>
      <div ref={list} className="flex-1 overflow-y-auto px-3 py-3">
        {phase !== "ready" ? null : calls.length === 0 ? (
          <p className="px-1 text-[12.5px] text-fg-muted">
            No tool calls yet. Every search, lookup and action you take appears here, as the agent-facing tool call it
            is.
          </p>
        ) : (
          <ol aria-label="Tool calls" aria-live="polite" className="space-y-2.5 border-l border-line">
            {calls.map((call) => (
              <TimelineItem key={call.idx} call={call} spec={tools.get(call.tool)} compact />
            ))}
          </ol>
        )}
      </div>
    </aside>
  );
}
