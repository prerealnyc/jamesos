"use client";

/**
 * Knowledge Base — the common entry point for the brand's company documents.
 * Upload files (or a whole ZIP); each is preserved, text-extracted (PDF, Word,
 * PowerPoint, Excel, HTML, RTF, audio→transcript, image→OCR), and indexed into
 * the brand's private memory — immediately askable and used to ground content.
 * Intelligence-parity Phase 1 (ported from the PreReal Intelligence platform).
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, type KnowledgeDoc, type KnowledgeIngestResult, type AskTurn,
  type Whitepaper, type IntelBrief, type Commitment,
} from "@/lib/api";
import { Button, Card, CardTitle, Badge, Spinner, PageHeader } from "@/components/ui";

const CATEGORIES = [
  ["company_doc", "Company document (facts)"],
  ["thesis", "Thesis / vision"],
  ["guideline", "Brand guideline"],
  ["reference", "Reference / strategy"],
] as const;

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

const STATUS_TONE: Record<KnowledgeDoc["indexing_status"], "ok" | "muted" | "destructive" | "accent"> = {
  indexed: "ok", skipped: "muted", failed: "destructive", pending: "accent",
};

export default function KnowledgePage() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [category, setCategory] = useState("company_doc");
  const [notes, setNotes] = useState("");
  const [summary, setSummary] = useState<KnowledgeIngestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Ask-your-docs — a conversation, not a one-shot: prior turns ride along
  // so follow-up questions ("what about the second one?") work.
  const [q, setQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [thread, setThread] = useState<(AskTurn & { citations?: number })[]>([]);

  async function load() {
    try {
      const r = await api.listKnowledgeDocuments();
      setDocs(r.documents);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setErr(null);
    setSummary(null);
    try {
      let agg: KnowledgeIngestResult | null = null;
      for (const f of Array.from(files)) {
        const r = await api.knowledgeIngest(f, { category, notes });
        if (agg === null) {
          agg = r;
        } else {
          const prev: KnowledgeIngestResult = agg;
          agg = {
            ...prev,
            total: prev.total + r.total,
            filed: prev.filed + r.filed,
            skipped: prev.skipped + r.skipped,
            failed: prev.failed + r.failed,
            results: [...prev.results, ...r.results],
          };
        }
      }
      setSummary(agg);
      setNotes("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteKnowledgeDocument(id);
      setDocs((d) => d.filter((x) => x.id !== id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "delete failed");
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

  async function ask() {
    const question = q.trim();
    if (!question || asking) return;   // Enter must not bypass the guard
    setAsking(true);
    const history: AskTurn[] = thread.map(({ role, content }) => ({ role, content }));
    setThread((t) => [...t, { role: "user", content: question }]);
    setQ("");
    try {
      const r = await api.ask(question, history);
      setThread((t) => [...t, {
        role: "assistant",
        content: r.response || "(no answer)",
        citations: r.citations?.length ?? 0,
      }]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ask failed");
      setThread((t) => t.slice(0, -1));   // question failed — let them retry
      setQ(question);
    } finally {
      setAsking(false);
    }
  }

  const indexed = docs.filter((d) => d.indexing_status === "indexed").length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Knowledge Base"
        sub="Your company documents, turned into private brand memory. Drop in PDFs, decks, spreadsheets, contracts, recordings — everything is preserved, read, and indexed so you can ask it questions and ground content (white papers, posts, reels) in your own facts."
      />

      <Card>
        <CardTitle>Add documents</CardTitle>
        <p className="text-[12px] text-muted-foreground mb-2">
          Files or a whole ZIP (up to 60 docs). Reads PDF, Word, PowerPoint,
          Excel, HTML, RTF, text — plus audio/video (transcribed) and images
          (OCR&apos;d). Unreadable formats are still stored, never lost.
        </p>
        <div className="flex gap-2 mb-2 flex-wrap">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="text-[12px] px-2 py-1.5 rounded-md border border-border bg-background"
            title="How the memory should treat these documents"
          >
            {CATEGORIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional note — what is this batch?"
            className="flex-1 min-w-[220px] text-[12px] px-3 py-1.5 rounded-md border border-border bg-background"
          />
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          onChange={(e) => onUpload(e.target.files)}
          className="block w-full text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
        />
        {busy && <p className="text-[12px] mt-2 flex items-center gap-2"><Spinner /> ingesting…</p>}
        {summary && (
          <p className="text-[12px] mt-2 text-muted-foreground">
            {summary.filed} filed · {summary.skipped} stored without text · {summary.failed} failed
            {summary.results.some((r) => r.reason) && (
              <> — {summary.results.filter((r) => r.reason).map((r) => `${r.originalName}: ${r.reason}`).join("; ")}</>
            )}
          </p>
        )}
        {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Ask your documents</CardTitle>
          {thread.length > 0 && (
            <button
              onClick={() => setThread([])}
              className="text-[12px] text-muted-foreground hover:text-foreground"
              title="Start a fresh conversation"
            >
              Clear chat
            </button>
          )}
        </div>
        {thread.length > 0 && (
          <div className="mt-3 flex flex-col gap-2">
            {thread.map((m, i) => (
              <div
                key={i}
                className={
                  m.role === "user"
                    ? "self-end max-w-[85%] text-[13px] leading-relaxed whitespace-pre-wrap rounded-md px-3 py-2 bg-primary text-primary-foreground"
                    : "self-start max-w-[85%] text-[13px] leading-relaxed whitespace-pre-wrap rounded-md px-3 py-2 border border-border"
                }
              >
                {m.content}
                {m.role === "assistant" && (m.citations ?? 0) > 0 && (
                  <div className="text-[11px] text-muted-foreground mt-1.5">
                    {m.citations} citation{m.citations === 1 ? "" : "s"} from your memory
                  </div>
                )}
              </div>
            ))}
            {asking && (
              <div className="self-start text-[12px] text-muted-foreground inline-flex items-center gap-2 px-1">
                <Spinner /> thinking…
              </div>
            )}
          </div>
        )}
        <div className="flex gap-2 mt-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") ask(); }}
            placeholder={thread.length
              ? "Ask a follow-up…"
              : "e.g. What were the key terms in the Main St contract?"}
            className="flex-1 text-[13px] px-3 py-2 rounded-md border border-border bg-background"
          />
          <Button onClick={ask} disabled={asking || !q.trim()}>
            {asking ? <Spinner /> : "Ask"}
          </Button>
        </div>
      </Card>

      <WhitepaperCard onSaved={load} />
      <IntelligenceCard onSaved={load} />
      <CommitmentsCard />

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>
      ) : docs.length === 0 ? (
        <Card>
          <p className="text-[13px] text-muted-foreground">
            No documents yet. Upload your company docs above — they become
            private, searchable brand memory.
          </p>
        </Card>
      ) : (
        <Card>
          <CardTitle>Documents ({docs.length} · {indexed} indexed)</CardTitle>
          <div className="flex flex-col divide-y divide-border mt-2">
            {docs.map((d) => (
              <div key={d.id} className="flex items-center gap-3 py-2">
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium truncate" title={d.filename}>
                    {d.filename}
                    {d.version > 1 && <span className="text-muted-foreground"> · v{d.version}</span>}
                  </div>
                  <div className="text-[11px] text-muted-foreground truncate">
                    {fmtSize(d.size_bytes)} · {d.category}
                    {d.doc_type && <> · {d.doc_type}</>}
                    {d.business_unit && <> · {d.business_unit}</>}
                    {d.entity_id && <> · {d.entity_id}</>}
                    {d.silo_id && <> · silo:{d.silo_id}</>}
                    {d.sensitivity && d.sensitivity !== "Restricted" && <> · {d.sensitivity}</>}
                    {d.chunks > 0 && <> · {d.chunks} chunks</>}
                    {d.notes && <> · {d.notes}</>}
                    {(d.indexing_error || d.review_reason) && (
                      <span className="text-destructive"> · {d.indexing_error || d.review_reason}</span>
                    )}
                  </div>
                </div>
                <Badge tone={STATUS_TONE[d.indexing_status]}>{d.indexing_status}</Badge>
                <button
                  onClick={() => download(d.id)}
                  className="text-[12px] text-primary hover:underline"
                  title="Signed, time-limited download"
                >
                  download
                </button>
                <button
                  onClick={() => remove(d.id)}
                  className="text-[12px] text-muted-foreground hover:text-destructive"
                  title="Remove the file AND its memory chunks"
                >
                  delete
                </button>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}


/** Generate a corpus-grounded white paper (thesis → paper → content). */
function WhitepaperCard({ onSaved }: { onSaved: () => void }) {
  const [topic, setTopic] = useState("");
  const [audience, setAudience] = useState("");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [paper, setPaper] = useState<Whitepaper | null>(null);
  const [openSection, setOpenSection] = useState<number | null>(null);
  const [composing, setComposing] = useState(false);
  const [composed, setComposed] = useState(false);
  const [err, setErr] = useState("");

  async function generate() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setPaper(null);
    setComposed(false);
    try {
      const { job_id } = await api.startWhitepaper({
        topic: topic.trim(),
        audience: audience.trim() || undefined,
        goal: goal.trim() || undefined,
      });
      // Poll the background job (generation takes ~1-2 min).
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        const j = await api.whitepaperStatus(job_id);
        if (j.status === "done" && j.result) {
          setPaper(j.result);
          onSaved();   // the paper was filed into the ledger below
          return;
        }
        if (j.status === "failed") {
          setErr(j.error || "generation failed");
          return;
        }
      }
      setErr("Still running — check the Knowledge Base list; the paper appears there when done.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function toContent() {
    if (!paper) return;
    setComposing(true);
    setErr("");
    try {
      await api.generate({
        topic: paper.title,
        research_subject: paper.topic,
        format: "post",
      });
      setComposed(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "compose failed");
    } finally {
      setComposing(false);
    }
  }

  return (
    <Card>
      <CardTitle>White paper</CardTitle>
      <p className="text-[12px] text-muted-foreground mb-2">
        Turn a topic (your weekly thesis) into a publication-grade white paper,
        grounded in the documents above with [n] citations. The finished paper
        is saved to the Knowledge Base and immediately available to ground
        posts, reels, and podcasts.
      </p>
      <div className="flex flex-col gap-2">
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Topic / thesis — e.g. Why Staten Island multifamily outperforms in a falling-rate cycle"
          className="w-full text-[13px] px-3 py-2 rounded-md border border-border bg-background"
        />
        <div className="flex gap-2 flex-wrap">
          <input
            value={audience}
            onChange={(e) => setAudience(e.target.value)}
            placeholder="Audience (default: executives & partners)"
            className="flex-1 min-w-[200px] text-[12px] px-3 py-1.5 rounded-md border border-border bg-background"
          />
          <input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Goal (default: inform strategy)"
            className="flex-1 min-w-[200px] text-[12px] px-3 py-1.5 rounded-md border border-border bg-background"
          />
          <Button onClick={generate} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : "Generate white paper"}
          </Button>
        </div>
      </div>
      {busy && (
        <p className="text-[12px] text-muted-foreground mt-2">
          Grounding in your documents, learning best-in-class structure, writing… (~1–2 min)
        </p>
      )}
      {err && <p className="text-[12px] text-destructive mt-2">✗ {err}</p>}

      {paper && (
        <div className="mt-4 border border-border rounded-md p-4 flex flex-col gap-3">
          <div>
            <div className="text-[16px] font-semibold leading-snug">{paper.title}</div>
            {paper.subtitle && (
              <div className="text-[13px] text-muted-foreground italic">{paper.subtitle}</div>
            )}
            <div className="flex gap-1.5 mt-1.5 flex-wrap">
              {paper.low_grounding && (
                <Badge tone="accent">thin corpus — add more docs for stronger grounding</Badge>
              )}
              {paper.file && <Badge tone="ok">saved to Knowledge Base</Badge>}
              <Badge tone="muted">{paper.sources.length} sources</Badge>
            </div>
          </div>
          {paper.abstract && (
            <p className="text-[13px] leading-relaxed">{paper.abstract}</p>
          )}
          <div className="flex flex-col divide-y divide-border">
            {paper.sections.map((s, i) => (
              <div key={i} className="py-1.5">
                <button
                  type="button"
                  onClick={() => setOpenSection(openSection === i ? null : i)}
                  className="w-full text-left text-[13px] font-medium flex items-center justify-between"
                >
                  {s.heading}
                  <span className="text-muted-foreground">{openSection === i ? "▲" : "▼"}</span>
                </button>
                {openSection === i && (
                  <div className="text-[13px] leading-relaxed whitespace-pre-wrap mt-1.5 text-muted-foreground">
                    {s.body}
                  </div>
                )}
              </div>
            ))}
          </div>
          {paper.key_takeaways.length > 0 && (
            <div>
              <div className="text-[12px] font-medium mb-1">Key takeaways</div>
              <ul className="text-[13px] list-disc pl-5 flex flex-col gap-0.5">
                {paper.key_takeaways.map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <Button onClick={toContent} disabled={composing || composed}>
              {composing ? <Spinner /> : composed ? "✓ Post queued" : "→ Create post from this paper"}
            </Button>
            {composed && (
              <Link href="/queue" className="text-[12px] text-primary hover:underline">
                Review it in the Approval Queue ↗
              </Link>
            )}
            <span className="text-[11px] text-muted-foreground">
              Reels/podcasts: the paper now grounds every render on this topic.
            </span>
          </div>
        </div>
      )}
    </Card>
  );
}


/** Topic Intelligence: AI-planned research sweep + connect-the-dots brief. */
function IntelligenceCard({ onSaved }: { onSaved: () => void }) {
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [brief, setBrief] = useState<IntelBrief | null>(null);
  const [err, setErr] = useState("");

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setBrief(null);
    try {
      const { job_id } = await api.startIntelligence({ topic: topic.trim(), force: true });
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        const j = await api.intelligenceStatus(job_id);
        if (j.status === "done" && j.result) {
          setBrief(j.result);
          onSaved();
          return;
        }
        if (j.status === "failed") { setErr(j.error || "intelligence build failed"); return; }
      }
      setErr("Still running — gathered briefs land in the document list as they finish.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "intelligence build failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardTitle>Topic intelligence</CardTitle>
      <p className="text-[12px] text-muted-foreground mb-2">
        Deep-research a topic: the strategist plans the angles worth
        investigating, runs live web research on each, files every brief into
        your Knowledge Base, and synthesizes a connect-the-dots intelligence
        brief. (Several minutes — each build runs multiple web searches.)
      </p>
      <div className="flex gap-2">
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Staten Island waterfront rezoning"
          className="flex-1 text-[13px] px-3 py-2 rounded-md border border-border bg-background"
        />
        <Button onClick={run} disabled={busy || !topic.trim()}>
          {busy ? <Spinner /> : "Build intelligence"}
        </Button>
      </div>
      {busy && (
        <p className="text-[12px] text-muted-foreground mt-2">
          Planning → researching angles in parallel → synthesizing… (~2–5 min)
        </p>
      )}
      {err && <p className="text-[12px] text-destructive mt-2">✗ {err}</p>}
      {brief && (
        <div className="mt-3 border border-border rounded-md p-4 flex flex-col gap-3">
          <div className="flex gap-1.5 flex-wrap">
            {brief.planned && <Badge tone="primary">AI-planned angles</Badge>}
            <Badge tone="muted">{brief.gathered.length} angles researched</Badge>
            <Badge tone="muted">{brief.sources.length} sources</Badge>
            {brief.synthesis.file && <Badge tone="ok">saved to Knowledge Base</Badge>}
          </div>
          {brief.gathered.length > 0 && (
            <div className="flex flex-col gap-0.5">
              {brief.gathered.map((g, i) => (
                <div key={i} className="text-[12px] text-muted-foreground">
                  {g.error ? "✗" : "✓"} <span className="font-medium text-foreground">{g.label}</span>
                  {" — "}{g.error || `${g.citations} citations${g.saved ? ", saved" : ""}`}
                </div>
              ))}
            </div>
          )}
          <div className="text-[13px] leading-relaxed whitespace-pre-wrap">
            {brief.synthesis.answer}
          </div>
        </div>
      )}
    </Card>
  );
}


const COMMITMENT_NEXT: Record<Commitment["status"], Commitment["status"]> = {
  open: "in_progress", in_progress: "done", blocked: "in_progress",
  done: "open", missed: "open",
};

/** Action items mined automatically from meeting notes / transcripts. */
function CommitmentsCard() {
  const [items, setItems] = useState<Commitment[]>([]);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    try {
      const r = await api.listCommitments();
      setItems(r.commitments);
    } catch { /* table empty / auth */ }
    finally { setLoaded(true); }
  }
  useEffect(() => { load(); }, []);

  async function cycle(c: Commitment) {
    const next = COMMITMENT_NEXT[c.status];
    try {
      await api.updateCommitment(c.id, next);
      setItems((xs) => xs.map((x) => (x.id === c.id ? { ...x, status: next } : x)));
    } catch { /* ignore */ }
  }

  if (!loaded || items.length === 0) return null;   // appears once docs yield action items

  const tone = (s: Commitment["status"]) =>
    s === "done" ? "ok" : s === "blocked" || s === "missed" ? "destructive"
    : s === "in_progress" ? "accent" : "muted";

  return (
    <Card>
      <CardTitle>Commitments ({items.filter((i) => i.status !== "done").length} open)</CardTitle>
      <p className="text-[12px] text-muted-foreground mb-1">
        Action items mined automatically from meeting notes and transcripts you upload.
      </p>
      <div className="flex flex-col divide-y divide-border">
        {items.map((c) => (
          <div key={c.id} className="flex items-center gap-3 py-1.5">
            <div className="flex-1 min-w-0">
              <div className="text-[13px] leading-snug">{c.text}</div>
              <div className="text-[11px] text-muted-foreground">
                {c.owner}{c.due && <> · due {c.due}</>}{c.entity_id && <> · {c.entity_id}</>}
              </div>
            </div>
            <button type="button" onClick={() => cycle(c)} title="Click to advance status">
              <Badge tone={tone(c.status)}>{c.status.replace("_", " ")}</Badge>
            </button>
          </div>
        ))}
      </div>
    </Card>
  );
}
