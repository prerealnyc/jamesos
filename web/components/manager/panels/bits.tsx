"use client";

/**
 * Small shared pieces for the Mission Control panels — formatting helpers,
 * tone badges (incl. the amber/warning tone ui.tsx Badge lacks), right-aligned
 * key/value rows, quiet notices for 409 "already running", and the per-step
 * report readout used by the daily cycle / snapshot buttons.
 */

import * as React from "react";

export function cx(...c: (string | false | undefined | null)[]) {
  return c.filter(Boolean).join(" ");
}

// ── formatting ──────────────────────────────────────────────────────────────

export function fmtNum(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "—";
    return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  return String(v);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function fmtDateLong(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function prettyKey(k: string): string {
  return k.replace(/[_.]/g, " ").trim();
}

// ── tone badges (the manager palette: blue/amber/green/red/grey) ────────────

export type Tone = "blue" | "amber" | "green" | "red" | "grey";

const TONE_CLS: Record<Tone, string> = {
  blue: "bg-primary/15 text-primary",
  amber: "bg-warning/15 text-warning",
  green: "bg-accent/20 text-accent",
  red: "bg-destructive/15 text-destructive",
  grey: "bg-secondary text-muted-foreground",
};

export function ToneBadge({
  tone,
  children,
  title,
}: {
  tone: Tone;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[.4px]",
        TONE_CLS[tone],
      )}
    >
      {children}
    </span>
  );
}

/** Action-item kind -> tone (donor followups/daily-brief palette). */
export const ACTION_KIND_TONE: Record<string, Tone> = {
  collaboration: "green",
  visibility: "blue",
  content: "amber",
  research: "grey",
  press: "amber",
  general: "grey",
};

/** Peer kind -> tone (donor competitor/collaboration palette). */
export const PEER_KIND_TONE: Record<string, Tone> = {
  leader: "blue",
  aspirational: "amber",
  collaborator: "green",
  competitor: "red",
};

/** Outreach confidence is a lead-quality signal, not a guarantee — muted. */
export const CONFIDENCE_TONE: Record<string, Tone> = {
  low: "grey",
  medium: "amber",
  high: "green",
};

/** Press signal -> tone (award/feature/appointment/coverage/milestone). */
export const PRESS_SIGNAL_TONE: Record<string, Tone> = {
  award: "amber",
  feature: "blue",
  appointment: "green",
  coverage: "grey",
  milestone: "green",
};

// ── layout bits ─────────────────────────────────────────────────────────────

/** Muted label left, bold right-aligned value (numbers land tabular via the
 * theme). The workhorse row of every dense card. */
export function KV({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-sm">
      <span className="min-w-0 text-muted-foreground">{label}</span>
      <span className="text-right font-semibold">{children}</span>
    </div>
  );
}

/** Quiet informational notice — the "already running" / heads-up tone, NOT an
 * error box (409s are progress reports, not failures). */
export function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-secondary/60 px-3 py-2 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

/** Section header inside a panel: title left, actions right. */
export function PanelHead({
  title,
  sub,
  children,
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
      </div>
      {children && <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

// ── report readout (daily cycle / peer snapshot: step -> outcome) ───────────

function outcome(v: unknown): { text: string; failed: boolean } {
  if (v === null || v === undefined) return { text: "—", failed: false };
  if (typeof v === "number") return { text: fmtNum(v), failed: false };
  if (typeof v === "boolean") return { text: v ? "yes" : "no", failed: false };
  if (typeof v === "string") return { text: v, failed: v.toLowerCase() === "failed" };
  if (Array.isArray(v)) return { text: `${v.length} item${v.length === 1 ? "" : "s"}`, failed: false };
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>).filter(
      ([, x]) => typeof x === "number" || typeof x === "string" || typeof x === "boolean",
    );
    const text = entries
      .slice(0, 4)
      .map(([k, x]) => `${prettyKey(k)} ${typeof x === "number" ? fmtNum(x) : String(x)}`)
      .join(" · ");
    return { text: text || "done", failed: false };
  }
  return { text: String(v), failed: false };
}

/** Per-step readout for a step->outcome report (run_daily_cycle, snapshots).
 * Values are counts, sub-reports, or the string "failed" — failures show red
 * but honestly, without pretending the whole run failed. */
export function ReportReadout({ report, title }: { report: Record<string, unknown>; title?: string }) {
  const entries = Object.entries(report);
  if (entries.length === 0) return <Notice>The run returned an empty report.</Notice>;
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      {title && <div className="mb-1 text-xs font-semibold uppercase tracking-[.4px] text-muted-foreground">{title}</div>}
      {entries.map(([step, v]) => {
        const o = outcome(v);
        return (
          <div key={step} className="flex items-baseline justify-between gap-3 py-0.5 text-sm">
            <span className="capitalize text-muted-foreground">{prettyKey(step)}</span>
            <span className={cx("text-right", o.failed ? "font-semibold text-destructive" : "font-medium")}>
              {o.text}
            </span>
          </div>
        );
      })}
    </div>
  );
}
