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

const PLATFORMS = ["instagram", "linkedin", "x", "youtube", "tiktok", "threads"];
const FORMATS = ["post", "caption", "reel_script", "thread", "short_hook"];

function scoreTone(s: number): "ok" | "accent" | "destructive" {
  if (s >= 0.7) return "ok";
  if (s >= 0.4) return "accent";
  return "destructive";
}

export default function ContentStudio() {
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
      const d = await api.generate({
        platform,
        format,
        pillar,
        topic,
        research_subject: research,
        extra_instructions: extra,
      });
      setOut(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Content Studio"
        sub="Generate on-voice drafts from the memory substrate. Voice + thesis + research + the frustration ledger feed it; an independent voice-QA pass scores it; nothing ships without a human approving it in the queue."
      />

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
        <Input
          placeholder="which brand pillar this serves"
          value={pillar}
          onChange={(e) => setPillar(e.target.value)}
        />
        <Label>Topic</Label>
        <Textarea
          rows={2}
          placeholder="what the piece is about"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <Label>Ground facts in research on (optional)</Label>
        <Input
          placeholder="a person/company already researched into memory"
          value={research}
          onChange={(e) => setResearch(e.target.value)}
        />
        <Label>Extra instructions (optional)</Label>
        <Input
          placeholder="a one-off steer for this draft"
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
        />
        <div className="mt-3">
          <Button onClick={run} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : "Generate draft"}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      {out && out.status === "not_generated" && (
        <Card>
          <CardTitle>Not generated — honestly refused</CardTitle>
          <p className="text-muted-foreground text-sm mt-1">{out.note}</p>
          <p className="text-[12px] text-muted-foreground mt-2">
            Memory available:{" "}
            {Object.entries(out.memory_used)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ")}
          </p>
        </Card>
      )}

      {out && out.status !== "not_generated" && (
        <Card>
          <div className="flex items-center justify-between gap-3">
            <CardTitle>Draft</CardTitle>
            <div className="flex items-center gap-2">
              <Badge tone={scoreTone(out.voice_score)}>
                voice {out.voice_score.toFixed(2)}
              </Badge>
              <Badge tone={out.status === "generated" ? "ok" : "accent"}>
                {out.status === "generated" ? "passed QA" : "flagged"}
              </Badge>
            </div>
          </div>

          <div className="mt-3 whitespace-pre-wrap text-sm bg-background border border-border rounded-md p-4">
            {out.draft}
          </div>

          {out.angle && (
            <p className="text-[13px] text-muted-foreground mt-3">
              <b className="text-foreground">Angle:</b> {out.angle}
            </p>
          )}

          {out.qa && out.qa.drift.length > 0 && (
            <div className="mt-3">
              <Label>Voice-QA drift notes</Label>
              <ul className="text-[13px] text-muted-foreground list-disc pl-5">
                {out.qa.drift.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3 text-[12px] text-muted-foreground">
            <span>
              grounded in {out.grounded_event_ids.length} cited event
              {out.grounded_event_ids.length === 1 ? "" : "s"}
            </span>
            <span>·</span>
            <span>
              memory:{" "}
              {Object.entries(out.memory_used)
                .map(([k, v]) => `${k} ${v}`)
                .join(" / ")}
            </span>
            <span>·</span>
            <span>{out.model}</span>
            <span>·</span>
            <span>{out.latency_ms}ms</span>
          </div>

          {out.note && (
            <p className="text-[12px] text-accent mt-2">{out.note}</p>
          )}

          {out.action_id && (
            <div className="mt-4 flex items-center gap-3">
              <Badge tone="primary">queued · pending approval</Badge>
              <Link
                href="/queue"
                className="text-sm text-primary hover:underline"
              >
                Review in the approval queue →
              </Link>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
