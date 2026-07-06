"use client";

/**
 * Brand Setup (the Intake) — James: "an intake form for whatever asset it
 * is or whatever personality it is… where we can walk them through this
 * intake and it knows: what are the goals, who am I, what am I doing."
 *
 * The output is the brand_profile that makes every engine identity-aware:
 * the voice engine, ideation, Ask, and the daily research job (which turns
 * ON when the intake completes — the brand manager starts doing homework
 * the next morning).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  api, type BrandProfile, type BrandQuestion, type BrandResearchProposal,
  type InterviewStats,
} from "@/lib/api";
import { Button, Card, CardTitle, Badge, Spinner, PageHeader } from "@/components/ui";

const KINDS = [
  ["person", "A person / influencer"],
  ["asset", "A physical asset (golf course, hotel, spaceport…)"],
  ["institution", "An institution / company"],
  ["politician", "A public official / campaign"],
] as const;

function ListField({
  label, hint, values, onChange, placeholder,
}: {
  label: string; hint?: string; values: string[];
  onChange: (v: string[]) => void; placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const v = draft.trim();
    if (!v) return;
    onChange([...values, v]);
    setDraft("");
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="text-[12px] font-medium">{label}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
      <div className="flex gap-2 flex-wrap">
        {values.map((v, i) => (
          <span key={i} className="inline-flex items-center gap-1 text-[12px] bg-secondary rounded-full px-2.5 py-1">
            {v}
            <button
              onClick={() => onChange(values.filter((_, j) => j !== i))}
              className="text-muted-foreground hover:text-destructive"
            >×</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          placeholder={placeholder}
          className="flex-1 text-[13px] px-3 py-1.5 rounded-md border border-border bg-background"
        />
        <Button variant="secondary" onClick={add} className="text-[12px] !px-3 !py-1">+ Add</Button>
      </div>
    </div>
  );
}

export default function IntakePage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [kind, setKind] = useState<BrandProfile["kind"]>("person");
  const [name, setName] = useState("");
  const [mission, setMission] = useState("");
  const [positioning, setPositioning] = useState("");
  const [audience, setAudience] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [pillars, setPillars] = useState<string[]>([]);
  const [taboos, setTaboos] = useState<string[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [peers, setPeers] = useState<string[]>([]);
  const [intakeDone, setIntakeDone] = useState(false);

  useEffect(() => {
    api.getBrandProfile().then((p) => {
      if (p && p.identity) {
        setKind(p.kind || "person");
        setName(p.identity.name || "");
        setMission(p.identity.mission || "");
        setPositioning(p.identity.positioning || "");
        setAudience(p.identity.audience || "");
        setGoals(p.goals || []);
        setPillars(p.pillars || []);
        setTaboos(p.taboos || []);
        setPlatforms(p.platforms || []);
        setPeers(p.peers || []);
        setIntakeDone(!!p.intake_done);
      }
    }).catch(() => { /* fresh brand */ }).finally(() => setLoading(false));
  }, []);

  async function save(complete: boolean) {
    setSaving(true);
    setErr(null);
    try {
      await api.saveBrandProfile({
        kind,
        identity: { name, mission, positioning, audience },
        goals, pillars, taboos, platforms, peers,
        ...(complete ? { intake_done: true } : {}),
      });
      if (complete) setIntakeDone(true);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>;
  }

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <PageHeader
        title="Brand Setup"
        sub="Teach the brand manager who this brand is. Everything here steers every engine — the voice, the ideas, the research, the strategy. Complete it once; refine it any time."
      />

      {intakeDone && (
        <Card>
          <p className="text-[13px]">
            ✓ Intake complete — the daily research job is ON. Fresh content
            suggestions appear every morning on{" "}
            <Link href="/create" className="text-primary hover:underline">Create</Link>.
            Edits here take effect on the next generation.
          </p>
        </Card>
      )}

      {/* ── "Just type your name — we'll do the rest" ── */}
      {!intakeDone && (
        <ResearchMyBrand onAccept={(p) => {
          setKind(p.kind);
          setName(p.identity.name || "");
          setMission(p.identity.mission || "");
          setPositioning(p.identity.positioning || "");
          setAudience(p.identity.audience || "");
          if (p.goals.length) setGoals(p.goals);
          if (p.pillars.length) setPillars(p.pillars);
          if (p.peers.length) setPeers(p.peers);
        }} />
      )}

      <Card className="flex flex-col gap-4">
        <CardTitle>1 · Who is this brand?</CardTitle>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] font-medium">Type</div>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as BrandProfile["kind"])}
            className="text-[13px] px-3 py-2 rounded-md border border-border bg-background"
          >
            {KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] font-medium">Name</div>
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Spaceport America"
            className="text-[13px] px-3 py-2 rounded-md border border-border bg-background" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] font-medium">Mission — what is this brand trying to do?</div>
          <textarea value={mission} onChange={(e) => setMission(e.target.value)} rows={2}
            placeholder="e.g. Position Spaceport America as THE place to do business for space exploration — private, commercial, and defense."
            className="text-[13px] px-3 py-2 rounded-md border border-border bg-background resize-y" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] font-medium">Positioning — the field it must win in</div>
          <input value={positioning} onChange={(e) => setPositioning(e.target.value)}
            placeholder="e.g. commercial space exploration in New Mexico"
            className="text-[13px] px-3 py-2 rounded-md border border-border bg-background" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] font-medium">Audience</div>
          <input value={audience} onChange={(e) => setAudience(e.target.value)}
            placeholder="e.g. elected officials, aerospace companies, the New Mexico public"
            className="text-[13px] px-3 py-2 rounded-md border border-border bg-background" />
        </div>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>2 · Goals & topics</CardTitle>
        <ListField label="Goals (ranked)" hint="Measurable where possible — these are what the strategy optimizes for."
          values={goals} onChange={setGoals}
          placeholder="e.g. Appear in top searches for space exploration business" />
        <ListField label="Topic pillars" hint="What this brand talks about — the content mix targets."
          values={pillars} onChange={setPillars}
          placeholder="e.g. economic benefit for New Mexico" />
        <ListField label="Never touch" hint="Taboo topics and angles — hard rules."
          values={taboos} onChange={setTaboos}
          placeholder="e.g. partisan politics" />
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>3 · Where & against whom</CardTitle>
        <ListField label="Platforms" values={platforms} onChange={setPlatforms}
          placeholder="e.g. instagram" />
        <ListField label="Peer set" hint="Accounts at the level this brand wants to reach — the benchmark."
          values={peers} onChange={setPeers}
          placeholder="e.g. @nasa" />
      </Card>

      <Card>
        <CardTitle>4 · Feed it everything</CardTitle>
        <p className="text-[13px] text-muted-foreground mt-1">
          Drop the brand&apos;s footage, documents, press, and guidelines into the{" "}
          <Link href="/knowledge" className="text-primary hover:underline">Knowledge Base</Link>{" "}
          and its photos/videos into{" "}
          <Link href="/jp-clips" className="text-primary hover:underline">Assets</Link>.
          Everything is transcribed, filed, and becomes the memory this brand
          manager thinks with.
        </p>
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={() => save(true)} disabled={saving || !name.trim() || !mission.trim()}>
          {saving ? <Spinner /> : intakeDone ? "Save changes" : "Complete intake → start the brand manager"}
        </Button>
        {!intakeDone && (
          <Button variant="secondary" onClick={() => save(false)} disabled={saving}>
            Save draft
          </Button>
        )}
        {saved && <span className="text-[12px] text-accent">✓ saved</span>}
        {err && <span className="text-[12px] text-destructive">✗ {err}</span>}
      </div>

      {intakeDone && <DeepInterview />}
    </div>
  );
}

