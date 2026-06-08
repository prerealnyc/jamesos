"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader, Card, Button, Input, Spinner, Badge } from "@/components/ui";

type Job = {
  id: string; source: string; category: string; status: string; stage: string;
  total: number; processed: number; chunks: number;
  files: { name: string; chunks: number; type: string }[];
  errors: { source?: string; error: string }[];
  created_at: string; completed_at: string | null;
};

const CATEGORIES = [
  { value: "voice_corpus", label: "Voice corpus (how the brand sounds)" },
  { value: "thesis", label: "Thesis (what the brand believes)" },
  { value: "guideline", label: "Guidelines / voice rules" },
  { value: "reference", label: "Reference (facts, not voice)" },
];

export default function VoiceStudioPage() {
  const [folderUrl, setFolderUrl] = useState("");
  const [links, setLinks] = useState("");
  const [category, setCategory] = useState("voice_corpus");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [corpus, setCorpus] = useState<{ total_chunks: number; sources: { filename: string; chunks: number; chars: number }[] } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    const [j, c] = await Promise.all([
      api.voiceJobs().catch(() => ({ jobs: [] as Job[] })),
      api.voiceCorpus().catch(() => null),
    ]);
    setJobs(j.jobs || []);
    setCorpus(c);
  }, []);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 5000); // live progress while jobs run
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [refresh]);

  async function ingest() {
    setBusy(true); setErr(null); setNotice(null);
    try {
      const linkList = links.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      if (!folderUrl.trim() && linkList.length === 0) {
        setErr("Paste a Drive folder URL or at least one file link."); return;
      }
      const r = await api.ingestVoiceDrive(folderUrl.trim(), linkList, category);
      if (!r.started) {
        setErr((r.errors || [])[0]?.error || "Nothing to ingest — check the folder is shared with the service account.");
        return;
      }
      setNotice(`Started — ${r.total} file(s) queued: ${(r.files || []).slice(0, 6).join(", ")}${(r.files || []).length > 6 ? "…" : ""}. ${r.note || ""}`);
      setFolderUrl(""); setLinks("");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Voice Studio"
        sub="Drop a Google Drive folder or file links — the brand's own talks, podcasts, posts, docs. We transcribe audio/video, extract text from docs, and feed it straight into the brand voice so every draft sounds more like you."
      />

      <Card className="space-y-4">
        <div>
          <label className="text-[12px] uppercase tracking-wider text-muted-foreground">Google Drive folder URL</label>
          <Input
            placeholder="https://drive.google.com/drive/folders/…"
            value={folderUrl}
            onChange={(e) => setFolderUrl(e.target.value)}
          />
        </div>
        <div>
          <label className="text-[12px] uppercase tracking-wider text-muted-foreground">…or individual file links (one per line)</label>
          <textarea
            className="w-full mt-1 rounded-md border border-border bg-background p-2 text-[13px] min-h-[72px]"
            placeholder="https://drive.google.com/file/d/…/view&#10;https://drive.google.com/file/d/…/view"
            value={links}
            onChange={(e) => setLinks(e.target.value)}
          />
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <label className="text-[12px] uppercase tracking-wider text-muted-foreground block">Ingest as</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1 rounded-md border border-border bg-background px-2 py-2 text-[13px]"
            >
              {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <Button onClick={ingest} disabled={busy}>
            {busy ? <Spinner /> : "Ingest into voice"}
          </Button>
        </div>

        <p className="text-[12px] text-muted-foreground border-l-2 border-border pl-3">
          ⚠️ The Drive folder/files must be <b>shared with the app's Google service account</b> (or be link-accessible). Audio &amp; video are auto-transcribed (any length); PDFs / DOCX / TXT / Google Docs have their text extracted. Re-ingesting the same file replaces its old version.
        </p>
        {notice && <div className="text-[13px] text-primary border border-primary/40 rounded-md p-3">{notice}</div>}
        {err && <div className="text-[13px] text-destructive">{err}</div>}
      </Card>

      {/* Live job progress */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-[15px]">Ingest jobs</h2>
          <button onClick={refresh} className="text-[12px] text-muted-foreground hover:text-foreground">refresh</button>
        </div>
        {jobs.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No ingest runs yet.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {jobs.map((j) => (
              <div key={j.id} className="border border-border rounded-md p-3 text-[13px]">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge tone={j.status === "succeeded" ? "primary" : j.status === "running" ? "accent" : "muted"}>{j.status}</Badge>
                  <span className="text-muted-foreground">{j.source}</span>
                  <span className="ml-auto tabular-nums">{j.processed}/{j.total} files · {j.chunks} chunks</span>
                </div>
                {j.status === "running" && (
                  <div className="mt-2 h-1.5 bg-secondary rounded-full overflow-hidden">
                    <div className="h-full bg-primary" style={{ width: `${j.total ? Math.round((100 * j.processed) / j.total) : 0}%` }} />
                  </div>
                )}
                {j.errors?.length > 0 && (
                  <div className="mt-1.5 text-[12px] text-destructive">
                    {j.errors.length} issue(s): {j.errors.slice(0, 3).map((e) => `${e.source || ""}: ${e.error}`).join(" · ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* What's in the voice corpus */}
      <Card>
        <h2 className="font-semibold text-[15px] mb-1">What's in your brand voice</h2>
        <p className="text-[12px] text-muted-foreground mb-3">
          {corpus ? `${corpus.total_chunks.toLocaleString()} chunks of voice corpus across ${corpus.sources.length} source files. Every draft is anchored on this.` : "Loading…"}
        </p>
        {corpus && corpus.sources.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {corpus.sources.slice(0, 20).map((s) => (
              <div key={s.filename} className="flex items-center gap-2 text-[13px]">
                <span className="truncate flex-1" title={s.filename}>{s.filename}</span>
                <span className="text-muted-foreground tabular-nums">{s.chunks} chunks · {(s.chars / 1000).toFixed(0)}k chars</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
