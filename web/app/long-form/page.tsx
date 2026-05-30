"use client";

/**
 * Long Form Cutter — upload a podcast / interview / long-form video,
 * the system extracts audio, transcribes with word timestamps, and
 * an LLM picks 3-5 standalone 30-45s reel candidates. The user picks
 * which candidates to render; each render runs the engaging-avatar
 * pipeline on the cut clip (real footage + B-roll inserts + captions
 * + music) and lands in the approval queue.
 *
 * State machine on the source row:
 *   uploading → transcribing → analyzing → ready / failed
 * Page polls every 5s while any source is mid-flight.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, mediaUrl, type LongSource, type ReelCandidate,
} from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Spinner, Badge, PageHeader,
} from "@/components/ui";

const STATUS_TONE: Record<string, "muted" | "accent" | "ok" | "destructive"> = {
  uploading: "muted",
  transcribing: "accent",
  analyzing: "accent",
  ready: "ok",
  failed: "destructive",
};

const STATUS_LABEL: Record<string, string> = {
  uploading: "uploading",
  transcribing: "transcribing (Whisper)",
  analyzing: "analyzing (LLM picks candidates)",
  ready: "ready",
  failed: "failed",
};

function fmtTime(s: number): string {
  if (!s || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function fmtRange(start: number, end: number): string {
  return `${fmtTime(start)} – ${fmtTime(end)} · ${(end - start).toFixed(1)}s`;
}

export default function LongFormPage() {
  const [sources, setSources] = useState<LongSource[]>([]);
  const [selected, setSelected] = useState<(LongSource & { candidates: ReelCandidate[] }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [title, setTitle] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [renderingId, setRenderingId] = useState<string | null>(null);

  async function loadSources() {
    try {
      const r = await api.listLongSources();
      setSources(r.sources);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }

  async function loadSelected(id: string) {
    try {
      const s = await api.getLongSource(id);
      setSelected(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not load source");
    }
  }

  useEffect(() => {
    loadSources();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // Poll while any source is mid-flight, OR while the selected
  // source is mid-flight. Keeps the table + the detail pane fresh.
  useEffect(() => {
    const midflight = sources.some(
      (s) => s.status !== "ready" && s.status !== "failed",
    ) || (selected && selected.status !== "ready" && selected.status !== "failed");
    if (!midflight) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      await loadSources();
      if (selected) await loadSelected(selected.id);
    }, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, selected?.id, selected?.status]);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true); setErr("");
    try {
      const src = await api.uploadLongSource(files[0], title.trim());
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
      await loadSources();
      await loadSelected(src.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function reanalyze(id: string) {
    setBusy(true); setErr("");
    try {
      await api.reanalyzeLongSource(id);
      await loadSources();
      await loadSelected(id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "reanalyze failed");
    } finally {
      setBusy(false);
    }
  }

  async function renderCandidate(c: ReelCandidate) {
    setRenderingId(c.id);
    setErr("");
    try {
      const prod = await api.renderLongCandidate(c.id);
      // Optimistically link production_id locally
      if (selected) {
        setSelected({
          ...selected,
          candidates: selected.candidates.map((x) =>
            x.id === c.id ? { ...x, production_id: prod.id } : x,
          ),
        });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "render failed");
    } finally {
      setRenderingId(null);
    }
  }

  async function dismissCandidate(c: ReelCandidate) {
    try {
      await api.dismissLongCandidate(c.id);
      if (selected) {
        setSelected({
          ...selected,
          candidates: selected.candidates.filter((x) => x.id !== c.id),
        });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "dismiss failed");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Long Form Cutter"
        sub="Upload a podcast / interview / long-form video. The system extracts audio, transcribes with word timestamps, and the LLM finds 3-5 standalone 30-45s windows that work as Reels. Click Render on the ones worth shipping — each runs the engaging-avatar pipeline (real footage + B-roll inserts + captions + music) and lands in the approval queue."
      />

      <Card>
        <CardTitle>Upload long source</CardTitle>
        <p className="text-[12px] text-muted-foreground mb-3">
          50-60 min files OK. Audio is extracted at low bitrate (mono
          16 kHz 32 kbps) so 60 min fits Whisper's 25 MB cap; longer
          sources get auto-chunked. Drive URLs land here after going
          through the Reference Library importer (coming soon as a
          one-click).
        </p>
        <Input
          placeholder="Title (optional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mb-2"
        />
        <input
          ref={fileRef}
          type="file"
          accept="video/*"
          onChange={(e) => onUpload(e.target.files)}
          disabled={busy}
          className="block w-full text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
        />
        {busy && <p className="text-[12px] mt-2"><Spinner /> uploading…</p>}
        {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      </Card>

      <Card>
        <CardTitle>Sources ({sources.length})</CardTitle>
        {sources.length === 0 ? (
          <p className="text-[12px] text-muted-foreground mt-2">
            Nothing uploaded yet. Drop a video above to start.
          </p>
        ) : (
          <div className="flex flex-col gap-2 mt-2">
            {sources.map((s) => (
              <div
                key={s.id}
                onClick={() => loadSelected(s.id)}
                className={`flex items-center gap-2 text-[12px] border rounded-md p-2 cursor-pointer transition-colors ${
                  selected?.id === s.id
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/60"
                }`}
              >
                <Badge tone={STATUS_TONE[s.status] || "muted"}>
                  {STATUS_LABEL[s.status] || s.status}
                </Badge>
                <span className="flex-1 truncate">{s.title || s.id.slice(0, 8)}</span>
                <span className="text-muted-foreground text-[11px]">
                  {fmtTime(s.duration_s)}
                </span>
                <span className="text-muted-foreground text-[11px]">
                  {new Date(s.created_at).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {selected && (
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>{selected.title || "Source"}</CardTitle>
            <Button
              variant="secondary"
              onClick={() => reanalyze(selected.id)}
              disabled={busy || selected.status === "transcribing" || selected.status === "analyzing"}
              className="text-[12px] !px-3 !py-1"
            >
              Re-analyze
            </Button>
          </div>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <Badge tone={STATUS_TONE[selected.status] || "muted"}>
              {STATUS_LABEL[selected.status] || selected.status}
            </Badge>
            <span className="text-[12px] text-muted-foreground">
              {fmtTime(selected.duration_s)} duration
            </span>
            <a
              href={mediaUrl(selected.source_url)}
              target="_blank" rel="noopener noreferrer"
              className="text-[12px] text-primary hover:underline ml-auto"
            >
              Open source ↗
            </a>
          </div>
          {selected.error && (
            <p className="text-destructive text-[12px] mt-2">✗ {selected.error}</p>
          )}

          {selected.status === "ready" && selected.candidates.length === 0 && (
            <p className="text-[12px] text-muted-foreground mt-3">
              No candidates returned by the LLM. Try Re-analyze, or the
              source may not contain clear standalone moments.
            </p>
          )}

          {selected.candidates.length > 0 && (
            <div className="mt-4 flex flex-col gap-3">
              {selected.candidates.map((c) => (
                <div
                  key={c.id}
                  className="border border-border rounded-md p-3 bg-background"
                >
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <Badge tone={c.score >= 8 ? "ok" : c.score >= 6 ? "accent" : "muted"}>
                      score {c.score}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground">
                      {fmtRange(c.start_s, c.end_s)}
                    </span>
                    <div className="ml-auto flex gap-2">
                      {c.production_id ? (
                        <Link
                          href="/queue"
                          className="text-[12px] text-primary hover:underline"
                        >
                          rendering →
                        </Link>
                      ) : (
                        <Button
                          onClick={() => renderCandidate(c)}
                          disabled={renderingId === c.id}
                          className="text-[12px] !px-3 !py-1"
                        >
                          {renderingId === c.id ? <Spinner /> : "Render reel"}
                        </Button>
                      )}
                      <button
                        onClick={() => dismissCandidate(c)}
                        className="text-[12px] text-muted-foreground hover:text-destructive"
                        title="Dismiss this candidate"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                  <p className="text-[13px] font-medium leading-snug">
                    “{c.hook_quote}”
                  </p>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {c.summary}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
