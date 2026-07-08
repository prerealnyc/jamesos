"use client";

/**
 * Competitors & peers — discover (202 + poll, 409 while running) → approve/
 * reject by handle → tracked roster + snapshot pass, then the collaboration
 * plays and always-on growth levers. Ported from bm2.0 competitor-panel.tsx +
 * collaboration-panel.tsx (the growth-plan panel's /growth and /daily-plan
 * routes don't exist on the merged backend — growth levers come from the
 * collab report's visibility plays, and daily activities via the daily cycle).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { EmptyState, ErrorBox, Loading, StatusBadge, useLoad } from "@/components/manager/primitives";
import {
  errorMessage,
  isConflict,
  managerApi,
  type CollabPlay,
  type CollabReport,
  type VisibilityPlay,
  type WatchlistEntry,
} from "@/lib/manager-api";
import {
  CONFIDENCE_TONE,
  Notice,
  PanelHead,
  PEER_KIND_TONE,
  ReportReadout,
  ToneBadge,
  type Tone,
} from "./bits";

/** The four buckets, in the order they read best: who's above you, who you
 * want to become, who you partner with, who you fight. */
const BUCKETS: { kind: string; title: string; blurb: string; tone: Tone }[] = [
  { kind: "leader", title: "Leaders", blurb: "Top of your industry", tone: "blue" },
  { kind: "aspirational", title: "Aspirational", blurb: "The next level to reach", tone: "amber" },
  { kind: "collaborator", title: "Collaborators", blurb: "Partner with", tone: "green" },
  { kind: "competitor", title: "Competitors", blurb: "Direct competition", tone: "red" },
];

function KindBadge({ kind }: { kind: string }) {
  return <ToneBadge tone={PEER_KIND_TONE[kind] ?? "grey"}>{kind}</ToneBadge>;
}

