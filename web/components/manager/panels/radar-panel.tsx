"use client";

/**
 * Radar — the five scanning eyes (POST /manager/scan/{eye}): Reddit problem
 * mining, industry trends, press about the brand, audience questions (AEO),
 * and appearance opportunities. Ported from bm2.0 radar-panel.tsx. Each lane
 * keeps its own busy/error/report so switching tabs never loses a result;
 * every finding also lands as a deduped Follow-up below.
 */

import { useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { CitationChips } from "@/components/manager/citations";
import { EmptyState, ErrorBox, Loading } from "@/components/manager/primitives";
import {
  errorMessage,
  isConflict,
  managerApi,
  type AppearanceItem,
  type AppearancesReport,
  type ContentOpportunity,
  type ContentRadarReport,
  type PressItem,
  type PressReport,
  type QuestionItem,
  type QuestionsReport,
  type TrendItem,
  type TrendsReport,
} from "@/lib/manager-api";
import { cx, fmtNum, Notice, PRESS_SIGNAL_TONE, ToneBadge } from "./bits";

type Lane = "reddit" | "trends" | "press" | "questions" | "appearances";

const LANES: { key: Lane; label: string }[] = [
  { key: "reddit", label: "Reddit problems" },
  { key: "trends", label: "Industry trends" },
  { key: "press", label: "Press about you" },
  { key: "questions", label: "Questions" },
  { key: "appearances", label: "Appearances" },
];

/** One lane's async state + runner. 409 = the eye is already scanning — a
 * notice, not an error. */
function useLane<T>(run: () => Promise<T>, onScanned?: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [report, setReport] = useState<T | null>(null);

  async function scan() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setReport(await run());
      onScanned?.(); // findings land as follow-up action items
    } catch (err) {
      if (isConflict(err)) setNotice("This scan is already running — try again in a moment.");
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, notice, report, scan };
}

function ScanButton({ busy, label, busyLabel, onClick }: { busy: boolean; label: string; busyLabel: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      disabled={busy}
      onClick={onClick}
    >
      {busy && <Spinner />}
      {busy ? busyLabel : label}
    </button>
  );
}

function LaneHead({ blurb, children }: { blurb: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground">{blurb}</span>
      {children}
    </div>
  );
}

