"use client";

/**
 * Pull the music bed out from under a voice, and keep it.
 *
 * Recovering a bed from a finished reel is source separation, not filtering —
 * so this runs Demucs on the backend, which is an OPTIONAL dependency. The
 * panel asks whether the deployment can do it at all before offering the
 * button, so a missing install reads as a plain explanation instead of a job
 * that fails halfway through.
 *
 * The extracted bed lands in the Audio Library as a normal music asset with a
 * mood tag, which is what makes it REUSABLE: every render asking for that mood
 * can then use it, and a template can pin it outright.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type MusicCapability, type MusicExtractJob } from "@/lib/api";
import { Button, Card, CardTitle, Input, Label, Select, Spinner } from "@/components/ui";

const MOODS = ["upbeat", "calm", "dramatic", "tension"] as const;

export function MusicExtractor({ onExtracted }: { onExtracted: () => void }) {
  const [cap, setCap] = useState<MusicCapability | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mood, setMood] = useState<string>("dramatic");
  const [title, setTitle] = useState("");
  const [job, setJob] = useState<MusicExtractJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Poll loops must stop when the panel unmounts, or they keep firing forever.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    api.musicCapability().then(setCap).catch(() => setCap(null));
    return () => {
      alive.current = false;
    };
  }, []);

  const poll = useCallback(async (jobId: string) => {
    for (let i = 0; i < 200; i++) {
      if (!alive.current) return;
      await new Promise((r) => setTimeout(r, 3000));
      if (!alive.current) return;
      try {
        const j = await api.getMusicExtract(jobId);
        setJob(j);
        if (j.status !== "running") {
          if (j.status === "succeeded") onExtracted();
          return;
        }
      } catch {
        /* keep polling — a transient error shouldn't kill the wait */
      }
    }
  }, [onExtracted]);

  async function run() {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setJob(null);
    try {
      const started = await api.extractMusic({ file, mood, title });
      setJob({ job_id: started.job_id, status: "running" });
      poll(started.job_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not start separation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardTitle>Extract the music bed from a video</CardTitle>
      <p className="text-[12px] text-muted-foreground mt-1 mb-3">
        Splits a clip into voice and music and keeps the music. Tag it with a
        mood and it becomes a reusable brand asset — every render asking for
        that mood can use it.{" "}
        <span className="text-foreground">
          A bed lifted from someone else&apos;s reel still belongs to whoever
          licensed it; your own footage is the clean case.
        </span>
      </p>

      {cap && !cap.available ? (
        <div className="rounded-md border border-border bg-secondary/40 px-3 py-2 text-[12px] text-muted-foreground">
          Not available on this deployment — {cap.reason}.
        </div>
      ) : (
        <>
          <div className="grid md:grid-cols-3 gap-3">
            <div>
              <Label>Video or audio file</Label>
              <Input
                type="file"
                accept="video/*,audio/*"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>
            <div>
              <Label>Tag the bed as</Label>
              <Select value={mood} onChange={(e) => setMood(e.target.value)}>
                {MOODS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                <option value="">no mood tag (unused by renders)</option>
              </Select>
            </div>
            <div>
              <Label>Name it</Label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Reel bed — warm underscore"
              />
            </div>
          </div>

          <div className="flex items-center gap-3 mt-3">
            <Button onClick={run} disabled={!file || busy || job?.status === "running"}>
              {busy || job?.status === "running" ? (
                <span className="flex items-center gap-2"><Spinner /> separating…</span>
              ) : (
                "Extract the bed"
              )}
            </Button>
            {cap && (
              <span className="text-[11px] text-muted-foreground">
                runs on CPU — tens of seconds; clips up to {cap.max_seconds}s
              </span>
            )}
          </div>
        </>
      )}

      {err && <div className="text-[13px] text-destructive mt-2">{err}</div>}

      {job?.status === "succeeded" && job.asset && (
        <div className="mt-3 rounded-md border border-accent/40 bg-accent/5 px-3 py-2">
          <div className="text-[12px] font-semibold">
            Saved to the Audio Library as “{job.asset.title}”
          </div>
          {job.meta && (
            <div className="text-[11px] text-muted-foreground mt-0.5">
              separated with {job.meta.model} from {job.meta.source_seconds}s of audio
            </div>
          )}
          <audio src={job.asset.uri} controls className="w-full mt-2" />
        </div>
      )}
      {job?.status === "failed" && (
        <div className="text-[13px] text-destructive mt-2">{job.error}</div>
      )}
    </Card>
  );
}
