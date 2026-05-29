"use client";

/**
 * Story Reel (mix) — ONE HeyGen render, used twice.
 *
 * The reel intercuts between:
 *   1. James on camera (silent slices from the HeyGen avatar video)
 *   2. AI-generated photoreal stills (B-roll beats)
 *
 * One continuous voiceover — the HeyGen audio carries every beat — so
 * lip-sync stays perfect on avatar cuts and word-pinned captions stay
 * locked across the whole timeline.
 *
 * Cost: 1 HeyGen render + 1 Whisper + N gpt-image-1 (only for B-roll
 * beats) + 1 Creatomate compose. Same HeyGen spend as avatar_only,
 * but the output looks like an edited reel instead of a static talking
 * head.
 *
 * The LLM picks who's on screen per beat. Rule: avatar for personal /
 * emotional / hook / CTA, B-roll for facts / numbers / locations.
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
  rendering_clips: "classifying + generating images + slicing avatar",
  assembling: "stitching (Creatomate)",
  succeeded: "done",
  failed: "failed",
};

type Beat = {
  index: number;
  start: number;
  end: number;
  text: string;
  image_prompt?: string;
  image_url?: string | null;
  image_error?: string;
  role?: "avatar" | "broll";
  role_reason?: string;
  video_url?: string | null;
  video_error?: string;
};

export default function StoryMixPage() {
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
          .filter(
            (p) => (p as Production & { mode?: string }).mode === "avatar_story_mix",
          )
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
        mode: "avatar_story_mix",
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
  const beats: Beat[] = (prod?.scenes as unknown as Beat[]) || [];
  const avatarCount = beats.filter((b) => b.role === "avatar").length;
  const brollCount = beats.filter((b) => b.role === "broll").length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Story Reel (mix)"
        sub="One HeyGen render, reused twice: James on camera for the personal beats, AI photoreal stills for the facts/numbers/locations. Same continuous voice across the whole reel — no audio drift. The LLM picks who's on screen per beat. Lands in the approval queue."
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
            Uses live research + brand voice. One continuous voiceover —
            the LLM decides per beat who's on screen.
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
          placeholder="Paste your script here, or click Auto-generate above. James will speak this verbatim; the LLM decides which beats show his face and which show a photoreal still."
          className="text-[13px] mt-2"
        />
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>3. Produce</CardTitle>
            <p className="text-[12px] text-muted-foreground mt-1">
              HeyGen voice (1 render, reused 100%) → Whisper word-stamps →
              LLM classifies each beat as avatar vs B-roll → photoreal
              still per B-roll beat + silent slice per avatar beat →
              Creatomate stitches one continuous reel.
            </p>
          </div>
          <Button onClick={produce} disabled={producing || active || !script.trim()}>
            {producing || active ? <Spinner /> : "Make story reel"}
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
              {beats.length > 0 && (
                <span className="text-[12px] text-muted-foreground">
                  · {avatarCount} avatar · {brollCount} B-roll
                </span>
              )}
              <span className="text-[12px] text-muted-foreground ml-auto">
                id: {prod.id.slice(0, 8)}…
              </span>
            </div>
            {prod.error && <p className="text-destructive text-[12px] mt-2">{prod.error}</p>}

            {/* Beat grid — populates progressively. Avatar beats show a
                tiny mp4 thumbnail (silent slice from HeyGen); B-roll
                beats show the gpt-image-1 still. */}
            {beats.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-2">
                {beats.map((b) => {
                  const isAvatar = b.role === "avatar";
                  const ready = isAvatar ? b.video_url : b.image_url;
                  const failed = isAvatar ? b.video_error : b.image_error;
                  return (
                    <div
                      key={b.index}
                      className="border border-border rounded-md overflow-hidden bg-background"
                    >
                      {ready ? (
                        isAvatar ? (
                          <video
                            src={mediaUrl(ready)}
                            muted
                            controls={false}
                            className="w-full aspect-video object-cover"
                          />
                        ) : (
                          <img
                            src={mediaUrl(ready)}
                            alt={b.text}
                            className="w-full aspect-video object-cover"
                          />
                        )
                      ) : (
                        <div className="w-full aspect-video flex items-center justify-center text-[10px] text-muted-foreground bg-secondary">
                          {failed ? `✗ ${failed}` : <Spinner />}
                        </div>
                      )}
                      <div className="p-1.5">
                        <div className="flex items-center gap-1 mb-0.5">
                          <Badge
                            tone={isAvatar ? "ok" : "primary"}
                          >
                            {isAvatar ? "avatar" : "B-roll"}
                          </Badge>
                          <span className="text-[10px] text-muted-foreground">
                            {b.start.toFixed(1)}–{b.end.toFixed(1)}s
                          </span>
                        </div>
                        <div className="text-[11px] line-clamp-2">{b.text}</div>
                        {b.role_reason && (
                          <div
                            className="text-[10px] text-muted-foreground mt-0.5 italic line-clamp-1"
                            title={b.role_reason}
                          >
                            {b.role_reason}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
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
          <CardTitle>Recent story reels</CardTitle>
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
