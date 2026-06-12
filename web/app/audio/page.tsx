"use client";

/**
 * Audio Library — music beds + SFX that make videos pop.
 *
 * Music: upload tracks and tag each with a mood (upbeat / calm / dramatic /
 * tension). Renders that want that mood pick from the library (random among
 * matches for variety), falling back to the static settings URLs.
 *
 * SFX: upload short hits and tag the kind (whoosh / hit / riser / pop).
 * Cutaway-based renders automatically drop a whoosh at every B-roll insert
 * start once one exists here — no library sound, no SFX layer.
 */

import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type MediaAsset } from "@/lib/api";
import { Button, Card, CardTitle, Spinner, PageHeader, Badge } from "@/components/ui";
import { MediaTabs } from "@/components/media-tabs";

const MOODS = ["upbeat", "calm", "dramatic", "tension"] as const;
const KINDS = ["whoosh", "hit", "riser", "pop"] as const;

const FREE_SOURCES = [
  { name: "Mixkit", url: "https://mixkit.co/free-stock-music/", what: "Music + SFX — free license, no attribution" },
  { name: "Pixabay Music", url: "https://pixabay.com/music/", what: "Music — free for commercial use, no attribution" },
  { name: "Pixabay SFX", url: "https://pixabay.com/sound-effects/search/whoosh/", what: "Whooshes/hits — free, no attribution" },
  { name: "YouTube Audio Library", url: "https://studio.youtube.com/", what: "Music + SFX (Studio → Audio Library; check per-track terms)" },
  { name: "Freesound", url: "https://freesound.org/search/?q=whoosh", what: "Huge SFX archive — CC0/CC-BY (check each file)" },
  { name: "Incompetech", url: "https://incompetech.com/music/royalty-free/music.html", what: "Kevin MacLeod music — CC-BY (credit required)" },
];

export default function AudioLibraryPage() {
  const [items, setItems] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const [m, s] = await Promise.all([api.listMedia("music"), api.listMedia("sfx")]);
      setItems([...m.media, ...s.media]);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function toggleTag(m: MediaAsset, tag: string, exclusiveSet: readonly string[]) {
    const has = (m.tags || []).includes(tag);
    // Mood/kind tags are exclusive within their set: picking one clears the others.
    const others = (m.tags || []).filter((t) => !exclusiveSet.includes(t));
    const tags = has ? others : [...others, tag];
    try {
      await api.updateMedia(m.id, { tags });
      setItems((all) => all.map((x) => (x.id === m.id ? { ...x, tags } : x)));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "tag update failed");
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteMedia(id);
      setItems((all) => all.filter((m) => m.id !== id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "delete failed");
    }
  }

  const music = items.filter((m) => m.role === "music");
  const sfx = items.filter((m) => m.role === "sfx");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Media Library"
        sub="Music beds + SFX for renders. Tag music with a mood and any render wanting that mood uses it; add a whoosh and every B-roll cutaway gets a transition pop automatically."
      />
      <MediaTabs />

      <Card>
        <CardTitle>Free sources to fill the library</CardTitle>
        <p className="text-[12px] text-muted-foreground mt-1 mb-2">
          Download from these, then upload below. Watch the license column —
          most need no attribution, Incompetech/CC-BY needs a credit line.
        </p>
        <div className="flex flex-col gap-1">
          {FREE_SOURCES.map((s) => (
            <div key={s.name} className="flex items-baseline gap-2 text-[12px]">
              <a href={s.url} target="_blank" rel="noreferrer" className="text-primary hover:underline font-medium shrink-0">
                {s.name} ↗
              </a>
              <span className="text-muted-foreground">{s.what}</span>
            </div>
          ))}
        </div>
      </Card>

      {err && <div className="text-sm text-destructive">{err}</div>}
      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>
      ) : (
        <>
          <AudioSection
            title={`Music beds (${music.length})`}
            role="music"
            hint="Tag each track with ONE mood — that's how renders find it. Untagged tracks are never used."
            tags={MOODS}
            items={music}
            onUploaded={load}
            onToggle={(m, t) => toggleTag(m, t, MOODS)}
            onDelete={remove}
            setErr={setErr}
          />
          <AudioSection
            title={`SFX (${sfx.length})`}
            role="sfx"
            hint="Short hits (≤1s works best). Tag the kind — 'whoosh' is what cutaway transitions use today."
            tags={KINDS}
            items={sfx}
            onUploaded={load}
            onToggle={(m, t) => toggleTag(m, t, KINDS)}
            onDelete={remove}
            setErr={setErr}
          />
        </>
      )}
    </div>
  );
}

function AudioSection({
  title, role, hint, tags, items, onUploaded, onToggle, onDelete, setErr,
}: {
  title: string;
  role: "music" | "sfx";
  hint: string;
  tags: readonly string[];
  items: MediaAsset[];
  onUploaded: () => void;
  onToggle: (m: MediaAsset, tag: string) => void;
  onDelete: (id: string) => void;
  setErr: (e: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        await api.uploadMedia(f, role, { title: f.name });
      }
      onUploaded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <p className="text-[12px] text-muted-foreground mt-1 mb-2">{hint}</p>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept="audio/*"
        onChange={(e) => onUpload(e.target.files)}
        className="block w-full text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
      />
      {busy && <p className="text-[12px] mt-2"><Spinner /> uploading…</p>}

      {items.length > 0 && (
        <div className="flex flex-col gap-2 mt-3">
          {items.map((m) => (
            <div key={m.id} className="border border-border rounded-md px-3 py-2 bg-background flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[12px] font-medium truncate">{m.title || "(untitled)"}</span>
                <button onClick={() => onDelete(m.id)} className="text-[11px] text-muted-foreground hover:text-destructive shrink-0">
                  delete
                </button>
              </div>
              <audio src={mediaUrl(m.uri)} controls preload="none" className="w-full h-8" />
              <div className="flex flex-wrap items-center gap-1.5">
                {tags.map((t) => {
                  const on = (m.tags || []).includes(t);
                  return (
                    <button
                      key={t}
                      onClick={() => onToggle(m, t)}
                      className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                        on
                          ? "border-primary text-primary bg-primary/10 font-semibold"
                          : "border-border text-muted-foreground hover:border-primary/60"
                      }`}
                    >
                      {t}
                    </button>
                  );
                })}
                {!(m.tags || []).some((t) => tags.includes(t)) && (
                  <Badge tone="destructive">untagged — not used</Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
