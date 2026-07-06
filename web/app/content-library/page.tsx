"use client";

/**
 * Content Library — every piece of footage and the clippable topic-reels the
 * clipper found inside it, with live status. Clippable candidates auto-render
 * into the Approval Queue (the backend auto-clipper); this is the dashboard
 * that shows what can be clipped and what's already been done.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, type ClipTopic, type ContentLibrary, type LibCandidate } from "@/lib/api";
import { Button, Card, CardTitle, Badge, Spinner, PageHeader } from "@/components/ui";

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

const STATE: Record<LibCandidate["state"], { label: string; tone: "muted" | "accent" | "ok" }> = {
  clippable: { label: "Clippable", tone: "muted" },
  clipping: { label: "Clipping…", tone: "accent" },
  clipped: { label: "Clipped ✓", tone: "ok" },
};

const TOPIC_STATE: Record<ClipTopic["state"], { label: string; tone: "muted" | "accent" | "ok" }> = {
  suggested: { label: "Ready to build", tone: "muted" },
  building: { label: "Building…", tone: "accent" },
  built: { label: "Built ✓", tone: "ok" },
};

export default function ContentLibraryPage() {
  const [lib, setLib] = useState<ContentLibrary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadSeqRef = useRef(0);        // drop out-of-order poll responses
  const errFromLoadRef = useRef(false); // poll success only clears poll errors

  async function load() {
    const seq = ++loadSeqRef.current;
    try {
      const data = await api.contentLibrary();
      if (seq !== loadSeqRef.current) return;   // a newer load already landed
      setLib(data);
      if (errFromLoadRef.current) {
        setErr(null);                 // never wipe an ACTION's error message
        errFromLoadRef.current = false;
      }
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      errFromLoadRef.current = true;
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, 6000);   // keep clipping→clipped fresh
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  async function clipOne(candidateId: string) {
    setBusy(candidateId);
    setErr(null);
    errFromLoadRef.current = false;
    try {
      await api.renderLongCandidate(candidateId, {});
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "clip failed");
    } finally {
      setBusy((p) => (p === candidateId ? null : p));
    }
  }

  async function autoClip(sourceId: string) {
    setBusy(sourceId);
    setErr(null);
    errFromLoadRef.current = false;
    try {
      await api.autoClipSource(sourceId);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "auto-clip failed");
    } finally {
      setBusy((p) => (p === sourceId ? null : p));
    }
  }

  async function refreshTopics() {
    setBusy("topics-refresh");
    setErr(null);
    errFromLoadRef.current = false;
    try {
      await api.refreshClipTopics();
      await load();   // topics_mining flips on; the 6s poll picks up the list
    } catch (e) {
      setErr(e instanceof Error ? e.message : "topic refresh failed");
    } finally {
      setBusy((p) => (p === "topics-refresh" ? null : p));
    }
  }

  async function buildTopic(topicId: string) {
    setBusy(topicId);
    setErr(null);
    errFromLoadRef.current = false;
    try {
      await api.buildClipTopic(topicId);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "build failed");
    } finally {
      setBusy((p) => (p === topicId ? null : p));
    }
  }

  async function dismissTopic(topicId: string) {
    setBusy(topicId);
    try {
      await api.dismissClipTopic(topicId);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "dismiss failed");
    } finally {
      setBusy((p) => (p === topicId ? null : p));
    }
  }

  const s = lib?.summary;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Content Library"
        sub="Every piece of footage and the topic-reels the clipper found inside it. Clippable ones auto-render into your Approval Queue — no searching required."
      />

      {s && (
        <div className="flex gap-2 flex-wrap text-[13px]">
          <Badge tone="muted">{s.sources} sources</Badge>
          <Badge tone="muted">{s.clippable} clippable</Badge>
          {s.clipping > 0 && <Badge tone="accent">{s.clipping} clipping…</Badge>}
          <Badge tone="ok">{s.clipped} clipped</Badge>
        </div>
      )}
      {err && <p className="text-[12px] text-destructive">✗ {err}</p>}

      {/* ── Topics the clipper can build for you ── */}
      {lib && (
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div>
              <CardTitle>Topics to build</CardTitle>
              <div className="text-[11px] text-muted-foreground mt-0.5">
                The clipper read every transcript and grouped the strongest moments
                into buildable reels — click Build and the edit lands in your queue.
              </div>
            </div>
            <Button
              variant="secondary"
              onClick={refreshTopics}
              disabled={busy === "topics-refresh" || lib.topics_mining}
              className="text-[12px] !px-3 !py-1"
              title="Re-scan all footage for fresh topic suggestions"
            >
              {busy === "topics-refresh" || lib.topics_mining
                ? <span className="inline-flex items-center gap-1"><Spinner /> finding topics…</span>
                : "Find topics"}
            </Button>
          </div>

          {(lib.topics ?? []).length === 0 ? (
            <p className="text-[12px] text-muted-foreground">
              {lib.topics_mining
                ? "Scanning your footage for topics…"
                : lib.sources.length === 0
                  ? "Upload footage first — topics are mined from your transcripts."
                  : "No topic suggestions yet — hit “Find topics” to scan your footage."}
            </p>
          ) : (
            <div className="flex flex-col divide-y divide-border">
              {(lib.topics ?? []).map((t) => {
                const st = TOPIC_STATE[t.state];
                return (
                  <div key={t.id} className="flex items-start gap-3 py-2.5">
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-semibold leading-snug">{t.title}</div>
                      {t.hook && (
                        <div className="text-[12px] text-muted-foreground mt-0.5 line-clamp-1" title={t.hook}>
                          Opens on: “{t.hook}”
                        </div>
                      )}
                      {t.why && (
                        <div className="text-[11px] text-muted-foreground line-clamp-1">{t.why}</div>
                      )}
                      <div className="text-[11px] text-muted-foreground mt-0.5">
                        score {t.score}/10 · {t.segments.length} segment{t.segments.length === 1 ? "" : "s"}
                        {t.segments.length > 0 && (
                          <> · from {Array.from(new Set(t.segments.map((sg) => sg.source_title || "footage"))).join(", ")}</>
                        )}
                      </div>
                    </div>
                    <Badge tone={st.tone}>{st.label}</Badge>
                    <div className="w-[104px] text-right flex items-center justify-end gap-2">
                      {t.state === "suggested" && (
                        <>
                          <Button
                            onClick={() => buildTopic(t.id)}
                            disabled={busy === t.id}
                            className="text-[12px] !px-3 !py-1"
                          >
                            {busy === t.id ? <Spinner /> : "Build"}
                          </Button>
                          <button
                            onClick={() => dismissTopic(t.id)}
                            disabled={busy === t.id}
                            className="text-[12px] text-muted-foreground hover:text-destructive"
                            title="Dismiss this topic"
                          >
                            ✕
                          </button>
                        </>
                      )}
                      {t.state === "building" && (
                        <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
                          <Spinner />
                        </span>
                      )}
                      {t.state === "built" && (
                        <Link href="/queue" className="text-[12px] text-primary hover:underline">
                          View ↗
                        </Link>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>
      ) : !lib || lib.sources.length === 0 ? (
        <Card>
          <p className="text-[13px] text-muted-foreground">
            No footage yet. Upload a long video on the{" "}
            <Link href="/long-form" className="text-primary hover:underline">Long-form</Link>{" "}
            page — the clipper transcribes it, finds the best topic-reels, and
            auto-clips the top ones into your queue.
          </p>
        </Card>
      ) : (
        lib.sources.map((src) => (
          <Card key={src.id} className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="min-w-0">
                <CardTitle>{src.title}</CardTitle>
                <div className="text-[11px] text-muted-foreground mt-0.5">
                  {fmtDur(src.duration_s)} · {src.candidates.length} clippable moment
                  {src.candidates.length === 1 ? "" : "s"}
                  {src.status !== "ready" && <> · <span className="text-accent">{src.status}</span></>}
                </div>
              </div>
              {src.candidates.some((c) => c.state === "clippable") && (
                <Button
                  variant="secondary"
                  onClick={() => autoClip(src.id)}
                  disabled={busy === src.id}
                  className="text-[12px] !px-3 !py-1"
                  title="Auto-render the top clippable moments of this source into reels"
                >
                  {busy === src.id ? <Spinner /> : "Auto-clip top"}
                </Button>
              )}
            </div>

            {src.candidates.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">No clippable moments found (yet).</p>
            ) : (
              <div className="flex flex-col divide-y divide-border">
                {src.candidates.map((c) => {
                  const st = STATE[c.state];
                  return (
                    <div key={c.id} className="flex items-start gap-3 py-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-[13px] font-medium leading-snug line-clamp-1" title={c.hook_quote}>
                          {c.hook_quote || c.summary || "(clip)"}
                        </div>
                        {c.summary && (
                          <div className="text-[11px] text-muted-foreground line-clamp-1">{c.summary}</div>
                        )}
                        <div className="text-[11px] text-muted-foreground mt-0.5">
                          {fmtDur(c.start_s)}–{fmtDur(c.end_s)} · score {c.score}
                        </div>
                      </div>
                      <Badge tone={st.tone}>{st.label}</Badge>
                      <div className="w-[76px] text-right">
                        {c.state === "clippable" && (
                          <Button
                            onClick={() => clipOne(c.id)}
                            disabled={busy === c.id}
                            className="text-[12px] !px-3 !py-1"
                          >
                            {busy === c.id ? <Spinner /> : "Clip"}
                          </Button>
                        )}
                        {c.state === "clipping" && (
                          <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
                            <Spinner />
                          </span>
                        )}
                        {c.state === "clipped" && (
                          <Link href="/queue" className="text-[12px] text-primary hover:underline">
                            View ↗
                          </Link>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        ))
      )}
    </div>
  );
}
