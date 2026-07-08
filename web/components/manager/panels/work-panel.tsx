"use client";

/**
 * The Work — the execution spine as a dense table (GET /manager/work):
 * Identified → Drafted → Approve → Published → Impact. Rows expand to the
 * artifact preview and the status-driven controls. Ported from bm2.0
 * execution-panel.tsx onto the merged routes: approve (which also publishes),
 * reject with a reason (the system learns from it), and publish/retry with
 * honest ok:false failures. Impact lands via the daily cycle (measured rows
 * show actual metrics; there is no manual measure route here).
 */

import { Fragment, useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { CitationChips } from "@/components/manager/citations";
import { EmptyState, ErrorBox, Loading, useLoad, WorkOrderStatusBadge } from "@/components/manager/primitives";
import {
  errorMessage,
  isConflict,
  managerApi,
  type PublishedRef,
  type PublishResult,
  type WorkResponse,
  type WorkRow,
} from "@/lib/manager-api";
import { cx, fmtDate, fmtDateTime, fmtNum, KV, Notice, prettyKey } from "./bits";

function publishFailureText(res: PublishResult): string {
  if (typeof res.detail === "string" && res.detail) return res.detail;
  if (res.error) return res.error;
  if (res.detail && typeof res.detail === "object") {
    const flat = Object.entries(res.detail)
      .map(([k, v]) => `${prettyKey(k)}: ${String(v)}`)
      .join(" · ");
    if (flat) return flat;
  }
  return "Couldn't publish — check your connected accounts. The outbox retries automatically.";
}

/** Where a published asset lives — a real link if we have a url, else the
 * provider + ref the aggregator handed back. */
function PublishedLink({ pref }: { pref: PublishedRef }) {
  let label = pref.url ?? "";
  if (pref.url) {
    try {
      label = new URL(pref.url).hostname.replace(/^www\./, "");
    } catch {
      /* keep raw */
    }
  } else {
    label = `${pref.provider ?? "published"}${pref.ref ? ` · ${pref.ref}` : ""}`;
  }
  return (
    <KV label="Published">
      {pref.url ? (
        <a href={pref.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
          {label}
        </a>
      ) : (
        label
      )}
    </KV>
  );
}

function MetricsRow({ label, metrics }: { label: string; metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">{label}</span>
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
        >
          {prettyKey(k)} <b className="text-right text-foreground">{fmtNum(v)}</b>
        </span>
      ))}
    </div>
  );
}

