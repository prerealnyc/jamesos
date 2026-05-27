"use client";

/**
 * Timeline Editor — Creatomate-style stitching UI.
 *
 * Layered on top of the existing produce pipeline: each block on the
 * timeline becomes a "scene" in the Creatomate assembly source, so we
 * reuse every effect we already support (per-scene mute, on-screen text,
 * transitions, logo overlay, SFX URL, music mood). The library aggregates
 * clips from past productions and the reference library so the user can
 * mix-and-match anything that's ever been rendered or uploaded.
 *
 * Important: Creatomate needs a publicly reachable URL — local
 * /media-files/* paths from pre-Storage uploads are flagged not
 * assemblable, with a visible warning rather than a silent failure.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, mediaUrl, type ClipLibraryItem, type Production, type Scene,
} from "@/lib/api";
import {
  Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner, PageHeader,
} from "@/components/ui";

type Block = {
  // local instance id so React keys survive reorder/edits
  uid: string;
  source_url: string;
  source_label: string;
  source_kind: ClipLibraryItem["kind"] | "external";
  duration: number;
  on_screen_text: string;
  transition_in: "cut" | "fade" | "slide";
  mute_native_audio: boolean;
  audio_sfx: string;
  branding_logo: boolean;
  branding_position: "none" | "bottom-right" | "bottom-center" | "top-right";
  // Inspector open/closed — keep the UI compact when stacking many blocks.
  open: boolean;
};

const STAGE_TONE: Record<string, "muted" | "accent" | "ok" | "destructive"> = {
  queued: "muted",
  planning: "accent",
  rendering_clips: "accent",
  assembling: "accent",
  succeeded: "ok",
  failed: "destructive",
};

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `b-${Math.random().toString(36).slice(2)}-${Date.now()}`;

function blockFromLibrary(c: ClipLibraryItem): Block {
  return {
    uid: uid(),
    source_url: c.url,
    source_label: c.label,
    source_kind: c.kind,
    // Reasonable default: use the clip's known duration if we have it,
    // else 5s. The user can tweak per block.
    duration: c.duration && c.duration > 0 ? Math.round(c.duration) : 5,
    on_screen_text: "",
    transition_in: "cut",
    // Reference clips often carry "mute_audio" — preserve the intent.
    mute_native_audio:
      (c.kind === "reference" && Boolean(c.source_meta?.mute_audio)) || false,
    audio_sfx: "",
    branding_logo: false,
    branding_position: "none",
    open: true,
  };
}

function blockFromUrl(url: string): Block {
  return {
    uid: uid(),
    source_url: url.trim(),
    source_label: "Pasted URL",
    source_kind: "external",
    duration: 5,
    on_screen_text: "",
    transition_in: "cut",
    mute_native_audio: false,
    audio_sfx: "",
    branding_logo: false,
    branding_position: "none",
    open: true,
  };
}

/** Pack a Block into the Scene shape the production pipeline already
 *  understands. Critical contract: `url` must start with http so the
 *  pipeline's render-skip branch kicks in and we go straight to assembly. */
function blockToScene(b: Block, index: number, musicMood: string): Scene {
  // Only attach the music mood to the first block — the assembler reads
  // mood from the first scene only and applies it as a single track.
  return {
    index,
    label: b.source_label,
    kind: "broll",
    source: null,
    voiceover: "",
    on_screen_text: b.on_screen_text,
    visual_prompt: "",
    duration: Math.max(0.5, Number(b.duration) || 5),
    url: b.source_url,
    clip_status: "ok",
    audio_music: index === 0 ? musicMood : "none",
    audio_sfx: b.audio_sfx,
    branding_logo: b.branding_logo,
    branding_position: b.branding_position,
    transition_in: b.transition_in,
    // The production pipeline forwards this to the Creatomate source builder
    // (see assembly.py: `if s.get("mute_native_audio"): elem["volume"] = 0`).
    ...(b.mute_native_audio ? { mute_native_audio: true } : {}),
  } as Scene & { mute_native_audio?: boolean };
}

