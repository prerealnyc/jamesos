"use client";

/**
 * Shared async-state + badge primitives for the /manager surface — ported
 * from bm2.0 frontend/components/{use-load.ts,async.tsx,badges.tsx}, restyled
 * onto this app's Tailwind HSL-var tokens (bg-card / border-border /
 * text-muted-foreground; see web/components/ui.tsx).
 */

import * as React from "react";
import { useEffect, useRef, useState } from "react";

import { errorMessage, type FieldStatus, type Source, type WorkOrderStatus } from "@/lib/manager-api";
import { Spinner } from "@/components/ui";

function cx(...c: (string | false | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

// ── useLoad: client-side data loader ────────────────────────────────────────

interface LoadState<T> {
  data: T | null;
  error: string | null;
  status: number | null; // HTTP status when the error was an ApiError
  loading: boolean;
}

/** Client-side data loader with loading/error/reload; cancels stale responses. */
export function useLoad<T>(fn: () => Promise<T>, deps: readonly unknown[] = []) {
  const [state, setState] = useState<LoadState<T>>({ data: null, error: null, status: null, loading: true });
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fnRef.current().then(
      (data) => {
        if (!cancelled) setState({ data, error: null, status: null, loading: false });
      },
      (err: unknown) => {
        if (!cancelled)
          setState((s) => ({
            ...s,
            error: errorMessage(err),
            status: typeof (err as { status?: number })?.status === "number" ? (err as { status: number }).status : null,
            loading: false,
          }));
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { ...state, reload: () => setTick((t) => t + 1) };
}

// ── async-state panels ──────────────────────────────────────────────────────

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground" role="status">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive"
      role="alert"
    >
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onRetry}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  children,
}: {
  title: string;
  body?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card/50 px-4 py-8 text-center">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {body && <p className="mt-1 text-sm text-muted-foreground">{body}</p>}
      {children && <div className="mt-3 flex justify-center gap-2">{children}</div>}
    </div>
  );
}

// ── badge kit (provenance / status, D2 confidence) ──────────────────────────

/** Local-token badge tones (ui.tsx Badge covers muted/primary/accent/
 * destructive; the manager kit also needs a warning tone for researched/
 * stale/review states). */
type BadgeTone = "green" | "blue" | "amber" | "red" | "grey";

const TONE_CLS: Record<BadgeTone, string> = {
  green: "bg-accent/20 text-accent",
  blue: "bg-primary/15 text-primary",
  amber: "bg-warning/15 text-warning",
  red: "bg-destructive/15 text-destructive",
  grey: "bg-secondary text-muted-foreground",
};

function ManagerBadge({ tone, children, title }: { tone: BadgeTone; children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[.4px]",
        TONE_CLS[tone],
      )}
    >
      {children}
    </span>
  );
}

const SOURCE_STYLE: Record<Source, { label: string; tone: BadgeTone }> = {
  user_stated: { label: "user", tone: "green" },
  negotiated: { label: "negotiated", tone: "green" },
  audited: { label: "audited", tone: "blue" },
  researched: { label: "researched", tone: "amber" },
  inferred: { label: "inferred", tone: "grey" },
  derived: { label: "derived", tone: "grey" },
  queue_signal: { label: "queue signal", tone: "grey" },
};

/** Computed-confidence display (D2): five dots, filled by the 0..1 score. */
export function ConfidenceDots({ value }: { value: number }) {
  const clamped = Math.min(Math.max(value, 0), 1);
  const filled = Math.round(clamped * 5);
  return (
    <span
      className="inline-flex items-center gap-0.5 align-middle"
      title={`Confidence ${clamped.toFixed(2)}`}
      aria-label={`Confidence ${clamped.toFixed(2)}`}
    >
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={cx(
            "inline-block h-1.5 w-1.5 rounded-full",
            i < filled ? "bg-primary" : "bg-border",
          )}
        />
      ))}
    </span>
  );
}

/** Provenance badge: user=green, audited=blue, researched=amber, inferred=grey. */
export function SourceBadge({ source, confidence }: { source: Source; confidence?: number }) {
  const style = SOURCE_STYLE[source] ?? { label: source, tone: "grey" as BadgeTone };
  return (
    <span className="inline-flex items-center gap-1.5">
      <ManagerBadge tone={style.tone}>{style.label}</ManagerBadge>
      {confidence !== undefined && <ConfidenceDots value={confidence} />}
    </span>
  );
}

const FIELD_STATUS_TONE: Record<FieldStatus, BadgeTone> = {
  unconfirmed: "grey",
  confirmed: "green",
  contradicted: "red",
  stale: "amber",
  superseded: "grey",
};

export function FieldStatusBadge({ status }: { status: FieldStatus }) {
  if (status === "unconfirmed") return null; // default state; badge would be noise
  return <ManagerBadge tone={FIELD_STATUS_TONE[status] ?? "grey"}>{status}</ManagerBadge>;
}

const WORK_ORDER_STATUS_TONE: Record<WorkOrderStatus, BadgeTone> = {
  queued: "grey",
  generating: "blue",
  review: "amber",
  pending_approval: "amber",
  approved: "green",
  scheduled: "blue",
  published: "green",
  measured: "green",
  rejected: "red",
  superseded: "grey",
  cancelled: "grey",
};

export function WorkOrderStatusBadge({ status }: { status: WorkOrderStatus | string }) {
  const tone = WORK_ORDER_STATUS_TONE[status as WorkOrderStatus] ?? "grey";
  return <ManagerBadge tone={tone}>{String(status).replace(/_/g, " ")}</ManagerBadge>;
}

/** Generic status badge for the lifecycles without a fixed vocabulary
 * (actions, peers, plans, job runs). */
const STATUS_TONE: Record<string, BadgeTone> = {
  // actions
  suggested: "grey",
  active: "blue",
  done: "green",
  dismissed: "grey",
  snoozed: "amber",
  // peers / plans / job runs
  candidate: "amber",
  tracked: "green",
  proposed: "amber",
  accepted: "green",
  partial: "blue",
  expired: "grey",
  running: "blue",
  succeeded: "green",
  failed: "red",
  never_run: "grey",
  // accounts
  connected: "green",
  not_connected: "grey",
  error: "red",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <ManagerBadge tone={STATUS_TONE[status] ?? "grey"}>{status.replace(/_/g, " ")}</ManagerBadge>
  );
}
