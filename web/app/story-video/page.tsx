"use client";

/**
 * Story Video — voice-driven slideshow with AI-generated B-roll.
 *
 * The "Agent Opus" pattern, adapted to James's cloned voice:
 *   1. HeyGen speaks the script in James's voice (we strip the audio).
 *   2. Whisper transcribes with per-word timestamps.
 *   3. The pipeline segments speech into 8-18 visual beats.
 *   4. gpt-image-1 generates one photoreal still per beat.
 *   5. Creatomate stitches audio + stills (subtle Ken Burns) + word-
 *      pinned captions into the final MP4.
 *
 * Lands in the Approval Queue exactly like every other mode. The
 * production row carries each beat's prompt and image URL so you can
 * inspect what was produced.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type Production } from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";

const STAGE_TONE: Record<string, "muted" | "accent" | "ok" | "destructive"> = {
  queued: "muted",
  planning: "accent",
  rendering_clips: "accent",
  assembling: "accent",
  succeeded: "ok",
  failed: "destructive",
};

const STAGE_LABEL: Record<string, string> = {
  queued: "queued",
  planning: "voicing (HeyGen)",
  rendering_clips: "transcribing + generating images",
  assembling: "stitching (Creatomate)",
  succeeded: "done",
  failed: "failed",
};

type Beat = {
  index: number;
  start: number;
  end: number;
  text: string;
  image_prompt: string;
  image_url: string | null;
  image_error?: string;
};

export default function StoryVideoPage() {
  const [topic, setTopic] = useState("");
  const [aspect, setAspect] = useState("9:16");
  const [platform, setPlatform] = useState("instagram");
  const [script, setScript] = useState("");
  const [composing, setComposing] = useState(false);
  const [producing, setProducing] = useState(false);
  const [err, setErr] = useState("");
  const [prod, setProd] = useState<Production | null>(null);
  const [recent, setRecent] = useState<Production[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadRecent() {
    try {
      const list = await api.listProductions();
      setRecent(
        list
          .filter((p) => (p as Production & { mode?: string }).mode === "story_audio")
          .slice(0, 8),
      );
    } catch {}
  }
  useEffect(() => {
    loadRecent();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  async function generateScript() {
    setComposing(true); setErr("");
    try {
      const r = await api.composeVideo(topic.trim(), platform, aspect);
      if (r.error) { setErr(r.error); return; }
      setScript(r.script || "");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not generate script");
    } finally {
      setComposing(false);
    }
  }

  async function produce() {
    if (!script.trim()) { setErr("Add a script first."); return; }
    setProducing(true); setErr(""); setProd(null);
    try {
      const p = await api.produceVideo({
        mode: "story_audio",
        script: script.trim(),
        platform, aspect,
        title: topic.trim() || script.trim().slice(0, 60),
      });
      setProd(p);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const u = await api.getProduction(p.id).catch(() => null);
        if (u) {
          setProd(u);
          if (u.status === "succeeded" || u.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            loadRecent();
          }
        }
      }, 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "produce failed");
    } finally {
      setProducing(false);
    }
  }

  const active = !!prod && prod.status !== "succeeded" && prod.status !== "failed";
  const estSec = Math.max(1, Math.round((script.length || 0) / 14));
  // beats appear on production.scenes during the rendering_clips stage
  const beats: Beat[] = (prod?.scenes as unknown as Beat[]) || [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Story Video"
        sub="James's voice over a slideshow of AI-generated stills, timed to his exact words. HeyGen voices the script, Whisper pulls word-level timestamps, gpt-image-1 makes one photoreal still per beat, Creatomate stitches it. ~3–5 minutes per render. Lands in the approval queue."
      />

      <Card>
        <CardTitle>1. Write or generate the script</CardTitle>
        <Label>Topic (optional, used by auto-generate)</Label>
        <Input
          placeholder="e.g. why Staten Island commercial RE is underpriced"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Platform</Label>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {["instagram", "tiktok", "youtube", "linkedin"].map((p) => <option key={p}>{p}</option>)}
            </Select>
          </div>
          <div>
            <Label>Aspect</Label>
            <Select value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {["9:16", "16:9", "1:1"].map((a) => <option key={a}>{a}</option>)}
            </Select>
          </div>
        </div>
        <div className="mt-3">
          <Button variant="secondary" onClick={generateScript} disabled={composing}>
            {composing ? <Spinner /> : "Auto-generate script"}
          </Button>
          <span className="ml-3 text-[12px] text-muted-foreground">
            Uses live research + brand voice. One continuous voiceover — beats
            are inferred later from spoken-word timestamps.
          </span>
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <CardTitle>2. Script (every word here is what James will speak)</CardTitle>
          <span className="text-[12px] text-muted-foreground">
            ~{estSec}s ({script.length} chars)
          </span>
        </div>
        <Textarea
          rows={12}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder="Paste your script here, or click Auto-generate above. James will speak this verbatim; one image will be generated per spoken beat (~4 seconds each)."
          className="text-[13px] mt-2"
        />
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>3. Produce</CardTitle>
            <p className="text-[12px] text-muted-foreground mt-1">
              HeyGen voice → Whisper word-stamps → image per beat →
              Creatomate stitch. Photoreal style, calm music underbed,
              burned-in captions. Cost roughly: 1 HeyGen render + Whisper
              + ~12 gpt-image-1 stills + 1 Creatomate render.
            </p>
          </div>
          <Button onClick={produce} disabled={producing || active || !script.trim()}>
            {producing || active ? <Spinner /> : "Make story video"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}

        {prod && (
          <div className="mt-4 border-t border-border pt-4">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={STAGE_TONE[prod.status] || "muted"}>
                {STAGE_LABEL[prod.status] || prod.status}
              </Badge>
              {active && <Spinner />}
              <span className="text-[12px] text-muted-foreground ml-auto">id: {prod.id.slice(0, 8)}…</span>
            </div>
            {prod.error && <p className="text-destructive text-[12px] mt-2">{prod.error}</p>}

            {/* Beat grid — populates during the rendering_clips stage as
                each gpt-image-1 call resolves. */}
            {beats.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-2">
                {beats.map((b) => (
                  <div
                    key={b.index}
                    className="border border-border rounded-md overflow-hidden bg-background"
                  >
                    {b.image_url ? (
                      <img
                        src={mediaUrl(b.image_url)}
                        alt={b.text}
                        className="w-full aspect-video object-cover"
                      />
                    ) : (
                      <div className="w-full aspect-video flex items-center justify-center text-[10px] text-muted-foreground bg-secondary">
                        {b.image_error ? `✗ ${b.image_error}` : <Spinner />}
                      </div>
                    )}
                    <div className="p-1.5">
                      <div className="text-[10px] text-muted-foreground">
                        {b.start.toFixed(1)}–{b.end.toFixed(1)}s
                      </div>
                      <div className="text-[11px] line-clamp-2">{b.text}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {prod.final_url && !prod.final_url.startsWith("stub://") && (
              <video
                src={mediaUrl(prod.final_url)}
                controls
                className="w-full max-w-md mx-auto mt-3 rounded-md bg-background"
              />
            )}
            {prod.queued_action_id && (
              <Link
                href="/queue"
                className="text-[12px] text-primary hover:underline mt-3 inline-block"
              >
                Review in Approval Queue →
              </Link>
            )}
          </div>
        )}
      </Card>

      {recent.length > 0 && (
        <Card>
          <CardTitle>Recent story videos</CardTitle>
          <div className="flex flex-col gap-2 mt-2">
            {recent.map((p) => (
              <div
                key={p.id}
                onClick={() => setProd(p)}
                className="flex items-center gap-2 text-[12px] border border-border rounded-md p-2 cursor-pointer hover:border-primary"
              >
                <Badge tone={STAGE_TONE[p.status] || "muted"}>{p.status}</Badge>
                <span className="flex-1 truncate">{p.title || p.script.slice(0, 60)}</span>
                <span className="text-muted-foreground text-[11px]">
                  {new Date(p.created_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
