"use client";

import { useState } from "react";

import { useRead } from "@/lib/episode";
import { when } from "@/lib/format";
import { SLOTS, useOpen } from "@/lib/open";
import { resultOf, type TicketDetail, type TicketSummary } from "@/lib/records";

import { CallOutcome } from "../call-view";
import { ToolForm } from "../tool-form";
import { Badge, Button, cn, EmptyState, KeyValues, Mono, PageHeader, Panel, PanelHeader, RowList, Spinner, StatusBadge } from "../ui";

export function TicketsView() {
  const list = useRead(SLOTS.ticketList, "list_tickets");
  const detail = useRead(SLOTS.ticket, "get_ticket");
  const tickets = resultOf<{ tickets: TicketSummary[] }>(list.call, "tickets")?.tickets;
  const open = resultOf<TicketDetail>(detail.call, "ticket");

  return (
    <>
      <PageHeader title="Tickets" description="The support queue. Searching runs list_tickets; opening a ticket runs get_ticket." />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel aria-labelledby="queue-title" className="min-w-0 self-start">
          <PanelHeader id="queue-title" title="Queue" meta={tickets ? <Badge>{tickets.length}</Badge> : null} />
          <div className="space-y-3 border-b border-line px-4 py-3">
            <ToolForm tool="list_tickets" layout="inline" submitLabel="Search" quiet onDone={list.setCall} />
            <ToolForm tool="get_ticket" layout="inline" submitLabel="Open" quiet onDone={detail.setCall} />
          </div>
          {!tickets ? (
            <EmptyState title="No search yet">Search the queue, or open a ticket by its id.</EmptyState>
          ) : tickets.length === 0 ? (
            <EmptyState title="No tickets match" />
          ) : (
            <RowList
              rows={tickets}
              rowKey={(t) => t.id}
              selected={open?.ticket.id}
              onOpen={(t) => detail.read({ ticket_id: t.id })}
              label={(t) => `Open ticket ${t.id}: ${t.subject}, ${t.status}, ${t.priority} priority`}
              render={(t) => (
                <div className="grid gap-0.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-fg">{t.subject}</span>
                    <StatusBadge value={t.status} />
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 text-[12px] text-fg-subtle">
                    <Mono>{t.id}</Mono>
                    <span>{t.customer_id}</span>
                    {t.order_id ? <span>{t.order_id}</span> : null}
                    <span className={cn(t.priority === "urgent" && "text-bad", t.priority === "high" && "text-warn")}>{t.priority}</span>
                    {t.escalated ? <span>escalated</span> : null}
                    <span>{when(t.opened_at)}</span>
                  </div>
                </div>
              )}
            />
          )}
        </Panel>

        <div className="min-w-0">
          {detail.busy && !open ? (
            <Spinner label="Opening ticket" />
          ) : detail.failure ? (
            <p className="text-[13px] text-bad">{detail.failure}</p>
          ) : detail.call && !detail.call.ok ? (
            <CallOutcome call={detail.call} />
          ) : open ? (
            <TicketCard detail={open} refresh={() => detail.read({ ticket_id: open.ticket.id })} />
          ) : (
            <Panel>
              <EmptyState title="No ticket open">Choose a ticket from the queue.</EmptyState>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

const ACTIONS = [
  { key: "reply", label: "Reply" },
  { key: "note", label: "Internal note" },
  { key: "status", label: "Status" },
  { key: "escalate", label: "Escalate" },
] as const;

function TicketCard({ detail, refresh }: { detail: TicketDetail; refresh: () => void }) {
  const { ticket, comments } = detail;
  const openRecord = useOpen();
  const [action, setAction] = useState<(typeof ACTIONS)[number]["key"]>("reply");
  const afterWrite = (call: { ok: boolean }) => {
    if (call.ok) refresh();
  };

  return (
    <Panel aria-labelledby="ticket-title">
      <PanelHeader
        id="ticket-title"
        title={ticket.subject}
        meta={
          <>
            <Mono className="text-fg-subtle">{ticket.id}</Mono>
            <StatusBadge value={ticket.status} />
            <StatusBadge value={ticket.priority} />
            {ticket.escalated ? <Badge tone="warn">escalated · {ticket.queue}</Badge> : <Badge>{ticket.queue}</Badge>}
          </>
        }
        actions={
          <Button size="sm" variant="ghost" onClick={refresh} aria-label={`Reload ticket ${ticket.id} (get_ticket)`}>
            Reload
          </Button>
        }
      />
      <div className="space-y-5 px-4 py-4">
        <KeyValues
          items={[
            [
              "Customer",
              <Button
                key="c"
                size="sm"
                variant="ghost"
                className="-ml-2 font-mono"
                onClick={() => openRecord("customers", SLOTS.customer, "get_customer", { customer_id: ticket.customer_id })}
                aria-label={`Open customer ${ticket.customer_id} (get_customer)`}
              >
                {ticket.customer_id}
              </Button>,
            ],
            [
              "Order",
              ticket.order_id ? (
                <Button
                  key="o"
                  size="sm"
                  variant="ghost"
                  className="-ml-2 font-mono"
                  onClick={() => openRecord("orders", SLOTS.order, "get_order", { order_id: ticket.order_id })}
                  aria-label={`Open order ${ticket.order_id} (get_order)`}
                >
                  {ticket.order_id}
                </Button>
              ) : (
                "—"
              ),
            ],
            ["Category", ticket.category],
            ["Opened", when(ticket.opened_at)],
            ["First response", when(ticket.first_response_at)],
            ["Resolved", when(ticket.resolved_at)],
          ]}
        />

        <section aria-labelledby="thread-title">
          <h3 id="thread-title" className="mb-2 text-[12px] font-medium tracking-wide text-fg-subtle uppercase">
            Conversation · {comments.length}
          </h3>
          {comments.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No comments yet.</p>
          ) : (
            <ol className="space-y-2.5">
              {comments.map((comment) => (
                <li
                  key={comment.id}
                  className={cn(
                    "rounded-md border px-3 py-2",
                    comment.public ? "border-line bg-panel" : "border-warn/40 bg-warn-soft",
                  )}
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-[12px]">
                    <span className="font-medium text-fg">{comment.author}</span>
                    {comment.public ? <Badge tone="accent">public reply</Badge> : <Badge tone="warn">internal note</Badge>}
                    <span className="text-fg-subtle">{when(comment.created_at)}</span>
                    <Mono className="text-fg-subtle">{comment.id}</Mono>
                  </div>
                  <p className="text-[13px] whitespace-pre-wrap text-fg">{comment.body}</p>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-labelledby="ticket-actions-title" className="rounded-md border border-line">
          <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1.5">
            <h3 id="ticket-actions-title" className="sr-only">
              Actions on this ticket
            </h3>
            {ACTIONS.map((a) => (
              <Button
                key={a.key}
                size="sm"
                variant={action === a.key ? "secondary" : "ghost"}
                aria-pressed={action === a.key}
                onClick={() => setAction(a.key)}
              >
                {a.label}
              </Button>
            ))}
          </div>
          <div className="px-3 py-3">
            {action === "reply" ? (
              <ToolForm key={`reply-${ticket.id}`} tool="add_ticket_comment" fixed={{ ticket_id: ticket.id, public: true }} submitLabel="Send reply" onDone={afterWrite} />
            ) : action === "note" ? (
              <ToolForm key={`note-${ticket.id}`} tool="add_ticket_comment" fixed={{ ticket_id: ticket.id, public: false }} submitLabel="Add note" onDone={afterWrite} />
            ) : action === "status" ? (
              <ToolForm key={`status-${ticket.id}`} tool="update_ticket_status" fixed={{ ticket_id: ticket.id }} submitLabel="Set status" onDone={afterWrite} />
            ) : (
              <ToolForm key={`escalate-${ticket.id}`} tool="escalate_ticket" fixed={{ ticket_id: ticket.id }} submitLabel="Escalate to tier 2" onDone={afterWrite} />
            )}
          </div>
        </section>
      </div>
    </Panel>
  );
}
