"use client";

/**
 * Long Form Cutter — upload a podcast / interview / long-form video,
 * the system extracts audio, transcribes with word timestamps, and
 * an LLM picks 3-5 standalone 30-45s reel candidates. The user picks
 * which candidates to render; each render runs the engaging-avatar
 * pipeline on the cut clip (real footage + B-roll inserts + captions
 * + music) and lands in the approval queue.
 *
 * State machine on the source row:
 *   uploading → transcribing → analyzing → ready / failed
 * Page polls every 5s while any source is mid-flight.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, mediaUrl, type LongSource, type ReelCandidate,
} from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Spinner, Badge, PageHeader,
} from "@/components/ui";
import { Toast } from "@/components/toast";

type DriveVideo = {
  id: string; name: string;
  mimeType?: string; size?: string; modifiedTime?: string;
};

const STATUS_TONE: Record<string, "muted" | "accent" | "ok" | "destructive"> = {
  uploading: "muted",
  transcribing: "accent",
  analyzing: "accent",
  ready: "ok",
  failed: "destructive",
};

const STATUS_LABEL: Record<string, string> = {
  uploading: "uploading",
  transcribing: "transcribing (Whisper)",
  analyzing: "analyzing (LLM picks candidates)",
  ready: "ready",
  failed: "failed",
};

function fmtTime(s: number): string {
  if (!s || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function fmtRange(start: number, end: number): string {
  return `${fmtTime(start)} – ${fmtTime(end)} · ${(end - start).toFixed(1)}s`;
}

export default function LongFormPage() {
  const [sources, setSources] = useState<LongSource[]>([]);
  const [selected, setSelected] = useState<(LongSource & { candidates: ReelCandidate[] }) | null>(null);
  const [busy, setBusy] = useState(false);
  // Two separate error slots so a stale upload failure doesn't appear
  // to be a Drive-import failure (and vice versa). Same source-load
  // error covers both since they share the source-list refresh.
  const [uploadErr, setUploadErr] = useState("");
  const [importErr, setImportErr] = useState("");
  const [err, setErr] = useState("");      // for source-load / render / dismiss
  const [title, setTitle] = useState("");
  const [driveUrl, setDriveUrl] = useState("");
  const [importing, setImporting] = useState(false);
  // Folder browser state — show tiles from a Drive folder so the user
  // can click-import instead of pasting URLs. Default folder comes
  // from the backend (settings.google_drive_folder_id).
  const [browseUrl, setBrowseUrl] = useState("");
  const [browseFolderId, setBrowseFolderId] = useState("");
  const [defaultFolderId, setDefaultFolderId] = useState("");
  const [browseVideos, setBrowseVideos] = useState<DriveVideo[]>([]);
  const [browsing, setBrowsing] = useState(false);
  const [browseErr, setBrowseErr] = useState("");
  const [importingId, setImportingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [renderingId, setRenderingId] = useState<string | null>(null);
  // Caption style for the next render — '' means auto-pick. Loaded
  // from /video/caption-styles so the dropdown stays in sync with
  // whatever presets the backend ships.
  const [captionStyle, setCaptionStyle] = useState("");
  const [captionStyles, setCaptionStyles] = useState<
    { name: string; label: string; description: string }[]
  >([]);
  const [renderingWhole, setRenderingWhole] = useState(false);
  // B-roll engine for the next render — '' = system default. Runway is
  // the engine with keys configured today; if the chosen engine's keys
  // are missing, inserts keep their stills (the render never fails).
  const [brollEngine, setBrollEngine] = useState("runway");
  // B-roll pacing — how long cutaways hold while James keeps talking.
  const [brollPacing, setBrollPacing] = useState("illustrative");
  const [toast, setToast] = useState<{ message: string; href?: string; hrefLabel?: string } | null>(null);
  // Tick counter that re-renders every second while a selected source is
  // mid-flight, so the "last update Ns ago" indicator next to the status
  // badge stays fresh. Independent from the 5s backend poll above.
  const [, setNowTick] = useState(0);

  async function loadSources() {
    try {
      const r = await api.listLongSources();
      setSources(r.sources);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }

  async function loadSelected(id: string) {
    try {
      const s = await api.getLongSource(id);
      setSelected(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not load source");
    }
  }

  useEffect(() => {
    loadSources();
    // Caption styles list — populates the dropdown above the candidates.
    api.listCaptionStyles()
      .then((r) => setCaptionStyles(r.presets))
      .catch(() => { /* dropdown stays empty, default to (auto-pick) */ });
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // Poll while any source is mid-flight, OR while the selected
  // source is mid-flight. Keeps the table + the detail pane fresh.
  useEffect(() => {
    const midflight = sources.some(
      (s) => s.status !== "ready" && s.status !== "failed",
    ) || (selected && selected.status !== "ready" && selected.status !== "failed");
    if (!midflight) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      await loadSources();
      if (selected) await loadSelected(selected.id);
    }, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, selected?.id, selected?.status]);

  // Re-render every second while the selected source is mid-flight so
  // the "last update Ns ago" hint stays current. Independent of the
  // backend poll — only forces a render of the relative-time text.
  useEffect(() => {
    const midflight =
      selected?.status === "uploading" ||
      selected?.status === "transcribing" ||
      selected?.status === "analyzing";
    if (!midflight) return;
    const t = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [selected?.status, selected?.id]);

  function secondsSince(iso: string): number {
    const t = new Date(iso).getTime();
    if (!t || Number.isNaN(t)) return 0;
    return Math.max(0, Math.floor((Date.now() - t) / 1000));
  }

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true); setUploadErr(""); setImportErr("");
    try {
      const src = await api.uploadLongSource(files[0], title.trim());
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
      await loadSources();
      await loadSelected(src.id);
    } catch (e) {
      setUploadErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDriveImport() {
    const url = driveUrl.trim();
    if (!url) return;
    setImporting(true); setImportErr(""); setUploadErr("");
    try {
      const src = await api.importLongSourceFromDrive(url, title.trim());
      setDriveUrl("");
      setTitle("");
      await loadSources();
      await loadSelected(src.id);
    } catch (e) {
      setImportErr(e instanceof Error ? e.message : "Drive import failed");
    } finally {
      setImporting(false);
    }
  }

  async function browseFolder(folderArg = "") {
    setBrowsing(true); setBrowseErr("");
    try {
      const r = await api.browseDriveFolder(folderArg);
      setBrowseVideos(r.videos);
      setBrowseFolderId(r.folder_id);
      if (r.default_folder_id) setDefaultFolderId(r.default_folder_id);
    } catch (e) {
      setBrowseErr(e instanceof Error ? e.message : "could not browse Drive");
    } finally {
      setBrowsing(false);
    }
  }

  // On first load, fetch the default folder's contents so the user
  // sees their videos immediately without clicking anything.
  useEffect(() => {
    browseFolder("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function importFromTile(v: DriveVideo) {
    setImportingId(v.id);
    setImportErr("");
    try {
      const src = await api.importLongSourceFromDriveId(v.id, v.name);
      await loadSources();
      await loadSelected(src.id);
    } catch (e) {
      setImportErr(e instanceof Error ? e.message : "import failed");
    } finally {
      setImportingId(null);
    }
  }

  async function reanalyze(id: string) {
    setBusy(true); setErr("");
    try {
      await api.reanalyzeLongSource(id);
      await loadSources();
      await loadSelected(id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "reanalyze failed");
    } finally {
      setBusy(false);
    }
  }

  async function renderCandidate(c: ReelCandidate) {
    setRenderingId(c.id);
    setErr("");
    try {
      const prod = await api.renderLongCandidate(c.id, {
        caption_style: captionStyle,
        video_engine: brollEngine,
        broll_pacing: brollPacing,
      });
      // Optimistically link production_id locally
      if (selected) {
        setSelected({
          ...selected,
          candidates: selected.candidates.map((x) =>
            x.id === c.id ? { ...x, production_id: prod.id } : x,
          ),
        });
      }
      setToast({
        message: "Reel queued — rendering in the background.",
        href: "/queue",
        hrefLabel: "Approval Queue",
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "render failed");
    } finally {
      setRenderingId(null);
    }
  }

  // Render the ENTIRE source as one reel — for short talking clips
  // where the candidate picker isn't useful. Uses the same caption
  // style the user selected for candidate rendering.
  async function renderWholeSource() {
    if (!selected) return;
    setRenderingWhole(true);
    setErr("");
    try {
      await api.renderLongSourceWhole(selected.id, {
        caption_style: captionStyle,
        video_engine: brollEngine,
        broll_pacing: brollPacing,
      });
      // Production goes straight to the queue; user can poll there.
      await loadSelected(selected.id);
      setToast({
        message: "Reel queued — rendering in the background.",
        href: "/queue",
        hrefLabel: "Approval Queue",
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "render-whole failed");
    } finally {
      setRenderingWhole(false);
    }
  }

  async function dismissCandidate(c: ReelCandidate) {
    try {
      await api.dismissLongCandidate(c.id);
      if (selected) {
        setSelected({
          ...selected,
          candidates: selected.candidates.filter((x) => x.id !== c.id),
        });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "dismiss failed");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {toast && (
        <Toast
          message={toast.message}
          href={toast.href}
          hrefLabel={toast.hrefLabel}
          onClose={() => setToast(null)}
        />
      )}
      <PageHeader
        title="Long Form Cutter"
        sub="Upload a podcast / interview / long-form video. The system extracts audio, transcribes with word timestamps, and the LLM finds 3-5 standalone 30-45s windows that work as Reels. Click Render on the ones worth shipping — each runs the engaging-avatar pipeline (real footage + B-roll inserts + captions + music) and lands in the approval queue."
      />

      <Card>
        <CardTitle>Add a long source</CardTitle>
        <p className="text-[12px] text-muted-foreground mb-3">
          50-60 min files OK. Audio is extracted at low bitrate (mono
          16 kHz 32 kbps) so 60 min fits Whisper's 25 MB cap; longer
          sources get auto-chunked. Drive URLs are downloaded directly
          via the service account — the file must be shared with the
          service-account email (configured in Settings).
        </p>
        <Input
          placeholder="Title (optional, applied to both upload + Drive paths)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mb-3"
        />

        <div className="text-[11px] uppercase tracking-[1px] text-muted-foreground mt-2 mb-1">
          From Google Drive
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="Paste sharable Drive URL (file/d/…/view or open?id=…)"
            value={driveUrl}
            onChange={(e) => setDriveUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onDriveImport(); }}
            disabled={importing}
            className="flex-1"
          />
          <Button
            onClick={onDriveImport}
            disabled={importing || !driveUrl.trim()}
            className="text-[12px] !px-3"
          >
            {importing ? <Spinner /> : "Import"}
          </Button>
        </div>
        {importing && (
          <p className="text-[12px] mt-2">
            <Spinner /> downloading from Drive…
          </p>
        )}
        {importErr && (
          <p className="text-[12px] mt-2 text-destructive">✗ {importErr}</p>
        )}

        <div className="text-[11px] uppercase tracking-[1px] text-muted-foreground mt-4 mb-1">
          Or upload a file
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="video/*"
          onChange={(e) => onUpload(e.target.files)}
          disabled={busy}
          className="block w-full text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
        />
        {busy && (
          <p className="text-[12px] mt-2"><Spinner /> uploading…</p>
        )}
        {uploadErr && (
          <p className="text-[12px] mt-2 text-destructive">✗ {uploadErr}</p>
        )}
        {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-2">
          <CardTitle>Pick from Drive folder</CardTitle>
          <Button
            variant="secondary"
            onClick={() => browseFolder(browseUrl.trim() || "")}
            disabled={browsing}
            className="text-[12px] !px-3 !py-1"
          >
            {browsing ? <Spinner /> : "↻ Refresh"}
          </Button>
        </div>
        <p className="text-[12px] text-muted-foreground mb-3">
          Click a tile to import — no URL pasting, no lowercase-l /
          capital-I typo risk. Default folder pulls from
          GOOGLE_DRIVE_FOLDER_ID in Settings ({defaultFolderId
            ? defaultFolderId.slice(0, 8) + "…"
            : "not set"}). To browse a different folder,
          paste its URL below.
        </p>
        <div className="flex gap-2 mb-3">
          <Input
            placeholder="(optional) Drive folder URL — drive.google.com/drive/folders/…"
            value={browseUrl}
            onChange={(e) => setBrowseUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") browseFolder(browseUrl.trim()); }}
            disabled={browsing}
            className="flex-1"
          />
          <Button
            onClick={() => browseFolder(browseUrl.trim())}
            disabled={browsing}
            className="text-[12px] !px-3"
          >
            Browse
          </Button>
        </div>
        {browseErr && (
          <p className="text-[12px] text-destructive mb-2">✗ {browseErr}</p>
        )}
        {browseVideos.length === 0 && !browsing && (
          <p className="text-[12px] text-muted-foreground">
            No videos in this folder yet (or it isn&apos;t shared with the
            service account).
          </p>
        )}
        {browseVideos.length > 0 && (
          <div className="grid grid-cols-2 gap-2 mt-2">
            {browseVideos.map((v) => {
              const isImporting = importingId === v.id;
              return (
                <button
                  key={v.id}
                  onClick={() => importFromTile(v)}
                  disabled={isImporting || !!importingId}
                  className="text-left border border-border rounded-md p-3 hover:border-primary transition-colors disabled:opacity-60 disabled:cursor-not-allowed bg-background"
                >
                  <div className="flex items-center gap-2 mb-1">
                    {isImporting ? <Spinner /> : <span className="text-[11px]">▶</span>}
                    <span className="text-[12px] font-medium truncate">{v.name}</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {v.modifiedTime
                      ? new Date(v.modifiedTime).toLocaleDateString()
                      : ""}
                    {v.size ? ` · ${(parseInt(v.size, 10) / (1024 * 1024)).toFixed(0)} MB` : ""}
                  </div>
                  <div className="text-[10px] text-muted-foreground font-mono mt-1 truncate">
                    {v.id}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Card>

      <Card>
        <CardTitle>Sources ({sources.length})</CardTitle>
        {sources.length === 0 ? (
          <p className="text-[12px] text-muted-foreground mt-2">
            Nothing uploaded yet. Drop a video above to start.
          </p>
        ) : (
          <div className="flex flex-col gap-2 mt-2">
            {sources.map((s) => (
              <div
                key={s.id}
                onClick={() => loadSelected(s.id)}
                className={`flex items-center gap-2 text-[12px] border rounded-md p-2 cursor-pointer transition-colors ${
                  selected?.id === s.id
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/60"
                }`}
              >
                <Badge tone={STATUS_TONE[s.status] || "muted"}>
                  {STATUS_LABEL[s.status] || s.status}
                </Badge>
                <span className="flex-1 truncate">{s.title || s.id.slice(0, 8)}</span>
                <span className="text-muted-foreground text-[11px]">
                  {fmtTime(s.duration_s)}
                </span>
                <span className="text-muted-foreground text-[11px]">
                  {new Date(s.created_at).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {selected && (
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>{selected.title || "Source"}</CardTitle>
            <Button
              variant="secondary"
              onClick={() => reanalyze(selected.id)}
              disabled={busy || selected.status === "transcribing" || selected.status === "analyzing"}
              className="text-[12px] !px-3 !py-1"
            >
              Re-analyze
            </Button>
          </div>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <Badge tone={STATUS_TONE[selected.status] || "muted"}>
              {STATUS_LABEL[selected.status] || selected.status}
            </Badge>
            {(selected.status === "uploading" ||
              selected.status === "transcribing" ||
              selected.status === "analyzing") && (
              <span className="text-[11px] text-muted-foreground">
                · last update {secondsSince(selected.updated_at)}s ago
              </span>
            )}
            <span className="text-[12px] text-muted-foreground">
              {fmtTime(selected.duration_s)} duration
            </span>
            <a
              href={mediaUrl(selected.source_url)}
              target="_blank" rel="noopener noreferrer"
              className="text-[12px] text-primary hover:underline ml-auto"
            >
              Open source ↗
            </a>
          </div>
          {selected.error && (
            <p className="text-destructive text-[12px] mt-2">✗ {selected.error}</p>
          )}

          {/* Caption style picker — applies to BOTH the candidate
             render buttons below AND the "Render whole" button. Blank
             value = AI auto-picks based on the script. */}
          {selected.status === "ready" && (
            <div className="mt-4 flex items-center gap-2 flex-wrap p-3 rounded-md bg-muted/40 border border-border">
              <span className="text-[12px] text-muted-foreground">
                Caption style
              </span>
              <select
                value={captionStyle}
                onChange={(e) => setCaptionStyle(e.target.value)}
                className="text-[12px] px-2 py-1 rounded border border-border bg-background"
              >
                <option value="">Auto-pick (AI chooses)</option>
                {captionStyles.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.label}
                  </option>
                ))}
              </select>
              {captionStyle && captionStyles.find((p) => p.name === captionStyle) && (
                <span className="text-[11px] text-muted-foreground">
                  — {captionStyles.find((p) => p.name === captionStyle)?.description}
                </span>
              )}
              <span className="text-[12px] text-muted-foreground ml-2">
                B-roll engine
              </span>
              <select
                value={brollEngine}
                onChange={(e) => setBrollEngine(e.target.value)}
                className="text-[12px] px-2 py-1 rounded border border-border bg-background"
                title="Which engine animates the B-roll cutaways. If the chosen engine's keys/credits are missing, those inserts keep their still image — the render never fails."
              >
                <option value="runway">Runway</option>
                <option value="higgsfield">Higgsfield</option>
                <option value="">System default</option>
              </select>
              <span className="text-[12px] text-muted-foreground ml-2">
                Pacing
              </span>
              <select
                value={brollPacing}
                onChange={(e) => setBrollPacing(e.target.value)}
                className="text-[12px] px-2 py-1 rounded border border-border bg-background"
                title="How long each B-roll cutaway holds while James keeps talking. Illustrative (4-5s holds, varied gaps) is the talking-head standard; Punchy is rapid 1.5-2.5s flashes; Reflective is slow 6-8s holds."
              >
                <option value="illustrative">Illustrative (4-5s holds)</option>
                <option value="punchy">Punchy (1.5-2.5s flashes)</option>
                <option value="reflective">Reflective (6-8s holds)</option>
              </select>
              <Button
                onClick={renderWholeSource}
                disabled={renderingWhole}
                variant="secondary"
                className="text-[12px] !px-3 !py-1 ml-auto"
                title="Skip the LLM picker and render the entire clip as one reel — for short talking clips where the whole thing IS the reel."
              >
                {renderingWhole ? <Spinner /> : "Render whole as reel"}
              </Button>
            </div>
          )}

          {selected.status === "ready" && selected.candidates.length === 0 && (
            <p className="text-[12px] text-muted-foreground mt-3">
              No candidates returned by the LLM. Try Re-analyze, or use
              "Render whole as reel" above to skip candidate selection
              and treat the whole clip as a single reel.
            </p>
          )}

          {selected.candidates.length > 0 && (
            <div className="mt-4 flex flex-col gap-3">
              {selected.candidates.map((c) => (
                <div
                  key={c.id}
                  className="border border-border rounded-md p-3 bg-background"
                >
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <Badge tone={c.score >= 8 ? "ok" : c.score >= 6 ? "accent" : "muted"}>
                      score {c.score}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground">
                      {fmtRange(c.start_s, c.end_s)}
                    </span>
                    <div className="ml-auto flex gap-2">
                      {c.production_id ? (
                        <Link
                          href="/queue"
                          className="text-[12px] text-primary hover:underline"
                        >
                          rendering →
                        </Link>
                      ) : (
                        <Button
                          onClick={() => renderCandidate(c)}
                          disabled={renderingId === c.id}
                          className="text-[12px] !px-3 !py-1"
                        >
                          {renderingId === c.id ? <Spinner /> : "Render reel"}
                        </Button>
                      )}
                      <button
                        onClick={() => dismissCandidate(c)}
                        className="text-[12px] text-muted-foreground hover:text-destructive"
                        title="Dismiss this candidate"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                  <p className="text-[13px] font-medium leading-snug">
                    “{c.hook_quote}”
                  </p>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {c.summary}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
