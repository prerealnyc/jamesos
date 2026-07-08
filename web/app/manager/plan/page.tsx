"use client";

/**
 * Weekly plan — the Strategist's evidence-backed prescription (ported from
 * bm2.0 brand/[id]/plan/page.tsx onto the merged planning routes). Draft with
 * POST /manager/plan/weekly, review each line's rationale / evidence /
 * predicted metrics, then activate all of it or a checkbox subset via
 * POST /manager/plan/{id}/activate. A 409 means a newer prescription overtook
 * this one — surface the nudge to re-draft, not an error.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card, PageHeader, Spinner } from "@/components/ui";
import { CitationChips } from "@/components/manager/citations";
import { EmptyState, ErrorBox, Loading, StatusBadge, useLoad } from "@/components/manager/primitives";
import {
  errorMessage,
  isConflict,
  managerApi,
  type ActivateResult,
  type CitationLike,
  type GrowthAction,
  type PlanItem,
  type WeeklyPlanDraft,
} from "@/lib/manager-api";
import { cx, fmtDate, fmtNum, Notice, prettyKey } from "@/components/manager/panels/bits";

// ── view model: one shape whether the plan came from a fresh draft or the
//    persisted latest prescription (whose items arrive as loose records) ─────

interface PlanView {
  planId: string;
  weekOf: string;
  status: string;
  rationale: string;
  goals: [string, unknown][];
  periodStart: string | null;
  periodEnd: string | null;
  items: PlanItem[];
  growthActions: GrowthAction[];
  acceptedCount: number;
}

function normalizeItem(raw: Record<string, unknown>): PlanItem {
  return {
    content_type: typeof raw.content_type === "string" ? raw.content_type : "",
    platform: typeof raw.platform === "string" ? raw.platform : "",
    topic: typeof raw.topic === "string" ? raw.topic : "",
    count: typeof raw.count === "number" ? raw.count : 1,
    format_spec: (raw.format_spec as Record<string, unknown>) ?? {},
    rationale: typeof raw.rationale === "string" ? raw.rationale : "",
    evidence: Array.isArray(raw.evidence) ? (raw.evidence as CitationLike[]) : [],
    predicted_metrics: (raw.predicted_metrics as Record<string, unknown>) ?? {},
  };
}

function fromDraft(d: WeeklyPlanDraft): PlanView {
  return {
    planId: d.plan_id,
    weekOf: d.week_of,
    status: d.status,
    rationale: d.plan.rationale,
    goals: Object.entries(d.plan.goals_snapshot ?? {}),
    periodStart: d.plan.period_start,
    periodEnd: d.plan.period_end,
    items: d.plan.items,
    growthActions: d.growth_actions ?? [],
    acceptedCount: 0,
  };
}

function valueToText(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtNum(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(valueToText).join(", ");
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${prettyKey(k)} ${valueToText(x)}`)
      .join(" · ");
  }
  return String(v);
}

function PredictedMetrics({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) return null;
  const numeric = entries.filter(([, v]) => typeof v === "number");
  const rest = entries.filter(([, v]) => typeof v !== "number");
  return (
    <div className="mt-2">
      {numeric.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">Predicted</span>
          {numeric.map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
            >
              {prettyKey(k)} <b className="text-right text-foreground">{fmtNum(v)}</b>
            </span>
          ))}
        </div>
      )}
      {rest.map(([k, v]) => (
        <p key={k} className="m-0 mt-1 text-xs text-muted-foreground">
          {prettyKey(k)}: {valueToText(v)}
        </p>
      ))}
    </div>
  );
}

function PlanItemCard({
  item,
  index,
  selectable,
  checked,
  onToggle,
}: {
  item: PlanItem;
  index: number;
  selectable: boolean;
  checked: boolean;
  onToggle: (index: number) => void;
}) {
  return (
    <Card variant="compact" className={cx(selectable && !checked && "opacity-60")}>
      <div className="flex items-start gap-2.5">
        {selectable && (
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 shrink-0 accent-[hsl(var(--primary))]"
            checked={checked}
            onChange={() => onToggle(index)}
            aria-label={`Include "${item.topic}" when activating`}
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm font-semibold">{item.topic || "Untitled line item"}</div>
              <div className="text-xs capitalize text-muted-foreground">
                {item.content_type.replace(/_/g, " ")}
                {item.platform ? ` · ${item.platform}` : ""}
              </div>
            </div>
            <span className="shrink-0 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-semibold">
              ×{fmtNum(Math.max(item.count, 1))}
            </span>
          </div>

          {/* Rationale + evidence are the differentiator — front and center. */}
          {item.rationale && (
            <div className="mt-2 rounded-md border border-border bg-background px-3 py-2">
              <div className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
                Why this, this week
              </div>
              <p className="m-0 mt-0.5 text-sm">{item.rationale}</p>
            </div>
          )}
          {item.evidence.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">Evidence</span>
              <CitationChips citations={item.evidence} />
            </div>
          )}

          <PredictedMetrics metrics={item.predicted_metrics} />
        </div>
      </div>
    </Card>
  );
}