export default function EditorPage() {
  const [title, setTitle] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [aspect, setAspect] = useState("9:16");
  const [musicMood, setMusicMood] =
    useState<"none" | "upbeat" | "calm" | "dramatic" | "tension">("none");

  const [library, setLibrary] = useState<ClipLibraryItem[]>([]);
  const [libFilter, setLibFilter] =
    useState<"all" | "reference" | "production_scene" | "production_final">("all");
  const [libLoading, setLibLoading] = useState(false);
  const [libErr, setLibErr] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");

  const [blocks, setBlocks] = useState<Block[]>([]);

  const [producing, setProducing] = useState(false);
  const [prod, setProd] = useState<Production | null>(null);
  const [err, setErr] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadLibrary() {
    setLibLoading(true);
    setLibErr("");
    try {
      const r = await api.listClipLibrary();
      setLibrary(r.items || []);
    } catch (e) {
      setLibErr(e instanceof Error ? e.message : "could not load clip library");
    } finally {
      setLibLoading(false);
    }
  }
  useEffect(() => {
    loadLibrary();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const filteredLib =
    libFilter === "all" ? library : library.filter((c) => c.kind === libFilter);

  function addFromLibrary(c: ClipLibraryItem) {
    setBlocks((bs) => [...bs, blockFromLibrary(c)]);
  }
  function addFromUrl() {
    const t = pasteUrl.trim();
    if (!t) return;
    setBlocks((bs) => [...bs, blockFromUrl(t)]);
    setPasteUrl("");
  }
  function patchBlock(uidVal: string, patch: Partial<Block>) {
    setBlocks((bs) => bs.map((b) => (b.uid === uidVal ? { ...b, ...patch } : b)));
  }
  function removeBlock(uidVal: string) {
    setBlocks((bs) => bs.filter((b) => b.uid !== uidVal));
  }
  function moveBlock(uidVal: string, dir: -1 | 1) {
    setBlocks((bs) => {
      const i = bs.findIndex((b) => b.uid === uidVal);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= bs.length) return bs;
      const next = bs.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }
  function duplicateBlock(uidVal: string) {
    setBlocks((bs) => {
      const i = bs.findIndex((b) => b.uid === uidVal);
      if (i < 0) return bs;
      const copy = { ...bs[i], uid: uid() };
      const next = bs.slice();
      next.splice(i + 1, 0, copy);
      return next;
    });
  }

  const totalDuration = blocks.reduce(
    (sum, b) => sum + (Number(b.duration) || 0),
    0
  );

  const nonAssemblable = blocks.filter(
    (b) => !b.source_url.startsWith("http")
  );

  async function render() {
    if (blocks.length === 0) {
      setErr("Add at least one clip to the timeline.");
      return;
    }
    if (nonAssemblable.length > 0) {
      setErr(
        `${nonAssemblable.length} block(s) use a URL Creatomate can't fetch. ` +
          `Re-upload those clips through the Reference Library (Supabase Storage) first.`
      );
      return;
    }
    setProducing(true);
    setErr("");
    setProd(null);
    try {
      const scenes = blocks.map((b, i) => blockToScene(b, i, musicMood));
      const p = await api.produceVideo({
        mode: "timeline",
        script: "",
        scenes,
        platform,
        aspect,
        title: title.trim() || `Timeline · ${blocks.length} clips`,
      });
      setProd(p);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const u = await api.getProduction(p.id).catch(() => null);
        if (u) {
          setProd(u);
          if (u.status === "succeeded" || u.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
          }
        }
      }, 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "render failed");
    } finally {
      setProducing(false);
    }
  }

  const active =
    !!prod && prod.status !== "succeeded" && prod.status !== "failed";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Timeline Editor"
        sub="Stitch clips from anything you've already rendered or uploaded. Add captions, transitions, SFX, a logo overlay, and a background music mood. Lands in the Approval Queue when it's done."
      />

      {/* GLOBAL CONFIG */}
      <Card>
        <CardTitle>Project</CardTitle>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Title</Label>
            <Input
              placeholder="e.g. Why Staten Island is the move (Apr 2026)"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div>
            <Label>Background music mood</Label>
            <Select
              value={musicMood}
              onChange={(e) =>
                setMusicMood(e.target.value as typeof musicMood)
              }
            >
              {["none", "upbeat", "calm", "dramatic", "tension"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Platform</Label>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {["instagram", "tiktok", "youtube", "linkedin"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Aspect</Label>
            <Select value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {["9:16", "16:9", "1:1"].map((a) => (
                <option key={a}>{a}</option>
              ))}
            </Select>
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button onClick={render} disabled={producing || active || blocks.length === 0}>
            {producing || active ? <Spinner /> : `Render ${blocks.length} block${blocks.length === 1 ? "" : "s"}`}
          </Button>
          <span className="text-[12px] text-muted-foreground">
            ~{Math.round(totalDuration)}s total · Creatomate stitch · lands in Approval Queue
          </span>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
      </Card>

      {/* RENDER STATUS (sticky-ish below project) */}
      {prod && (
        <Card>
          <CardTitle>Render</CardTitle>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone={STAGE_TONE[prod.status] || "muted"}>{prod.status}</Badge>
            {active && <Spinner />}
            <span className="text-[12px] text-muted-foreground ml-auto">
              id: {prod.id.slice(0, 8)}…
            </span>
          </div>
          {prod.error && (
            <p className="text-destructive text-[12px] mt-2">{prod.error}</p>
          )}
          {prod.final_url && !prod.final_url.startsWith("stub://") && (
            <video
              src={mediaUrl(prod.final_url)}
              controls
              className="w-full max-w-md mx-auto mt-3 rounded-md bg-background"
            />
          )}
          {prod.queued_action_id && (
            <Link
              href="/queue"
              className="text-[12px] text-primary hover:underline mt-3 inline-block"
            >
              Review in Approval Queue →
            </Link>
          )}
        </Card>
      )}

      {/* TWO COLUMNS: library | timeline */}
      <div className="grid grid-cols-[260px_1fr] gap-5 items-start">
        {/* LIBRARY */}
        <Card className="sticky top-4 max-h-[calc(100vh-2rem)] overflow-hidden flex flex-col">
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
          <Select
            value={libFilter}
            onChange={(e) =>
              setLibFilter(e.target.value as typeof libFilter)
            }
          >
            <option value="all">All clips</option>
            <option value="reference">Reference uploads</option>
            <option value="production_scene">Scene clips</option>
            <option value="production_final">Finished videos</option>
          </Select>
          <div className="flex-1 overflow-y-auto mt-3 -mr-2 pr-2 flex flex-col gap-1.5">
            {libLoading && <Spinner />}
            {libErr && <p className="text-destructive text-[12px]">{libErr}</p>}
            {!libLoading && filteredLib.length === 0 && (
              <p className="text-[11px] text-muted-foreground">
                Nothing in this bucket yet. Upload to the Reference Library
                or render a production first.
              </p>
            )}
            {filteredLib.map((c) => (
              <button
                key={`${c.kind}-${c.source_id}-${c.url}`}
                onClick={() => addFromLibrary(c)}
                disabled={!c.assemblable}
                title={
                  c.assemblable
                    ? "Click to add to the timeline"
                    : "Not assemblable: Creatomate needs an https URL"
                }
                className="text-left text-[12px] border border-border rounded-md px-2 py-1.5 hover:border-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <div className="flex items-center gap-1.5">
                  <Badge
                    tone={
                      c.kind === "reference"
                        ? "primary"
                        : c.kind === "production_final"
                          ? "ok"
                          : "muted"
                    }
                  >
                    {c.kind === "production_scene"
                      ? "scene"
                      : c.kind === "production_final"
                        ? "final"
                        : "ref"}
                  </Badge>
                  {!c.assemblable && <Badge tone="destructive">local</Badge>}
                </div>
                <div className="font-medium mt-1 truncate">{c.label}</div>
                {c.duration ? (
                  <div className="text-[10px] text-muted-foreground">
                    ~{Math.round(c.duration)}s
                  </div>
                ) : null}
              </button>
            ))}
          </div>
          <div className="mt-3 border-t border-border pt-3">
            <Label>Paste any URL</Label>
            <Input
              placeholder="https://…mp4"
              value={pasteUrl}
              onChange={(e) => setPasteUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") addFromUrl();
              }}
            />
            <Button
              variant="secondary"
              className="w-full mt-2"
              onClick={addFromUrl}
              disabled={!pasteUrl.trim()}
            >
              Add to timeline
            </Button>
          </div>
        </Card>

        {/* TIMELINE */}
        <div className="flex flex-col gap-3">
          {blocks.length === 0 && (
            <Card className="text-center py-10">
              <div className="text-[13px] text-muted-foreground">
                Timeline is empty. Click any clip on the left to add it.
              </div>
            </Card>
          )}
          {blocks.map((b, i) => (
            <Card key={b.uid} className="!p-3">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-muted-foreground w-6 text-right">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="text-[12px] font-medium flex-1 truncate">
                  {b.source_label}
                </span>
                <Badge
                  tone={
                    b.source_kind === "reference"
                      ? "primary"
                      : b.source_kind === "production_final"
                        ? "ok"
                        : b.source_kind === "external"
                          ? "accent"
                          : "muted"
                  }
                >
                  {b.source_kind === "production_scene"
                    ? "scene"
                    : b.source_kind === "production_final"
                      ? "final"
                      : b.source_kind === "reference"
                        ? "ref"
                        : "url"}
                </Badge>
                <button
                  onClick={() => moveBlock(b.uid, -1)}
                  disabled={i === 0}
                  className="text-[12px] px-1.5 hover:text-primary disabled:opacity-30"
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  onClick={() => moveBlock(b.uid, 1)}
                  disabled={i === blocks.length - 1}
                  className="text-[12px] px-1.5 hover:text-primary disabled:opacity-30"
                  title="Move down"
                >
                  ↓
                </button>
                <button
                  onClick={() => patchBlock(b.uid, { open: !b.open })}
                  className="text-[12px] px-1.5 text-muted-foreground hover:text-foreground"
                  title={b.open ? "Collapse" : "Expand"}
                >
                  {b.open ? "▾" : "▸"}
                </button>
                <button
                  onClick={() => duplicateBlock(b.uid)}
                  className="text-[12px] px-1.5 text-muted-foreground hover:text-foreground"
                  title="Duplicate"
                >
                  ⎘
                </button>
                <button
                  onClick={() => removeBlock(b.uid)}
                  className="text-[12px] px-1.5 text-muted-foreground hover:text-destructive"
                  title="Remove"
                >
                  ✕
                </button>
              </div>

              {b.open && (
                <div className="mt-3 grid grid-cols-[1fr_180px] gap-3 items-start">
                  {/* CONTROLS */}
                  <div>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <Label>Duration (s)</Label>
                        <Input
                          type="number"
                          min={0.5}
                          step={0.5}
                          value={b.duration}
                          onChange={(e) =>
                            patchBlock(b.uid, {
                              duration: Number(e.target.value) || 0,
                            })
                          }
                        />
                      </div>
                      <div>
                        <Label>Transition in</Label>
                        <Select
                          value={b.transition_in}
                          onChange={(e) =>
                            patchBlock(b.uid, {
                              transition_in: e.target.value as Block["transition_in"],
                            })
                          }
                        >
                          <option value="cut">cut</option>
                          <option value="fade">fade</option>
                          <option value="slide">slide</option>
                        </Select>
                      </div>
                      <div>
                        <Label>Logo overlay</Label>
                        <Select
                          value={b.branding_logo ? b.branding_position : "none"}
                          onChange={(e) => {
                            const v = e.target.value as Block["branding_position"];
                            patchBlock(b.uid, {
                              branding_logo: v !== "none",
                              branding_position: v,
                            });
                          }}
                        >
                          <option value="none">none</option>
                          <option value="bottom-right">bottom-right</option>
                          <option value="bottom-center">bottom-center</option>
                          <option value="top-right">top-right</option>
                        </Select>
                      </div>
                    </div>
                    <Label>On-screen caption</Label>
                    <Textarea
                      rows={2}
                      placeholder="Burned-in text shown across this block (leave empty for none)."
                      value={b.on_screen_text}
                      onChange={(e) =>
                        patchBlock(b.uid, { on_screen_text: e.target.value })
                      }
                    />
                    <div className="grid grid-cols-[auto_1fr] gap-3 items-end">
                      <label className="text-[12px] flex items-center gap-2 mt-3">
                        <input
                          type="checkbox"
                          checked={b.mute_native_audio}
                          onChange={(e) =>
                            patchBlock(b.uid, {
                              mute_native_audio: e.target.checked,
                            })
                          }
                        />
                        Mute clip audio
                      </label>
                      <div>
                        <Label>SFX URL (drops at start)</Label>
                        <Input
                          placeholder="https://…mp3 (optional)"
                          value={b.audio_sfx}
                          onChange={(e) =>
                            patchBlock(b.uid, { audio_sfx: e.target.value })
                          }
                        />
                      </div>
                    </div>
                    {!b.source_url.startsWith("http") && (
                      <p className="text-destructive text-[11px] mt-2">
                        ✗ This URL ({b.source_url.slice(0, 60)}…) isn&apos;t
                        publicly reachable. Re-upload via the Reference
                        Library so Creatomate can fetch it.
                      </p>
                    )}
                  </div>

                  {/* PREVIEW */}
                  <div>
                    {b.source_url.startsWith("http") ? (
                      <video
                        src={mediaUrl(b.source_url)}
                        controls
                        muted
                        className="w-full rounded-md bg-background border border-border"
                      />
                    ) : (
                      <div className="text-[11px] text-muted-foreground border border-border rounded-md p-3 bg-background">
                        Preview unavailable for local URLs.
                      </div>
                    )}
                    <div className="text-[10px] text-muted-foreground mt-1 break-all">
                      {b.source_url}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
