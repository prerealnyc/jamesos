"use client";

/**
 * Output Library — every finished video render in one place.
 *
 * Lists production rows with status='succeeded' and a final_url, sorted
 * newest-first. Each card has an inline HTML5 preview, a Download
 * button (anchor with the download attribute), and a Copy URL button.
 *
 * Filter chips along the top let you narrow to a single mode
 * (long_form_reel, engaging_avatar, etc.) — useful as the library
 * grows. Lightweight client-side filter, no server roundtrip.
 *
 * Honest scope: this is a viewer / catalog only — it does NOT delete
 * or re-render. Use /pipeline for that. Plays from Creatomate's
 * Backblaze URL directly; if Creatomate's TTL ever expires those, we
 * fall back to the persisted mirror at /video/clips/library.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Production } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";

const MODE_LABEL: Record<string, string> = {
  long_form_reel: "Long Form Reel",
  engaging_avatar: "Engaging Avatar",
  avatar_story_mix: "Avatar+Story Mix",
  story_audio: "Story Audio",
  avatar_only: "Avatar Only",
  timeline: "Timeline",
  mixed: "Mixed",
};

const CAPTION_LABEL: Record<string, { label: string; tone: "muted" | "accent" | "ok" | "destructive" }> = {
  tiktok_yellow: { label: "TikTok yellow", tone: "accent" },
  clean_white: { label: "Clean white", tone: "muted" },
  bold_pop: { label: "Bold pop", tone: "accent" },
  subtle_minimal: { label: "Subtle minimal", tone: "muted" },
  branded_red: { label: "Branded red", tone: "destructive" },
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch { return iso; }
}

function downloadName(p: Production): string {
  // Make something filesystem-friendly so the download lands with a
  // recognizable name instead of a UUID.
  const slug = (p.script || p.title || "reel")
    .replace(/^\[WHOLE\]\s*/i, "")
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .slice(0, 60)
    .toLowerCase();
  return `${slug || "reel"}-${p.id.slice(0, 8)}.mp4`;
}

export default function LibraryPage() {
  const [items, setItems] = useState<Production[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [modeFilter, setModeFilter] = useState<string>("all");
  const [copiedId, setCopiedId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const all = await api.listProductions();
      // Keep only finished renders that actually have a playable URL.
      const finished = (all || [])
        .filter((p) => p.status === "succeeded" && !!p.final_url)
        .sort((a, b) => (b.completed_at || b.updated_at).localeCompare(a.completed_at || a.updated_at));
      setItems(finished);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  // Collect distinct modes present in the current data so the filter
  // strip only shows what's actually here.
  const modes = Array.from(new Set(items.map((p) => p.mode || "mixed")));
  const filtered = modeFilter === "all"
    ? items
    : items.filter((p) => (p.mode || "mixed") === modeFilter);

  async function copyUrl(p: Production) {
    if (!p.final_url) return;
    try {
      await navigator.clipboard.writeText(p.final_url);
      setCopiedId(p.id);
      setTimeout(() => setCopiedId((id) => (id === p.id ? null : id)), 1500);
    } catch { /* noop — older browsers */ }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Output Library"
        sub="Every finished video render. Click Download to save, Copy to share the URL, or watch inline."
      />

      {err && (
        <Card className="border-destructive/40 bg-destructive/10">
          <p className="text-destructive text-[13px]">✗ {err}</p>
        </Card>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground"><Spinner /> Loading…</div>
      ) : items.length === 0 ? (
        <Card>
          <p className="text-muted-foreground text-[14px]">
            No finished renders yet. Kick a video render from{" "}
            <Link href="/long-form" className="text-primary hover:underline">Long Form Cutter</Link>,{" "}
            <Link href="/engaging-video" className="text-primary hover:underline">Engaging Reel</Link>, or{" "}
            <Link href="/pipeline" className="text-primary hover:underline">Video Studio</Link>{" "}
            and it'll land here when it finishes.
          </p>
        </Card>
      ) : (
        <>
          {/* Mode filter chips — show counts so the user knows what's there. */}
          {modes.length > 1 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] text-muted-foreground">Filter:</span>
              <button
                onClick={() => setModeFilter("all")}
                className={`text-[12px] px-3 py-1 rounded-full border transition-colors ${
                  modeFilter === "all"
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-background border-border hover:bg-muted"
                }`}
              >
                All · {items.length}
              </button>
              {modes.map((m) => {
                const n = items.filter((p) => (p.mode || "mixed") === m).length;
                return (
                  <button
                    key={m}
                    onClick={() => setModeFilter(m)}
                    className={`text-[12px] px-3 py-1 rounded-full border transition-colors ${
                      modeFilter === m
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background border-border hover:bg-muted"
                    }`}
                  >
                    {MODE_LABEL[m] || m} · {n}
                  </button>
                );
              })}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((p) => {
              const cap = (p.caption_style || "").trim();
              const capPreset = CAPTION_LABEL[cap];
              const aspectRatio = p.aspect === "9:16" ? "9 / 16" : p.aspect === "16:9" ? "16 / 9" : "1 / 1";
              return (
                <Card key={p.id} className="overflow-hidden flex flex-col gap-3">
                  <div
                    className="bg-black rounded-md overflow-hidden"
                    style={{ aspectRatio }}
                  >
                    <video
                      src={p.final_url || ""}
                      controls
                      preload="metadata"
                      className="w-full h-full"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <p
                      className="text-[13px] font-medium leading-snug line-clamp-2"
                      title={p.script}
                    >
                      {(p.script || "").replace(/^\[WHOLE\]\s*/, "") || p.title || "(untitled)"}
                    </p>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {p.mode && (
                        <Badge tone="muted">
                          {MODE_LABEL[p.mode] || p.mode}
                        </Badge>
                      )}
                      {capPreset && (
                        <Badge tone={capPreset.tone}>
                          {capPreset.label}
                        </Badge>
                      )}
                      <Badge tone="muted">
                        {p.platform} · {p.aspect}
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {fmtDate(p.completed_at || p.updated_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 mt-auto">
                    <a
                      href={p.final_url || "#"}
                      download={downloadName(p)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[12px] px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
                    >
                      ⬇ Download
                    </a>
                    <button
                      onClick={() => copyUrl(p)}
                      className="text-[12px] px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors"
                    >
                      {copiedId === p.id ? "✓ Copied" : "Copy URL"}
                    </button>
                    <a
                      href={p.final_url || "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ml-auto text-[12px] text-primary hover:underline"
                    >
                      Open ↗
                    </a>
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
