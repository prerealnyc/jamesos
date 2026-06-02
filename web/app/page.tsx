"use client";

/**
 * Ask the memory — two modes side-by-side:
 *
 *   Ask  → grounded Q&A over the events table (the original feature).
 *          Cite-or-refuse with a verification pass; doesn't touch state.
 *
 *   Do   → agent mode. Claude with tool-use, calls real endpoints
 *          (render reels, refresh analytics, approve queue items,
 *          import Drive videos, etc.). Every run lands in agent_runs
 *          so the user sees what got initiated, what completed, and
 *          what each tool returned.
 *
 * Both modes share the same input box — a mode toggle at the top
 * decides where the prompt goes.
 */

import { useEffect, useRef, useState } from "react";
import { api, type AskResponse } from "@/lib/api";
import { Button, Card, Input, Spinner, Badge, PageHeader } from "@/components/ui";

type AgentRun = Awaited<ReturnType<typeof api.getAgentRun>>;
type AgentRunListItem = Awaited<ReturnType<typeof api.listAgentRuns>>["runs"][number];
type Tool = Awaited<ReturnType<typeof api.listAgentTools>>["tools"][number];

const POLL_MS = 1200;

function fmtRelative(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function AskPage() {
  const [mode, setMode] = useState<"ask" | "do">("ask");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<AskResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Do-mode state
  const [activeRun, setActiveRun] = useState<AgentRun | null>(null);
  const [runs, setRuns] = useState<AgentRunListItem[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [showTools, setShowTools] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadRuns() {
    try {
      const r = await api.listAgentRuns(15);
      setRuns(r.runs);
    } catch { /* noop */ }
  }

  useEffect(() => {
    loadRuns();
    api.listAgentTools()
      .then((r) => setTools(r.tools))
      .catch(() => { /* keep empty */ });
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // Poll the active run while it's running.
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!activeRun || activeRun.status !== "running") return;
    pollRef.current = setInterval(async () => {
      try {
        const fresh = await api.getAgentRun(activeRun.id);
        setActiveRun(fresh);
        if (fresh.status !== "running") {
          await loadRuns();
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch { /* keep polling */ }
    }, POLL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeRun?.id, activeRun?.status]);

  async function ask() {
    const question = q.trim();
    if (!question) return;
    if (mode === "ask") {
      setLoading(true); setErr(null); setRes(null);
      try { setRes(await api.ask(question)); }
      catch (e) { setErr(e instanceof Error ? e.message : "request failed"); }
      finally { setLoading(false); }
      return;
    }
    // Do mode — kick an agent run and start polling.
    setLoading(true); setErr(null); setActiveRun(null);
    try {
      const kicked = await api.agentRun(question);
      const fresh = await api.getAgentRun(kicked.id);
      setActiveRun(fresh);
      setQ("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not start run");
    } finally {
      setLoading(false);
    }
  }

  async function openRun(id: string) {
    try { setActiveRun(await api.getAgentRun(id)); }
    catch (e) { setErr(e instanceof Error ? e.message : "could not load run"); }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Ask the memory"
        sub={
          mode === "ask"
            ? "Grounded Q&A over ingested events. Cite-or-refuse — no guessing."
            : "Agent mode — Claude calls real tools to render reels, refresh analytics, approve items, and more. Every run is logged."
        }
      />

      {/* Mode toggle */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setMode("ask")}
          className={`text-[13px] px-3.5 py-1.5 rounded-full border transition-colors ${
            mode === "ask"
              ? "bg-primary text-primary-foreground border-primary"
              : "bg-background border-border hover:bg-muted"
          }`}
        >
          🔍 Ask · grounded Q&A
        </button>
        <button
          onClick={() => setMode("do")}
          className={`text-[13px] px-3.5 py-1.5 rounded-full border transition-colors ${
            mode === "do"
              ? "bg-primary text-primary-foreground border-primary"
              : "bg-background border-border hover:bg-muted"
          }`}
        >
          ⚡ Do · agent (initiates work)
        </button>
        {mode === "do" && tools.length > 0 && (
          <button
            onClick={() => setShowTools((v) => !v)}
            className="ml-auto text-[11px] text-muted-foreground hover:text-foreground"
          >
            {showTools ? "Hide" : `What can it do? (${tools.length} tools)`}
          </button>
        )}
      </div>

      {/* What-can-it-do panel (Do mode only) */}
      {mode === "do" && showTools && (
        <Card className="!p-3">
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-2">
            Tools the agent can call
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {tools.map((t) => (
              <div key={t.name} className="text-[12px] flex gap-2">
                <span title={t.writes ? "Mutates state / spends credits" : "Read-only"}>
                  {t.writes ? "✏️" : "👁"}
                </span>
                <div className="flex-1 min-w-0">
                  <span className="font-mono">{t.name}</span>
                  <span className="text-muted-foreground"> — {t.description}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="flex gap-3">
          <Input
            placeholder={
              mode === "ask"
                ? "e.g. What did Ryan Serhant say about pricing?"
                : "e.g. Refresh analytics for the brand accounts and tell me what changed"
            }
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            autoFocus
          />
          <Button onClick={ask} disabled={loading || !q.trim()}>
            {loading ? <Spinner /> : mode === "ask" ? "Ask" : "Run"}
          </Button>
        </div>

        {err && (
          <div className="mt-4 text-sm text-destructive border border-destructive/40 rounded-md p-3">
            ✗ {err}
          </div>
        )}

        {/* Ask-mode result (original Q&A panel) */}
        {mode === "ask" && res && (
          <div
            className={`mt-5 rounded-lg border p-4 bg-background ${
              res.refused ? "border-primary" : "border-border"
            }`}
          >
            {res.refused ? (
              <>
                <div className="text-primary font-semibold text-xs uppercase tracking-[.5px] mb-2">
                  ⚠ Refused — not grounded in memory
                </div>
                <p className="text-[15px] leading-relaxed">{res.refusal_reason}</p>
              </>
            ) : (
              <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{res.response}</p>
            )}
            {res.citations.length > 0 && (
              <div className="mt-4 flex flex-col gap-2">
                {res.citations.map((c, i) => (
                  <div key={i} className="bg-card border border-border rounded-md p-3 text-[13px]">
                    <div>&ldquo;{c.span}&rdquo;</div>
                    <div className="text-muted-foreground text-[11px] font-mono mt-1">
                      source {c.event_id.slice(0, 8)} · confidence {(c.confidence * 100).toFixed(0)}%
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-4 text-[12px] text-muted-foreground">
              <span>confidence <b className="text-foreground">{(res.confidence * 100).toFixed(0)}%</b></span>
              <span>retrieved <b className="text-foreground">{res.retrieved_event_ids.length}</b> events</span>
              <span>model <b className="text-foreground">{res.model}</b></span>
              <span>latency <b className="text-foreground">{res.latency_ms} ms</b></span>
            </div>
          </div>
        )}

        {/* Do-mode active run with live tool-call log */}
        {mode === "do" && activeRun && (
          <div className="mt-5 space-y-3">
            <div className="flex items-center gap-2">
              <Badge tone={
                activeRun.status === "running" ? "accent" :
                activeRun.status === "succeeded" ? "ok" :
                activeRun.status === "failed" ? "destructive" : "muted"
              }>
                {activeRun.status === "running" && <Spinner />}{" "}
                {activeRun.status}
              </Badge>
              <span className="text-[12px] text-muted-foreground">
                {activeRun.tool_calls.length} tool call{activeRun.tool_calls.length === 1 ? "" : "s"}
              </span>
            </div>

            {activeRun.tool_calls.map((c, i) => (
              <div
                key={i}
                className={`rounded-md border p-3 text-[12px] ${
                  c.ok ? "border-border" : "border-destructive/40 bg-destructive/5"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span>{c.ok ? "✓" : "✗"}</span>
                  <span className="font-mono text-[12px] font-medium">{c.name}</span>
                  <span className="text-muted-foreground text-[11px]">
                    {c.duration_ms}ms
                  </span>
                </div>
                {Object.keys(c.args || {}).length > 0 && (
                  <pre className="text-[11px] text-muted-foreground font-mono whitespace-pre-wrap break-words">
                    args: {JSON.stringify(c.args)}
                  </pre>
                )}
                <pre className="text-[11px] text-muted-foreground font-mono whitespace-pre-wrap break-words mt-1 max-h-48 overflow-y-auto">
                  {JSON.stringify(c.result, null, 2).slice(0, 1200)}
                </pre>
              </div>
            ))}

            {activeRun.status !== "running" && (
              <div className="rounded-md border border-primary/40 bg-primary/5 p-3">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
                  {activeRun.status === "succeeded" ? "Summary" : "Result"}
                </p>
                <p className="text-[14px] leading-relaxed whitespace-pre-wrap">
                  {activeRun.summary || activeRun.answer || activeRun.error || "(no summary)"}
                </p>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Do-mode run history */}
      {mode === "do" && runs.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-[13px] font-medium">Recent runs</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Every command you've sent. Click to expand.
            </p>
          </div>
          <ul className="divide-y divide-border">
            {runs.map((r) => (
              <li
                key={r.id}
                onClick={() => openRun(r.id)}
                className={`px-4 py-2 text-[12px] cursor-pointer hover:bg-muted/30 transition-colors ${
                  activeRun?.id === r.id ? "bg-muted/30" : ""
                }`}
              >
                <div className="flex items-center gap-2">
                  <Badge tone={
                    r.status === "running" ? "accent" :
                    r.status === "succeeded" ? "ok" :
                    r.status === "failed" ? "destructive" : "muted"
                  }>
                    {r.status}
                  </Badge>
                  <span className="text-muted-foreground text-[11px] whitespace-nowrap">
                    {fmtRelative(r.created_at)}
                  </span>
                  {r.tool_call_count > 0 && (
                    <span className="text-muted-foreground text-[11px]">
                      · {r.tool_call_count} calls
                    </span>
                  )}
                </div>
                <p className="mt-1 line-clamp-2 leading-snug" title={r.prompt}>
                  {r.prompt}
                </p>
                {r.summary && (
                  <p className="text-muted-foreground text-[11px] mt-1 line-clamp-2">
                    → {r.summary}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="text-[11px] text-muted-foreground flex items-center gap-2 flex-wrap">
        {mode === "ask" ? (
          <>
            <Badge tone="primary">cite-or-refuse</Badge>
            <span>+ independent verification pass before any answer.</span>
          </>
        ) : (
          <>
            <Badge tone="primary">tool-use</Badge>
            <span>
              The agent picks tools from a registry; every call is logged. ✏️ = mutates / spends credits. Read tool descriptions before approving destructive prompts.
            </span>
          </>
        )}
      </div>
    </div>
  );
}
