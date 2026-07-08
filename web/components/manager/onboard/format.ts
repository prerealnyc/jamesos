/**
 * Display helpers for the onboarding wizard — ported from bm2.0
 * frontend/lib/format.ts (entity options, key/section prettifiers, the
 * value-to-text renderer, the "why we ask" fallback).
 */

import type { EntityType } from "@/lib/manager-api";

export const ENTITY_OPTIONS: { value: EntityType; label: string; hint: string }[] = [
  { value: "person", label: "Person", hint: "A public figure or personal brand" },
  { value: "company", label: "Company", hint: "A business or startup" },
  { value: "physical_asset", label: "Physical asset", hint: "A venue, property, or destination" },
  { value: "institution", label: "Institution", hint: "An organization, board, or public body" },
];

export const ENTITY_VALUES: EntityType[] = ENTITY_OPTIONS.map((o) => o.value);

export function isEntityType(v: unknown): v is EntityType {
  return typeof v === "string" && (ENTITY_VALUES as string[]).includes(v);
}

export function prettyKey(key: string): string {
  const last = key.includes(".") ? key.slice(key.indexOf(".") + 1) : key;
  const words = last.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function prettySection(section: string): string {
  const names: Record<string, string> = {
    identity: "Identity",
    audience: "Audience",
    voice: "Voice & style",
    positioning: "Positioning & authority",
    products: "Products & offers",
    competitors: "Competitors & peers",
    goals: "Goals",
    guardrails: "Constraints & guardrails",
    channels: "Channels & assets",
    performance: "Performance memory",
  };
  return names[section] ?? prettyKey(section);
}

export const SECTION_ORDER = [
  "identity",
  "audience",
  "voice",
  "positioning",
  "products",
  "competitors",
  "goals",
  "guardrails",
  "channels",
  "performance",
];

export function valueToText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number") return v.toLocaleString();
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.map(valueToText).join(", ");
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${prettyKey(k)}: ${valueToText(x)}`)
      .join(" · ");
  }
  return String(v);
}

export function isUrl(s: unknown): s is string {
  return typeof s === "string" && /^https?:\/\//i.test(s);
}

/** URLs inside a flat profile value — rendered as citation chips. */
export function urlsIn(v: unknown): string[] {
  if (isUrl(v)) return [v];
  if (Array.isArray(v)) return v.filter(isUrl);
  return [];
}

/** The Interviewer always supplies `why`; this is the defensive fallback. */
export function whyWeAsk(why: string | null | undefined): string {
  return why || "Research couldn't settle this — your answer goes straight into your brand profile.";
}