export default function ManagerPlanPage() {
  const latest = useLoad(() => managerApi.latestPlan(), []);

  const [fresh, setFresh] = useState<PlanView | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);

  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [superseded, setSuperseded] = useState(false);
  const [activated, setActivated] = useState<ActivateResult | null>(null);

  // A fresh draft supersedes the loaded latest plan for display.
  const plan: PlanView | null = useMemo(() => {
    if (fresh) return fresh;
    const lp = latest.data?.plan;
    if (!lp) return null;
    return {
      planId: lp.plan_id,
      weekOf: lp.week_of,
      status: lp.status,
      rationale: "",
      goals: [],
      periodStart: null,
      periodEnd: null,
      items: (lp.items ?? []).map(normalizeItem),
      growthActions: lp.growth_actions ?? [],
      acceptedCount: (lp.accepted_items ?? []).length,
    };
  }, [fresh, latest.data]);

  // Partial activation: all items selected by default; reset when the plan
  // identity changes.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const planId = plan?.planId ?? null;
  const itemCount = plan?.items.length ?? 0;
  useEffect(() => {
    setSelected(new Set(Array.from({ length: itemCount }, (_, i) => i)));
    setActivated(null);
    setSuperseded(false);
    setActivateError(null);
  }, [planId, itemCount]);

  function toggle(i: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  async function draftPlan() {
    if (drafting || activating) return;
    setDrafting(true);
    setDraftError(null);
    setDraftNotice(null);
    setActivated(null);
    setSuperseded(false);
    try {
      const d = await managerApi.draftWeeklyPlan();
      setFresh(fromDraft(d));
      latest.reload();
    } catch (err) {
      if (isConflict(err)) setDraftNotice("A plan draft is already running — check back in a moment.");
      else setDraftError(errorMessage(err));
    } finally {
      setDrafting(false);
    }
  }

  async function activate() {
    if (!plan || activating || drafting) return;
    if (selected.size === 0) {
      setActivateError("Pick at least one line item to activate.");
      return;
    }
    setActivating(true);
    setActivateError(null);
    setSuperseded(false);
    try {
      const indices = Array.from(selected).sort((a, b) => a - b);
      const res = await managerApi.activatePlan(
        plan.planId,
        indices.length === plan.items.length ? undefined : indices, // omit = accept-all
      );
      setActivated(res);
      setFresh(null); // the persisted plan now carries the truth
      latest.reload();
    } catch (err) {
      if (isConflict(err)) {
        // a newer prescription overtook this one
        setSuperseded(true);
      } else {
        setActivateError(errorMessage(err));
      }
    } finally {
      setActivating(false);
    }
  }

  const byPlatform = useMemo(() => {
    const groups = new Map<string, { item: PlanItem; index: number }[]>();
    (plan?.items ?? []).forEach((item, index) => {
      const key = item.platform || "unspecified";
      const list = groups.get(key) ?? [];
      list.push({ item, index });
      groups.set(key, list);
    });
    return Array.from(groups.entries());
  }, [plan]);

  const totalSelected = useMemo(
    () =>
      (plan?.items ?? []).reduce((n, it, i) => (selected.has(i) ? n + Math.max(it.count, 1) : n), 0),
    [plan, selected],
  );

  const activatable = plan !== null && plan.status === "proposed" && !activated;

  return (
    <div id="main-content" tabIndex={-1} className="outline-none">
      <PageHeader
        title="Weekly plan"
        sub="The Strategist's prescription for the week — every line cites its evidence; nothing is queued until you activate it."
      />

      <div className="mt-4 space-y-3">
        {draftNotice && <Notice>{draftNotice}</Notice>}
        {draftError && <ErrorBox message={draftError} onRetry={draftPlan} />}
        {latest.error && !plan && <ErrorBox message={latest.error} onRetry={latest.reload} />}

        {latest.loading && !plan && !drafting && <Loading label="Loading the latest prescription…" />}
        {drafting && <Loading label="The Strategist is drafting your week — profile, baseline, peer evidence…" />}

        {!plan && !drafting && latest.data && (
          <EmptyState
            title="No plan drafted yet"
            body="The Strategist reads your profile, baseline, and peer digest, then proposes the week — every line cites its evidence. Nothing is queued until you activate it."
          >
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
              disabled={drafting}
              onClick={draftPlan}
            >
              Draft this week&apos;s plan
            </button>
          </EmptyState>
        )}

        {plan && !drafting && (
          <>
            {/* ------------------------------------------------ plan header */}
            <Card variant="compact">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold">
                    Week of {plan.periodStart ? `${fmtDate(plan.periodStart)} – ${fmtDate(plan.periodEnd)}` : fmtDate(plan.weekOf)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {plan.items.length} line item{plan.items.length === 1 ? "" : "s"}
                    {plan.acceptedCount > 0 ? ` · ${plan.acceptedCount} already accepted` : ""}
                  </div>
                </div>
                <StatusBadge status={plan.status} />
              </div>

              {plan.rationale && (
                <div className="mt-2 rounded-md border border-border bg-background px-3 py-2">
                  <div className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
                    The Strategist&apos;s reasoning
                  </div>
                  <p className="m-0 mt-0.5 text-sm">{plan.rationale}</p>
                </div>
              )}

              {plan.goals.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
                    Goals in play
                  </span>
                  {plan.goals.map(([k, v]) => (
                    <span
                      key={k}
                      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
                    >
                      {prettyKey(k)} <b className="text-foreground">{valueToText(v)}</b>
                    </span>
                  ))}
                </div>
              )}

              {/* ------------------------------------------ activate controls */}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {activatable && (
                  <>
                    <button
                      type="button"
                      className="inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
                      disabled={activating || selected.size === 0}
                      onClick={activate}
                    >
                      {activating && <Spinner />}
                      {activating
                        ? "Activating…"
                        : selected.size === plan.items.length
                          ? `Activate plan — queue ${fmtNum(totalSelected)} draft${totalSelected === 1 ? "" : "s"}`
                          : `Activate ${selected.size} of ${plan.items.length} — queue ${fmtNum(totalSelected)} draft${totalSelected === 1 ? "" : "s"}`}
                    </button>
                    <button
                      type="button"
                      className="rounded-md px-2.5 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                      onClick={() =>
                        setSelected(
                          selected.size === plan.items.length
                            ? new Set()
                            : new Set(plan.items.map((_, i) => i)),
                        )
                      }
                    >
                      {selected.size === plan.items.length ? "Select none" : "Select all"}
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                  disabled={drafting || activating}
                  onClick={draftPlan}
                >
                  Re-draft
                </button>
              </div>
              {activatable && (
                <p className="m-0 mt-2 text-xs text-muted-foreground">
                  Untick a line to leave it out — activating supersedes still-queued orders from earlier
                  plans; work already in review or beyond survives and reattaches.
                </p>
              )}
              {!activatable && !activated && plan.status !== "proposed" && (
                <p className="m-0 mt-2 text-xs text-muted-foreground">
                  This prescription is {plan.status.replace(/_/g, " ")} — re-draft to propose a new week.
                </p>
              )}

              {superseded && (
                <div className="mt-3">
                  <Notice>
                    A newer prescription overtook this one — it can&apos;t be activated anymore.{" "}
                    <button
                      type="button"
                      className="font-semibold text-primary hover:underline"
                      onClick={draftPlan}
                    >
                      Re-draft the week
                    </button>{" "}
                    to get the current plan.
                  </Notice>
                </div>
              )}
              {activateError && (
                <div className="mt-3">
                  <ErrorBox message={activateError} />
                </div>
              )}

              {/* -------------------------------------- activation readout */}
              {activated && (
                <div className="mt-3 rounded-md border border-border bg-background px-3 py-2">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={activated.plan_status} />
                    <span className="text-sm font-medium">
                      {fmtNum(activated.work_orders_created)} work order
                      {activated.work_orders_created === 1 ? "" : "s"} queued
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {fmtNum(activated.activated_items.length)} line item
                    {activated.activated_items.length === 1 ? "" : "s"} activated ·{" "}
                    {fmtNum(activated.work_orders_reattached)} reattached ·{" "}
                    {fmtNum(activated.work_orders_superseded)} superseded
                  </div>
                  {activated.work_orders.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {activated.work_orders.map((wo) => (
                        <div key={wo.id} className="flex items-center justify-between gap-2 text-xs">
                          <span className="min-w-0 truncate text-muted-foreground">
                            {typeof wo.payload?.topic === "string" ? (wo.payload.topic as string) : wo.id}
                          </span>
                          <StatusBadge status={wo.status} />
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="mt-2">
                    <Link href="/manager" className="text-xs font-semibold text-primary hover:underline">
                      Track them in The Work →
                    </Link>
                  </div>
                </div>
              )}
            </Card>

            {/* ------------------------------------------------- the items */}
            {byPlatform.map(([platform, entries]) => (
              <section key={platform}>
                <div className="mb-2 mt-4 flex items-baseline justify-between gap-2">
                  <h3 className="text-[13px] font-semibold uppercase tracking-[1px] text-muted-foreground">
                    {platform}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {fmtNum(entries.reduce((n, e) => n + Math.max(e.item.count, 1), 0))} piece
                    {entries.reduce((n, e) => n + Math.max(e.item.count, 1), 0) === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="space-y-3">
                  {entries.map(({ item, index }) => (
                    <PlanItemCard
                      key={`${item.topic}-${index}`}
                      item={item}
                      index={index}
                      selectable={activatable}
                      checked={selected.has(index)}
                      onToggle={toggle}
                    />
                  ))}
                </div>
              </section>
            ))}

            {/* -------------------------------------------- growth actions */}
            {plan.growthActions.length > 0 && (
              <section>
                <h3 className="mb-2 mt-4 text-[13px] font-semibold uppercase tracking-[1px] text-muted-foreground">
                  Growth actions
                </h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  {plan.growthActions.map((g, i) => (
                    <Card key={i} variant="compact">
                      <div className="text-sm font-semibold">{g.action ?? "Growth action"}</div>
                      {g.why && <p className="m-0 mt-1 text-xs text-muted-foreground">Why: {g.why}</p>}
                      {g.evidence && g.evidence.length > 0 && (
                        <div className="mt-2">
                          <CitationChips citations={g.evidence} />
                        </div>
                      )}
                    </Card>
                  ))}
                </div>
                <p className="m-0 mt-2 text-xs text-muted-foreground">
                  Non-posting moves for the week — these also land as Follow-ups on Mission Control.
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