export function WorkPanel({ refreshKey, onMutated }: { refreshKey: number; onMutated?: () => void }) {
  const work = useLoad<WorkResponse>(() => managerApi.listWork(), [refreshKey]);
  const rows = work.data?.work ?? [];
  const [openId, setOpenId] = useState<string | null>(null);

  function onDone() {
    work.reload();
    onMutated?.();
  }

  return (
    <Card variant="compact">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="m-0 text-xs text-muted-foreground">
          Identified → Drafted → Approve → Published → Impact. Every opportunity the manager put hands on.
        </p>
        {work.loading && work.data && <Spinner />}
      </div>

      {work.error && (
        <div className="mb-3">
          <ErrorBox message={work.error} onRetry={work.reload} />
        </div>
      )}
      {work.loading && !work.data && <Loading label="Loading the work…" />}

      {work.data && rows.length === 0 && (
        <EmptyState
          title="No work yet"
          body='Draft an opportunity from Follow-ups below — pick one and hit "Draft it".'
        />
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-[.4px] text-muted-foreground">
                <th className="py-1.5 pr-3 font-semibold">Topic</th>
                <th className="py-1.5 pr-3 font-semibold">Type</th>
                <th className="py-1.5 pr-3 font-semibold">Platform</th>
                <th className="py-1.5 pr-3 font-semibold">Status</th>
                <th className="py-1.5 pr-3 text-right font-semibold">Attempts</th>
                <th className="py-1.5 pr-3 text-right font-semibold">Created</th>
                <th className="py-1.5 text-right font-semibold" aria-label="Expand" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const open = openId === r.work_order_id;
                return (
                  <Fragment key={r.work_order_id}>
                    <tr
                      className={cx("cursor-pointer border-b border-border/60 transition-colors hover:bg-secondary/40", open && "bg-secondary/40")}
                      onClick={() => setOpenId(open ? null : r.work_order_id)}
                    >
                      <td className="max-w-[280px] truncate py-2 pr-3 font-medium">
                        {r.topic || r.opportunity?.title || r.work_order_id.slice(0, 8)}
                      </td>
                      <td className="py-2 pr-3 capitalize text-muted-foreground">
                        {(r.content_type ?? "—").replace(/_/g, " ")}
                        {r.routed_to === "production" && (
                          <span className="ml-1.5 text-[10px] uppercase text-primary">→ production</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 capitalize text-muted-foreground">{r.platform ?? "—"}</td>
                      <td className="py-2 pr-3">
                        <WorkOrderStatusBadge status={r.status} />
                      </td>
                      <td className="py-2 pr-3 text-right text-muted-foreground">{fmtNum(r.publish_attempts.length)}</td>
                      <td className="whitespace-nowrap py-2 pr-3 text-right text-muted-foreground">{fmtDate(r.created_at)}</td>
                      <td className="py-2 text-right text-xs text-muted-foreground" aria-hidden>
                        {open ? "▴" : "▾"}
                      </td>
                    </tr>
                    {open && (
                      <tr className="border-b border-border/60">
                        <td colSpan={7} className="bg-background/60 px-2 py-3">
                          <WorkDetail row={r} onDone={onDone} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** The expanded row: opportunity, artifact preview, decision + publish
 * controls per status, publish attempts, approvals log, and impact. `busy`
 * always resets in a finally — a control revealed by the new status must not
 * be stuck disabled. */
function WorkDetail({ row, onDone }: { row: WorkRow; onDone: () => void }) {
  const artifact = row.artifact;
  const media = (artifact?.media ?? {}) as Record<string, unknown>;
  const isProduction = row.routed_to === "production";
  const inReview = row.status === "review" || row.status === "pending_approval";

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [publishNote, setPublishNote] = useState<string | null>(null);

  const busyAny = busy !== null;

  async function run(label: string, fn: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (err) {
      if (isConflict(err)) setNotice(errorMessage(err)); // refused state-machine edge, not a crash
      else setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const approve = () =>
    run("approve", async () => {
      const res = await managerApi.approveWorkOrder(row.work_order_id, reason.trim());
      if (res.publish && res.publish.ok === false) {
        // approved, but the publish leg failed — say so honestly
        setPublishNote(publishFailureText(res.publish));
      }
      onDone();
    });

  const reject = () =>
    run("reject", async () => {
      await managerApi.rejectWorkOrder(row.work_order_id, reason.trim());
      onDone();
    });

  const publish = () =>
    run("publish", async () => {
      setPublishNote(null);
      const res = await managerApi.publishWorkOrder(row.work_order_id);
      if (!res.ok) {
        setPublishNote(publishFailureText(res));
        return; // honest failure — not a success
      }
      onDone();
    });

  const tags = Array.isArray(media.tags) ? (media.tags as string[]) : [];
  const links = Array.isArray(media.links) ? (media.links as string[]) : [];
  const actual = row.actual_metrics ?? {};

  return (
    <div className="space-y-3">
      {/* Identified — the opportunity this work put hands on */}
      {row.opportunity && (
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <div className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">Identified</div>
          <div className="mt-0.5 text-sm">{row.opportunity.title}</div>
          {row.opportunity.source && (
            <div className="mt-0.5 text-xs text-muted-foreground">source: {row.opportunity.source}</div>
          )}
        </div>
      )}

      {/* video/image routed to the in-process production pipelines */}
      {isProduction && (
        <Notice>
          Handed to the production pipelines (video/image) — nothing to draft or approve here; it
          surfaces in the render queue.
        </Notice>
      )}

      {/* Drafted — the artifact preview */}
      {artifact && !isProduction && (
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
              Draft · v{artifact.version} · {artifact.kind}
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {typeof artifact.review?.voice_score === "number" && (
                <span>
                  voice <b className="text-foreground">{artifact.review.voice_score.toFixed(2)}</b>
                </span>
              )}
              {artifact.review?.passed === true && <span className="text-accent">review passed</span>}
              {artifact.review?.passed === false && <span className="text-destructive">review flagged</span>}
            </div>
          </div>
          {(typeof media.title === "string" || typeof media.subject === "string") && (
            <div className="mt-1 text-sm font-semibold">{String(media.title ?? media.subject)}</div>
          )}
          {typeof media.preheader === "string" && <p className="m-0 mt-0.5 text-xs text-muted-foreground">{media.preheader}</p>}
          {typeof media.meta_description === "string" && (
            <p className="m-0 mt-0.5 text-xs text-muted-foreground">{media.meta_description}</p>
          )}
          <div className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background px-3 py-2 text-sm">
            {artifact.content}
          </div>
          {artifact.review?.drift && artifact.review.drift.length > 0 && (
            <p className="m-0 mt-1.5 text-xs text-warning">Drift: {artifact.review.drift.join(" · ")}</p>
          )}
          {tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <span key={t} className="rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          )}
          {links.length > 0 && (
            <div className="mt-2">
              <CitationChips citations={links} />
            </div>
          )}
        </div>
      )}

      {!artifact && !isProduction && (row.status === "queued" || row.status === "generating") && (
        <p className="m-0 text-xs text-muted-foreground">
          {row.status === "generating" ? "Drafting now…" : "Queued — waiting to be drafted."}
        </p>
      )}

      {row.predicted_metrics && Object.keys(row.predicted_metrics).length > 0 && (
        <MetricsRow label="Predicted" metrics={row.predicted_metrics} />
      )}

      {error && <ErrorBox message={error} />}
      {notice && <Notice>{notice}</Notice>}

      {/* review / pending_approval — the decision, with reason capture */}
      {inReview && !isProduction && (
        <div className="rounded-md border border-border bg-card px-3 py-2">
          {row.status === "review" && (
            <p className="m-0 mb-2 text-xs text-muted-foreground">
              In review — the Reviewer hasn&apos;t cleared this draft for approval yet. You can still reject it.
            </p>
          )}
          <textarea
            className="min-h-[56px] w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            value={reason}
            disabled={busyAny}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional for approve, required for reject) — the system learns from it."
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {row.status === "pending_approval" && (
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-md bg-accent/20 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/30 disabled:pointer-events-none disabled:opacity-50"
                disabled={busyAny}
                onClick={approve}
              >
                {busy === "approve" && <Spinner />}
                {busy === "approve" ? "Approving…" : "Approve & publish"}
              </button>
            )}
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-1.5 text-xs font-semibold text-destructive transition-colors hover:bg-destructive/20 disabled:pointer-events-none disabled:opacity-50"
              disabled={busyAny || !reason.trim()}
              title={!reason.trim() ? "A reject needs a reason — that's what the system learns from" : undefined}
              onClick={reject}
            >
              {busy === "reject" && <Spinner />}
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
          </div>
        </div>
      )}

      {/* approved — publish (or retry a failed publish) */}
      {row.status === "approved" && (
        <div>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
            disabled={busyAny}
            onClick={publish}
          >
            {busy === "publish" && <Spinner />}
            {busy === "publish" ? "Publishing…" : row.publish_attempts.length > 0 ? "Retry publish" : "Publish"}
          </button>
          {row.publish_attempts.length > 0 && (
            <span className="ml-2 text-xs text-muted-foreground">
              {fmtNum(row.publish_attempts.length)} earlier attempt{row.publish_attempts.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}
      {publishNote && (
        <p className="m-0 text-xs font-medium text-destructive" role="alert">
          {publishNote}
        </p>
      )}

      {/* published / measured — the live link + impact */}
      {(row.status === "published" || row.status === "measured") && (
        <div className="rounded-md border border-border bg-card px-3 py-2">
          {row.published_ref && <PublishedLink pref={row.published_ref} />}
          {row.status === "published" && (
            <p className="m-0 mt-1 text-xs text-muted-foreground">
              Live — impact is measured by the daily cycle and lands here as actual metrics.
            </p>
          )}
          {row.status === "measured" && (
            <div className="mt-1.5 space-y-1.5">
              <MetricsRow label="Actual" metrics={actual} />
              {row.measured_at && <p className="m-0 text-xs text-muted-foreground">Measured {fmtDateTime(row.measured_at)}</p>}
            </div>
          )}
        </div>
      )}

      {/* approvals log */}
      {row.approvals.length > 0 && (
        <div className="space-y-1">
          {row.approvals.map((a, i) => (
            <div key={i} className="flex items-baseline gap-2 text-xs text-muted-foreground">
              <span className="whitespace-nowrap">{fmtDateTime(a.at)}</span>
              <span className={a.action === "approve" ? "font-semibold text-accent" : "font-semibold text-destructive"}>
                {a.action}
              </span>
              {a.reason && <span className="min-w-0">— {a.reason}</span>}
              <span className="ml-auto whitespace-nowrap">{a.actor}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
