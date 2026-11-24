/**
 * Form fields from a tool's JSON Schema (the `params` of its ToolSpec), so every action form is generated from the
 * spec the agents see. Values are kept as strings and booleans while editing; `buildArgs` turns them into the call's
 * arguments, leaving empty optional fields out. Validation is the tool's: whatever it refuses comes back as a
 * recorded refusal, exactly as an agent would receive it. So the schema's limits are stated (`limits`), never
 * enforced by the browser: a form sends what was typed, as an API or MCP agent would.
 */
import { labelOf } from "./format";
import type { Json, JsonObject, JsonSchema, ToolSpec } from "./types";

export type FieldKind = "text" | "textarea" | "integer" | "number" | "boolean" | "enum";

export interface Field {
  name: string;
  label: string;
  description?: string;
  kind: FieldKind;
  required: boolean;
  options: string[];
  defaultValue?: Json;
  minLength?: number;
  maxLength?: number;
  /** The schema's bounds in words ("3 to 500 characters"), for the hint: the tool enforces them, not the form. */
  limits?: string;
  money: boolean;
}

export type FormValues = Record<string, string | boolean>;

/** `anyOf: [T, {type: "null"}]` (an optional value) -> T. */
function unwrap(schema: JsonSchema): { base: JsonSchema; nullable: boolean } {
  if (!schema.anyOf) return { base: schema, nullable: false };
  const options = schema.anyOf.filter((option) => option.type !== "null");
  return { base: { ...schema, ...(options[0] ?? {}), anyOf: undefined }, nullable: options.length < schema.anyOf.length };
}

function kindOf(name: string, schema: JsonSchema): FieldKind {
  if (schema.enum) return "enum";
  if (schema.type === "boolean") return "boolean";
  if (schema.type === "integer") return "integer";
  if (schema.type === "number") return "number";
  const long = (schema.maxLength ?? 0) > 200 || ["body", "summary"].includes(name);
  return long ? "textarea" : "text";
}

function characters(n: number): string {
  return `${n} character${n === 1 ? "" : "s"}`;
}

/** A schema's bounds in words, as the tool states them (exclusive bounds stay exclusive). */
function limitsOf(kind: FieldKind, schema: JsonSchema): string | undefined {
  if (kind === "text" || kind === "textarea") {
    const { minLength: lo, maxLength: hi } = schema;
    if (lo !== undefined && hi !== undefined) return `${lo} to ${hi} characters`;
    if (lo !== undefined) return `at least ${characters(lo)}`;
    if (hi !== undefined) return `at most ${characters(hi)}`;
    return undefined;
  }
  if (kind !== "integer" && kind !== "number") return undefined;
  const { minimum, maximum, exclusiveMinimum, exclusiveMaximum } = schema;
  const lo = minimum !== undefined ? `at least ${minimum}` : exclusiveMinimum !== undefined ? `more than ${exclusiveMinimum}` : null;
  const hi = maximum !== undefined ? `at most ${maximum}` : exclusiveMaximum !== undefined ? `less than ${exclusiveMaximum}` : null;
  const range =
    minimum !== undefined && maximum !== undefined ? `from ${minimum} to ${maximum}` : [lo, hi].filter(Boolean).join(" and ");
  if (kind === "integer") return range ? `a whole number, ${range}` : "a whole number";
  return range || undefined;
}

export function fieldsOf(spec: ToolSpec): Field[] {
  const properties = spec.params.properties ?? {};
  const required = new Set(spec.params.required ?? []);
  return Object.entries(properties).map(([name, raw]) => {
    const { base, nullable } = unwrap(raw);
    const kind = kindOf(name, base);
    return {
      name,
      label: labelOf(name),
      description: raw.description ?? base.description,
      kind,
      required: required.has(name) && !nullable,
      options: (base.enum ?? []).map(String),
      defaultValue: raw.default,
      minLength: base.minLength,
      maxLength: base.maxLength,
      limits: limitsOf(kind, base),
      money: kind === "integer" && name.endsWith("_cents"),
    };
  });
}

export function initialValues(fields: Field[], initial: JsonObject = {}): FormValues {
  const values: FormValues = {};
  for (const field of fields) {
    const given = field.name in initial ? initial[field.name] : field.defaultValue;
    if (field.kind === "boolean") values[field.name] = given === true;
    else values[field.name] = given === null || given === undefined ? "" : String(given);
  }
  return values;
}

/**
 * The call's arguments: empty optional fields are left out, numbers are parsed, booleans stay booleans. Anything
 * else is sent as typed (an empty required field as "", "12abc" as text), so the tool refuses it as it would refuse
 * an agent.
 */
export function buildArgs(fields: Field[], values: FormValues): JsonObject {
  const args: JsonObject = {};
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === "boolean") {
      if (value === true || value === false) args[field.name] = value;
      continue;
    }
    const text = typeof value === "string" ? value : "";
    if (text.trim() === "" && !field.required) continue;
    if (field.kind === "integer" || field.kind === "number") {
      const parsed = Number(text);
      args[field.name] = text.trim() !== "" && Number.isFinite(parsed) ? parsed : text;
    } else {
      args[field.name] = field.kind === "textarea" ? text : text.trim();
    }
  }
  return args;
}
