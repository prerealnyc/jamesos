"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type ContentDraft } from "@/lib/api";
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

const MULTI_PLATFORMS = ["linkedin", "facebook", "instagram", "x", "tiktok"];
const PLATFORMS = ["instagram", "linkedin", "x", "youtube", "tiktok", "threads"];
const FORMATS = ["post", "caption", "reel_script", "thread", "short_hook"];

function scoreTone(s: number): "ok" | "accent" | "destructive" {
  if (s >= 0.7) return "ok";
  if (s >= 0.4) return "accent";
  return "destructive";
}

/** Split a carousel draft ("Slide 1: ... Slide 2: ...") into slides. */
function parseSlides(draft: string): string[] | null {
  if (!/slide\s*1\s*[:.\-]/i.test(draft)) return null;
  const parts = draft
    .split(/\n?\s*slide\s*\d+\s*[:.\-]\s*/i)
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length > 1 ? parts : null;
}

export default function ContentStudio() {
  const [mode, setMode] = useState<"multi" | "single">("multi");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Content Studio"
        sub="Turn one idea into on-voice content. Grounded in voice + thesis + research + the learned guardrails; an independent voice-QA scores every draft; nothing ships without approval."
      />
      <div className="flex gap-2">
        <TabBtn active={mode === "multi"} onClick={() => setMode("multi")}>
          Multi-platform
        </TabBtn>
        <TabBtn active={mode === "single"} onClick={() => setMode("single")}>
          Single draft
        </TabBtn>
      </div>
      {mode === "multi" ? <MultiMode /> : <SingleMode />}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
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

// ── Multi-platform: one idea → posts per platform + a carousel ──

function MultiMode() {
  const [topic, setTopic] = useState("");
  const [pillar, setPillar] = useState("");
  const [platforms, setPlatforms] = useState<string[]>(["linkedin", "facebook", "instagram"]);
  const [carousel, setCarousel] = useState(true);
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<ContentDraft[] | null>(null);
  const [queued, setQueued] = useState(0);
  const [err, setErr] = useState("");

  function toggle(p: string) {
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setDrafts(null);
    try {
      const r = await api.generateMulti({
        topic,
        pillar,
        platforms,
        carousel,
        extra_instructions: extra,
      });
      setDrafts(r.drafts);
      setQueued(r.queued);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardTitle>One idea → many channels</CardTitle>
        <Label>Topic</Label>
        <Textarea
          rows={2}
          placeholder="what the content is about"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <Label>Pillar (optional)</Label>
        <Input value={pillar} onChange={(e) => setPillar(e.target.value)} placeholder="which brand pillar" />
        <Label>Channels</Label>
        <div className="flex flex-wrap gap-2">
          {MULTI_PLATFORMS.map((p) => (
            <button
              key={p}
              onClick={() => toggle(p)}
              className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
                platforms.includes(p)
                  ? "border-primary text-foreground bg-primary/10"
                  : "border-border text-muted-foreground"
              }`}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => setCarousel((c) => !c)}
            className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
              carousel ? "border-accent text-foreground bg-accent/10" : "border-border text-muted-foreground"
            }`}
          >
            + carousel
          </button>
        </div>
        <Label>Extra instructions (optional)</Label>
        <Input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="a one-off steer" />
        <div className="mt-3">
          <Button onClick={run} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : `Generate ${platforms.length + (carousel ? 1 : 0)} pieces`}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
        {busy && (
          <p className="text-[12px] text-muted-foreground mt-2">
            Generating + voice-QA on each in parallel — this takes ~30–60s.
          </p>
        )}
      </Card>

      {drafts && (
        <>
          <div className="text-[12px] text-muted-foreground flex items-center gap-2">
            <Badge tone="primary">{queued} queued for approval</Badge>
            <Link href="/queue" className="text-primary hover:underline">
              Review in the queue →
            </Link>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {drafts.map((d, i) => (
              <DraftCard key={i} draft={d} />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function DraftCard({ draft }: { draft: ContentDraft }) {
  const [copied, setCopied] = useState(false);
  const slides = draft.format === "carousel" ? parseSlides(draft.draft) : null;

  function copy() {
    navigator.clipboard.writeText(draft.draft);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  function download() {
    const blob = new Blob([draft.draft], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${draft.platform}-${draft.format}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="border border-border rounded-lg p-4 bg-card flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone="primary">{draft.platform}</Badge>
          <span className="text-[12px] text-muted-foreground">{draft.format}</span>
        </div>
        <Badge tone={scoreTone(draft.voice_score)}>voice {draft.voice_score.toFixed(2)}</Badge>
      </div>

      {draft.status === "not_generated" ? (
        <p className="text-[12px] text-muted-foreground">{draft.note}</p>
      ) : slides ? (
        <div className="flex flex-col gap-2">
          {slides.map((s, i) => (
            <div key={i} className="bg-background border border-border rounded-md p-2 text-[13px]">
              <span className="text-[10px] uppercase tracking-[.4px] text-muted-foreground">
                Slide {i + 1}
              </span>
              <div>{s}</div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap bg-background border border-border rounded-md p-3">
          {draft.draft}
        </p>
      )}

      {draft.qa && draft.qa.drift.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[.4px]">voice-QA: </span>
          {draft.qa.drift.join(" · ")}
        </div>
      )}

      {draft.draft && (
        <div className="flex items-center gap-3 text-[12px] pt-1">
          <button onClick={copy} className="text-primary hover:underline">
            {copied ? "copied!" : "copy"}
          </button>
          <button onClick={download} className="text-muted-foreground hover:text-foreground">
            export .txt
          </button>
          {draft.action_id && <span className="ml-auto text-muted-foreground">queued ✓</span>}
        </div>
      )}
    </div>
  );
}

// ── Single draft (original) ──

function SingleMode() {
  const [platform, setPlatform] = useState("instagram");
  const [format, setFormat] = useState("post");
  const [pillar, setPillar] = useState("");
  const [topic, setTopic] = useState("");
  const [research, setResearch] = useState("");
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<ContentDraft | null>(null);
  const [err, setErr] = useState("");

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setOut(null);
    try {
      setOut(
        await api.generate({
          platform,
          format,
          pillar,
          topic,
          research_subject: research,
          extra_instructions: extra,
        })
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardTitle>Brief</CardTitle>
        <div className="grid grid-cols-2 gap-4 mt-2">
          <div>
            <Label>Platform</Label>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Format</Label>
            <Select value={format} onChange={(e) => setFormat(e.target.value)}>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <Label>Pillar (optional)</Label>
        <Input value={pillar} onChange={(e) => setPillar(e.target.value)} />
        <Label>Topic</Label>
        <Textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)} />
        <Label>Ground facts in research on (optional)</Label>
        <Input value={research} onChange={(e) => setResearch(e.target.value)} />
        <Label>Extra instructions (optional)</Label>
        <Input value={extra} onChange={(e) => setExtra(e.target.value)} />
        <div className="mt-3">
          <Button onClick={run} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : "Generate draft"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      {out && <DraftCard draft={out} />}
    </>
  );
}
