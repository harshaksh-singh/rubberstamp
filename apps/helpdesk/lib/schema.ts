/**
 * Form fields from a tool's JSON Schema (the `params` of its ToolSpec), so every action form is generated from the
 * spec the agents see. Values are kept as strings and booleans while editing; `buildArgs` turns them into the call's
 * arguments, leaving empty optional fields out. Validation is the tool's: whatever it refuses comes back as a
 * recorded refusal, exactly as an agent would receive it.
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
  min?: number;
  max?: number;
  step?: number;
  minLength?: number;
  maxLength?: number;
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

export function fieldsOf(spec: ToolSpec): Field[] {
  const properties = spec.params.properties ?? {};
  const required = new Set(spec.params.required ?? []);
  return Object.entries(properties).map(([name, raw]) => {
    const { base, nullable } = unwrap(raw);
    const kind = kindOf(name, base);
    const integer = kind === "integer";
    const min = base.minimum ?? (base.exclusiveMinimum !== undefined ? base.exclusiveMinimum + (integer ? 1 : 0) : undefined);
    const max = base.maximum ?? (base.exclusiveMaximum !== undefined ? base.exclusiveMaximum - (integer ? 1 : 0) : undefined);
    return {
      name,
      label: labelOf(name),
      description: raw.description ?? base.description,
      kind,
      required: required.has(name) && !nullable,
      options: (base.enum ?? []).map(String),
      defaultValue: raw.default,
      min,
      max,
      step: integer ? 1 : undefined,
      minLength: base.minLength,
      maxLength: base.maxLength,
      money: integer && name.endsWith("_cents"),
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

/** The call's arguments: empty optional fields are left out, numbers are parsed, booleans stay booleans. */
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
