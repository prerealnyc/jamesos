"use client";

/**
 * Engaging Reel — HeyGen avatar full video + cinematic B-roll cutaways.
 *
 * James is on camera continuously (full HeyGen render plays end-to-end).
 * The LLM picks 2-5 short windows (1.5-2.5s each) where cutting in
 * with a cinematic B-roll image amplifies what he's saying — a place,
 * an object, a number, a dramatic concept. The image fades in over
 * the avatar, holds for the window, fades out — then back to James.
 *
 * Hero refs flow on uses_hero-tagged inserts (same path as the story
 * modes). Captions use safe-zone placement: when an insert overlays,
 * captions take the broll position; otherwise the avatar-safe bottom.
 *
 * Different from Story Reel (mix): that mode SPLITS time between
 * avatar beats and broll beats (50/50ish). This mode keeps James
 * on screen MOST of the time and uses B-roll as punctuation.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type Production } from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";
import { CaptionPicker } from "@/components/caption-picker";
import { ImageStylePicker } from "@/components/image-style-picker";

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
  planning: "rendering avatar (HeyGen)",
  rendering_clips: "picking inserts + generating B-roll",
  assembling: "stitching (Creatomate)",
  succeeded: "done",
  failed: "failed",
};

type Insert = {
  index: number;
  start: number;
  end: number;
  text: string;
  image_prompt: string;
  image_url: string | null;
  image_error?: string;
  uses_hero?: boolean;
};

export default function EngagingVideoPage() {
  const [topic, setTopic] = useState("");
  const [aspect, setAspect] = useState("9:16");
  const [platform, setPlatform] = useState("instagram");
  const [script, setScript] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [imageStyle, setImageStyle] = useState("");
  // Video format: full-frame avatar (engaging_avatar) vs split-screen
  // (split_horizontal — avatar top, B-roll bottom, captions on the seam).
  const [mode, setMode] = useState<"engaging_avatar" | "split_horizontal">("engaging_avatar");
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
            (p) => {
              const m = String((p as Production & { mode?: string }).mode ?? "");
              return m === "engaging_avatar" || m === "split_horizontal";
            },
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
        mode,
        script: script.trim(),
        platform, aspect,
        caption_style: captionStyle,
        image_style: imageStyle,
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
  // engaging_avatar stores `inserts` (not beats) in the same scenes column
  const inserts: Insert[] = (prod?.scenes as unknown as Insert[]) || [];
  const heroInserts = inserts.filter((i) => i.uses_hero).length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Engaging Reel"
        sub="James on camera full-time, with 2-5 cinematic B-roll cutaways punching in at the moments the LLM picks as visually amplifiable. Hero references flow when an insert is about James himself. Captions are placed safely around the speaker's face. Same HeyGen spend as avatar-only; visibly more engaging."
      />

      <Card>
        <CardTitle>Format</CardTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {([
            { id: "engaging_avatar", title: "Full-frame avatar", desc: "James on camera the whole time; cinematic B-roll punches in as brief cutaways. Captions placed around the face." },
            { id: "split_horizontal", title: "Split-screen", desc: "James pinned to the top half, B-roll in the bottom half the whole time, pink captions across the middle seam." },
          ] as const).map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => setMode(opt.id)}
              className={`text-left rounded-lg border p-3 transition-colors ${mode === opt.id ? "border-primary bg-primary/5" : "border-border hover:bg-muted/30"}`}
            >
              <div className="flex items-center gap-2">
                <span className={`inline-block w-3.5 h-3.5 rounded-full border ${mode === opt.id ? "border-primary bg-primary" : "border-muted-foreground"}`} />
                <span className="text-[13px] font-medium">{opt.title}</span>
              </div>
              <p className="text-[11px] text-muted-foreground mt-1.5">{opt.desc}</p>
            </button>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle>1. Write or generate the script</CardTitle>
        <Label>Topic (optional, used by auto-generate)</Label>
        <Input
          placeholder="e.g. how I knew the deal that would change my life"
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
            Uses live research + brand voice. The LLM decides per-script where
            to cut in B-roll once the audio + word timestamps are in.
          </span>
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <CardTitle>2. Script (every word James will speak)</CardTitle>
          <span className="text-[12px] text-muted-foreground">
            ~{estSec}s ({script.length} chars)
          </span>
        </div>
        <Textarea
          rows={12}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder="Paste your script here, or click Auto-generate above. James will be on camera the whole time; the system finds 2-5 moments to cut in a cinematic B-roll image for 1.5-2.5 seconds each."
          className="text-[13px] mt-2"
        />
      </Card>

      <Card>
        <CardTitle>3. Image style (B-roll cutaways only)</CardTitle>
        <ImageStylePicker value={imageStyle} onChange={setImageStyle} />
      </Card>

      <Card>
        <CardTitle>4. Caption style</CardTitle>
        <CaptionPicker value={captionStyle} onChange={setCaptionStyle} />
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>5. Produce</CardTitle>
            <p className="text-[12px] text-muted-foreground mt-1">
              HeyGen render (1) → Whisper word stamps → LLM picks 2-5
              insert windows → cinematic still per insert (hero refs
              when uses_hero) → Creatomate composes the avatar + overlays
              + captions. ~2-3 minutes total.
            </p>
          </div>
          <Button onClick={produce} disabled={producing || active || !script.trim()}>
            {producing || active ? <Spinner /> : (mode === "split_horizontal" ? "Make split-screen reel" : "Make engaging reel")}
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
              {inserts.length > 0 && (
                <span className="text-[12px] text-muted-foreground">
                  · {inserts.length} cutaway{inserts.length === 1 ? "" : "s"}
                  {heroInserts > 0 && ` (${heroInserts} hero)`}
                </span>
              )}
              <span className="text-[12px] text-muted-foreground ml-auto">
                id: {prod.id.slice(0, 8)}…
              </span>
            </div>
            {prod.error && <p className="text-destructive text-[12px] mt-2">{prod.error}</p>}

            {/* Insert grid */}
            {inserts.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-2">
                {inserts.map((ins) => (
                  <div
                    key={ins.index}
                    className="border border-border rounded-md overflow-hidden bg-background"
                  >
                    {ins.image_url ? (
                      <img
                        src={mediaUrl(ins.image_url)}
                        alt={ins.text}
                        className="w-full aspect-video object-cover"
                      />
                    ) : (
                      <div className="w-full aspect-video flex items-center justify-center text-[10px] text-muted-foreground bg-secondary">
                        {ins.image_error ? `✗ ${ins.image_error}` : <Spinner />}
                      </div>
                    )}
                    <div className="p-1.5">
                      <div className="flex items-center gap-1 mb-0.5">
                        {ins.uses_hero && <Badge tone="ok">hero</Badge>}
                        <span className="text-[10px] text-muted-foreground">
                          {ins.start.toFixed(1)}–{ins.end.toFixed(1)}s
                        </span>
                      </div>
                      <div className="text-[11px] line-clamp-2">{ins.text}</div>
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
          <CardTitle>Recent engaging reels</CardTitle>
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
