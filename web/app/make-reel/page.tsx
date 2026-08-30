"use client";

/**
 * The front door: drop a talking-head video in, get a reel out.
 *
 * Everything behind this already existed as separate steps — upload, wait for
 * "ready", pick a candidate, render. That's a reasonable API and a poor
 * product, because the user's action is "make me a reel", not five calls with
 * a wait in the middle.
 *
 * Two things this page has to get right:
 *   - ONE progress bar across the whole chain. The render's own progress starts
 *     at the render, so a user waiting through a 45-second transcription would
 *     watch a bar sit at zero and assume it had hung.
 *   - honesty about B-roll. It is optional, and saying so plainly matters: with
 *     none, the cutaways are designed cards built from the speaker's own words,
 *     which is the format working as intended and not a degraded mode.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReelJob } from "@/lib/api";
import { Button, Card, CardTitle, Input, Label, PageHeader, Select, Spinner } from "@/components/ui";

const MOODS = ["calm", "upbeat", "dramatic", "tension", ""] as const;

export default function MakeReelPage() {
  const [file, setFile] = useState<File | null>(null);
  const [broll, setBroll] = useState<File[]>([]);
  const [title, setTitle] = useState("");
  const [mood, setMood] = useState<string>("calm");
  const [cards, setCards] = useState(true);
  const [job, setJob] = useState<ReelJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // A poll loop outliving the page keeps firing forever; this stops it.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const poll = useCallback(async (jobId: string) => {
    for (let i = 0; i < 900; i++) {
      if (!alive.current) return;
      await new Promise((r) => setTimeout(r, 3000));
      if (!alive.current) return;
      try {
        const j = await api.getReelJob(jobId);
        setJob(j);
        if (j.status !== "running") return;
      } catch {
        /* transient — keep waiting rather than dropping the job */
      }
    }
  }, []);

  async function go() {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setJob(null);
    try {
      const started = await api.makeReel({
        file, broll, title, musicMood: mood, cards,
      });
      setJob(started);
      poll(started.job_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not start");
    } finally {
      setBusy(false);
    }
  }

  const running = job?.status === "running";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Make a reel"
        sub="Upload a talking-head video and get a finished reel. The whole upload becomes the reel — no trimming, no window picking. B-roll is optional."
      />

      <Card>
        <CardTitle>Your video</CardTitle>
        <div className="grid md:grid-cols-2 gap-3 mt-2">
          <div>
            <Label>Talking head (required)</Label>
            <Input
              type="file"
              accept="video/*"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>
          <div>
            <Label>Title</Label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="What this reel is about"
            />
          </div>
        </div>

        <div className="mt-3">
          <Label>B-roll (optional)</Label>
          <Input
            type="file"
            accept="video/*,image/*"
            multiple
            onChange={(e) => setBroll(Array.from(e.target.files || []))}
          />
          <p className="text-[11px] text-muted-foreground mt-1">
            {broll.length > 0 ? (
              <>
                {broll.length} file{broll.length === 1 ? "" : "s"}. Each one gets
                looked at so it can be matched to what you say — a clip only gets
                cut in where it genuinely fits.
              </>
            ) : (
              <>
                Leave this empty and the cutaways become designed cards built
                from your own words. That&apos;s the format working as intended,
                not a fallback.
              </>
            )}
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-3 items-end mt-3">
          <div>
            <Label>Music bed</Label>
            <Select value={mood} onChange={(e) => setMood(e.target.value)}>
              {MOODS.map((m) => (
                <option key={m || "none"} value={m}>{m || "no music"}</option>
              ))}
            </Select>
          </div>
          <label className="flex items-center gap-2 text-[13px] pb-2">
            <input
              type="checkbox"
              checked={cards}
              onChange={(e) => setCards(e.target.checked)}
            />
            Cut away to designed cards
          </label>
          <Button onClick={go} disabled={!file || busy || running}>
            {busy ? (
              <span className="flex items-center gap-2"><Spinner /> starting…</span>
            ) : (
              "Make the reel"
            )}
          </Button>
        </div>
      </Card>

      {err && <div className="text-sm text-destructive">{err}</div>}

      {job && (
        <Card>
          <CardTitle>
            {job.status === "succeeded" ? "Your reel" :
              job.status === "failed" ? "That didn't finish" : "Working"}
          </CardTitle>

          {job.status !== "failed" && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[12px] mb-1">
                <span className="font-semibold">{job.progress?.label}</span>
                <span className="text-muted-foreground">{job.progress?.pct}%</span>
              </div>
              <div className="h-2 rounded-full bg-secondary overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-700"
                  style={{ width: `${job.progress?.pct ?? 0}%` }}
                />
              </div>
              {running && (
                <p className="text-[11px] text-muted-foreground mt-2">
                  Transcribing and rendering take a few minutes — you can leave
                  this page open.
                </p>
              )}
            </div>
          )}

          {job.broll && (
            <p className="text-[12px] text-muted-foreground mt-3">
              {job.broll.described} of {job.broll.uploaded} B-roll file
              {job.broll.uploaded === 1 ? "" : "s"} could be read and matched.
              {job.broll.described < job.broll.uploaded &&
                " The rest stay in your library but won't be cut in until they can be described."}
            </p>
          )}

          {job.status === "failed" && (
            <p className="text-[13px] text-destructive mt-2">{job.error}</p>
          )}

          {job.status === "succeeded" && job.final_url && (
            <div className="mt-3">
              <video src={job.final_url} controls className="w-full max-w-sm rounded-md" />
              <p className="text-[12px] text-muted-foreground mt-2">
                It&apos;s also waiting in the approval queue for review.
              </p>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