function SmallButton({
  children,
  variant = "ghost",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  const v =
    variant === "primary"
      ? "bg-primary text-primary-foreground hover:bg-primary/90"
      : variant === "danger"
        ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
        : "text-muted-foreground hover:bg-secondary hover:text-foreground";
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1 text-xs font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${v}`}
    >
      {children}
    </button>
  );
}

type DiscoveryState = "idle" | "running" | "succeeded" | "failed";

export function CompetitorsPanel({ onChange }: { onChange?: () => void }) {
  const candidates = useLoad(() => managerApi.peerCandidates(), []);
  const profile = useLoad(() => managerApi.brandProfile(), []);

  // ---- discovery job (202 + poll; 409 = already in flight, just poll it)
  const [discState, setDiscState] = useState<DiscoveryState>("idle");
  const [discError, setDiscError] = useState<string | null>(null);
  const [discNotice, setDiscNotice] = useState<string | null>(null);
  const polling = useRef(false);

  const poll = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      const status = await managerApi.awaitPeerDiscovery();
      if (status.status === "succeeded") {
        setDiscState("succeeded");
        candidates.reload();
      } else {
        setDiscState("failed");
        setDiscError(status.error || "Discovery couldn't finish. Try again.");
      }
    } catch (err) {
      setDiscState("failed");
      setDiscError(errorMessage(err));
    } finally {
      polling.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reflect any prior/in-flight discovery run so a reload doesn't hide progress.
  useEffect(() => {
    let cancelled = false;
    managerApi
      .peerDiscoveryStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.status === "running") {
          setDiscState("running");
          void poll();
        }
      })
      .catch(() => {
        /* status is best-effort on mount */
      });
    return () => {
      cancelled = true;
    };
  }, [poll]);

  async function discover() {
    if (discState === "running") return;
    setDiscState("running");
    setDiscError(null);
    setDiscNotice(null);
    try {
      await managerApi.startPeerDiscovery();
    } catch (err) {
      if (isConflict(err)) {
        // a run is already in flight — watch it instead of erroring
        setDiscNotice("Discovery is already running — watching that run.");
      } else {
        setDiscState("failed");
        setDiscError(errorMessage(err));
        return;
      }
    }
    void poll();
  }

  // ---- per-candidate approve/reject (by HANDLE on the merged backend)
  const [actingHandle, setActingHandle] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function act(handle: string, fn: (h: string) => Promise<unknown>) {
    if (actingHandle) return;
    setActingHandle(handle);
    setActionError(null);
    try {
      await fn(handle);
      candidates.reload(); // leaves the candidate list…
      profile.reload(); // …and (approve) joins the tracked roster
      onChange?.();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActingHandle(null);
    }
  }

  // ---- snapshot pass over the tracked roster
  const [snapBusy, setSnapBusy] = useState(false);
  const [snapError, setSnapError] = useState<string | null>(null);
  const [snapReport, setSnapReport] = useState<Record<string, unknown> | null>(null);

  async function snapshot() {
    if (snapBusy) return;
    setSnapBusy(true);
    setSnapError(null);
    try {
      setSnapReport(await managerApi.snapshotPeers());
    } catch (err) {
      if (isConflict(err)) setSnapReport({ note: "A snapshot pass is already running — check back shortly." });
      else setSnapError(errorMessage(err));
    } finally {
      setSnapBusy(false);
    }
  }

  const cands = (candidates.data?.candidates ?? []).filter((c) => c.status === "candidate");
  const tracked = profile.data?.tracked_peers ?? [];
  const busy = discState === "running";

  return (
    <div className="space-y-3">
      <Card variant="compact">
        <PanelHead
          title="Competitors & peers"
          sub="Discovery proposes candidates across four tiers — approve the ones worth watching and only those are monitored."
        >
          <SmallButton variant="primary" disabled={busy} onClick={discover}>
            {busy && <Spinner />}
            {busy ? "Scanning…" : "Discover competitors & peers"}
          </SmallButton>
        </PanelHead>

        {busy && <Loading label="Scanning your industry for leaders, competitors, and collaborators…" />}
        {discNotice && busy && <Notice>{discNotice}</Notice>}
        {discState === "failed" && discError && (
          <div className="mb-3">
            <ErrorBox message={discError} onRetry={discover} />
          </div>
        )}

        {/* ------------------------------------------------- candidates */}
        {candidates.error && (
          <div className="mb-3">
            <ErrorBox message={candidates.error} onRetry={candidates.reload} />
          </div>
        )}
        {actionError && (
          <div className="mb-3">
            <ErrorBox message={actionError} />
          </div>
        )}

        {cands.length > 0 && (
          <div className="space-y-4">
            {BUCKETS.map((b) => {
              const inBucket = cands.filter((c) => c.kind === b.kind);
              if (inBucket.length === 0) return null;
              return (
                <div key={b.kind}>
                  <div className="mb-2 flex items-center gap-2">
                    <ToneBadge tone={b.tone}>{b.title}</ToneBadge>
                    <span className="text-xs text-muted-foreground">{b.blurb}</span>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {inBucket.map((c) => (
                      <CandidateCard
                        key={`${c.platform}-${c.handle}`}
                        candidate={c}
                        acting={actingHandle}
                        onApprove={() => act(c.handle, managerApi.approvePeer)}
                        onReject={() => act(c.handle, managerApi.rejectPeer)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
            {/* candidates with an unknown kind still get rendered */}
            {cands.some((c) => !BUCKETS.find((b) => b.kind === c.kind)) && (
              <div className="grid gap-3 sm:grid-cols-2">
                {cands
                  .filter((c) => !BUCKETS.find((b) => b.kind === c.kind))
                  .map((c) => (
                    <CandidateCard
                      key={`${c.platform}-${c.handle}`}
                      candidate={c}
                      acting={actingHandle}
                      onApprove={() => act(c.handle, managerApi.approvePeer)}
                      onReject={() => act(c.handle, managerApi.rejectPeer)}
                    />
                  ))}
              </div>
            )}
          </div>
        )}

        {!busy && candidates.data && cands.length === 0 && discState !== "failed" && (
          <EmptyState
            title="No candidates to review"
            body={
              discState === "succeeded"
                ? "Discovery didn't surface new peers this time — try again later."
                : "Run Discover to find leaders, competitors, and collaborators in your space."
            }
          />
        )}
        {candidates.loading && !candidates.data && <Loading label="Loading peer candidates…" />}

        {/* ---------------------------------------------------- tracked */}
        <div className="mt-4">
          <PanelHead title="Who you're tracking" sub="Approved peers only — the Peer Agent monitors these.">
            <SmallButton disabled={snapBusy || tracked.length === 0} onClick={snapshot}>
              {snapBusy && <Spinner />}
              {snapBusy ? "Snapshotting…" : "Snapshot peers"}
            </SmallButton>
          </PanelHead>
          {profile.error && <ErrorBox message={profile.error} onRetry={profile.reload} />}
          {snapError && (
            <div className="mb-2">
              <ErrorBox message={snapError} onRetry={snapshot} />
            </div>
          )}
          {snapReport && !snapBusy && (
            <div className="mb-3">
              <ReportReadout report={snapReport} title="Snapshot report" />
            </div>
          )}
          {profile.data && tracked.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No approved peers yet — approve a candidate above and monitoring starts from there.
            </p>
          )}
          {tracked.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {tracked.map((p) => (
                <div key={`${p.platform}-${p.handle}`} className="rounded-md border border-border bg-background p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{p.name || `@${p.handle}`}</div>
                      <div className="text-xs text-muted-foreground">
                        @{p.handle} · <span className="capitalize">{p.platform}</span>
                      </div>
                    </div>
                    <KindBadge kind={p.kind} />
                  </div>
                  {p.why && <p className="mt-1.5 text-xs text-muted-foreground">{p.why}</p>}
                  <p className="mt-1.5 text-[11px] text-muted-foreground">
                    Monitoring — snapshots build growth history over time.
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      <CollabCard onGenerated={onChange} />
    </div>
  );
}

function CandidateCard({
  candidate: c,
  acting,
  onApprove,
  onReject,
}: {
  candidate: WatchlistEntry;
  acting: string | null;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{c.display_name || `@${c.handle}`}</div>
          <div className="text-xs text-muted-foreground">
            @{c.handle} · <span className="capitalize">{c.platform}</span>
          </div>
        </div>
        <StatusBadge status={c.status} />
      </div>
      {c.reason && <p className="mt-1.5 text-xs text-muted-foreground">{c.reason}</p>}
      <div className="mt-2.5 flex gap-2">
        <SmallButton variant="primary" disabled={acting !== null} onClick={onApprove}>
          {acting === c.handle ? "Working…" : "Approve"}
        </SmallButton>
        <SmallButton disabled={acting !== null} onClick={onReject}>
          Reject
        </SmallButton>
      </div>
    </div>
  );
}

// ── collaboration plays + always-on growth levers ───────────────────────────

function CollabCard({ onGenerated }: { onGenerated?: () => void }) {
  const latest = useLoad(() => managerApi.latestCollab(), []);

  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genNotice, setGenNotice] = useState<string | null>(null);
  // Local report supersedes the loaded one after a generate.
  const [fresh, setFresh] = useState<CollabReport | null>(null);

  const report = fresh ?? latest.data ?? null;
  const generated = fresh != null || latest.data?.generated === true;
  const plays = report?.plays ?? [];
  const visibility = report?.visibility_plays ?? [];

  async function generate() {
    if (busy) return;
    setBusy(true);
    setGenError(null);
    setGenNotice(null);
    try {
      const r = await managerApi.generateCollab();
      setFresh(r);
      onGenerated?.(); // plays/levers land as follow-up action items
    } catch (err) {
      if (isConflict(err)) setGenNotice("A collaboration pass is already running — check back shortly.");
      else setGenError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card variant="compact">
      <PanelHead
        title="Collaboration & growth plays"
        sub="For each approved peer, a play matched to your relationship — plus always-on growth levers so there's a next action even with no partner."
      >
        <SmallButton variant="primary" disabled={busy} onClick={generate}>
          {busy && <Spinner />}
          {busy ? "Thinking…" : "Plan collaborations & growth"}
        </SmallButton>
      </PanelHead>

      {busy && <Loading label="Thinking like a brand manager — matching plays to each peer…" />}
      {genNotice && <Notice>{genNotice}</Notice>}
      {genError && (
        <div className="mb-3">
          <ErrorBox message={genError} onRetry={generate} />
        </div>
      )}
      {latest.error && !report && (
        <div className="mb-3">
          <ErrorBox message={latest.error} onRetry={latest.reload} />
        </div>
      )}
      {latest.loading && !report && !busy && <Loading label="Loading the latest plays…" />}

      {report?.note && !busy && <p className="mb-3 mt-0 text-xs text-muted-foreground">{report.note}</p>}

      {plays.length > 0 && (
        <div className="mb-4 space-y-4">
          {BUCKETS.map((b) => {
            const inBucket = plays.filter((p) => p.kind === b.kind);
            if (inBucket.length === 0) return null;
            return (
              <div key={b.kind}>
                <div className="mb-2">
                  <ToneBadge tone={b.tone}>{b.title}</ToneBadge>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {inBucket.map((p) => (
                    <PlayCard key={`${p.kind}-${p.peer_handle}`} play={p} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!busy && report && generated && plays.length === 0 && (
        <div className="mb-4">
          <EmptyState
            title="No collaboration targets yet"
            body="Approve some peers above and re-run this — the growth levers below always apply in the meantime."
          />
        </div>
      )}
      {!busy && latest.data && !generated && plays.length === 0 && visibility.length === 0 && (
        <div className="mb-4">
          <EmptyState
            title="Plan your next moves"
            body="Approve peers above for tailored collaboration plays, or generate always-on visibility plays right now."
          />
        </div>
      )}

      {visibility.length > 0 && (
        <div>
          <div className="mb-2 flex items-center gap-2">
            <ToneBadge tone="grey">Growth levers</ToneBadge>
            <span className="text-xs text-muted-foreground">always-on — the floor when no partner exists</span>
          </div>
          <div className="space-y-2">
            {visibility.map((v, i) => (
              <VisibilityCard key={`${v.title}-${i}`} play={v} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function PlayCard({ play }: { play: CollabPlay }) {
  const confTone = CONFIDENCE_TONE[play.outreach_confidence] ?? "grey";
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0 truncate text-sm font-semibold">@{play.peer_handle}</div>
        <ToneBadge tone={PEER_KIND_TONE[play.kind] ?? "grey"}>{play.kind}</ToneBadge>
      </div>
      <p className="m-0 mb-1.5 text-sm">{play.play}</p>
      {play.mutual_interest && <p className="m-0 mb-2 text-xs text-muted-foreground">{play.mutual_interest}</p>}
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-muted-foreground">{play.outreach_path}</span>
        <ToneBadge tone={confTone}>{play.outreach_confidence}</ToneBadge>
      </div>
      <p className="m-0 mt-1.5 text-[11px] italic text-muted-foreground">Lead — verify before outreach</p>
    </div>
  );
}

function VisibilityCard({ play }: { play: VisibilityPlay }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="text-sm font-semibold">{play.title}</div>
      {play.action && <p className="m-0 mt-1 text-sm">{play.action}</p>}
      {play.why && <p className="m-0 mt-1 text-xs text-muted-foreground">Why: {play.why}</p>}
    </div>
  );
}
