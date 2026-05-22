"use client";

import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type MediaAsset, type MediaRole } from "@/lib/api";
import { PageHeader, Card, Button, Input, Spinner, Badge } from "@/components/ui";

const ROLES: { key: MediaRole; label: string; blurb: string }[] = [
  {
    key: "james_clip",
    label: "James's clips",
    blurb: "Real talking-head footage — inserted into generated videos.",
  },
  {
    key: "style_reference",
    label: "Style references",
    blurb: "Example videos to replicate. Add notes on what to copy (hook, pacing, captions).",
  },
  { key: "broll", label: "B-roll", blurb: "Supplemental footage to mix in." },
];

export default function ReferenceLibraryPage() {
  const [role, setRole] = useState<MediaRole>("james_clip");
  const [items, setItems] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const active = ROLES.find((r) => r.key === role)!;

  async function load() {
    setLoading(true);
    try {
      setItems((await api.listMedia(role)).media);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  // Poll while any asset is still being analyzed.
  useEffect(() => {
    if (!items.some((m) => m.analysis_status === "pending")) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        await api.uploadMedia(f, role, { title: f.name });
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function addLink() {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    setErr(null);
    try {
      await api.linkMedia({ url: u, role });
      setUrl("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "add failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Reference Library"
        sub="Upload James's clips, style references, and B-roll. These drive the video pipeline — the system learns the target style and reuses your footage."
      />

      <div className="flex gap-2">
        {ROLES.map((r) => (
          <button
            key={r.key}
            onClick={() => setRole(r.key)}
            className={`px-4 py-2 text-sm font-semibold rounded-md transition-colors ${
              role === r.key
                ? "bg-primary text-primary-foreground"
                : "bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      <Card>
        <p className="text-[13px] text-muted-foreground mb-3">{active.blurb}</p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            multiple
            onChange={(e) => onUpload(e.target.files)}
            className="hidden"
            id="media-file"
          />
          <Button
            variant="secondary"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            {busy ? <Spinner /> : "Upload video(s)"}
          </Button>
          <span className="text-[12px] text-muted-foreground">or</span>
          <Input
            placeholder="Paste a video URL (YouTube / Drive / CDN)"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addLink()}
            className="max-w-[360px]"
          />
          <Button onClick={addLink} disabled={busy}>
            Add link
          </Button>
        </div>
        {err && <div className="mt-3 text-sm text-destructive">{err}</div>}
      </Card>

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2">
          <Spinner /> loading…
        </div>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">
          Nothing in <b>{active.label}</b> yet. Upload a file or add a link above.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-3">
          {items.map((m) => (
            <MediaTile key={m.id} item={m} onChange={load} />
          ))}
        </div>
      )}
    </div>
  );
}

const STATUS_TONE: Record<string, "ok" | "primary" | "destructive" | "muted"> = {
  done: "ok",
  pending: "primary",
  failed: "destructive",
  unsupported: "muted",
  none: "muted",
};
const STATUS_LABEL: Record<string, string> = {
  done: "analyzed",
  pending: "analyzing…",
  failed: "analysis failed",
  unsupported: "not analyzable",
  none: "not analyzed",
};

function asText(v?: string | string[]): string {
  if (Array.isArray(v)) return v.join(" · ");
  return v || "";
}

function MediaTile({ item, onChange }: { item: MediaAsset; onChange: () => void }) {
  const [notes, setNotes] = useState(item.notes);
  const [savedAt, setSavedAt] = useState<number>(0);
  const [analyzing, setAnalyzing] = useState(false);
  const isUpload = item.source_type === "upload";
  const fp = item.analysis?.fingerprint;

  // Keep notes in sync when perception fills them in.
  useEffect(() => {
    setNotes(item.notes);
  }, [item.notes]);

  async function saveNotes() {
    await api.updateMedia(item.id, { notes });
    setSavedAt(Date.now());
  }
  async function analyze() {
    setAnalyzing(true);
    try {
      await api.analyzeMedia(item.id);
      onChange();
    } catch {
      /* surfaced via status on reload */
    } finally {
      setAnalyzing(false);
    }
  }
  async function del() {
    if (!confirm("Delete this asset?")) return;
    await api.deleteMedia(item.id);
    onChange();
  }

  const pending = item.analysis_status === "pending" || analyzing;

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card flex flex-col">
      <div className="aspect-video bg-background flex items-center justify-center">
        {isUpload ? (
          <video src={mediaUrl(item.uri)} controls className="w-full h-full object-contain" />
        ) : (
          <a
            href={item.uri}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[12px] text-primary px-3 text-center break-all hover:underline"
          >
            {item.uri} ↗
          </a>
        )}
      </div>
      <div className="p-3 flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] font-semibold truncate">{item.title || "untitled"}</span>
          <Badge tone={STATUS_TONE[item.analysis_status] || "muted"}>
            {pending ? "analyzing…" : STATUS_LABEL[item.analysis_status]}
          </Badge>
        </div>

        {fp && (
          <div className="rounded-md bg-background border border-border p-2 text-[11px] leading-relaxed flex flex-col gap-1">
            <div className="uppercase tracking-[.4px] text-muted-foreground">What it sees</div>
            {fp.hook && <div><b>Hook:</b> {asText(fp.hook)}</div>}
            {fp.structure && <div><b>Structure:</b> {asText(fp.structure)}</div>}
            {fp.pacing && <div><b>Pacing:</b> {asText(fp.pacing)}</div>}
            {fp.captions && <div><b>Captions:</b> {asText(fp.captions)}</div>}
            {fp.visual_style && <div><b>Look:</b> {asText(fp.visual_style)}</div>}
            {fp.replication_tips && (
              <div className="text-primary"><b>Replicate:</b> {asText(fp.replication_tips)}</div>
            )}
          </div>
        )}

        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={saveNotes}
          placeholder="Style notes — what to replicate (auto-filled by analysis; edit freely)…"
          className="w-full bg-background border border-input rounded-md px-2 py-1.5 text-[12px] resize-y min-h-[52px] outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{savedAt ? "notes saved" : item.tags.join(" · ")}</span>
          <div className="flex items-center gap-3">
            {isUpload && (
              <button onClick={analyze} disabled={pending} className="hover:text-primary disabled:opacity-50">
                {item.analyzed ? "re-analyze" : "analyze"}
              </button>
            )}
            <button onClick={del} className="hover:text-destructive">
              delete
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
