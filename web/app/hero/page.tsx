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

import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type MediaAsset } from "@/lib/api";
import {
  Button, Card, CardTitle, Spinner, PageHeader,
} from "@/components/ui";

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
        title="Hero Library"
        sub="Upload photos and videos of the brand's hero. The system describes the hero from the photos and uses that description in cinematic image prompts so the same recurring character shows up across story reels — not 12 random AI people."
      />

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
