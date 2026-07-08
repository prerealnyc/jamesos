"use client";

/**
 * Brand Voice — the voice profile distilled from the brand's OWN words
 * (GET /manager/voice/profile), with the harvest as a background run:
 * POST /voice/harvest and /voice/add-source return 202 (409 while one is in
 * flight — treat as "already running" and watch it), polled via
 * GET /voice/status until the run settles. Ported from bm2.0 voice-panel.tsx;
 * the merged profile carries exemplar counts by origin rather than the
 * cited exemplar list.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { EmptyState, ErrorBox, Loading, useLoad } from "@/components/manager/primitives";
import { errorMessage, isConflict, managerApi, type VoiceProfileResponse } from "@/lib/manager-api";
import { fmtNum, KV, PanelHead, prettyKey } from "./bits";

export function VoicePanel({ onChange }: { onChange?: () => void }) {
  const profile = useLoad<VoiceProfileResponse>(() => managerApi.voiceProfile(), []);

  const [kicking, setKicking] = useState(false); // the POST itself
  const [polling, setPolling] = useState(false); // a run is in flight
  const [actionError, setActionError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const pollGuard = useRef(false);

  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const reloadRef = useRef(profile.reload);
  reloadRef.current = profile.reload;

  const watchRun = useCallback(async () => {
    if (pollGuard.current) return;
    pollGuard.current = true;
    setPolling(true);
    setRunError(null);
    try {
      const status = await managerApi.awaitVoiceHarvest();
      if (status.status === "failed") {
        setRunError(status.error || "The voice harvest failed. Try again.");
      }
      reloadRef.current();
      onChangeRef.current?.();
    } catch (err) {
      setRunError(errorMessage(err));
    } finally {
      pollGuard.current = false;
      setPolling(false);
    }
  }, []);

  // Resume watching a harvest that was already running when the page loaded.
  const state = profile.data?.state;
  useEffect(() => {
    if (state === "running") void watchRun();
  }, [state, watchRun]);

  async function kick(fn: () => Promise<unknown>) {
    if (kicking || polling) return;
    setKicking(true);
    setActionError(null);
    setRunError(null);
    try {
      await fn();
      void watchRun();
    } catch (err) {
      // 409 = a harvest is already in flight — watch it instead of erroring
      if (isConflict(err)) void watchRun();
      else setActionError(errorMessage(err));
    } finally {
      setKicking(false);
    }
  }

  const harvest = () => kick(() => managerApi.harvestVoice());
  const addSource = () => {
    const value = url.trim();
    if (!value) return;
    void kick(async () => {
      await managerApi.addVoiceSource(value);
      setUrl("");
    });
  };

  const running = polling || state === "running";
  const voice = profile.data?.voice ?? {};
  const hasVoice =
    Boolean(voice.summary || voice.register || voice.tone || voice.sentence_style) ||
    (profile.data?.exemplar_count ?? 0) > 0;
  const origins = Object.entries(profile.data?.exemplars_by_origin ?? {}).filter(([, n]) => n > 0);

  return (
    <Card variant="compact">
      <PanelHead
        title="Brand Voice — learned from your own words"
        sub="Pulled from what you've actually said and posted, then distilled; everything drafted is written in this voice."
      >
        {state === "ready" && !running && (
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md px-2.5 py-1 text-xs font-semibold text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={kicking}
            onClick={harvest}
          >
            {kicking && <Spinner />}
            {kicking ? "Starting…" : "Re-learn"}
          </button>
        )}
      </PanelHead>

      {actionError && (
        <div className="mb-3">
          <ErrorBox message={actionError} />
        </div>
      )}
      {runError && (
        <div className="mb-3">
          <ErrorBox message={runError} onRetry={harvest} />
        </div>
      )}

      {profile.loading && !profile.data && <Loading label="Loading your brand voice…" />}
      {profile.error && !profile.data && <ErrorBox message={profile.error} onRetry={profile.reload} />}

      {/* --------------------------------------------------------- empty */}
      {profile.data && state === "empty" && !running && (
        <EmptyState
          title="We haven't heard you yet"
          body="Learn your voice from your own uploads and posts. Then everything drafted sounds like you."
        >
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
            disabled={kicking}
            onClick={harvest}
          >
            {kicking && <Spinner />}
            {kicking ? "Starting…" : "Learn my brand voice"}
          </button>
        </EmptyState>
      )}

      {/* ------------------------------------------------------- running */}
      {running && (
        <div className="rounded-md border border-border bg-background px-4 py-6 text-center">
          <div className="flex justify-center">
            <Loading label="Listening to your videos and posts — this runs in the background…" />
          </div>
        </div>
      )}

      {/* --------------------------------------------------------- ready */}
      {profile.data && state === "ready" && !running && hasVoice && (
        <div className="space-y-3">
          {voice.summary && (
            <div className="rounded-md border border-border bg-background px-3 py-2.5">
              <p className="m-0 text-sm leading-relaxed">{voice.summary}</p>
            </div>
          )}

          {(voice.register || voice.tone || voice.sentence_style) && (
            <div className="rounded-md border border-border bg-background px-3 py-2">
              {voice.register && <KV label="Register">{voice.register}</KV>}
              {voice.tone && <KV label="Tone">{voice.tone}</KV>}
              {voice.sentence_style && <KV label="Sentence style">{voice.sentence_style}</KV>}
            </div>
          )}

          <ChipGroup title="Signature phrases" items={voice.signature_phrases} />
          <ChipGroup title="Vocabulary" items={voice.vocabulary} />
          <ChipGroup title="Do" items={voice.dos} tone="do" />
          <ChipGroup title="Don't" items={voice.donts} tone="dont" />

          <div className="rounded-md border border-border bg-background px-3 py-2">
            <KV label="Exemplars on file">{fmtNum(profile.data.exemplar_count)}</KV>
            {origins.map(([origin, n]) => (
              <KV key={origin} label={<span className="capitalize">· {prettyKey(origin)}</span>}>
                {fmtNum(n)}
              </KV>
            ))}
          </div>
        </div>
      )}
      {profile.data && state === "ready" && !running && !hasVoice && (
        <EmptyState
          title="Nothing to learn from yet"
          body="We couldn't find enough of your own content to distil a voice. Add a talk or interview URL below, or connect accounts and re-learn."
        >
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
            disabled={kicking}
            onClick={harvest}
          >
            {kicking ? "Starting…" : "Try again"}
          </button>
        </EmptyState>
      )}

      {/* -------------------------------- add a source (available when idle) */}
      {profile.data && !running && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
            Add a source
          </div>
          <div className="flex gap-2">
            <input
              className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addSource();
                }
              }}
              placeholder="https://…"
              inputMode="url"
              disabled={kicking}
              aria-label="Source URL to add to your voice"
            />
            <button
              type="button"
              className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
              disabled={kicking || !url.trim()}
              onClick={addSource}
            >
              {kicking ? "Adding…" : "Add"}
            </button>
          </div>
          <p className="m-0 mt-1 text-[11px] text-muted-foreground">Paste a talk, interview, or podcast URL.</p>
        </div>
      )}
    </Card>
  );
}

function ChipGroup({ title, items, tone }: { title: string; items?: string[]; tone?: "do" | "dont" }) {
  if (!items || items.length === 0) return null;
  const chipCls =
    tone === "do"
      ? "bg-accent/15 text-accent border-accent/30"
      : tone === "dont"
        ? "bg-destructive/10 text-destructive border-destructive/30"
        : "bg-secondary text-muted-foreground border-border";
  return (
    <div>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <span key={`${it}-${i}`} className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${chipCls}`}>
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}
