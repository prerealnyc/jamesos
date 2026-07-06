"use client";

/**
 * Weekly Thesis — the CEO's front door. Speak it, upload it, or paste it;
 * it's transcribed and filed as brand memory (category=thesis). One click
 * then DEVELOPS it: claims extracted → topic intelligence researches the
 * theme (briefs filed) → a white paper is written that argues the thesis —
 * everything lands back in the Knowledge Base ready to become content.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, type KnowledgeDoc, type PodcastEpisode, type PodcastJob,
  type ThesisDevelopJob,
} from "@/lib/api";
import { Button, Card, CardTitle, Badge, Spinner, PageHeader } from "@/components/ui";

const STAGE_LABEL: Record<string, string> = {
  reading: "Reading the thesis — extracting theme & claims…",
  researching: "Researching the theme — web sweep + briefs (2-5 min)…",
  writing: "Writing the white paper (1-2 min)…",
  content: "Fanning out the content pack — posts + reels (2-4 min)…",
  podcast: "Writing & narrating the podcast episode (2-4 min)…",
};

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString(); } catch { return ""; }
}

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
}

export default function ThesisPage() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // paste-a-thesis
  const [text, setText] = useState("");
  // voice memo
  const [recording, setRecording] = useState(false);
  const [recSeconds, setRecSeconds] = useState(0);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // A recording whose UPLOAD failed — kept so it can be retried, never lost.
  const [pendingFile, setPendingFile] = useState<File | null>(null);

  // develop jobs keyed by thesis doc id
  const [jobs, setJobs] = useState<Record<string, ThesisDevelopJob & { job_id: string }>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // podcast jobs keyed by SOURCE doc id + the episode list
  const [podJobs, setPodJobs] = useState<Record<string, PodcastJob & { job_id: string }>>({});
  const [episodes, setEpisodes] = useState<PodcastEpisode[]>([]);
  const [composedFor, setComposedFor] = useState<Record<string, boolean>>({});
  const [composingFor, setComposingFor] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.listKnowledgeDocuments();
      setDocs(r.documents.filter((d) => d.category === "thesis"));
      api.listPodcasts().then((p) => setEpisodes(p.episodes)).catch(() => {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  // Stop the mic + timer if the user navigates away mid-recording.
  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
    try {
      mediaRef.current?.stream.getTracks().forEach((t) => t.stop());
      mediaRef.current?.stop();
    } catch { /* already stopped */ }
  }, []);

  // Poll running develop + podcast jobs every 5s. A 404 means the server
  // restarted and the in-memory job is gone — mark it failed so the button
  // comes back instead of spinning forever.
  useEffect(() => {
    pollRef.current = setInterval(async () => {
      for (const [docId, j] of Object.entries(jobs)) {
        if (j.status !== "running") continue;
        try {
          const s = await api.thesisDevelopStatus(j.job_id);
          setJobs((prev) => ({ ...prev, [docId]: { ...s, job_id: j.job_id } }));
          if (s.status === "done") load();
        } catch {
          setJobs((prev) => ({
            ...prev,
            [docId]: {
              status: "failed", job_id: j.job_id,
              error: "server restarted — finished artifacts are in the Knowledge Base",
            },
          }));
        }
      }
      for (const [docId, j] of Object.entries(podJobs)) {
        if (j.status !== "running") continue;
        try {
          const s = await api.podcastStatus(j.job_id);
          setPodJobs((prev) => ({ ...prev, [docId]: { ...s, job_id: j.job_id } }));
          if (s.status === "done") {
            api.listPodcasts().then((p) => setEpisodes(p.episodes)).catch(() => {});
          }
        } catch {
          setPodJobs((prev) => ({
            ...prev,
            [docId]: {
              status: "failed", job_id: j.job_id,
              error: "server restarted — check the episode list below",
            },
          }));
        }
      }
    }, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobs, podJobs]);

  async function ingestThesis(file: File) {
    setUploading(true);
    setErr(null);
    try {
      const week = new Date().toISOString().slice(0, 10);
      await api.knowledgeIngest(file, {
        category: "thesis",
        notes: `Weekly thesis — week of ${week}`,
      });
      setPendingFile(null);
      await load();
    } catch (e) {
      // NEVER lose a recording to a failed upload — keep it retryable.
      setPendingFile(file);
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function submitText() {
    const t = text.trim();
    if (!t) return;
    const week = new Date().toISOString().slice(0, 10);
    const file = new File([t], `weekly-thesis-${week}.txt`, { type: "text/plain" });
    await ingestThesis(file);
    setText("");
  }

  async function startRecording() {
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      // Local per-recording buffer: a quick stop→re-record must never mix
      // one memo's chunks into the next recording's blob.
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        const week = new Date().toISOString().slice(0, 10);
        const ext = (rec.mimeType || "audio/webm").includes("mp4") ? "mp4" : "webm";
        await ingestThesis(new File([blob], `weekly-thesis-${week}.${ext}`, { type: blob.type }));
      };
      rec.start();
      mediaRef.current = rec;
      setRecording(true);
      setRecSeconds(0);
      timerRef.current = setInterval(() => setRecSeconds((s) => s + 1), 1000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "microphone unavailable");
    }
  }

  function stopRecording() {
    mediaRef.current?.stop();
    mediaRef.current = null;
    setRecording(false);
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  }

  async function develop(docId: string, full = false) {
    setErr(null);
    try {
      const { job_id } = await api.developThesis(docId, full);
      setJobs((prev) => ({
        ...prev,
        [docId]: { status: "running", stage: "reading", job_id, full },
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "develop failed");
    }
  }

  async function download(id: string) {
    try {
      const { url } = await api.knowledgeDownloadUrl(id);
      window.open(url, "_blank", "noopener");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "download failed");
    }
  }

  async function toPost(docId: string, job: ThesisDevelopJob) {
    const wp = job.result?.whitepaper;
    if (!wp?.title || composingFor) return;
    setComposingFor(docId);
    try {
      await api.generate({
        topic: wp.title,
        research_subject: job.result?.theme || wp.title,
        format: "post",
      });
      setComposedFor((prev) => ({ ...prev, [docId]: true }));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "compose failed");
    } finally {
      setComposingFor(null);
    }
  }

  async function makePodcast(sourceDocId: string, thesisDocId: string) {
    setErr(null);
    try {
      const { job_id } = await api.startPodcast(sourceDocId);
      setPodJobs((prev) => ({
        ...prev,
        [thesisDocId]: { status: "running", stage: "scripting", job_id },
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "podcast failed to start");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Weekly Thesis"
        sub="Drop this week's thesis — speak it, upload it, or paste it. Then hit Develop: the intelligence engine researches your theme, files the briefs, and writes a white paper that argues your position. All of it becomes memory the content engine draws from."
      />

      <Card>
        <CardTitle>This week&apos;s thesis</CardTitle>
        <div className="flex gap-2 mt-3 flex-wrap items-center">
          {!recording ? (
            <Button variant="secondary" onClick={startRecording} disabled={uploading}>
              🎙️ Record voice memo
            </Button>
          ) : (
            <Button onClick={stopRecording}>
              ⏹ Stop ({Math.floor(recSeconds / 60)}:{String(recSeconds % 60).padStart(2, "0")}) — save thesis
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => fileRef.current?.click()}
            disabled={uploading || recording}
          >
            Upload file (audio / video / doc)
          </Button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) ingestThesis(f);
              e.target.value = "";
            }}
          />
          {uploading && (
            <span className="text-[12px] text-muted-foreground inline-flex items-center gap-2">
              <Spinner /> transcribing & filing…
            </span>
          )}
        </div>
        <div className="flex gap-2 mt-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="…or paste / type the thesis here"
            rows={3}
            className="flex-1 text-[13px] px-3 py-2 rounded-md border border-border bg-background resize-y"
          />
        </div>
        <div className="mt-2 flex items-center gap-3">
          <Button onClick={submitText} disabled={!text.trim() || uploading}>
            Save thesis
          </Button>
          {pendingFile && !uploading && (
            <Button variant="secondary" onClick={() => ingestThesis(pendingFile)}>
              ↻ Retry upload ({pendingFile.name})
            </Button>
          )}
        </div>
        {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      </Card>

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>
      ) : docs.length === 0 ? (
        <Card>
          <p className="text-[13px] text-muted-foreground">
            No theses yet. Record or upload the first one above — it becomes
            searchable memory immediately.
          </p>
        </Card>
      ) : (
        <Card>
          <CardTitle>Thesis library ({docs.length})</CardTitle>
          <div className="flex flex-col divide-y divide-border mt-2">
            {docs.map((d) => {
              const job = jobs[d.id];
              return (
                <div key={d.id} className="py-3 flex flex-col gap-2">
                  <div className="flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium truncate" title={d.filename}>
                        {d.filename}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {fmtDate(d.created_at)}
                        {d.chunks > 0 && <> · {d.chunks} chunks in memory</>}
                        {d.notes && <> · {d.notes}</>}
                      </div>
                    </div>
                    {!job || job.status === "failed" ? (
                      <div className="flex items-center gap-2">
                        <Button
                          onClick={() => develop(d.id, true)}
                          // 'failed' = text extracted but embedding failed —
                          // develop only needs the text, so allow it.
                          disabled={d.indexing_status === "skipped" || d.indexing_status === "pending"}
                          title={d.indexing_status === "skipped"
                            ? `No readable text/transcript${d.indexing_error ? ` — ${d.indexing_error}` : ""}`
                            : d.indexing_status === "pending"
                              ? "Still indexing — refresh in a moment"
                              : "THE one button: research → white paper → 3 posts + 2 reels → podcast, all into your Approval Queue (~10-15 min)"}
                          className="text-[12px] !px-3 !py-1"
                        >
                          ⚡ Full week →
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => develop(d.id, false)}
                          disabled={d.indexing_status === "skipped" || d.indexing_status === "pending"}
                          title="Research the theme + write the white paper only"
                          className="text-[12px] !px-3 !py-1"
                        >
                          Paper only
                        </Button>
                      </div>
                    ) : job.status === "running" ? (
                      <Badge tone="accent">Developing…</Badge>
                    ) : (
                      <Badge tone="ok">Developed ✓</Badge>
                    )}
                  </div>

                  {job?.status === "running" && (
                    <div className="text-[12px] text-muted-foreground inline-flex items-center gap-2">
                      <Spinner /> {STAGE_LABEL[job.stage || "reading"] || "working…"}
                    </div>
                  )}
                  {job?.status === "failed" && (
                    <div className="text-[12px] text-destructive">✗ {job.error || "develop failed"} — hit Develop to retry</div>
                  )}
                  {job?.status === "done" && job.result && (
                    <div className="border border-border rounded-md p-3 flex flex-col gap-2">
                      <div className="text-[12px]">
                        <span className="font-medium">Theme:</span> {job.result.theme}
                      </div>
                      {job.result.claims.length > 0 && (
                        <ul className="text-[12px] text-muted-foreground list-disc pl-5">
                          {job.result.claims.map((c, i) => <li key={i}>{c}</li>)}
                        </ul>
                      )}
                      <div className="text-[12px] text-muted-foreground">
                        Research: {job.result.intelligence.briefs_saved} brief
                        {job.result.intelligence.briefs_saved === 1 ? "" : "s"} filed into memory
                        {job.result.intelligence.synthesis_file && <> · synthesis saved</>}
                      </div>
                      <div className="text-[13px] font-medium">
                        📄 {job.result.whitepaper.title || "White paper"}
                        <span className="text-muted-foreground font-normal">
                          {" "}· {job.result.whitepaper.sections} sections
                          {job.result.whitepaper.low_grounding && " · ⚠ thin grounding"}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 flex-wrap">
                        {job.result.whitepaper.document_id && (
                          <button
                            onClick={() => download(job.result!.whitepaper.document_id!)}
                            className="text-[12px] text-primary hover:underline"
                          >
                            ⬇ Download paper
                          </button>
                        )}
                        <Button
                          onClick={() => toPost(d.id, job)}
                          disabled={composingFor === d.id || !!composedFor[d.id]}
                          className="text-[12px] !px-3 !py-1"
                        >
                          {composingFor === d.id ? <Spinner />
                            : composedFor[d.id] ? "✓ Post queued" : "→ Create post from it"}
                        </Button>
                        {(!podJobs[d.id] || podJobs[d.id].status === "failed") && (
                          <Button
                            variant="secondary"
                            onClick={() => makePodcast(
                              job.result!.whitepaper.document_id || d.id, d.id)}
                            className="text-[12px] !px-3 !py-1"
                            title="Write an episode script in the brand voice and narrate it with the cloned voice"
                          >
                            🎙 Make podcast
                          </Button>
                        )}
                        {composedFor[d.id] && (
                          <Link href="/queue" className="text-[12px] text-primary hover:underline">
                            Approval Queue ↗
                          </Link>
                        )}
                        <Link href="/knowledge" className="text-[12px] text-primary hover:underline">
                          See it in the Knowledge Base ↗
                        </Link>
                      </div>
                      {podJobs[d.id]?.status === "running" && (
                        <div className="text-[12px] text-muted-foreground inline-flex items-center gap-2">
                          <Spinner /> {podJobs[d.id].stage === "narrating"
                            ? "Narrating with the brand voice…"
                            : podJobs[d.id].stage === "publishing"
                              ? "Publishing the episode…"
                              : "Writing the episode script…"}
                        </div>
                      )}
                      {podJobs[d.id]?.status === "failed" && (
                        <div className="text-[12px] text-destructive">
                          ✗ podcast: {podJobs[d.id].error || "failed"} — try again
                        </div>
                      )}
                      {podJobs[d.id]?.status === "done" && podJobs[d.id].result && (
                        <div className="flex flex-col gap-1">
                          <div className="text-[12px] font-medium">
                            🎙 {podJobs[d.id].result!.title}
                            {podJobs[d.id].result!.duration_s > 0 && (
                              <span className="text-muted-foreground font-normal">
                                {" "}· {fmtDur(podJobs[d.id].result!.duration_s)}
                              </span>
                            )}
                          </div>
                          <audio controls src={podJobs[d.id].result!.audio_url} className="w-full max-w-md" />
                        </div>
                      )}

                      {/* full-week extras: content pack + podcast from the run itself */}
                      {job.result.content_pack && (
                        <div className="text-[12px]">
                          {job.result.content_pack.error ? (
                            <span className="text-destructive">✗ content pack: {job.result.content_pack.error}</span>
                          ) : (
                            <>
                              <span className="font-medium">Content pack:</span>{" "}
                              {job.result.content_pack.posts_queued} post{job.result.content_pack.posts_queued === 1 ? "" : "s"} queued
                              {" "}+ {job.result.content_pack.reels_started} reel{job.result.content_pack.reels_started === 1 ? "" : "s"} rendering
                              {" "}· <Link href="/queue" className="text-primary hover:underline">Approval Queue ↗</Link>
                              {(job.result.content_pack.errors?.length ?? 0) > 0 && (
                                <span className="text-muted-foreground"> · {job.result.content_pack.errors!.length} item(s) failed</span>
                              )}
                            </>
                          )}
                        </div>
                      )}
                      {job.result.podcast && (
                        job.result.podcast.audio_url ? (
                          <div className="flex flex-col gap-1">
                            <div className="text-[12px] font-medium">
                              🎙 {job.result.podcast.title}
                              {(job.result.podcast.duration_s ?? 0) > 0 && (
                                <span className="text-muted-foreground font-normal"> · {fmtDur(job.result.podcast.duration_s!)}</span>
                              )}
                            </div>
                            <audio controls src={job.result.podcast.audio_url} className="w-full max-w-md" />
                          </div>
                        ) : (
                          <div className="text-[12px] text-muted-foreground">
                            🎙 podcast: {job.result.podcast.skipped || job.result.podcast.error || "not generated"}
                          </div>
                        )
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {episodes.length > 0 && (
        <Card>
          <CardTitle>Podcast episodes ({episodes.length})</CardTitle>
          <div className="flex flex-col divide-y divide-border mt-2">
            {episodes.map((ep) => (
              <div key={ep.id} className="py-3 flex flex-col gap-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[13px] font-medium">🎙 {ep.title}</span>
                  {ep.duration_s > 0 && (
                    <span className="text-[11px] text-muted-foreground">{fmtDur(ep.duration_s)}</span>
                  )}
                  <Badge tone={ep.status === "approved" ? "ok" : ep.status === "rejected" ? "destructive" : "muted"}>
                    {ep.status}
                  </Badge>
                </div>
                {ep.description && (
                  <div className="text-[12px] text-muted-foreground whitespace-pre-wrap line-clamp-3">
                    {ep.description}
                  </div>
                )}
                {ep.audio_url && (
                  <audio controls src={ep.audio_url} className="w-full max-w-md" preload="none" />
                )}
                <div className="text-[11px] text-muted-foreground">
                  from {ep.source_filename || "the knowledge base"} · {fmtDate(ep.created_at)}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
