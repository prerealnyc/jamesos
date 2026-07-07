"use client";

/**
 * Morning Brief — the operator's daily page (adopted from the vision PRD).
 * One screen: this week's Prescription (the strategy engine's quantified,
 * evidence-backed plan), what changed in the platform playbooks, today's
 * suggestions, the interview status, and the queue snapshot.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type StrategyState } from "@/lib/api";
import { Button, Card, CardTitle, Badge, Spinner, PageHeader } from "@/components/ui";

export default function BriefPage() {
  const [state, setState] = useState<StrategyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    try {
      setState(await api.strategyState());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function prescribe() {
    setBusy("prescribe");
    setErr(null);
    setNote("Composing the prescription (1-2 min)…");
    try {
      await api.prescribeNow();
      for (let i = 0; i < 18; i++) {
        await new Promise((r) => setTimeout(r, 10000));
        const s = await api.strategyState();
        setState(s);
        if (s.prescription && s.prescription.status === "proposed") break;
      }
      setNote(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "prescribe failed");
      setNote(null);
    } finally {
      setBusy(null);
    }
  }

  async function refreshInputs() {
    setBusy("inputs");
    setErr(null);
    setNote("Refreshing playbooks + peer benchmarks (2-5 min)…");
    try {
      await api.refreshStrategyInputs();
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 10000));
        await load();
      }
      setNote(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "refresh failed");
      setNote(null);
    } finally {
      setBusy(null);
    }
  }

  async function accept(items?: number[]) {
    if (!state?.prescription) return;
    setBusy("accept");
    setErr(null);
    try {
      const r = await api.acceptPrescription(state.prescription.id, items);
      setNote(`✓ ${r.produced} piece(s) heading to the Approval Queue${r.capped ? " (capped this run — accept again for more)" : ""}`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "accept failed");
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>;
  }

  const p = state?.prescription;
  const changedBooks = (state?.playbooks || []).filter((b) => b.changed);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Morning Brief"
        sub="What the brand manager thinks you should do — a quantified weekly plan with the evidence for every line, plus what changed on the platforms overnight."
      />

      <div className="flex gap-2 flex-wrap text-[13px]">
        <Badge tone="muted">{state?.queue_pending ?? 0} awaiting approval</Badge>
        <Badge tone="muted">{state?.suggestions.length ?? 0} suggestions today</Badge>
        {state?.interview && (
          <Badge tone="muted">{state.interview.answered} interview answers await you</Badge>
        )}
        {changedBooks.length > 0 && (
          <Badge tone="accent">⚠ {changedBooks.map((b) => b.platform).join(", ")} algorithm changed</Badge>
        )}
      </div>
      {err && <p className="text-[12px] text-destructive">✗ {err}</p>}
      {note && <p className="text-[12px] text-accent">{note}</p>}

      <Card className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <CardTitle>This week&apos;s prescription</CardTitle>
            {p && (
              <div className="text-[11px] text-muted-foreground mt-0.5">
                week of {p.week_of} · {p.status}
                {p.accepted_items.length > 0 && <> · {p.accepted_items.length} produced</>}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={refreshInputs} disabled={!!busy}
              className="text-[12px] !px-3 !py-1"
              title="Re-research platform algorithms + peer benchmarks (auto-runs weekly)">
              {busy === "inputs" ? <Spinner /> : "↻ Refresh inputs"}
            </Button>
            <Button onClick={prescribe} disabled={!!busy}
              className="text-[12px] !px-3 !py-1"
              title="Compose a fresh prescription now (auto-runs weekly)">
              {busy === "prescribe" ? <Spinner /> : "🧠 Prescribe now"}
            </Button>
          </div>
        </div>

        {!p ? (
          <p className="text-[12px] text-muted-foreground">
            No prescription yet. Hit &ldquo;↻ Refresh inputs&rdquo; once (playbooks + peers),
            then &ldquo;🧠 Prescribe now&rdquo; — or wait for Monday&apos;s automatic run.
            Requires a completed <Link href="/intake" className="text-primary hover:underline">Brand Setup</Link>.
          </p>
        ) : (
          <>
            <div className="flex flex-col divide-y divide-border">
              {p.plan.map((ln, i) => (
                <div key={i} className="py-2.5 flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium">
                      {ln.per_week}× {ln.format}/week · {ln.platform}
                    </div>
                    {ln.topics.length > 0 && (
                      <div className="text-[12px] text-muted-foreground">
                        Topics: {ln.topics.join(" · ")}
                      </div>
                    )}
                    {ln.why && <div className="text-[12px]">{ln.why}</div>}
                    <div className="text-[11px] text-muted-foreground mt-0.5">
                      {ln.evidence.map((e, j) => <span key={j}>[{e}] </span>)}
                    </div>
                  </div>
                  {p.status === "proposed" && (
                    <Button variant="secondary" onClick={() => accept([i])}
                      disabled={!!busy} className="text-[12px] !px-3 !py-1">
                      Accept line
                    </Button>
                  )}
                </div>
              ))}
            </div>
            {p.growth_actions.length > 0 && (
              <div>
                <div className="text-[12px] font-medium mb-1">Growth moves</div>
                <ul className="flex flex-col gap-1.5">
                  {p.growth_actions.map((g, i) => (
                    <li key={i} className="text-[13px]">
                      → {g.action}
                      {g.why && <span className="text-muted-foreground"> — {g.why}</span>}
                      <span className="text-[11px] text-muted-foreground">
                        {" "}{g.evidence.map((e, j) => <span key={j}>[{e}] </span>)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {p.status === "proposed" && (
              <div className="flex items-center gap-3">
                <Button onClick={() => accept()} disabled={!!busy}>
                  {busy === "accept" ? <Spinner /> : "✓ Accept the plan → fill my queue"}
                </Button>
                <span className="text-[11px] text-muted-foreground">
                  Everything lands in the Approval Queue — nothing publishes itself.
                </span>
              </div>
            )}
            {p.status !== "proposed" && (
              <Link href="/queue" className="text-[12px] text-primary hover:underline">
                Review the produced pieces in the Approval Queue ↗
              </Link>
            )}
          </>
        )}
      </Card>

      {(state?.playbooks.length ?? 0) > 0 && (
        <Card className="flex flex-col gap-2">
          <CardTitle>Platform playbooks</CardTitle>
          {state!.playbooks.map((b) => (
            <details key={b.platform} className="border border-border rounded-md p-3">
              <summary className="text-[13px] font-medium cursor-pointer">
                {b.platform} · v{b.version}
                {b.changed && <span className="text-accent"> · ⚠ changed</span>}
                <span className="text-[11px] text-muted-foreground">
                  {" "}· refreshed {new Date(b.refreshed_at).toLocaleDateString()}
                </span>
              </summary>
              <ul className="mt-2 text-[12px] flex flex-col gap-1">
                {b.key_points.map((k, i) => <li key={i}>• {k}</li>)}
              </ul>
            </details>
          ))}
        </Card>
      )}
    </div>
  );
}