/** The Researcher agent's front door: name in → "is this your brand?" out. */
function ResearchMyBrand({
  onAccept,
}: { onAccept: (p: BrandResearchProposal["proposal"]) => void }) {
  const [name, setName] = useState("");
  const [hints, setHints] = useState("");
  const [busy, setBusy] = useState(false);
  const [prop, setProp] = useState<BrandResearchProposal | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);

  async function research() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setErr(null);
    setProp(null);
    try {
      setProp(await api.researchBrand(name.trim(), hints.trim()));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "research failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      <CardTitle>Skip the typing — let the brand manager research you</CardTitle>
      <p className="text-[12px] text-muted-foreground">
        Type the brand&apos;s name (a website or handle helps) and the Researcher
        agent pulls what&apos;s online into a draft profile. You just confirm.
      </p>
      <div className="flex gap-2 flex-wrap">
        <input value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") research(); }}
          placeholder="Brand name — e.g. Spaceport America"
          className="flex-1 min-w-[220px] text-[13px] px-3 py-2 rounded-md border border-border bg-background" />
        <input value={hints} onChange={(e) => setHints(e.target.value)}
          placeholder="Optional: website / @handle / city"
          className="flex-1 min-w-[180px] text-[13px] px-3 py-2 rounded-md border border-border bg-background" />
        <Button onClick={research} disabled={busy || !name.trim()}>
          {busy ? <span className="inline-flex items-center gap-1"><Spinner /> researching…</span> : "🔍 Research my brand"}
        </Button>
      </div>
      {err && <p className="text-[12px] text-destructive">✗ {err}</p>}
      {prop && (
        <div className="border border-border rounded-md p-3 flex flex-col gap-2">
          <div className="text-[13px] font-semibold">Is this your brand?</div>
          {prop.summary && (
            <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{prop.summary}</p>
          )}
          <div className="text-[12px] text-muted-foreground">
            {prop.proposal.identity.mission && <>Mission: {prop.proposal.identity.mission}<br /></>}
            {prop.proposal.pillars.length > 0 && <>Topics: {prop.proposal.pillars.join(" · ")}<br /></>}
            {prop.proposal.peers.length > 0 && <>Peers: {prop.proposal.peers.join(", ")}</>}
          </div>
          {prop.sources.length > 0 && (
            <div className="text-[11px] text-muted-foreground">
              Sources: {prop.sources.slice(0, 4).map((s, i) => (
                <a key={i} href={s} target="_blank" rel="noreferrer" className="text-primary hover:underline mr-2">
                  [{i + 1}]
                </a>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button onClick={() => { onAccept(prop.proposal); setAccepted(true); }} disabled={accepted}>
              {accepted ? "✓ Filled in below — review & complete" : "Yes — fill the form for me"}
            </Button>
            <span className="text-[11px] text-muted-foreground">
              Everything stays editable below before anything is saved.
            </span>
          </div>
        </div>
      )}
    </Card>
  );
}

/** The 10,000-question interview: Interviewer asks, Researcher answers,
 *  the human confirms — every confirmation becomes brand memory. */
function DeepInterview() {
  const [questions, setQuestions] = useState<BrandQuestion[]>([]);
  const [stats, setStats] = useState<InterviewStats | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.listIntakeQuestions();
      setQuestions(r.questions.filter((q) => q.status === "answered" || q.status === "open"));
      setStats(r.stats);
    } catch { /* table empty until first run */ }
  }
  useEffect(() => { load(); }, []);

  async function runNow() {
    setRunning(true);
    setErr(null);
    try {
      await api.runInterview();
      for (let i = 0; i < 18; i++) {
        await new Promise((r) => setTimeout(r, 10000));
        await load();
      }
    } finally {
      setRunning(false);
    }
  }

  async function confirm(q: BrandQuestion, answer?: string) {
    setBusy(q.id);
    setErr(null);
    try {
      await api.confirmIntakeQuestion(q.id, answer);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy((p) => (p === q.id ? null : p));
    }
  }

  async function dismiss(id: string) {
    setBusy(id);
    try {
      await api.dismissIntakeQuestion(id);
      setQuestions((prev) => prev.filter((q) => q.id !== id));
    } finally {
      setBusy((p) => (p === id ? null : p));
    }
  }

  const answered = questions.filter((q) => q.status === "answered");
  const open = questions.filter((q) => q.status === "open").slice(0, 6);

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <CardTitle>The deep interview</CardTitle>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            Two agents build your brand&apos;s intelligence: one asks the questions
            a great brand manager would ask, one researches the answers. You
            just confirm — every confirmation becomes permanent brand memory.
          </div>
        </div>
        <div className="flex items-center gap-2">
          {stats && (
            <Badge tone="primary">
              {stats.confirmed} confirmed · {stats.answered} awaiting you · {stats.open} researching
            </Badge>
          )}
          <Button variant="secondary" onClick={runNow} disabled={running}
            className="text-[12px] !px-3 !py-1"
            title="Generate the next batch of questions and research answers now (also runs automatically every 12h)">
            {running ? <span className="inline-flex items-center gap-1"><Spinner /> agents working…</span> : "▶ Run agents now"}
          </Button>
        </div>
      </div>
      {err && <p className="text-[12px] text-destructive">✗ {err}</p>}

      {answered.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="text-[12px] font-medium">The Researcher answered these — confirm or correct:</div>
          {answered.slice(0, 5).map((q) => (
            <div key={q.id} className="border border-border rounded-md p-3 flex flex-col gap-1.5">
              <div className="text-[12px] text-muted-foreground">[{q.dimension}] {q.question}</div>
              <div className="text-[13px] whitespace-pre-wrap">{q.answer}</div>
              <div className="flex items-center gap-2 flex-wrap">
                <Button onClick={() => confirm(q)} disabled={busy === q.id} className="text-[12px] !px-3 !py-1">
                  {busy === q.id ? <Spinner /> : "✓ Correct"}
                </Button>
                <input
                  value={drafts[q.id] ?? ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                  placeholder="…or type the correction and press Enter"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (drafts[q.id] || "").trim()) confirm(q, drafts[q.id].trim());
                  }}
                  className="flex-1 min-w-[220px] text-[12px] px-3 py-1.5 rounded-md border border-border bg-background"
                />
                <button onClick={() => dismiss(q.id)} disabled={busy === q.id}
                  className="text-[12px] text-muted-foreground hover:text-destructive">✕</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {open.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="text-[12px] font-medium">Only you can answer these:</div>
          {open.map((q) => (
            <div key={q.id} className="flex items-start gap-2 py-1">
              <div className="flex-1 min-w-0">
                <div className="text-[12px] text-muted-foreground">[{q.dimension}]</div>
                <div className="text-[13px]">{q.question}</div>
                <input
                  value={drafts[q.id] ?? ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                  placeholder="Answer in a sentence or two — Enter to save"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (drafts[q.id] || "").trim()) confirm(q, drafts[q.id].trim());
                  }}
                  className="w-full mt-1 text-[12px] px-3 py-1.5 rounded-md border border-border bg-background"
                />
              </div>
              <button onClick={() => dismiss(q.id)} disabled={busy === q.id}
                className="text-[12px] text-muted-foreground hover:text-destructive mt-5">✕</button>
            </div>
          ))}
        </div>
      )}

      {answered.length === 0 && open.length === 0 && (
        <p className="text-[12px] text-muted-foreground">
          No questions yet — hit &ldquo;Run agents now&rdquo; and the interview begins.
          It also runs automatically every 12 hours.
        </p>
      )}
    </Card>
  );
}
