"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorBox, Loading } from "@/components/manager/primitives";
import { Button, Card } from "@/components/ui";
import {
  errorMessage,
  isConflict,
  managerApi,
  type VoiceProfileResponse,
} from "@/lib/manager-api";

const VOICE_POLL_MS = 3000;

const ORIGIN_LABEL: Record<string, string> = {
  harvested: "Harvested from your channels",
  uploaded: "Uploaded to Voice Studio",
  approved: "Approved content you shipped",
  audited: "Found by the baseline audit",
};

/** Step 5 — the brand-voice reveal, ported from bm2.0's VoicePanel onto the
 * merged /manager/voice/* routes. Self-loads via GET /manager/voice/profile;
 * "Learn my brand voice" kicks a background harvest (202) and polls the
 * profile every ~3s until it settles — a 409 means a harvest is already in
 * flight, which is treated as resume-the-poll, not an error. When a run ends
 * with nothing to show, the failed job_runs row's error is surfaced honestly. */
export function VoiceReveal() {
  const [data, setData] = useState<VoiceProfileResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); // kicking off the harvest POST
  const [actionError, setActionError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null); // poll gave up / run failed

  const load = useCallback(async () => {
    try {
      const res = await managerApi.voiceProfile();
      setData(res);
      setLoadError(null);
      return res;
    } catch (err) {
      setLoadError(errorMessage(err));
      return null;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const state = data?.state;
  const running = state === "running";

  // Poll while a harvest is in flight; transient poll failures are tolerated
  // (the run itself is unaffected) but we give up after ~30s of misses.
  useEffect(() => {
    if (!running || runError) return;
    let cancelled = false;
    let misses = 0;
    const tick = async () => {
      try {
        const res = await managerApi.voiceProfile();
        if (cancelled) return;
        misses = 0;
        setData(res);
        if (res.state !== "running") {
          // The run settled — if it failed, say so instead of a silent shrug.
          try {
            const run = await managerApi.voiceStatus();
            if (!cancelled && run.status === "failed") {
              setRunError(run.error || "The voice harvest failed — try again.");
            }
          } catch {
            /* profile already refreshed; the run row is a nicety */
          }
        }
      } catch (err) {
        if (cancelled) return;
        misses += 1;
        if (misses >= 10) setRunError(`Lost contact with the voice harvest (${errorMessage(err)}).`);
      }
    };
    const interval = setInterval(tick, VOICE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [running, runError]);

  // Optimistically flip to "running" so the poller starts immediately.
  function markRunning() {
    setData((d) =>
      d
        ? { ...d, state: "running" }
        : { state: "running", voice: {}, exemplar_count: 0, exemplars_by_origin: {} },
    );
  }

  async function harvest() {
    if (busy || running) return;
    setBusy(true);
    setActionError(null);
    setRunError(null);
    try {
      await managerApi.harvestVoice();
      markRunning();
    } catch (err) {
      if (isConflict(err)) markRunning(); // already running — resume the poll
      else setActionError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const voice = data?.voice ?? {};
  const hasVoice =
    !!(voice.summary || voice.register || voice.tone || voice.sentence_style) ||
    (data?.exemplar_count ?? 0) > 0;
  const origins = Object.entries(data?.exemplars_by_origin ?? {});

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold">
            Brand Voice — learned from your own words
          </h2>
          <p className="text-[12px] text-muted-foreground mt-1">
            We pull what you&apos;ve actually said and posted, distil it, and write only in this voice.
          </p>
        </div>
        {state === "ready" && (
          <Button variant="ghost" className="!px-3 !py-1.5 text-xs shrink-0" disabled={busy} onClick={harvest}>
            {busy ? "Starting…" : "Re-learn"}
          </Button>
        )}
      </div>

      {actionError && <ErrorBox message={actionError} />}
      {runError && <ErrorBox message={runError} onRetry={busy ? undefined : () => void load()} />}

      {/* initial load */}
      {data === null && !loadError && <Loading label="Loading your brand voice…" />}
      {data === null && loadError && <ErrorBox message={loadError} onRetry={() => void load()} />}

      {/* empty */}
      {state === "empty" && (
        <EmptyState
          title="We haven't heard you yet"
          body="Learn your voice from your own channels and posts — then everything we write sounds like you."
        >
          <Button disabled={busy} onClick={harvest}>
            {busy ? "Starting…" : "Learn my brand voice"}
          </Button>
        </EmptyState>
      )}

      {/* running */}
      {running && (
        <Card className="py-10 text-center">
          <div className="flex justify-center">
            <Loading label="Listening to your videos and posts — this can take a couple of minutes" />
          </div>
          <p className="text-[12px] text-muted-foreground max-w-md mx-auto">
            Keep this tab open — the voice appears here the moment it&apos;s distilled.
          </p>
        </Card>
      )}

      {/* ready */}
      {state === "ready" && hasVoice && (
        <>
          {typeof voice.summary === "string" && voice.summary && (
            <Card className="border-primary/30 bg-primary/5">
              <p className="text-[15px] leading-relaxed">{voice.summary}</p>
            </Card>
          )}

          {(voice.register || voice.tone || voice.sentence_style) && (
            <Card variant="flush" className="divide-y divide-border">
              {voice.register && <VoiceRow label="Register" value={String(voice.register)} />}
              {voice.tone && <VoiceRow label="Tone" value={String(voice.tone)} />}
              {voice.sentence_style && (
                <VoiceRow label="Sentence style" value={String(voice.sentence_style)} />
              )}
            </Card>
          )}

          <ChipGroup title="Signature phrases" items={voice.signature_phrases} />
          <ChipGroup title="Vocabulary" items={voice.vocabulary} />
          <ChipGroup title="Do" items={voice.dos} tone="do" />
          <ChipGroup title="Don't" items={voice.donts} tone="dont" />

          {origins.length > 0 && (
            <div>
              <div className="text-[12px] font-medium text-muted-foreground mb-2">
                Built on {data?.exemplar_count ?? 0}{" "}
                {(data?.exemplar_count ?? 0) === 1 ? "exemplar" : "exemplars"} of your own content
              </div>
              <Card variant="flush" className="divide-y divide-border">
                {origins.map(([origin, n]) => (
                  <div key={origin} className="flex items-center justify-between px-4 py-2.5">
                    <span className="text-[13px]">{ORIGIN_LABEL[origin] ?? origin}</span>
                    <b className="text-[13px] whitespace-nowrap">
                      {n} {n === 1 ? "quote" : "quotes"}
                    </b>
                  </div>
                ))}
              </Card>
            </div>
          )}
        </>
      )}

      {state === "ready" && !hasVoice && (
        <EmptyState
          title="Nothing to learn from yet"
          body="We couldn't find enough of your own content to distil a voice. Connect your accounts and try again."
        >
          <Button disabled={busy} onClick={harvest}>
            {busy ? "Starting…" : "Try again"}
          </Button>
        </EmptyState>
      )}
    </div>
  );
}

function VoiceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-2.5">
      <span className="text-[12px] text-muted-foreground shrink-0">{label}</span>
      <b className="text-[13px] text-right">{value}</b>
    </div>
  );
}

function ChipGroup({
  title,
  items,
  tone,
}: {
  title: string;
  items?: string[];
  tone?: "do" | "dont";
}) {
  if (!items || items.length === 0) return null;
  const chip =
    tone === "do"
      ? "border-accent/30 bg-accent/15 text-accent"
      : tone === "dont"
        ? "border-destructive/30 bg-destructive/10 text-destructive"
        : "border-border bg-secondary text-secondary-foreground";
  return (
    <div>
      <div className="text-[12px] font-medium text-muted-foreground mb-2">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <span
            key={`${it}-${i}`}
            className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${chip}`}
          >
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}
