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
import { api, type BrandProfile } from "@/lib/api";
import { Button, Card, CardTitle, Spinner, PageHeader } from "@/components/ui";

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
    </div>
  );
}
