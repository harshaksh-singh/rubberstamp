import type { Json } from "./types";

const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const dateTime = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

/** Integer cents as dollars: 21999 -> "$219.99". */
export function money(cents: number | null | undefined): string {
  return typeof cents === "number" ? currency.format(cents / 100) : "—";
}

/** A world timestamp (UTC ISO) for people: "Sep 15, 2026, 12:00 UTC". The world's clock is frozen and in UTC. */
export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : `${dateTime.format(date)} UTC`;
}

/** "amount_cents" -> "Amount (cents)", "order_id" -> "Order ID". */
export function labelOf(name: string): string {
  const words = name.split("_").filter(Boolean);
  const cents = words.at(-1) === "cents";
  const text = (cents ? words.slice(0, -1) : words)
    .map((word, i) => (word === "id" ? "ID" : i === 0 ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(" ");
  return cents ? `${text} (cents)` : text;
}

/** One line for an argument value in the timeline. */
export function brief(value: Json, max = 60): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function pretty(value: Json): string {
  return JSON.stringify(value, null, 2);
}