function ScanNote({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 mt-0 text-xs text-muted-foreground">{children}</p>;
}

/** Caption reminding the operator that scan results are pushed downstream. */
function PushedCaption() {
  return <p className="mt-3 text-xs text-muted-foreground">Opportunities are pushed to your Follow-ups below.</p>;
}

export function RadarPanel({ onScanned }: { onScanned?: () => void }) {
  const [lane, setLane] = useState<Lane>("reddit");

  const reddit = useLane<ContentRadarReport>(() => managerApi.contentRadar(), onScanned);
  const trends = useLane<TrendsReport>(() => managerApi.trends(), onScanned);
  const press = useLane<PressReport>(() => managerApi.press(), onScanned);
  const questions = useLane<QuestionsReport>(() => managerApi.searchQuestions(), onScanned);
  const appearances = useLane<AppearancesReport>(() => managerApi.appearances(), onScanned);

  return (
    <Card variant="compact">
      <p className="mb-3 mt-0 text-xs text-muted-foreground">
        Your eyes on the outside world — Reddit for recurring problems, industry news for trends to
        ride, coverage to amplify, questions to own, and places to show up.
      </p>

      <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Radar lanes">
        {LANES.map((l) => (
          <button
            key={l.key}
            type="button"
            role="tab"
            aria-selected={lane === l.key}
            className={cx(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              lane === l.key
                ? "border-border bg-secondary text-foreground"
                : "border-transparent text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
            )}
            onClick={() => setLane(l.key)}
          >
            {l.label}
          </button>
        ))}
      </div>

      {/* ------------------------------------------------- Reddit problems */}
      {lane === "reddit" && (
        <div>
          <LaneHead blurb="Recurring niche problems worth answering with content.">
            <ScanButton busy={reddit.busy} label="Scan Reddit" busyLabel="Mining Reddit…" onClick={reddit.scan} />
          </LaneHead>
          {reddit.busy && <Loading label="Mining Reddit for recurring problems…" />}
          {reddit.notice && <Notice>{reddit.notice}</Notice>}
          {reddit.error && <ErrorBox message={reddit.error} onRetry={reddit.scan} />}
          {reddit.report && !reddit.busy && (
            <>
              <ScanNote>
                Scanned {fmtNum(reddit.report.reddit_threads_scanned)} thread
                {reddit.report.reddit_threads_scanned === 1 ? "" : "s"}
                {reddit.report.note ? ` · ${reddit.report.note}` : ""}
              </ScanNote>
              {reddit.report.competitor_themes.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {reddit.report.competitor_themes.map((t) => (
                    <span key={t} className="rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {reddit.report.opportunities.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {reddit.report.opportunities.map((o, i) => (
                    <OpportunityCard key={i} opp={o} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No recurring problems surfaced"
                  body="Nothing hit the recurrence bar this scan — try again later as threads accumulate."
                />
              )}
              <PushedCaption />
            </>
          )}
        </div>
      )}

      {/* ------------------------------------------------- Industry trends */}
      {lane === "trends" && (
        <div>
          <LaneHead blurb="Industry news moving right now — a timely angle to post about.">
            <ScanButton busy={trends.busy} label="Scan news" busyLabel="Scanning news…" onClick={trends.scan} />
          </LaneHead>
          {trends.busy && <Loading label="Scanning industry news…" />}
          {trends.notice && <Notice>{trends.notice}</Notice>}
          {trends.error && <ErrorBox message={trends.error} onRetry={trends.scan} />}
          {trends.report && !trends.busy && (
            <>
              <ScanNote>
                Scanned {fmtNum(trends.report.news_scanned)} news item{trends.report.news_scanned === 1 ? "" : "s"}
                {trends.report.note ? ` · ${trends.report.note}` : ""}
              </ScanNote>
              {trends.report.trends.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {trends.report.trends.map((t, i) => (
                    <TrendCard key={i} trend={t} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No trends to ride yet"
                  body="No industry news rose to a postable trend this scan — check back soon."
                />
              )}
              <PushedCaption />
            </>
          )}
        </div>
      )}

      {/* -------------------------------------------------- Press about you */}
      {lane === "press" && (
        <div>
          <LaneHead blurb="Coverage of your brand worth amplifying while it's fresh.">
            <ScanButton busy={press.busy} label="Scan press" busyLabel="Scanning coverage…" onClick={press.scan} />
          </LaneHead>
          {press.busy && <Loading label="Scanning coverage…" />}
          {press.notice && <Notice>{press.notice}</Notice>}
          {press.error && <ErrorBox message={press.error} onRetry={press.scan} />}
          {press.report && !press.busy && (
            <>
              <ScanNote>
                Scanned {fmtNum(press.report.mentions_scanned)} mention
                {press.report.mentions_scanned === 1 ? "" : "s"}
                {press.report.note ? ` · ${press.report.note}` : ""}
              </ScanNote>
              {press.report.items.length > 0 ? (
                <div className="space-y-3">
                  {press.report.items.map((it, i) => (
                    <PressCard key={i} item={it} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No content-worthy coverage yet"
                  body="No amplifiable mentions surfaced this scan — connected coverage will show here."
                />
              )}
              <PushedCaption />
            </>
          )}
        </div>
      )}

      {/* --------------------------------------------------------- Questions */}
      {lane === "questions" && (
        <div>
          <LaneHead blurb="High-intent questions your audience asks — own the authoritative answer.">
            <ScanButton busy={questions.busy} label="Scan questions" busyLabel="Finding questions…" onClick={questions.scan} />
          </LaneHead>
          {questions.busy && <Loading label="Finding what your audience asks…" />}
          {questions.notice && <Notice>{questions.notice}</Notice>}
          {questions.error && <ErrorBox message={questions.error} onRetry={questions.scan} />}
          {questions.report && !questions.busy && (
            <>
              <ScanNote>
                Scanned {fmtNum(questions.report.results_scanned)} result
                {questions.report.results_scanned === 1 ? "" : "s"}
                {questions.report.note ? ` · ${questions.report.note}` : ""}
              </ScanNote>
              {questions.report.questions.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {questions.report.questions.map((q, i) => (
                    <QuestionCard key={i} item={q} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No questions to own yet"
                  body="No high-intent questions surfaced this scan — check back soon."
                />
              )}
              <PushedCaption />
            </>
          )}
        </div>
      )}

      {/* ------------------------------------------------------- Appearances */}
      {lane === "appearances" && (
        <div>
          <LaneHead blurb="Guest, podcast, and speaking spots to reach a bigger audience.">
            <ScanButton busy={appearances.busy} label="Scan appearances" busyLabel="Finding places…" onClick={appearances.scan} />
          </LaneHead>
          {appearances.busy && <Loading label="Finding places to show up…" />}
          {appearances.notice && <Notice>{appearances.notice}</Notice>}
          {appearances.error && <ErrorBox message={appearances.error} onRetry={appearances.scan} />}
          {appearances.report && !appearances.busy && (
            <>
              <ScanNote>
                Scanned {fmtNum(appearances.report.results_scanned)} result
                {appearances.report.results_scanned === 1 ? "" : "s"}
                {appearances.report.note ? ` · ${appearances.report.note}` : ""}
              </ScanNote>
              {appearances.report.appearances.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {appearances.report.appearances.map((a, i) => (
                    <AppearanceCard key={i} item={a} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No appearance opportunities yet"
                  body="No places to show up surfaced this scan — check back soon."
                />
              )}
              <PushedCaption />
            </>
          )}
        </div>
      )}
    </Card>
  );
}

// ── suggestion cards (title + WHY + links as citation chips) ────────────────

function SuggestionShell({
  title,
  badge,
  children,
}: {
  title: string;
  badge: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0 text-sm font-semibold">{title}</div>
        {badge}
      </div>
      {children}
    </div>
  );
}

function OpportunityCard({ opp }: { opp: ContentOpportunity }) {
  return (
    <SuggestionShell title={opp.title} badge={<ToneBadge tone="amber">{opp.content_type}</ToneBadge>}>
      {opp.problem && <p className="m-0 mb-1.5 text-sm">{opp.problem}</p>}
      {opp.angle && <p className="m-0 mb-1.5 text-xs text-muted-foreground">{opp.angle}</p>}
      {opp.why && <p className="m-0 text-xs text-muted-foreground">Why: {opp.why}</p>}
      {typeof opp.recurring_count === "number" && opp.recurring_count > 0 && (
        <div className="mt-1.5">
          <ToneBadge tone="grey">
            recurring across {fmtNum(opp.recurring_count)} thread{opp.recurring_count === 1 ? "" : "s"}
          </ToneBadge>
        </div>
      )}
      {opp.reddit_links && opp.reddit_links.length > 0 && (
        <div className="mt-2">
          <CitationChips citations={opp.reddit_links} />
        </div>
      )}
    </SuggestionShell>
  );
}

function TrendCard({ trend }: { trend: TrendItem }) {
  return (
    <SuggestionShell title={trend.title} badge={<ToneBadge tone="blue">{trend.content_type}</ToneBadge>}>
      {trend.angle && <p className="m-0 mb-1.5 text-sm">{trend.angle}</p>}
      {trend.why && <p className="m-0 text-xs text-muted-foreground">Why: {trend.why}</p>}
      {trend.links && trend.links.length > 0 && (
        <div className="mt-2">
          <CitationChips citations={trend.links} />
        </div>
      )}
    </SuggestionShell>
  );
}

function PressCard({ item }: { item: PressItem }) {
  return (
    <SuggestionShell
      title={item.title}
      badge={<ToneBadge tone={PRESS_SIGNAL_TONE[item.signal] ?? "grey"}>{item.signal}</ToneBadge>}
    >
      {item.post && <p className="m-0 mb-1.5 whitespace-pre-line text-sm">{item.post}</p>}
      {item.links && item.links.length > 0 && (
        <div className="mt-2">
          <CitationChips citations={item.links} />
        </div>
      )}
    </SuggestionShell>
  );
}

function QuestionCard({ item }: { item: QuestionItem }) {
  return (
    <SuggestionShell title={item.question} badge={<ToneBadge tone="blue">{item.content_type}</ToneBadge>}>
      {item.answer_angle && <p className="m-0 mb-1.5 text-sm">{item.answer_angle}</p>}
      {item.why && <p className="m-0 text-xs text-muted-foreground">Why: {item.why}</p>}
      {item.links && item.links.length > 0 && (
        <div className="mt-2">
          <CitationChips citations={item.links} />
        </div>
      )}
    </SuggestionShell>
  );
}

function AppearanceCard({ item }: { item: AppearanceItem }) {
  return (
    <SuggestionShell title={item.title} badge={<ToneBadge tone="amber">{item.kind}</ToneBadge>}>
      {item.fit && <p className="m-0 mb-1.5 text-sm">{item.fit}</p>}
      {item.approach && <p className="m-0 text-xs text-muted-foreground">Approach: {item.approach}</p>}
      {item.links && item.links.length > 0 && (
        <div className="mt-2">
          <CitationChips citations={item.links} />
        </div>
      )}
    </SuggestionShell>
  );
}
