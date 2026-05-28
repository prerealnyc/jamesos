"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, type AutopilotConfig, type AutopilotRun } from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";

const PLATFORMS = ["instagram", "linkedin", "facebook", "tiktok", "x"];
const FORMATS = ["reel_script", "post", "caption", "thread"];

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

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Autopilot"
        sub="Autonomous daily content. It invents fresh story-framed ideas grounded in James's voice, drafts them through voice-QA + your learned guardrails, and drops them in the approval queue. Nothing publishes — you still approve every piece."
      />

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
        {runs.length === 0 && <p className="text-muted-foreground text-sm mt-2">No runs yet.</p>}
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
