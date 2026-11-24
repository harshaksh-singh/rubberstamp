"use client";

import { useId, useMemo, useState } from "react";

import { useEpisode } from "@/lib/episode";
import { money } from "@/lib/format";
import { buildArgs, fieldsOf, initialValues, type Field, type FormValues } from "@/lib/schema";
import type { JsonObject, ToolCall } from "@/lib/types";

import { CallOutcome, EffectBadge } from "./call-view";
import { Button, cn, Notice } from "./ui";

export interface ToolFormProps {
  tool: string;
  /** Prefilled values; the person can still change them. */
  initial?: JsonObject;
  /** Fields fixed by the page (the open ticket's id): shown, sent, not editable. */
  fixed?: JsonObject;
  submitLabel?: string;
  layout?: "stack" | "inline";
  showDescription?: boolean;
  /** Id values to suggest per field (a <datalist>), e.g. the approvals of the open order. */
  suggestions?: Record<string, string[]>;
  /** Called with every recorded call, refused or not (e.g. to refresh the open record after a write). */
  onDone?: (call: ToolCall) => void;
  danger?: boolean;
  /** Hide the success outcome (a query bar whose result the page shows); refusals are always shown. */
  quiet?: boolean;
}

/**
 * A form generated from the tool's JSON Schema (its ToolSpec), submitted as one tool call through rs-api. The outcome
 * is shown as recorded: the result, or the tool's refusal.
 */
export function ToolForm({
  tool,
  initial,
  fixed = {},
  submitLabel,
  layout = "stack",
  showDescription = false,
  suggestions = {},
  onDone,
  danger = false,
  quiet = false,
}: ToolFormProps) {
  const { tools, run, submitted } = useEpisode();
  const spec = tools.get(tool);
  const fields = useMemo(() => (spec ? fieldsOf(spec) : []), [spec]);
  const editable = fields.filter((field) => !(field.name in fixed));
  const [values, setValues] = useState<FormValues>(() => initialValues(fields, { ...initial }));
  const [last, setLast] = useState<ToolCall | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const formId = useId();

  if (!spec) {
    return <Notice tone="warn" title={`${tool} is not available in this task`} />;
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      const args = { ...buildArgs(editable, values), ...fixed };
      const call = await run(tool, args);
      setLast(call);
      if (call.ok && spec?.effect === "write") setValues(initialValues(fields, { ...initial })); // ready for the next one
      onDone?.(call);
    } catch (exc) {
      setFailure(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const inline = layout === "inline";
  return (
    <form onSubmit={onSubmit} className="space-y-3" aria-describedby={showDescription ? `${formId}-desc` : undefined}>
      {showDescription ? (
        <p id={`${formId}-desc`} className="text-[13px] whitespace-pre-line text-fg-muted">
          {spec.description}
        </p>
      ) : null}
      {Object.keys(fixed).length > 0 ? (
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-fg-muted">
          {Object.entries(fixed).map(([key, value]) => (
            <span key={key}>
              {key} = <span className="font-mono text-fg">{String(value)}</span>
            </span>
          ))}
        </p>
      ) : null}
      <div className={cn(inline ? "flex flex-wrap items-end gap-3" : "space-y-3")}>
        {editable.map((field) => (
          <FieldInput
            key={field.name}
            id={`${formId}-${field.name}`}
            field={field}
            value={values[field.name] ?? ""}
            onChange={(value) => setValues((prev) => ({ ...prev, [field.name]: value }))}
            suggestions={suggestions[field.name]}
            inline={inline}
            disabled={submitted}
          />
        ))}
        <div className={cn("flex items-center gap-3", inline && "pb-px")}>
          <Button type="submit" variant={danger ? "danger" : spec.effect === "write" ? "primary" : "secondary"} disabled={busy || submitted}>
            {busy ? "Working…" : (submitLabel ?? tool)}
          </Button>
          {!inline ? (
            <span className="flex items-center gap-1.5 text-[12px] text-fg-subtle">
              <EffectBadge spec={spec} /> <span className="font-mono">{tool}</span>
            </span>
          ) : null}
        </div>
      </div>
      {failure ? <Notice tone="bad" title="The API did not take this call">{failure}</Notice> : null}
      {last && !(quiet && last.ok) ? <CallOutcome call={last} /> : null}
    </form>
  );
}

function FieldInput({
  id,
  field,
  value,
  onChange,
  suggestions,
  inline,
  disabled,
}: {
  id: string;
  field: Field;
  value: string | boolean;
  onChange: (value: string | boolean) => void;
  suggestions?: string[];
  inline: boolean;
  disabled: boolean;
}) {
  const hintId = `${id}-hint`;
  const hint = inline ? undefined : field.description; // inline query bars stay compact
  const listId = suggestions && suggestions.length > 0 ? `${id}-options` : undefined;
  const label = (
    <span className="mb-1 block text-[12px] font-medium text-fg-muted">
      {field.label}
      {field.required ? <span className="text-bad"> *</span> : <span className="font-normal text-fg-subtle"> (optional)</span>}
    </span>
  );

  if (field.kind === "boolean") {
    return (
      <div className={cn(inline ? "pb-1.5" : "")}>
        <label htmlFor={id} className="inline-flex items-center gap-2 text-[13px] text-fg">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={(event) => onChange(event.target.checked)}
            disabled={disabled}
            aria-describedby={hint ? hintId : undefined}
            className="size-4 accent-[var(--accent)]"
          />
          {field.label}
        </label>
        {hint ? (
          <p id={hintId} className="mt-0.5 text-[12px] text-fg-subtle">
            {hint}
          </p>
        ) : null}
      </div>
    );
  }

  const text = typeof value === "string" ? value : "";
  const common = {
    id,
    disabled,
    required: field.required,
    "aria-describedby": hint || field.money ? hintId : undefined,
    className: "field-input",
  };

  let control: React.ReactNode;
  if (field.kind === "enum") {
    control = (
      <select {...common} value={text} onChange={(event) => onChange(event.target.value)}>
        {!field.required ? <option value="">Any / none</option> : null}
        {field.required && text === "" ? <option value="">Choose…</option> : null}
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (field.kind === "textarea") {
    control = (
      <textarea
        {...common}
        rows={4}
        value={text}
        minLength={field.minLength}
        maxLength={field.maxLength}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  } else if (field.kind === "integer" || field.kind === "number") {
    control = (
      <input
        {...common}
        type="number"
        inputMode="numeric"
        min={field.min}
        max={field.max}
        step={field.step ?? "any"}
        value={text}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  } else {
    control = (
      <input
        {...common}
        type="text"
        autoComplete="off"
        spellCheck={false}
        list={listId}
        minLength={field.minLength}
        maxLength={field.maxLength}
        value={text}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  const cents = field.money && text.trim() !== "" && Number.isFinite(Number(text)) ? Number(text) : null;
  return (
    <div className={cn(inline ? (field.kind === "textarea" ? "w-full" : "w-44 grow sm:grow-0") : "")}>
      <label htmlFor={id}>{label}</label>
      {control}
      {listId ? (
        <datalist id={listId}>
          {suggestions!.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      ) : null}
      {hint || field.money ? (
        <p id={hintId} className="mt-0.5 text-[12px] text-fg-subtle">
          {field.money ? <span className="font-medium text-fg-muted">{cents !== null ? `= ${money(cents)}` : "In cents"}</span> : null}
          {field.money && hint ? " · " : null}
          {hint}
        </p>
      ) : null}
    </div>
  );
}
