"use client";

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

export default function HeyGenVideoPage() {
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
      // Only show avatar-only productions on this page so it's never
      // confused with the mixed-mode pipeline.
      setRecent(list.filter((p) => (p as Production & { mode?: string }).mode === "avatar_only").slice(0, 8));
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
      // We only want the script — scenes are deliberately ignored on this page.
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
        mode: "avatar_only",
        script: script.trim(),
        platform,
        aspect,
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
      }, 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "produce failed");
    } finally {
      setProducing(false);
    }
  }

  const active = !!prod && prod.status !== "succeeded" && prod.status !== "failed";
  const estSec = Math.max(1, Math.round((script.length || 0) / 14));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="HeyGen Video"
        sub="One HeyGen avatar speaking your script end-to-end. James's cloned voice, auto-burned spoken-word captions, no B-roll, no scene cuts. Lands in the approval queue."
      />

      <Card>
        <CardTitle>1. Write or generate the script</CardTitle>
        <Label>Topic (optional, used by auto-generate)</Label>
        <Input
          placeholder="e.g. why Staten Island is underpriced for commercial RE"
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
            Uses live research + brand voice. Always one continuous script — no scenes.
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
          placeholder="Paste your script here, or click Auto-generate above. The HeyGen avatar will speak this verbatim, in James's cloned voice, with spoken-word captions burned in."
          className="text-[13px] mt-2"
        />
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>3. Produce</CardTitle>
            <p className="text-[12px] text-muted-foreground mt-1">
              One HeyGen render. ~1–3 min depending on script length. Lands in the Approval Queue.
            </p>
          </div>
          <Button onClick={produce} disabled={producing || active || !script.trim()}>
            {producing || active ? <Spinner /> : "Make HeyGen video"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}

        {prod && (
          <div className="mt-4 border-t border-border pt-4">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={STAGE_TONE[prod.status] || "muted"}>{prod.status}</Badge>
              {active && <Spinner />}
              <span className="text-[12px] text-muted-foreground ml-auto">id: {prod.id.slice(0, 8)}…</span>
            </div>
            {prod.error && <p className="text-destructive text-[12px] mt-2">{prod.error}</p>}
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
          <CardTitle>Recent HeyGen-only videos</CardTitle>
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
