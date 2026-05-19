"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type VideoJob } from "@/lib/api";
import {
  Button,
  Card,
  CardTitle,
  Input,
  Textarea,
  Label,
  Badge,
  Spinner,
  PageHeader,
} from "@/components/ui";

function statusTone(s: VideoJob["status"]): "muted" | "accent" | "ok" | "destructive" {
  if (s === "succeeded") return "ok";
  if (s === "failed") return "destructive";
  if (s === "processing" || s === "submitted") return "accent";
  return "muted";
}

function isInFlight(j: VideoJob) {
  return j.status === "submitted" || j.status === "processing" || j.status === "queued";
}

export default function VideoStudio() {
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [jobs, setJobs] = useState<VideoJob[]>([]);

  async function loadJobs() {
    try {
      setJobs(await api.listVideoJobs());
    } catch {}
  }
  useEffect(() => {
    loadJobs();
  }, []);

  // Live polling: refresh every in-flight job every 5s so the user sees
  // a job move queued → submitted → processing → succeeded without F5.
  useEffect(() => {
    const inflight = jobs.filter(isInFlight);
    if (inflight.length === 0) return;
    const t = setInterval(async () => {
      const updated = await Promise.all(
        inflight.map((j) => api.getVideoJob(j.id).catch(() => j))
      );
      setJobs((cur) =>
        cur.map((j) => updated.find((u) => u.id === j.id) ?? j)
      );
    }, 5000);
    return () => clearInterval(t);
  }, [jobs]);

  async function submit() {
    if (!prompt.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const j = await api.generateVideo(prompt.trim(), image.trim());
      setJobs((cur) => [j, ...cur.filter((x) => x.id !== j.id)]);
      setPrompt("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "submit failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Video Studio"
        sub="Generative clips via Runway (real API; stub by default so the durable pipeline is provable without burning credits). Jobs persist, poll live, and land in the approval queue on success. Higgsfield / Descript / MiniMax are intentionally not faked here."
      />

      <Card>
        <CardTitle>New render</CardTitle>
        <Label>Prompt</Label>
        <Textarea
          rows={3}
          placeholder="describe the clip — motion, framing, mood"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <Label>Still image URL (required by Runway&apos;s dev API)</Label>
        <Input
          placeholder="https://… (Runway is image-conditioned; with VIDEO_PROVIDER=stub this is ignored)"
          value={image}
          onChange={(e) => setImage(e.target.value)}
        />
        <div className="mt-3 flex items-center gap-3">
          <Button onClick={submit} disabled={busy || !prompt.trim()}>
            {busy ? <Spinner /> : "Submit render"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Submits → row persists in <b className="text-foreground">video_jobs</b> →
            poll → on success a pending video lands in the approval queue.
          </span>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      <Card>
        <CardTitle>Jobs</CardTitle>
        {jobs.length === 0 && (
          <p className="text-muted-foreground text-sm mt-2">No renders yet.</p>
        )}
        <div className="mt-3 flex flex-col gap-2">
          {jobs.map((j) => {
            const isStub = (j.result_url || "").startsWith("stub://");
            return (
              <div
                key={j.id}
                className="bg-background border border-border rounded-md p-3"
              >
                <div className="flex items-center gap-2 mb-1">
                  <Badge tone={statusTone(j.status)}>{j.status}</Badge>
                  <span className="text-[11px] font-mono text-muted-foreground">
                    {j.provider}
                  </span>
                  {isStub && <Badge tone="muted">stub — no real render</Badge>}
                  <span className="text-[11px] text-muted-foreground ml-auto">
                    {new Date(j.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="text-sm whitespace-pre-wrap">{j.prompt}</div>
                {j.result_url && !isStub && (
                  <a
                    href={j.result_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm text-primary hover:underline mt-2 inline-block"
                  >
                    Open result →
                  </a>
                )}
                {j.error && (
                  <p className="text-destructive text-[13px] mt-2">✗ {j.error}</p>
                )}
                {j.queued_action_id && (
                  <div className="mt-2">
                    <Link
                      href="/queue"
                      className="text-[13px] text-primary hover:underline"
                    >
                      Review in approval queue →
                    </Link>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
