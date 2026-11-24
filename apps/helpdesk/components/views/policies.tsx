"use client";

import { useRead } from "@/lib/episode";
import { SLOTS } from "@/lib/open";
import { resultOf, type Policy, type PolicySummary } from "@/lib/records";

import { CallOutcome, JsonBlock } from "../call-view";
import { Badge, Button, EmptyState, Mono, PageHeader, Panel, PanelHeader, RowList, Spinner } from "../ui";

export function PoliciesView() {
  const list = useRead(SLOTS.policyList, "get_policy");
  const detail = useRead(SLOTS.policy, "get_policy");
  const policies = resultOf<{ policies: PolicySummary[] }>(list.call, "policies")?.policies;
  const open = resultOf<Policy>(detail.call, "value");

  return (
    <>
      <PageHeader
        title="Policies"
        description="The support rules the prompt cites. get_policy with no id lists them; with an id it returns the rule and its value."
        actions={
          <Button variant="primary" onClick={() => list.read({})} disabled={list.busy}>
            {list.busy ? "Listing…" : policies ? "List again" : "List policies"}
          </Button>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel aria-labelledby="policies-title" className="min-w-0 self-start">
          <PanelHeader id="policies-title" title="Policies" meta={policies ? <Badge>{policies.length}</Badge> : null} />
          {list.call && !list.call.ok ? (
            <div className="px-4 py-3">
              <CallOutcome call={list.call} />
            </div>
          ) : !policies ? (
            <EmptyState title="Not listed yet">List the policies (get_policy) to read them here.</EmptyState>
          ) : (
            <RowList
              rows={policies}
              rowKey={(p) => p.id}
              selected={open?.id}
              onOpen={(p) => detail.read({ policy_id: p.id })}
              label={(p) => `Open policy ${p.id}`}
              render={(p) => (
                <div className="grid gap-0.5">
                  <Mono className="font-medium text-fg">{p.id}</Mono>
                  <span className="line-clamp-2 text-[12px] text-fg-muted">{p.description}</span>
                </div>
              )}
            />
          )}
        </Panel>
        <div className="min-w-0">
          {detail.busy && !open ? (
            <Spinner label="Reading policy" />
          ) : detail.call && !detail.call.ok ? (
            <CallOutcome call={detail.call} />
          ) : open ? (
            <Panel aria-labelledby="policy-title">
              <PanelHeader id="policy-title" title={<Mono className="text-[13px]">{open.id}</Mono>} />
              <div className="space-y-3 px-4 py-3">
                <p className="text-[14px] leading-relaxed text-fg">{open.description}</p>
                <div>
                  <p className="mb-1 text-[12px] font-medium tracking-wide text-fg-subtle uppercase">Value</p>
                  <JsonBlock value={open.value} label={`Value of ${open.id}`} />
                </div>
              </div>
            </Panel>
          ) : (
            <Panel>
              <EmptyState title="No policy open">Choose a policy from the list.</EmptyState>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
