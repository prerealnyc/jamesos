"use client";

/**
 * Hero Library — photos + videos of the brand's hero.
 *
 * Uploads are stored as media_assets with role=hero_photo / hero_video.
 * On first upload, GPT-4o vision writes a 2-3 sentence visual
 * description of the recurring person across the photos. That
 * description is then injected into the cinematic image-prompt LLM so
 * subsequent story renders can place consistent hero shots (1-2 per
 * reel, in Act 1 for stakes or Act 3 for the moment of conviction).
 *
 * The description is what the user uses to verify the system "sees"
 * the hero correctly — and to manually re-run via the refresh button
 * after uploading more photos.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type MediaAsset, type Production } from "@/lib/api";
import {
  Button, Card, CardTitle, Spinner, PageHeader,
} from "@/components/ui";
import { MediaTabs } from "@/components/media-tabs";

type Bucket = "hero_photo" | "hero_video";

export default function HeroLibraryPage() {
  const [bucket, setBucket] = useState<Bucket>("hero_photo");
  const [photos, setPhotos] = useState<MediaAsset[]>([]);
  const [videos, setVideos] = useState<MediaAsset[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ctx, setCtx] = useState<{
    description: string;
    photo_count: number;
    photo_urls: string[];
  } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      const [p, v, c] = await Promise.all([
        api.listMedia("hero_photo"),
        api.listMedia("hero_video"),
        api.getHeroContext(),
      ]);
      setPhotos(p.media);
      setVideos(v.media);
      setCtx(c);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        await api.uploadMedia(f, bucket, { title: f.name });
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function refreshDescription() {
    setRefreshing(true);
    setErr(null);
    try {
      const c = await api.refreshHeroContext();
      setCtx(c);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  const acceptForBucket = bucket === "hero_photo"
    ? "image/*"
    : "video/*";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Media Library"
        sub="Photos and videos of the brand's hero. The system describes the hero from the photos and uses that description in cinematic image prompts so the same recurring character shows up across story reels — not 12 random AI people."
      />
      <MediaTabs />

      <Card>
        <CardTitle>Upload</CardTitle>
        <div className="flex gap-2 mb-3 flex-wrap">
          {(["hero_photo", "hero_video"] as const).map((b) => (
            <button
              key={b}
              type="button"
              onClick={() => setBucket(b)}
              className={`text-[12px] px-3 py-1.5 rounded-full border transition-colors ${
                bucket === b
                  ? "border-primary text-primary bg-primary/10"
                  : "border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
              }`}
            >
              {b === "hero_photo" ? `Photos (${photos.length})` : `Videos (${videos.length})`}
            </button>
          ))}
        </div>
        <p className="text-[12px] text-muted-foreground mb-3">
          {bucket === "hero_photo" ? (
            <>
              Upload 3-8 photos that show the hero clearly — face, build,
              dress, signature look. Different angles + lighting help.
              Used by GPT-4o vision to write the character description.
            </>
          ) : (
            <>
              Upload short clips of the hero on camera. Reserved for a
              future avatar-swap path; not used by the current cinematic
              image generator.
            </>
          )}
        </p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={acceptForBucket}
          onChange={(e) => onUpload(e.target.files)}
          className="block w-full text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
        />
        {busy && <p className="text-[12px] mt-2"><Spinner /> uploading…</p>}
        {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <CardTitle>Hero description (used by cinematic prompts)</CardTitle>
          <Button
            variant="secondary"
            onClick={refreshDescription}
            disabled={refreshing || photos.length === 0}
            className="text-[12px] !px-3 !py-1"
          >
            {refreshing ? <Spinner /> : "Re-describe"}
          </Button>
        </div>
        {photos.length === 0 ? (
          <p className="text-[12px] text-muted-foreground mt-2">
            Upload hero photos above. Without them, story-mode image
            prompts can&apos;t reference the brand&apos;s hero and will
            generate generic characters instead.
          </p>
        ) : !ctx || !ctx.description ? (
          <p className="text-[12px] text-muted-foreground mt-2">
            <Spinner /> Computing description from {photos.length} photo
            {photos.length === 1 ? "" : "s"}…
          </p>
        ) : (
          <div className="mt-2 text-[13px] leading-relaxed border border-border rounded-md p-3 bg-background">
            {ctx.description}
            <p className="text-[10px] text-muted-foreground mt-2">
              Based on {ctx.photo_count} photo{ctx.photo_count === 1 ? "" : "s"}. Cached;
              re-runs on next upload OR when you hit Re-describe.
            </p>
          </div>
        )}
      </Card>

      <HeroCloneCard ready={photos.length > 0} />

      <HiggsfieldSoulsCard />

      {photos.length > 0 && (
        <Card>
          <CardTitle>Photos ({photos.length})</CardTitle>
          <div className="grid grid-cols-3 gap-2 mt-2">
            {photos.map((p) => (
              <div
                key={p.id}
                className="border border-border rounded-md overflow-hidden bg-background"
              >
                <img
                  src={mediaUrl(p.uri)}
                  alt={p.title}
                  className="w-full aspect-square object-cover"
                />
                <div className="p-1.5 text-[10px] text-muted-foreground truncate">
                  {p.title}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {videos.length > 0 && (
        <Card>
          <CardTitle>Videos ({videos.length})</CardTitle>
          <div className="grid grid-cols-3 gap-2 mt-2">
            {videos.map((v) => (
              <div
                key={v.id}
                className="border border-border rounded-md overflow-hidden bg-background"
              >
                <video
                  src={mediaUrl(v.uri)}
                  muted
                  controls
                  className="w-full aspect-video object-cover"
                />
                <div className="p-1.5 text-[10px] text-muted-foreground truncate">
                  {v.title}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function HeroCloneCard({ ready }: { ready: boolean }) {
  const [mode, setMode] = useState<"topic" | "script">("topic");
  const [text, setText] = useState("");
  const [aspect, setAspect] = useState("9:16");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Production | null>(null);

  async function go() {
    if (!text.trim()) {
      setErr("Add a topic or paste a script.");
      return;
    }
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const body = mode === "topic" ? { topic: text.trim() } : { script: text.trim() };
      setResult(await api.heroTalkingVideo({ ...body, aspect }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardTitle>Clone the hero into a talking video</CardTitle>
      <p className="text-[12px] text-muted-foreground mt-1 mb-3">
        Builds a hyper-real still of the hero from the photos above, then animates
        it into a <b>lip-synced talking clip in the brand voice</b> (HeyGen Talking
        Photo). Give it a topic (script written in your voice) or paste a script.
        Lands in the <Link href="/queue" className="underline">Approval Queue</Link>.
      </p>
      {!ready && (
        <p className="text-[12px] text-destructive mb-2">
          Upload hero photos above first — the clone is built from them.
        </p>
      )}
      <div className="flex items-center gap-3 text-[12px] mb-2">
        <button
          onClick={() => setMode("topic")}
          className={mode === "topic" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          Topic → auto-script
        </button>
        <span className="text-muted-foreground">·</span>
        <button
          onClick={() => setMode("script")}
          className={mode === "script" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          Paste a script
        </button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={mode === "topic" ? 2 : 5}
        placeholder={
          mode === "topic"
            ? "What should the hero talk about? e.g. 'why now is the moment to buy Staten Island commercial real estate'"
            : "Paste the exact script the hero should say…"
        }
        className="w-full bg-background border border-input rounded-md px-2 py-1.5 text-[13px] resize-y outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
      <div className="flex flex-wrap items-center gap-2 mt-2">
        <select
          value={aspect}
          onChange={(e) => setAspect(e.target.value)}
          className="bg-background border border-input rounded-md px-2 py-1 text-[12px]"
        >
          {["9:16", "1:1", "16:9"].map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <Button onClick={go} disabled={busy || !ready}>
          {busy ? <span className="flex items-center gap-2"><Spinner /> producing…</span> : "Generate talking clone"}
        </Button>
      </div>
      {err && <p className="text-[12px] mt-2 text-destructive">✗ {err}</p>}
      {result && (
        <div className="mt-2 rounded-md border border-border bg-background p-2.5 text-[12px] flex flex-col gap-1">
          <span className="text-accent font-semibold">✓ Producing “{result.title}” — rendering in the background.</span>
          <Link href="/library" className="text-primary hover:underline">Watch it in Output Library →</Link>
        </div>
      )}
    </Card>
  );
}


// ── Higgsfield Soul IDs ───────────────────────────────────────────────
// Lists the trained characters (custom-references) on the connected
// Higgsfield account, and lets you generate one consistent character image
// from a Soul ID — the building block for a Soul-driven hero (e.g. James).
function HiggsfieldSoulsCard() {
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [configured, setConfigured] = useState(true);
  const [souls, setSouls] = useState<
    { id: string; name: string; status: string; thumbnail: string; created_at: string }[]
  >([]);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  // The Soul ID currently wired into B-roll renders (settings.higgsfield_soul_id).
  const [activeId, setActiveId] = useState<string>("");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // True when the Soul ID is pinned by the HIGGSFIELD_SOUL_ID env var — it
  // can't be turned off from the UI (clearing reverts to the env value).
  const [envPinned, setEnvPinned] = useState(false);

  function applyCred(c: { fields: { name: string; masked: string; configured: boolean; source: string }[] }) {
    const f = c.fields.find((x) => x.name === "higgsfield_soul_id");
    setActiveId(f?.masked || "");
    setEnvPinned(!!f && f.configured && f.source === "env");
  }

  // Read which Soul (if any) is the active hero on mount.
  useEffect(() => {
    api.getCredentials().then(applyCred).catch(() => {});
  }, []);

  async function setActive(id: string) {
    setSaving(id || "__clear__");
    setSaveErr(null);
    try {
      applyCred(await api.setCredentials({ higgsfield_soul_id: id }));
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "failed to save the active Soul");
    } finally {
      setSaving(null);
    }
  }

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.listHiggsfieldSouls();
      setConfigured(r.configured);
      setSouls(r.souls || []);
      setErr(r.error || null);
      setLoaded(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to reach Higgsfield");
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  }

  // Train a brand-new Soul from the uploaded hero photo library.
  async function trainSoul() {
    setTraining(true); setTrainMsg(null);
    try {
      const r = await api.trainHiggsfieldSoul({ name: "James" });
      if (r.ok) {
        setTrainMsg(r.note || `Training started on ${r.trained_on ?? "your"} photos.`);
        setTimeout(() => { load(); }, 2000); // surface the new Soul once it appears
      } else {
        setTrainMsg("✗ " + (r.error || "training did not start"));
      }
    } catch (e) {
      setTrainMsg("✗ " + (e instanceof Error ? e.message : "request failed"));
    } finally {
      setTraining(false);
    }
  }

  return (
    <Card>
      <CardTitle>Higgsfield Soul IDs (trained characters)</CardTitle>
      <p className="text-[12px] text-muted-foreground mt-1 mb-2">
        Soul IDs are characters you trained on Higgsfield — a reusable, consistent
        person across every generation. This reads them straight from your connected
        account. Pick <b>“Use for James”</b> to drive every hero B-roll shot through that Soul.
      </p>
      {activeId
        ? <p className="text-[12px] mb-2 rounded-md border border-border bg-secondary/40 px-3 py-2">
            ✓ Active hero Soul: <span className="font-mono">{activeId}</span> — B-roll shots about
            James now render through it.{" "}
            {envPinned
              ? <span className="block mt-1 text-muted-foreground">
                  Pinned by HIGGSFIELD_SOUL_ID in the environment — clear it there to disable.
                </span>
              : <button onClick={() => setActive("")} disabled={saving !== null}
                  className="text-primary hover:underline">turn off</button>}
          </p>
        : <p className="text-[12px] mb-2 text-muted-foreground">
            No active hero Soul yet — hero B-roll falls back to your uploaded photos.
          </p>}
      {saveErr && <p className="text-[12px] mb-2 text-destructive">✗ {saveErr}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={load} disabled={loading}>
          {loading ? <span className="flex items-center gap-2"><Spinner /> checking your account…</span>
            : (loaded ? "↻ Refresh Soul IDs" : "Find my Soul IDs")}
        </Button>
        <Button variant="secondary" onClick={trainSoul} disabled={training}
          title="Train a new Soul on your uploaded hero photos (needs 5–20 photos and a paid Higgsfield plan).">
          {training ? <span className="flex items-center gap-2"><Spinner /> training…</span>
            : "Train a Soul from my hero photos"}
        </Button>
      </div>
      {trainMsg && (
        <p className={`text-[12px] mt-2 ${trainMsg.startsWith("✗") ? "text-destructive" : "text-muted-foreground"}`}>
          {trainMsg}
        </p>
      )}

      {loaded && !configured && (
        <p className="text-[12px] mt-2 text-destructive">
          ✗ Higgsfield API key + secret aren’t set. Add them in Settings → API connections.
        </p>
      )}
      {loaded && configured && err && (
        <p className="text-[12px] mt-2 text-destructive break-words">✗ {err}</p>
      )}
      {loaded && configured && !err && souls.length === 0 && (
        <p className="text-[12px] mt-2 text-muted-foreground">
          No Soul IDs found on this account yet. Train one in the Higgsfield app
          (cloud.higgsfield.ai) and it’ll show up here.
        </p>
      )}

      {souls.length > 0 && (
        <div className="flex flex-col gap-1.5 mt-3">
          {souls.map((s) => (
            <div
              key={s.id}
              className="flex items-center gap-3 border border-border rounded-md px-3 py-2 bg-background"
            >
              {s.thumbnail
                ? <img src={s.thumbnail} alt={s.name} className="w-10 h-10 rounded object-cover" />
                : <div className="w-10 h-10 rounded bg-secondary" />}
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium truncate">{s.name || "(unnamed)"}</div>
                <div className="text-[11px] text-muted-foreground font-mono truncate">{s.id}</div>
                {s.status && <div className="text-[10px] uppercase tracking-wide opacity-60">{s.status}</div>}
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                {activeId === s.id
                  ? <span className="text-[11px] font-medium text-primary">✓ Active hero</span>
                  : <button
                      onClick={() => setActive(s.id)}
                      disabled={saving !== null}
                      className="text-[11px] text-primary hover:underline disabled:opacity-50"
                    >
                      {saving === s.id ? "saving…" : "Use for James"}
                    </button>}
                <button
                  onClick={() => { navigator.clipboard?.writeText(s.id); setCopied(s.id); }}
                  className="text-[11px] text-muted-foreground hover:underline"
                >
                  {copied === s.id ? "copied ✓" : "copy ID"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
