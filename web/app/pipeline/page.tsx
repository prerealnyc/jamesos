"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  mediaUrl,
  type VideoJob,
  type ScenePlan,
  type Production,
} from "@/lib/api";
import {
  Button,
  Card,
  CardTitle,
  Input,
  Textarea,
  Select,
  Label,
  Badge,
  Spinner,
  PageHeader,
} from "@/components/ui";

export default function VideoStudio() {
  const [mode, setMode] = useState<"producer" | "clip">("producer");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Video Studio"
        sub="Turn a script into a finished video — scene plan → talking-head (avatar or your clips) + B-roll → assembled cut → approval queue. Durable and stub-first, so it runs end-to-end before you add render keys."
      />
      <div className="flex gap-2">
        <Tab active={mode === "producer"} onClick={() => setMode("producer")}>
          Producer
        </Tab>
        <Tab active={mode === "clip"} onClick={() => setMode("clip")}>
          Single clip
        </Tab>
      </div>
      {mode === "producer" ? <Producer /> : <SingleClip />}
    </div>
  );
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-semibold rounded-md transition-colors ${
        active ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

const STAGE_TONE: Record<string, "muted" | "accent" | "ok" | "destructive"> = {
  queued: "muted", planning: "accent", rendering_clips: "accent",
  assembling: "accent", succeeded: "ok", failed: "destructive",
};

function Producer() {
  const [script, setScript] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [aspect, setAspect] = useState("9:16");
  const [plan, setPlan] = useState<ScenePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [prod, setProd] = useState<Production | null>(null);
  const [productions, setProductions] = useState<Production[]>([]);
  const [err, setErr] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadList() {
    try { setProductions(await api.listProductions()); } catch {}
  }
  useEffect(() => { loadList(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);

  async function preview() {
    if (!script.trim()) return;
    setPlanning(true); setErr(""); setPlan(null);
    try { setPlan(await api.planVideo(script.trim(), platform, aspect)); }
    catch (e) { setErr(e instanceof Error ? e.message : "planning failed"); }
    finally { setPlanning(false); }
  }

  async function produce() {
    if (!script.trim()) return;
    setErr("");
    try {
      const p = await api.produceVideo(script.trim(), platform, aspect);
      setProd(p);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const u = await api.getProduction(p.id).catch(() => null);
        if (u) {
          setProd(u);
          if (u.status === "succeeded" || u.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            loadList();
          }
        }
      }, 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "produce failed");
    }
  }

  const active = prod && prod.status !== "succeeded" && prod.status !== "failed";

  return (
    <>
      <Card>
        <CardTitle>Script → video</CardTitle>
        <Label>Script</Label>
        <Textarea rows={4} placeholder="paste an approved script (or one from Content Studio)"
          value={script} onChange={(e) => setScript(e.target.value)} />
        <div className="grid grid-cols-2 gap-4">
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
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={preview} disabled={planning || !script.trim()}>
            {planning ? <Spinner /> : "Preview plan"}
          </Button>
          <Button onClick={produce} disabled={!script.trim() || !!active}>
            {active ? <Spinner /> : "Produce video"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      {plan && !prod && <PlanView plan={plan} />}
      {prod && <ProductionView prod={prod} />}

      {productions.length > 0 && (
        <Card>
          <CardTitle>Recent productions</CardTitle>
          <div className="flex flex-col gap-2 mt-2">
            {productions.map((p) => (
              <div key={p.id} className="flex items-center gap-2 text-[13px] border border-border rounded-md p-2">
                <Badge tone={STAGE_TONE[p.status]}>{p.status}</Badge>
                <span className="flex-1 truncate">{p.title || p.script.slice(0, 60)}</span>
                <span className="text-muted-foreground text-[11px]">{p.scenes?.length || 0} scenes</span>
                <button className="text-primary text-[12px]" onClick={() => setProd(p)}>view</button>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

function PlanView({ plan }: { plan: ScenePlan }) {
  return (
    <Card>
      <CardTitle>Scene plan — {plan.title || "(untitled)"}</CardTitle>
      {plan.error && <p className="text-destructive text-sm">{plan.error}</p>}
      <div className="flex flex-col gap-2 mt-2">
        {plan.scenes.map((s) => (
          <div key={s.index} className="border border-border rounded-md p-3 text-[13px]">
            <div className="flex items-center gap-2 mb-1">
              {s.label && <Badge tone="accent">{s.label}</Badge>}
              <Badge tone={s.kind === "broll" ? "muted" : "primary"}>
                {s.kind === "broll" ? "B-roll" : `talking head · ${s.source}`}
              </Badge>
              <span className="text-[11px] text-muted-foreground">{s.duration}s</span>
            </div>
            {s.voiceover && <p><b>VO:</b> {s.voiceover}</p>}
            {s.on_screen_text && <p className="text-muted-foreground">caption: {s.on_screen_text}</p>}
            {s.visual_prompt && <p className="text-muted-foreground">visual: {s.visual_prompt}</p>}
          </div>
        ))}
      </div>
    </Card>
  );
}

function ProductionView({ prod }: { prod: Production }) {
  const isStub = (prod.final_url || "").startsWith("stub://");
  const stages = ["planning", "rendering_clips", "assembling", "succeeded"];
  const curIdx = stages.indexOf(prod.status);
  return (
    <Card>
      <div className="flex items-center justify-between">
        <CardTitle>Production — {prod.title || "(untitled)"}</CardTitle>
        <Badge tone={STAGE_TONE[prod.status]}>{prod.status}</Badge>
      </div>

      <div className="flex gap-1 mt-2 mb-3">
        {stages.map((st, i) => (
          <div key={st} className={`h-1.5 flex-1 rounded ${
            prod.status === "failed" ? "bg-destructive/40" : i <= curIdx ? "bg-primary" : "bg-secondary"
          }`} title={st} />
        ))}
      </div>

      {prod.error && <p className="text-destructive text-sm mb-2">✗ {prod.error}</p>}

      <div className="text-[11px] text-muted-foreground mb-3">
        avatar: {prod.avatar_provider} · b-roll: {prod.broll_provider} · assembly: {prod.assembly_provider}
      </div>

      {prod.final_url && (
        isStub ? (
          <div className="text-[13px] text-accent border border-accent/40 rounded-md p-3 mb-3">
            Stub render complete (no real mp4). Add HeyGen <code>voice_id</code> + a Creatomate key to produce a real cut.
          </div>
        ) : (
          <video src={mediaUrl(prod.final_url)} controls className="w-full rounded-md mb-3" />
        )
      )}

      {prod.scenes?.length > 0 && (
        <div className="flex flex-col gap-2">
          {prod.scenes.map((s) => (
            <div key={s.index} className="border border-border rounded-md p-2 text-[12px]">
              <div className="flex items-center gap-2">
                {s.label && <Badge tone="accent">{s.label}</Badge>}
                <Badge tone={s.clip_status === "ok" ? "ok" : "muted"}>
                  {s.kind === "broll" ? "B-roll" : `${s.source}`}
                </Badge>
                <span className="text-muted-foreground">{s.duration}s</span>
                {s.clip_status === "stub" && <span className="text-muted-foreground">stub</span>}
              </div>
              {s.on_screen_text && <div className="mt-1">{s.on_screen_text}</div>}
              {s.note && <div className="text-muted-foreground text-[11px] mt-1">{s.note}</div>}
            </div>
          ))}
        </div>
      )}

      {prod.queued_action_id && (
        <Link href="/queue" className="text-[13px] text-primary hover:underline mt-3 inline-block">
          Review in approval queue →
        </Link>
      )}
    </Card>
  );
}

// ── existing single-clip Runway flow ──

function statusTone(s: VideoJob["status"]): "muted" | "accent" | "ok" | "destructive" {
  if (s === "succeeded") return "ok";
  if (s === "failed") return "destructive";
  if (s === "processing" || s === "submitted") return "accent";
  return "muted";
}
function isInFlight(j: VideoJob) {
  return j.status === "submitted" || j.status === "processing" || j.status === "queued";
}

function SingleClip() {
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [jobs, setJobs] = useState<VideoJob[]>([]);

  async function loadJobs() {
    try { setJobs(await api.listVideoJobs()); } catch {}
  }
  useEffect(() => { loadJobs(); }, []);
  useEffect(() => {
    const inflight = jobs.filter(isInFlight);
    if (inflight.length === 0) return;
    const t = setInterval(async () => {
      const updated = await Promise.all(inflight.map((j) => api.getVideoJob(j.id).catch(() => j)));
      setJobs((cur) => cur.map((j) => updated.find((u) => u.id === j.id) ?? j));
    }, 5000);
    return () => clearInterval(t);
  }, [jobs]);

  async function submit() {
    if (!prompt.trim()) return;
    setBusy(true); setErr("");
    try {
      const j = await api.generateVideo(prompt.trim(), image.trim());
      setJobs((cur) => [j, ...cur.filter((x) => x.id !== j.id)]);
      setPrompt("");
    } catch (e) { setErr(e instanceof Error ? e.message : "submit failed"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <Card>
        <CardTitle>New render</CardTitle>
        <Label>Prompt</Label>
        <Textarea rows={3} placeholder="describe the clip — motion, framing, mood"
          value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <Label>Still image URL (required by Runway&apos;s dev API)</Label>
        <Input placeholder="https://… (ignored when VIDEO_PROVIDER=stub)"
          value={image} onChange={(e) => setImage(e.target.value)} />
        <div className="mt-3">
          <Button onClick={submit} disabled={busy || !prompt.trim()}>
            {busy ? <Spinner /> : "Submit render"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      <Card>
        <CardTitle>Jobs</CardTitle>
        {jobs.length === 0 && <p className="text-muted-foreground text-sm mt-2">No renders yet.</p>}
        <div className="mt-3 flex flex-col gap-2">
          {jobs.map((j) => {
            const isStub = (j.result_url || "").startsWith("stub://");
            return (
              <div key={j.id} className="bg-background border border-border rounded-md p-3">
                <div className="flex items-center gap-2 mb-1">
                  <Badge tone={statusTone(j.status)}>{j.status}</Badge>
                  <span className="text-[11px] font-mono text-muted-foreground">{j.provider}</span>
                  {isStub && <Badge tone="muted">stub</Badge>}
                </div>
                <div className="text-sm whitespace-pre-wrap">{j.prompt}</div>
                {j.result_url && !isStub && (
                  <a href={j.result_url} target="_blank" rel="noreferrer"
                    className="text-sm text-primary hover:underline mt-2 inline-block">Open result →</a>
                )}
                {j.error && <p className="text-destructive text-[13px] mt-2">✗ {j.error}</p>}
              </div>
            );
          })}
        </div>
      </Card>
    </>
  );
}
