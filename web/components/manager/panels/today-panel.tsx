"use client";

/**
 * Today — the daily heartbeat. Morning brief (managerApi.brief — deterministic,
 * cheap), the latest daily digest, and the "Run today's cycle" button with a
 * per-step report readout. Ported from bm2.0 daily-brief-panel.tsx + the
 * morning-brief card on brand/[id]/page.tsx.
 */

import Link from "next/link";
import { useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { CitationChips } from "@/components/manager/citations";
import { EmptyState, ErrorBox, Loading, useLoad } from "@/components/manager/primitives";
import {
  errorMessage,
  isConflict,
  managerApi,
  type DailyCycleReport,
  type DailyDigest,
  type DailyDigestItem,
} from "@/lib/manager-api";
import {
  ACTION_KIND_TONE,
  fmtDateLong,
  fmtNum,
  Notice,
  PanelHead,
  prettyKey,
  ReportReadout,
  ToneBadge,
} from "./bits";

export function TodayPanel({
  refreshKey,
  onCycleRan,
  onScrollToAction,
}: {
  refreshKey: number;
  onCycleRan: () => void;
  onScrollToAction: (actionId: string) => void;
}) {
  const brief = useLoad(() => managerApi.brief(), []);
  const digest = useLoad<DailyDigest>(() => managerApi.dailyDigest(), [refreshKey]);

  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);
  const [report, setReport] = useState<DailyCycleReport | null>(null);

  const digestData = digest.data ?? null;
  const items = digestData?.items ?? [];
  const hasRun = Boolean(digestData && digestData.date);

  async function runCycle() {
    if (busy) return;
    setBusy(true);
    setRunError(null);
    setRunNotice(null);
    try {
      const r = await managerApi.runDailyCycle();
      setReport(r);
      digest.reload();
      onCycleRan(); // the cycle mutates follow-ups downstream
    } catch (err) {
      if (isConflict(err)) setRunNotice("Today's cycle is already running — check back in a moment.");
      else setRunError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const b = brief.data;

  return (
    <div className="space-y-3">
      {/* ------------------------------------------------ morning brief */}
      <Card variant="compact">
        {brief.loading && <Loading label="Assembling your morning brief…" />}
        {brief.error && !brief.loading && <ErrorBox message={brief.error} onRetry={brief.reload} />}
        {b && !brief.loading && (
          <>
            <div className="text-[11px] font-semibold uppercase tracking-[1px] text-muted-foreground">
              Morning Brief · {fmtDateLong(b.date)}
            </div>
            <h3 className="mt-1 text-lg font-semibold leading-snug">{b.headline}</h3>

            {b.sections.map((s) => (
              <div key={s.title} className="mt-3">
                <div className="text-xs font-semibold uppercase tracking-[.4px] text-muted-foreground">{s.title}</div>
                <p className="mt-1 whitespace-pre-line text-sm">{s.body}</p>
                {s.citations && s.citations.length > 0 && (
                  <div className="mt-1.5">
                    <CitationChips citations={s.citations} />
                  </div>
                )}
              </div>
            ))}

            {b.recommended_actions.length > 0 && (
              <div className="mt-3 rounded-md border border-border bg-background px-3 py-2">
                <div className="text-xs font-semibold uppercase tracking-[.4px] text-muted-foreground">
                  Recommended actions
                </div>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                  {b.recommended_actions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {Object.entries(b.queue_counts).map(([k, v]) => (
                <span
                  key={k}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
                >
                  {prettyKey(k)} <b className="text-foreground">{fmtNum(v)}</b>
                </span>
              ))}
              {b.plan ? (
                <Link
                  href="/manager/plan"
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:border-ring hover:text-foreground"
                >
                  plan · week of {b.plan.week_of} <b className="text-foreground">{b.plan.status}</b>
                </Link>
              ) : (
                <Link
                  href="/manager/plan"
                  className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:border-ring hover:text-foreground"
                >
                  no weekly plan yet — draft one →
                </Link>
              )}
            </div>
          </>
        )}
      </Card>

      {/* -------------------------------------------------- daily digest */}
      <Card variant="compact">
        <PanelHead
          title={<>Today&rsquo;s cycle{hasRun && digestData?.date ? ` · ${fmtDateLong(digestData.date)}` : ""}</>}
          sub={digestData?.summary || "Your daily heartbeat — the follow-ups due plus the next move to make."}
        >
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={busy}
            onClick={runCycle}
          >
            {busy && <Spinner />}
            {busy ? "Running…" : hasRun ? "Re-run today's cycle" : "Run today's cycle"}
          </button>
        </PanelHead>

        {runNotice && <div className="mb-2"><Notice>{runNotice}</Notice></div>}
        {runError && (
          <div className="mb-2">
            <ErrorBox message={runError} onRetry={runCycle} />
          </div>
        )}
        {digest.error && !digestData && <ErrorBox message={digest.error} onRetry={digest.reload} />}

        {report && !busy && (
          <div className="mb-3">
            <ReportReadout report={report} title="Cycle report — step by step" />
          </div>
        )}

        {items.length > 0 ? (
          <div className="space-y-1.5">
            {items.map((it, i) => (
              <DigestRow key={`${it.action_id ?? "x"}-${i}`} item={it} onScrollToAction={onScrollToAction} />
            ))}
          </div>
        ) : (
          !digest.loading &&
          digestData && (
            <EmptyState
              title={hasRun ? "Nothing due today" : "No cycle run yet today"}
              body={
                hasRun
                  ? "Line up the next move with a radar scan or a collaboration play below."
                  : "Run it now to assemble your follow-ups and next steps."
              }
            />
          )
        )}
        {digest.loading && !digestData && <Loading label="Loading today's digest…" />}
      </Card>
    </div>
  );
}

function DigestRow({
  item,
  onScrollToAction,
}: {
  item: DailyDigestItem;
  onScrollToAction: (actionId: string) => void;
}) {
  const clickable = Boolean(item.action_id);
  return (
    <div
      className={`flex items-start gap-2.5 rounded-md border border-border bg-background px-3 py-2 ${
        clickable ? "cursor-pointer transition-colors hover:border-ring" : ""
      }`}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? () => onScrollToAction(item.action_id as string) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onScrollToAction(item.action_id as string);
              }
            }
          : undefined
      }
    >
      <ToneBadge tone={ACTION_KIND_TONE[item.kind] ?? "grey"}>{item.kind}</ToneBadge>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{item.title}</div>
        <div className="text-xs text-muted-foreground">{item.why}</div>
      </div>
      {clickable && <span className="shrink-0 text-xs text-muted-foreground">View →</span>}
    </div>
  );
}
