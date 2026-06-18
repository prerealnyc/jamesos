"use client";

import { useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type ContentDraft } from "@/lib/api";
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
  const [mode, setMode] = useState<"post" | "multi">("post");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Post — text + image"
        sub="One topic → an on-voice written post AND a matching image of James (from your hero library), composed together and queued as one item. Grounded in voice + thesis + research + the learned guardrails; voice-QA scores every draft; nothing ships without approval."
      />
      <div className="flex gap-2">
        <TabBtn active={mode === "post"} onClick={() => setMode("post")}>
          Post + image
        </TabBtn>
        <TabBtn active={mode === "multi"} onClick={() => setMode("multi")}>
          Multi-platform (text)
        </TabBtn>
      </div>
      {mode === "post" ? <PostImageMode /> : <MultiMode />}
    </div>
  );
}

// ── Post + image: one topic → on-voice post + a matching hero image ──

function PostImageMode() {
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [pillar, setPillar] = useState("");
  const [extra, setExtra] = useState("");
  const [includeImage, setIncludeImage] = useState(true);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<ContentDraft | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageErr, setImageErr] = useState<string | null>(null);
  const [err, setErr] = useState("");

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setDraft(null);
    setImageUrl(null);
    setImageErr(null);
    try {
      const r = await api.composePost({
        topic,
        platform,
        pillar,
        extra_instructions: extra,
        include_image: includeImage,
      });
      setDraft(r.draft);
      setImageUrl(r.image_url);
      setImageErr(r.image_error);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardTitle>One topic → post + matching image</CardTitle>
        <p className="text-[12px] text-muted-foreground -mt-1 mb-2">
          Writes the post in James&apos;s voice, then renders a cinematic image
          of James from your hero library to match it. Both land together in the
          Approval Queue for review.
        </p>
        <Label>Topic</Label>
        <Textarea
          rows={2}
          placeholder="what the post is about"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-4 mt-1">
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
            <Label>Pillar (optional)</Label>
            <Input value={pillar} onChange={(e) => setPillar(e.target.value)} placeholder="which brand pillar" />
          </div>
        </div>
        <Label>Extra instructions (optional)</Label>
        <Input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="a one-off steer" />
        <label className="flex items-center gap-2 mt-3 text-[13px] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeImage}
            onChange={(e) => setIncludeImage(e.target.checked)}
            className="accent-primary"
          />
          Generate a matching image (James, from your hero library)
        </label>
        <div className="mt-3">
          <Button onClick={run} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : includeImage ? "Generate post + image" : "Generate post"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
        {busy && (
          <p className="text-[12px] text-muted-foreground mt-2">
            {includeImage
              ? "Writing the post + voice-QA, then rendering the image — ~30–60s."
              : "Writing the post + voice-QA — ~15–30s."}
          </p>
        )}
      </Card>

      {draft && (
        <>
          <div className="text-[12px] text-muted-foreground flex items-center gap-2">
            {draft.action_id ? (
              <>
                <Badge tone="primary">queued for approval</Badge>
                <Link href="/queue" className="text-primary hover:underline">
                  Review in the queue →
                </Link>
              </>
            ) : (
              <Badge tone="accent">not queued</Badge>
            )}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <DraftCard draft={draft} />
            {includeImage && (
              <Card>
                <CardTitle>Matching image</CardTitle>
                {imageUrl ? (
                  <>
                    <a href={mediaUrl(imageUrl)} target="_blank" rel="noopener noreferrer">
                      <img
                        src={mediaUrl(imageUrl)}
                        alt="post image"
                        className="w-full rounded-md border border-border mt-2 bg-background"
                      />
                    </a>
                    <p className="text-[11px] text-muted-foreground mt-2">
                      Attached to the queued post. Referenced from your hero
                      library so James stays consistent across posts.
                    </p>
                  </>
                ) : (
                  <p className="text-[12px] text-muted-foreground mt-2">
                    {imageErr || "No image returned."}
                  </p>
                )}
              </Card>
            )}
          </div>
        </>
      )}
    </>
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
