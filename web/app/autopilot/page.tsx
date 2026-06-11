"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, type AutopilotConfig, type AutopilotRun } from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";

const PLATFORMS = ["instagram", "linkedin", "facebook", "tiktok", "x"];
const FORMATS = ["reel_script", "post", "caption", "thread"];
const BULK_SIZES = [5, 7, 10, 14, 30];

function runTone(s: string): "muted" | "accent" | "ok" | "destructive" {
  if (s === "succeeded") return "ok";
  if (s === "failed") return "destructive";
  return "accent";
}

export default function AutopilotPage() {
  const [cfg, setCfg] = useState<AutopilotConfig | null>(null);
  const [runs, setRuns] = useState<AutopilotRun[]>([]);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState("");
  const [bulkCount, setBulkCount] = useState(10);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadAll() {
    const [c, r] = await Promise.all([
      api.getAutopilotConfig().catch(() => null),
      api.listAutopilotRuns().catch(() => []),
    ]);
    if (c) setCfg(c);
    setRuns(r);
  }
  useEffect(() => {
    loadAll();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // Poll while any run is in progress.
  useEffect(() => {
    if (!runs.some((r) => r.status === "running")) return;
    const t = setInterval(() => api.listAutopilotRuns().then(setRuns).catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [runs]);

  if (!cfg) return <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>;

  function patch(p: Partial<AutopilotConfig>) { setCfg({ ...cfg!, ...p }); }
  function togglePlatform(pl: string) {
    const has = cfg!.platforms.includes(pl);
    patch({ platforms: has ? cfg!.platforms.filter((x) => x !== pl) : [...cfg!.platforms, pl] });
  }

  async function save() {
    setSaving(true); setMsg("");
    try { setCfg(await api.setAutopilotConfig(cfg!)); setMsg("saved"); }
    finally { setSaving(false); setTimeout(() => setMsg(""), 1500); }
  }
  async function runNow() {
    setRunning(true); setMsg("");
    try {
      await api.runAutopilot();
      setMsg("batch started");
      setTimeout(() => api.listAutopilotRuns().then(setRuns), 800);
    } finally { setRunning(false); }
  }
  async function bulkGenerate() {
    const n = Math.max(1, Math.min(60, Math.round(bulkCount) || 1));
    setBulkBusy(true); setBulkNotice(null);
    try {
      await api.bulkGenerate(n);
      setBulkNotice(`Started — ${n} pieces generating in the background. They'll appear in the Approval Queue as they finish.`);
      setTimeout(() => api.listAutopilotRuns().then(setRuns).catch(() => {}), 800);
    } finally { setBulkBusy(false); }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Autopilot"
        sub="Autonomous daily content. It invents fresh story-framed ideas grounded in James's voice, drafts them through voice-QA + your learned guardrails, and drops them in the approval queue. Nothing publishes — you still approve every piece."
      />

      <Card className="border-primary/40 bg-primary/5">
        <div className="flex items-center justify-between">
          <CardTitle>Bulk create</CardTitle>
          <Badge tone="primary">one click</Badge>
        </div>
        <p className="text-[12px] text-muted-foreground mt-1">
          Half become text+image posts, half become video reels — all land in the{" "}
          <Link href="/queue" className="underline">Approval Queue</Link> for review.
        </p>

        <Label>Batch size</Label>
        <div className="flex flex-wrap items-center gap-2">
          {BULK_SIZES.map((n) => (
            <button
              key={n}
              onClick={() => setBulkCount(n)}
              className={`text-[13px] rounded-full px-3.5 py-1.5 border transition-colors ${
                bulkCount === n
                  ? "border-primary text-foreground bg-primary/15 font-semibold"
                  : "border-border text-muted-foreground hover:bg-secondary"
              }`}
            >
              {n}
            </button>
          ))}
          <div className="w-24">
            <Input
              type="number" min={1} max={60} value={bulkCount}
              aria-label="Custom batch size"
              onChange={(e) => setBulkCount(Math.max(1, Math.min(60, +e.target.value || 1)))}
            />
          </div>
          <span className="text-[11px] text-muted-foreground self-center">pieces (~N days of content)</span>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={bulkGenerate} disabled={bulkBusy}>
            {bulkBusy ? <Spinner /> : `Generate ${Math.max(1, Math.min(60, Math.round(bulkCount) || 1))} pieces`}
          </Button>
        </div>
        {bulkNotice && (
          <div className="mt-3 rounded-md border border-primary/40 bg-primary/10 p-3 text-[12px] text-foreground">
            {bulkNotice}{" "}
            <Link href="/queue" className="text-primary underline font-medium">Open the Approval Queue →</Link>
          </div>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <CardTitle>Settings</CardTitle>
          <button
            onClick={() => patch({ enabled: !cfg.enabled })}
            className={`px-3 py-1.5 rounded-md text-sm font-semibold transition-colors ${
              cfg.enabled ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground"
            }`}
          >
            {cfg.enabled ? "Enabled" : "Disabled"}
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4 mt-3">
          <div>
            <Label>Pieces per day</Label>
            <Input type="number" min={1} max={10} value={cfg.daily_count}
              onChange={(e) => patch({ daily_count: Math.max(1, Math.min(10, +e.target.value || 1)) })} />
          </div>
          <div>
            <Label>Run hour (server time, 0–23)</Label>
            <Input type="number" min={0} max={23} value={cfg.hour}
              onChange={(e) => patch({ hour: Math.max(0, Math.min(23, +e.target.value || 0)) })} />
          </div>
        </div>

        <Label>Format</Label>
        <Select value={cfg.format} onChange={(e) => patch({ format: e.target.value })}>
          {FORMATS.map((f) => <option key={f}>{f}</option>)}
        </Select>

        <Label>Platforms</Label>
        <div className="flex flex-wrap gap-2">
          {PLATFORMS.map((p) => (
            <button key={p} onClick={() => togglePlatform(p)}
              className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
                cfg.platforms.includes(p) ? "border-primary text-foreground bg-primary/10" : "border-border text-muted-foreground"
              }`}>{p}</button>
          ))}
          <span className="text-[11px] text-muted-foreground self-center">(first platform is used per piece)</span>
        </div>

        <Label>Video styles</Label>
        <div className="flex items-center gap-3">
          <button
            onClick={() => patch({ use_style_templates: !(cfg.use_style_templates !== false) })}
            className={`px-3 py-1.5 rounded-md text-sm font-semibold transition-colors ${
              cfg.use_style_templates !== false
                ? "bg-primary text-primary-foreground"
                : "bg-secondary text-muted-foreground"
            }`}
          >
            {cfg.use_style_templates !== false ? "Vary styles: On" : "Vary styles: Off"}
          </button>
          <span className="text-[11px] text-muted-foreground">
            Each reel uses a different style from your{" "}
            <Link href="/style-templates" className="underline">Style Templates</Link> library
            (cycles when you have fewer styles than videos). Falls back to the standard look when the library is empty.
          </span>
        </div>

        <Label>Caption mode</Label>
        <div className="flex items-center gap-3">
          <Select
            value={cfg.caption_mode || "rotate"}
            onChange={(e) => patch({ caption_mode: e.target.value })}
          >
            <option value="rotate">Rotate styles (compare)</option>
            <option value="smart">AI best-fit per video</option>
            <option value="template">Template default</option>
          </Select>
          <span className="text-[11px] text-muted-foreground">
            <b>Rotate</b>: each reel gets the next style (viral hook → magenta blocks →
            editorial serif → mint scatter → TikTok yellow → highlight box → karaoke
            green, continuing across batches) — compare on real renders, finalise
            favourites. <b>AI best-fit</b>: the editor LLM reads each script and picks
            the style that matches it (how-to hook → viral hook, hot take → magenta
            blocks, launch → editorial serif…). <b>Template default</b>: the replicated
            style template&apos;s analysed preset decides.
          </span>
        </div>

        <Label>B-roll engine</Label>
        <div className="flex items-center gap-3">
          <Select value={cfg.broll_engine || ""} onChange={(e) => patch({ broll_engine: e.target.value })}>
            <option value="">System default</option>
            <option value="runway">Runway</option>
            <option value="higgsfield">Higgsfield</option>
          </Select>
          <span className="text-[11px] text-muted-foreground">
            Which engine animates the B-roll cutaways in every batch reel. If the
            chosen engine&apos;s key/credits are missing, that insert keeps its
            still image (the render never fails).
          </span>
        </div>

        <Label>Topic hint (optional)</Label>
        <Input placeholder="e.g. Staten Island commercial real estate, mindset, deals"
          value={cfg.topic_hint} onChange={(e) => patch({ topic_hint: e.target.value })} />

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={save} disabled={saving}>{saving ? <Spinner /> : "Save settings"}</Button>
          <Button variant="secondary" onClick={runNow} disabled={running}>
            {running ? <Spinner /> : "Run a batch now"}
          </Button>
          {msg && <span className="text-[12px] text-primary">{msg}</span>}
        </div>
        <p className="text-[11px] text-muted-foreground mt-3">
          {cfg.enabled
            ? `Scheduled: ~${cfg.daily_count} piece(s)/day after ${cfg.hour}:00 server time. Last run: ${cfg.last_run_date || "never"}.`
            : "Autopilot is off — it won't run on a schedule until enabled. You can still 'Run a batch now'."}
          {" "}Honest note: the scheduler runs in-process, so scheduled runs only fire while the server is up.
        </p>
      </Card>

      <Card>
        <CardTitle>Recent runs</CardTitle>
        {runs.length === 0 && (
          <p className="text-muted-foreground text-sm mt-2">
            No batches run yet. Click <strong>Run a batch now</strong> above to test your configured settings, or enable the daily schedule. The autopilot will then propose drafts to the <Link href="/queue" className="underline">Approval Queue</Link> every day.
          </p>
        )}
        <div className="flex flex-col gap-3 mt-3">
          {runs.map((r) => (
            <div key={r.id} className="border border-border rounded-md p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge tone={runTone(r.status)}>{r.status === "running" ? "running…" : r.status}</Badge>
                <span className="text-[11px] text-muted-foreground">{r.trigger}</span>
                {r.status === "running" && r.stage && (
                  <span className="text-[11px] text-primary">· {r.stage}</span>
                )}
                <span className="text-[12px] ml-auto">
                  {r.generated}/{r.requested} drafted · <b className="text-foreground">{r.queued} queued</b>
                </span>
              </div>
              {r.error && <p className="text-destructive text-[12px] mt-1">✗ {r.error}</p>}
              {r.research?.summary && (
                <div className="mt-2 rounded-md bg-background border border-border p-2 text-[11px]">
                  <span className="uppercase tracking-[.4px] text-muted-foreground">
                    Researched first ({r.research.provider}) ·{" "}
                    {r.research.sources?.length || 0} sources
                  </span>
                  <p className="mt-1 text-muted-foreground line-clamp-3">{r.research.summary}</p>
                </div>
              )}
              {(r.research?.cohort_creators?.length || 0) > 0 && (
                <div className="mt-2 rounded-md bg-background border border-border p-2 text-[11px]">
                  <span className="uppercase tracking-[.4px] text-muted-foreground">
                    Cohort match · {r.research?.cohort_creators?.length} creator(s) ·{" "}
                    {r.research?.cohort_trends?.length || 0} trend events
                  </span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {r.research?.cohort_creators?.slice(0, 6).map((c, n) => (
                      <span
                        key={n}
                        className="text-[10px] bg-secondary text-foreground rounded px-1.5 py-0.5"
                        title={(c.interests || []).join(", ")}
                      >
                        {c.name}
                      </span>
                    ))}
                  </div>
                  {(r.research?.cohort_trends?.length || 0) === 0 && (
                    <p className="text-[10px] text-muted-foreground mt-1">
                      No scraped events from this cohort yet — refresh the
                      watchlist on Social Companion to populate.
                    </p>
                  )}
                </div>
              )}
              {r.ideas?.length > 0 && (
                <ul className="mt-2 text-[12px] text-muted-foreground flex flex-col gap-1">
                  {r.ideas.map((i, n) => (
                    <li key={n}>
                      <span className="text-foreground">{i.title}</span>
                      {i.trend_basis && <span className="text-primary"> · rides: {i.trend_basis}</span>}
                    </li>
                  ))}
                </ul>
              )}
              {r.results?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {r.results.map((res, n) => (
                    <Badge key={n} tone={res.voice_score >= 0.7 ? "ok" : "accent"}>
                      {res.platform} · voice {res.voice_score}
                    </Badge>
                  ))}
                </div>
              )}
              {r.queued > 0 && (
                <Link href="/queue" className="text-[12px] text-primary hover:underline mt-2 inline-block">
                  Review {r.queued} in the approval queue →
                </Link>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
