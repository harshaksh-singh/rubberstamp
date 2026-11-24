import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover border-transparent",
  secondary: "bg-panel text-fg hover:bg-muted border-line-strong",
  ghost: "bg-transparent text-fg-muted hover:bg-muted hover:text-fg border-transparent",
  danger: "bg-bad text-bg hover:opacity-90 border-transparent",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-[13px]",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

export type Tone = "neutral" | "accent" | "ok" | "bad" | "warn";

const TONES: Record<Tone, string> = {
  neutral: "bg-muted text-fg-muted border-line",
  accent: "bg-accent-soft text-accent border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
  bad: "bg-bad-soft text-bad border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-px text-[11px] leading-4 font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  open: "accent",
  pending: "warn",
  resolved: "ok",
  closed: "neutral",
  processing: "accent",
  shipped: "accent",
  delivered: "ok",
  returned: "neutral",
  cancelled: "neutral",
  refunded: "ok",
  approved: "ok",
  rejected: "bad",
  urgent: "bad",
  high: "warn",
  normal: "neutral",
  low: "neutral",
  gold: "warn",
  silver: "neutral",
  standard: "neutral",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-fg-subtle">—</span>;
  return <Badge tone={STATUS_TONES[value] ?? "neutral"}>{value}</Badge>;
}

export function Panel({ children, className, ...rest }: { children: ReactNode; className?: string } & Record<`aria-${string}`, string>) {
  return (
    <section className={cn("rounded-lg border border-line bg-panel", className)} {...rest}>
      {children}
    </section>
  );
}

export function PanelHeader({ title, meta, actions, id }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 id={id} className="text-[13px] font-semibold text-fg">
          {title}
        </h2>
        {meta}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight text-fg">{title}</h1>
        {description ? <p className="mt-0.5 max-w-3xl text-[13px] text-fg-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <p className="text-[13px] font-medium text-fg">{title}</p>
      {children ? <div className="max-w-md text-[13px] text-fg-muted">{children}</div> : null}
    </div>
  );
}

export function KeyValues({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,max-content)_1fr] gap-x-4 gap-y-1.5 text-[13px]">
      {items.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-fg-subtle">{key}</dt>
          <dd className="min-w-0 break-words text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12px]", className)}>{children}</span>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-[13px] text-fg-muted">
      <span aria-hidden className="size-3.5 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
      {label}
    </span>
  );
}

/** A message box. A "bad" notice is an alert (announced at once); the others are status messages. */
export function Notice({ tone, title, children }: { tone: "bad" | "warn" | "ok" | "accent"; title: string; children?: ReactNode }) {
  const tones = {
    bad: "border-bad/40 bg-bad-soft",
    warn: "border-warn/40 bg-warn-soft",
    ok: "border-ok/40 bg-ok-soft",
    accent: "border-accent/40 bg-accent-soft",
  };
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={cn("rounded-md border px-3 py-2 text-[13px]", tones[tone])}>
      <p className="font-medium text-fg">{title}</p>
      {children ? <div className="mt-0.5 text-fg-muted">{children}</div> : null}
    </div>
  );
}

/**
 * An rs-api error (HTTP error, or no answer), as opposed to a tool's refusal, which is a recorded call. `compact`
 * fits it under a small control (a link in a table, the Approve and Reject buttons).
 */
export function ApiFailure({
  title = "The API did not take this call",
  message,
  compact = false,
}: {
  title?: string;
  message: string;
  compact?: boolean;
}) {
  if (compact) {
    return (
      <p role="alert" className="mt-1 max-w-xs text-[12px] text-bad">
        <span className="font-medium">{title}.</span> {message}
      </p>
    );
  }
  return (
    <Notice tone="bad" title={title}>
      {message}
    </Notice>
  );
}

/** A list of records as full-width buttons: every row is keyboard reachable and announces what it opens. */
export function RowList<T>({
  rows,
  rowKey,
  render,
  onOpen,
  selected,
  label,
  disabled = false,
}: {
  rows: T[];
  rowKey: (row: T) => string;
  render: (row: T) => ReactNode;
  onOpen: (row: T) => void;
  selected?: string | null;
  label: (row: T) => string;
  /** No row can be opened (the episode ended, so a read would be refused with HTTP 409). */
  disabled?: boolean;
}) {
  return (
    <ul className="divide-y divide-line">
      {rows.map((row) => {
        const key = rowKey(row);
        const active = key === selected;
        return (
          <li key={key}>
            <button
              type="button"
              onClick={() => onOpen(row)}
              disabled={disabled}
              aria-label={label(row)}
              aria-current={active ? "true" : undefined}
              className={cn(
                "w-full px-4 py-2 text-left text-[13px] transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:hover:bg-transparent",
                active && "bg-accent-soft hover:bg-accent-soft disabled:hover:bg-accent-soft",
              )}
            >
              {render(row)}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
