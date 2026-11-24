"use client";

import { useEpisode, useRead } from "@/lib/episode";
import { when } from "@/lib/format";
import { emailsSent } from "@/lib/ledger";
import { SLOTS, useOpen } from "@/lib/open";
import { resultOf, type Customer, type CustomerDetail } from "@/lib/records";

import { CallOutcome } from "../call-view";
import { ToolForm } from "../tool-form";
import {
  ApiFailure,
  Badge,
  Button,
  EmptyState,
  KeyValues,
  Mono,
  PageHeader,
  Panel,
  PanelHeader,
  RowList,
  Spinner,
  StatusBadge,
} from "../ui";

export function CustomersView() {
  const { submitted } = useEpisode();
  const list = useRead(SLOTS.customerList, "search_customers");
  const detail = useRead(SLOTS.customer, "get_customer");
  const found = resultOf<{ customers: Pick<Customer, "id" | "name" | "email">[] }>(list.call, "customers")?.customers;
  const open = resultOf<CustomerDetail>(detail.call, "customer");

  return (
    <>
      <PageHeader
        title="Customers"
        description="Search runs search_customers (names are not unique); a lookup by id or exact email runs get_customer."
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel aria-labelledby="customers-title" className="min-w-0 self-start">
          <PanelHeader id="customers-title" title="Directory" meta={found ? <Badge>{found.length}</Badge> : null} />
          <div className="space-y-3 border-b border-line px-4 py-3">
            <ToolForm tool="search_customers" layout="inline" submitLabel="Search" quiet onDone={list.setCall} />
            <ToolForm tool="get_customer" layout="inline" submitLabel="Look up" quiet onDone={detail.setCall} />
          </div>
          {!found ? (
            <EmptyState title="No search yet">Search by name, or look a customer up by id or email.</EmptyState>
          ) : found.length === 0 ? (
            <EmptyState title="No customer matches" />
          ) : (
            <RowList
              rows={found}
              rowKey={(c) => c.id}
              selected={open?.customer.id}
              onOpen={(c) => detail.read({ customer_id: c.id })}
              disabled={submitted}
              label={(c) => `Open customer ${c.id}: ${c.name}, ${c.email}`}
              render={(c) => (
                <div className="grid gap-0.5">
                  <span className="font-medium text-fg">{c.name}</span>
                  <span className="flex flex-wrap gap-x-2 text-[12px] text-fg-subtle">
                    <Mono>{c.id}</Mono>
                    <span className="truncate">{c.email}</span>
                  </span>
                </div>
              )}
            />
          )}
        </Panel>

        <div className="min-w-0 space-y-3">
          {detail.failure ? <ApiFailure title="Could not read the customer" message={detail.failure} /> : null}
          {detail.busy && !open ? (
            <Spinner label="Opening customer" />
          ) : detail.call && !detail.call.ok ? (
            <CallOutcome call={detail.call} />
          ) : open ? (
            <CustomerCard detail={open} />
          ) : (
            <Panel>
              <EmptyState title="No customer open">Choose a customer from the directory.</EmptyState>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

function CustomerCard({ detail }: { detail: CustomerDetail }) {
  const { customer, account } = detail;
  const { calls } = useEpisode();
  const opener = useOpen();
  const sent = emailsSent(calls).filter((email) => email.customer_id === customer.id);

  return (
    <div className="space-y-4">
      <Panel aria-labelledby="customer-title">
        <PanelHeader
          id="customer-title"
          title={customer.name}
          meta={
            <>
              <Mono className="text-fg-subtle">{customer.id}</Mono>
              <StatusBadge value={account.tier} />
            </>
          }
          actions={
            <>
              <Button
                size="sm"
                onClick={() => opener.open("orders", SLOTS.orderList, "search_orders", { customer_id: customer.id })}
                disabled={opener.disabled}
                aria-label={`Orders of ${customer.id} (search_orders)`}
              >
                Orders
              </Button>
              <Button
                size="sm"
                onClick={() => opener.open("tickets", SLOTS.ticketList, "list_tickets", { customer_id: customer.id })}
                disabled={opener.disabled}
                aria-label={`Tickets of ${customer.id} (list_tickets)`}
              >
                Tickets
              </Button>
            </>
          }
        />
        <div className="grid gap-4 px-4 py-4 md:grid-cols-2">
          <KeyValues
            items={[
              ["Email", customer.email],
              ["Phone", customer.phone],
              ["Customer since", when(customer.created_at)],
            ]}
          />
          <KeyValues
            items={[
              ["Account", <Mono key="a">{account.id}</Mono>],
              ["Tier", account.tier],
              ["Status", account.status],
              ["Opened", when(account.opened_at)],
            ]}
          />
          {opener.failure ? (
            <div className="md:col-span-2">
              <ApiFailure title="Could not open the customer's records" message={opener.failure} />
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel aria-labelledby="email-title">
        <PanelHeader id="email-title" title="Email the customer" meta={<span className="text-[12px] text-fg-subtle">sent to the address on file</span>} />
        <div className="px-4 py-3">
          <ToolForm key={customer.id} tool="send_email" fixed={{ customer_id: customer.id }} submitLabel="Send email" />
        </div>
      </Panel>

      {sent.length > 0 ? (
        <Panel aria-labelledby="sent-title">
          <PanelHeader id="sent-title" title="Sent in this episode" meta={<Badge>{sent.length}</Badge>} />
          <ul className="divide-y divide-line">
            {sent.map((email) => (
              <li key={email.idx} className="px-4 py-2.5">
                <p className="text-[13px] font-medium text-fg">{email.subject}</p>
                <p className="text-[12px] text-fg-subtle">
                  <Mono>{email.id}</Mono> to {email.to_address} · call #{email.idx}
                </p>
                <p className="mt-1 text-[13px] whitespace-pre-wrap text-fg-muted">{email.body}</p>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
