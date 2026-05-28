"use client";

/**
 * Post Images — AI hero images for LinkedIn / Twitter / Instagram posts.
 *
 * One-click generation via OpenAI gpt-image-1, tuned for the editorial
 * "uncluttered, single focal point, no text overlays" aesthetic. The
 * library below shows every image the tenant has generated, with copy-
 * URL and remove affordances. Images are persisted to the same storage
 * backend the rest of the media library uses (Supabase if configured,
 * local-disk fallback), so URLs work across the app.
 */

import { useEffect, useState } from "react";
import {
  api, mediaUrl, type MediaAsset,
} from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";

const PLATFORM_HELP: Record<string, string> = {
  linkedin: "16:9 hero — desktop-feed dominant, works for newsletters",
  twitter:  "16:9 — Twitter/X card crop is more forgiving than IG",
  instagram: "1:1 square — feed default",
  facebook: "16:9 — Facebook post hero",
};

export default function PostImagesPage() {
  const [topic, setTopic] = useState("");
  const [brief, setBrief] = useState("");
  const [platform, setPlatform] = useState<"linkedin"|"twitter"|"instagram"|"facebook">("linkedin");
  const [aspect, setAspect] = useState<""|"1:1"|"16:9"|"9:16">("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [latest, setLatest] = useState<MediaAsset | null>(null);
  const [library, setLibrary] = useState<MediaAsset[]>([]);

  async function loadLibrary() {
    try {
      const r = await api.listMedia("post_image");
      setLibrary(r.media || []);
    } catch (e) {
      // Honest empty state — don't fake-clear the UI on a fetch fail
      setErr((prev) => prev || (e instanceof Error ? e.message : "could not load library"));
    }
  }
  useEffect(() => { loadLibrary(); }, []);

  async function generate() {
    if (!topic.trim()) { setErr("Topic is required."); return; }
    setBusy(true); setErr(""); setLatest(null);
    try {
      const asset = await api.generatePostImage({
        topic: topic.trim(),
        platform,
        brief: brief.trim(),
        aspect,
      });
      setLatest(asset);
      // Optimistic prepend; reload to sync with server ordering
      setLibrary((cur) => [asset, ...cur]);
      loadLibrary();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "image generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this image? This cannot be undone.")) return;
    try {
      await api.deleteMedia(id);
      setLibrary((cur) => cur.filter((m) => m.id !== id));
      if (latest?.id === id) setLatest(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "delete failed");
    }
  }

  async function copyUrl(uri: string) {
    try { await navigator.clipboard.writeText(mediaUrl(uri)); } catch {}
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Post Images"
        sub="Editorial AI hero images for LinkedIn / Twitter / Instagram. One topic, one clean image — no text overlays, no busy collages, no logos. Lands in the library so any post can reuse it."
      />

      <Card>
        <CardTitle>1. Describe the post</CardTitle>
        <Label>Topic (the subject of the image)</Label>
        <Input
          placeholder="e.g. why Staten Island commercial RE is underpriced"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <Label>Brief / context (optional — the angle or a fact to evoke)</Label>
        <Textarea
          rows={3}
          placeholder="Optional: paste the draft post body, or describe the specific angle the image should evoke."
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-3 mt-1">
          <div>
            <Label>Platform</Label>
            <Select
              value={platform}
              onChange={(e) => setPlatform(e.target.value as typeof platform)}
            >
              {(["linkedin","twitter","instagram","facebook"] as const).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </Select>
            <p className="text-[11px] text-muted-foreground mt-1">
              {PLATFORM_HELP[platform]}
            </p>
          </div>
          <div>
            <Label>Aspect (override)</Label>
            <Select
              value={aspect}
              onChange={(e) => setAspect(e.target.value as typeof aspect)}
            >
              <option value="">platform default</option>
              <option value="1:1">1:1 square</option>
              <option value="16:9">16:9 landscape</option>
              <option value="9:16">9:16 portrait</option>
            </Select>
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button onClick={generate} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : "Generate image"}
          </Button>
          <span className="text-[12px] text-muted-foreground">
            gpt-image-1 · editorial style · ~10–25s
          </span>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      {latest && (
        <Card>
          <CardTitle>Latest</CardTitle>
          <ImageRow
            asset={latest}
            onCopy={() => copyUrl(latest.uri)}
            onRemove={() => remove(latest.id)}
          />
        </Card>
      )}

      <Card>
        <div className="flex items-center justify-between">
          <CardTitle>Library</CardTitle>
          <button
            onClick={loadLibrary}
            className="text-[11px] text-muted-foreground hover:text-foreground"
            title="Refresh"
          >
            ↻
          </button>
        </div>
        {library.length === 0 ? (
          <p className="text-[13px] text-muted-foreground mt-2">
            No post images yet. Generate one above.
          </p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-2">
            {library.map((m) => (
              <ImageTile
                key={m.id}
                asset={m}
                onCopy={() => copyUrl(m.uri)}
                onRemove={() => remove(m.id)}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function ImageRow({
  asset, onCopy, onRemove,
}: {
  asset: MediaAsset;
  onCopy: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="grid grid-cols-[260px_1fr] gap-4 mt-2">
      <a href={mediaUrl(asset.uri)} target="_blank" rel="noopener noreferrer">
        <img
          src={mediaUrl(asset.uri)}
          alt={asset.title}
          className="w-full rounded-md bg-background border border-border"
        />
      </a>
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge tone="primary">{asset.platform || "post"}</Badge>
          <span className="text-[13px] font-medium truncate">{asset.title}</span>
        </div>
        {asset.notes && (
          <p className="text-[11px] text-muted-foreground line-clamp-4">
            {asset.notes}
          </p>
        )}
        <div className="flex gap-2 mt-auto">
          <Button variant="secondary" onClick={onCopy}>Copy URL</Button>
          <Button variant="ghost" onClick={onRemove}>Delete</Button>
        </div>
      </div>
    </div>
  );
}

function ImageTile({
  asset, onCopy, onRemove,
}: {
  asset: MediaAsset;
  onCopy: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="border border-border rounded-md overflow-hidden bg-background flex flex-col">
      <a href={mediaUrl(asset.uri)} target="_blank" rel="noopener noreferrer" className="block">
        <img
          src={mediaUrl(asset.uri)}
          alt={asset.title}
          className="w-full aspect-video object-cover"
        />
      </a>
      <div className="p-2 flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge tone="muted">{asset.platform || "post"}</Badge>
          <span className="text-[11px] font-medium truncate flex-1">{asset.title}</span>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={onCopy}
            className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:border-primary hover:text-primary"
          >
            copy url
          </button>
          <button
            onClick={onRemove}
            className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:border-destructive hover:text-destructive ml-auto"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}
