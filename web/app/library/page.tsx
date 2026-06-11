"use client";

/**
 * Output Library — every finished output in one place: video reels AND
 * approved text posts (paired with their generated hero image).
 *
 * A content-kind toggle (All / 🎬 Videos / 📝 Posts) splits the two
 * surfaces:
 *  - Videos = production rows with status='succeeded' and a final_url,
 *    each with an inline HTML5 preview, Download/Copy URL, and the
 *    manager Approve/Reject review loop.
 *  - Posts = APPROVED, non-video queue items (format !== 'video') — the
 *    written content paired with the complementary image the engine
 *    generated for it. Copy text + Download image.
 *
 * Mode + review filter chips narrow the video grid. All client-side,
 * no server roundtrip.
 *
 * Honest scope: this is a viewer / catalog only — it does NOT delete
 * or re-render. Use /pipeline for that. Videos play from Creatomate's
 * Backblaze URL directly; if Creatomate's TTL ever expires those, we
 * fall back to the persisted mirror at /video/clips/library.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type Production, type QueueItem } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";
import { SkeletonCard } from "@/components/skeleton";
import { FilterChip } from "@/components/filter-chip";

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
  karaoke_green: { label: "Karaoke green", tone: "ok" },
  highlight_box: { label: "Highlight box", tone: "accent" },
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

type Feedback = Awaited<ReturnType<typeof api.listVideoFeedback>>["feedback"][number];

export default function LibraryPage() {
  const [items, setItems] = useState<Production[]>([]);
  // Approved, non-video queue items — the text posts paired with their
  // generated hero image. Loaded alongside the video productions.
  const [posts, setPosts] = useState<QueueItem[]>([]);
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  // Content-kind tab — what the library shows: everything, finished
  // video renders, or approved text+image posts. Default "all" so the
  // user sees both surfaces at once.
  const [kind, setKind] = useState<"all" | "videos" | "posts">("all");
  const [modeFilter, setModeFilter] = useState<string>("all");
  const [reviewFilter, setReviewFilter] = useState<"all" | "unreviewed" | "approved" | "rejected">("all");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // Posts have their own copy-feedback id namespace so a copied video
  // URL chip and a copied post-text chip don't collide.
  const [copiedPostId, setCopiedPostId] = useState<string | null>(null);
  // Reject modal — open with the target production, capture reason,
  // submit. Empty reasons are not allowed (the backend learns from
  // the reason, so blank rejections are useless).
  const [rejectFor, setRejectFor] = useState<Production | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [acting, setActing] = useState<string | null>(null);
  const [learned, setLearned] = useState<string | null>(null);
  const [showFeedback, setShowFeedback] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [all, q, fb] = await Promise.all([
        api.listProductions(),
        api.queue().catch(() => [] as QueueItem[]),
        api.listVideoFeedback(30).catch(() => ({ feedback: [] })),
      ]);
      // Keep only finished renders that actually have a playable URL.
      const finished = (all || [])
        .filter((p) => p.status === "succeeded" && !!p.final_url)
        .sort((a, b) => (b.completed_at || b.updated_at).localeCompare(a.completed_at || a.updated_at));
      setItems(finished);
      // Posts surface = APPROVED, non-video queue items. These are the
      // written drafts (with their generated hero image) the manager
      // has signed off on — the text+image output the library now shows
      // alongside finished reels.
      const approvedPosts = (q || [])
        .filter((it) => it.status === "approved" && it.format !== "video")
        .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      setPosts(approvedPosts);
      setFeedback(fb.feedback || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function approve(p: Production) {
    setActing(p.id); setErr("");
    try {
      await api.approveProduction(p.id);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "approve failed");
    } finally {
      setActing(null);
    }
  }

  async function submitReject() {
    if (!rejectFor) return;
    const reason = rejectReason.trim();
    if (!reason) {
      setErr("Reject reason is required — the system learns from it.");
      return;
    }
    setActing(rejectFor.id); setErr("");
    try {
      const r = await api.rejectProduction(rejectFor.id, reason);
      setRejectFor(null);
      setRejectReason("");
      if (r.learned_id) {
        setLearned("Logged — future renders will read this feedback.");
        setTimeout(() => setLearned(null), 4500);
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "reject failed");
    } finally {
      setActing(null);
    }
  }

  // Collect distinct modes present in the current data so the filter
  // strip only shows what's actually here.
  const modes = Array.from(new Set(items.map((p) => p.mode || "mixed")));
  const modeFiltered = modeFilter === "all"
    ? items
    : items.filter((p) => (p.mode || "mixed") === modeFilter);
  const filtered = reviewFilter === "all"
    ? modeFiltered
    : modeFilter === "all"
      ? modeFiltered.filter((p) => {
          if (reviewFilter === "unreviewed") return !p.review_status;
          if (reviewFilter === "approved") return p.review_status === "approved" || p.review_status === "approved_with_notes";
          return p.review_status === "rejected";
        })
      : modeFiltered.filter((p) => {
          if (reviewFilter === "unreviewed") return !p.review_status;
          if (reviewFilter === "approved") return p.review_status === "approved" || p.review_status === "approved_with_notes";
          return p.review_status === "rejected";
        });
  const reviewCounts = {
    unreviewed: items.filter((p) => !p.review_status).length,
    approved: items.filter((p) => p.review_status === "approved" || p.review_status === "approved_with_notes").length,
    rejected: items.filter((p) => p.review_status === "rejected").length,
  };

  async function copyUrl(p: Production) {
    if (!p.final_url) return;
    try {
      await navigator.clipboard.writeText(p.final_url);
      setCopiedId(p.id);
      setTimeout(() => setCopiedId((id) => (id === p.id ? null : id)), 1500);
    } catch { /* noop — older browsers */ }
  }

  async function copyPostText(it: QueueItem) {
    const text = (it.content || it.caption || "").trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedPostId(it.id);
      setTimeout(() => setCopiedPostId((id) => (id === it.id ? null : id)), 1500);
    } catch { /* noop — older browsers */ }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Output Library"
        sub="Every finished output — video reels and approved text posts with their generated images. Download, copy, or review inline."
      />

      {err && (
        <Card className="border-destructive/40 bg-destructive/10">
          <p className="text-destructive text-[13px]">✗ {err}</p>
        </Card>
      )}

      {/* Content-kind tabs — split the library into finished video reels
          vs approved text+image posts. Same FilterChip pattern as the
          Approval Queue. Always visible (even while empty) so the user
          understands the two surfaces exist. */}
      {!loading && (
        <div className="flex items-center gap-2 flex-wrap">
          {(["all", "videos", "posts"] as const).map((k) => {
            const count = k === "videos" ? items.length : k === "posts" ? posts.length : items.length + posts.length;
            return (
              <FilterChip
                key={k}
                active={kind === k}
                onClick={() => setKind(k)}
                count={count}
              >
                {k === "videos" ? "🎬 Videos" : k === "posts" ? "📝 Posts" : "All"}
              </FilterChip>
            );
          })}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} aspect="9 / 16" />)}
        </div>
      ) : items.length === 0 && posts.length === 0 ? (
        <Card>
          <p className="text-muted-foreground text-[14px]">
            Nothing here yet. Kick a video render from{" "}
            <Link href="/long-form" className="text-primary hover:underline">Long Form Cutter</Link>,{" "}
            <Link href="/engaging-video" className="text-primary hover:underline">Engaging Reel</Link>, or{" "}
            <Link href="/pipeline" className="text-primary hover:underline">Video Studio</Link>{" "}
            — or approve text posts in the{" "}
            <Link href="/queue" className="text-primary hover:underline">Approval Queue</Link>{" "}
            and they'll land here with their images.
          </p>
        </Card>
      ) : (
        <>
          {/* ── Videos surface (also shown in "All") ──────────────── */}
          {kind !== "posts" && (
            items.length === 0 ? (
              kind === "videos" && (
                <Card>
                  <p className="text-muted-foreground text-[14px]">
                    No finished renders yet. Kick a video render from{" "}
                    <Link href="/long-form" className="text-primary hover:underline">Long Form Cutter</Link>,{" "}
                    <Link href="/engaging-video" className="text-primary hover:underline">Engaging Reel</Link>, or{" "}
                    <Link href="/pipeline" className="text-primary hover:underline">Video Studio</Link>{" "}
                    and it'll land here when it finishes.
                  </p>
                </Card>
              )
            ) : (
              <>
          {/* Mode filter chips — show counts so the user knows what's there. */}
          {modes.length > 1 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] text-muted-foreground">Filter:</span>
              <FilterChip
                active={modeFilter === "all"}
                onClick={() => setModeFilter("all")}
                count={items.length}
              >
                All
              </FilterChip>
              {modes.map((m) => {
                const n = items.filter((p) => (p.mode || "mixed") === m).length;
                return (
                  <FilterChip
                    key={m}
                    active={modeFilter === m}
                    onClick={() => setModeFilter(m)}
                    count={n}
                  >
                    {MODE_LABEL[m] || m}
                  </FilterChip>
                );
              })}
            </div>
          )}

          {/* Review-status filter strip — secondary axis alongside mode. */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[12px] text-muted-foreground">Review:</span>
            {([
              ["all", "All", items.length],
              ["unreviewed", "Unreviewed", reviewCounts.unreviewed],
              ["approved", "✓ Approved", reviewCounts.approved],
              ["rejected", "✗ Rejected", reviewCounts.rejected],
            ] as const).map(([k, label, n]) => (
              <FilterChip
                key={k}
                active={reviewFilter === k}
                onClick={() => setReviewFilter(k as typeof reviewFilter)}
                count={n}
              >
                {label}
              </FilterChip>
            ))}
            {feedback.length > 0 && (
              <button
                onClick={() => setShowFeedback((v) => !v)}
                className="ml-auto text-[11px] text-muted-foreground hover:text-foreground"
              >
                {showFeedback ? "Hide" : `Learned ${feedback.length} items →`}
              </button>
            )}
          </div>

          {/* "What we've learned" panel — mirrors guardrails on /queue.
              Surfaces every rejection reason so the team can see what
              the system will avoid on the next render. */}
          {showFeedback && feedback.length > 0 && (
            <Card className="!p-3">
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-2">
                Video feedback learned from rejections
              </p>
              <ul className="space-y-2">
                {feedback.map((f) => (
                  <li
                    key={f.id}
                    className="text-[12px] border border-border rounded-md p-2.5 flex items-start gap-2"
                  >
                    <Badge tone={f.status === "rejected" ? "destructive" : "accent"}>
                      {f.status === "rejected" ? "avoid" : "note"}
                    </Badge>
                    <div className="flex-1 min-w-0">
                      <p className="leading-snug">{f.reason}</p>
                      <div className="flex gap-1.5 flex-wrap mt-1">
                        {f.tags.map((t) => (
                          <Badge key={t} tone="muted">{t}</Badge>
                        ))}
                        {f.mode && <Badge tone="muted">{f.mode}</Badge>}
                        {f.caption_style && <Badge tone="muted">{f.caption_style}</Badge>}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-muted-foreground mt-3">
                Each rejection becomes a memory event the video pipeline reads on the next render — same loop as the text feedback in the Approval Queue.
              </p>
            </Card>
          )}

          {learned && (
            <div className="text-[13px] text-accent border border-accent/40 rounded-md p-3 bg-accent/10">
              ✓ {learned}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((p) => {
              const cap = (p.caption_style || "").trim();
              const capPreset = CAPTION_LABEL[cap];
              const aspectRatio = p.aspect === "9:16" ? "9 / 16" : p.aspect === "16:9" ? "16 / 9" : "1 / 1";
              const review = p.review_status;
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
                      {review === "approved" && <Badge tone="ok">✓ Approved</Badge>}
                      {review === "approved_with_notes" && <Badge tone="ok">✓ Approved (note)</Badge>}
                      {review === "rejected" && <Badge tone="destructive">✗ Rejected</Badge>}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {fmtDate(p.completed_at || p.updated_at)}
                    </p>
                    {review === "rejected" && p.review_reason && (
                      <p className="text-[11px] text-destructive italic line-clamp-2" title={p.review_reason}>
                        Why: {p.review_reason}
                      </p>
                    )}
                  </div>
                  {/* Review row — Approve / Reject is the primary action.
                      Hidden once a review exists; user can still re-open
                      via the per-card Reject (we don't allow un-reviewing,
                      same as text feedback). */}
                  {!review && (
                    <div className="flex items-center gap-2">
                      <Button onClick={() => approve(p)} disabled={acting === p.id}>
                        {acting === p.id ? <Spinner /> : "✓ Approve"}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => { setRejectFor(p); setRejectReason(""); setErr(""); }}
                        disabled={acting === p.id}
                      >
                        ✗ Reject…
                      </Button>
                    </div>
                  )}
                  <div className="flex items-center gap-2 mt-auto">
                    <a
                      href={p.final_url || "#"}
                      download={downloadName(p)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[12px] px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors"
                    >
                      ⬇ Download
                    </a>
                    <Button variant="secondary" onClick={() => copyUrl(p)}>
                      {copiedId === p.id ? "✓ Copied" : "Copy URL"}
                    </Button>
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
            )
          )}

          {/* ── Posts surface (also shown in "All") ───────────────
              Approved, non-video queue items: the written content paired
              with the hero image the engine generated for it. Copy the
              text, download the image. This is the text+image output the
              library surfaces alongside finished reels. */}
          {kind !== "videos" && (
            posts.length === 0 ? (
              <Card>
                <p className="text-muted-foreground text-[14px]">
                  No approved posts yet. Approve text posts in the{" "}
                  <Link href="/queue" className="text-primary hover:underline">Approval Queue</Link>{" "}
                  and they'll appear here with their images.
                </p>
              </Card>
            ) : (
              <>
                {kind === "all" && (
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground pt-2">
                    📝 Posts
                  </p>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {posts.map((it) => {
                    const text = (it.content || it.caption || "").trim();
                    const img = it.imageUrl ? mediaUrl(it.imageUrl) : null;
                    return (
                      <Card key={it.id} className="overflow-hidden flex flex-col gap-3">
                        {img ? (
                          <a href={img} target="_blank" rel="noopener noreferrer" className="block">
                            <img
                              src={img}
                              alt=""
                              loading="lazy"
                              className="w-full aspect-square object-cover rounded-md bg-muted"
                            />
                          </a>
                        ) : (
                          <div className="w-full aspect-square rounded-md bg-muted flex items-center justify-center text-[12px] text-muted-foreground">
                            no image
                          </div>
                        )}
                        <div className="flex flex-col gap-1.5">
                          <p
                            className="text-[13px] leading-snug line-clamp-5 whitespace-pre-wrap"
                            title={text}
                          >
                            {text || "(no text)"}
                          </p>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <Badge tone="muted">{it.platform}</Badge>
                            {it.pillar && <Badge tone="accent">{it.pillar}</Badge>}
                            <Badge tone="muted">{it.format}</Badge>
                            <Badge tone="ok">✓ Approved</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">
                            {fmtDate(it.createdAt)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 mt-auto">
                          <Button variant="secondary" onClick={() => copyPostText(it)}>
                            {copiedPostId === it.id ? "✓ Copied" : "Copy text"}
                          </Button>
                          {img && (
                            <a
                              href={img}
                              download={`${it.platform || "post"}-${it.id.slice(0, 8)}.png`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-auto text-[12px] text-primary hover:underline"
                            >
                              ⬇ Download image
                            </a>
                          )}
                        </div>
                      </Card>
                    );
                  })}
                </div>
              </>
            )
          )}
        </>
      )}

      {/* Reject modal — captures the reason that becomes the learning
          event. Required (no empty submits) because the pipeline reads
          these on the next render. */}
      {rejectFor && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50"
          onClick={(e) => { if (e.target === e.currentTarget) setRejectFor(null); }}
        >
          <Card className="max-w-lg w-full !p-4 space-y-3">
            <div>
              <p className="text-[13px] font-medium">Reject — tell the system what to do differently</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Your reason becomes a memory event the video pipeline reads on the next render — same loop as the script feedback in the Approval Queue.
              </p>
            </div>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              autoFocus
              placeholder="e.g. Captions are in the danger zone — center on his chest. Or: the 2s B-roll inserts feel uncanny — make them 4s minimum…"
              className="w-full bg-background border border-input rounded-md px-3 py-2 text-[13px] resize-y min-h-[100px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="flex gap-2 justify-end">
              <Button
                variant="ghost"
                onClick={() => setRejectFor(null)}
                disabled={acting === rejectFor.id}
              >
                Cancel
              </Button>
              <Button
                variant="secondary"
                onClick={submitReject}
                disabled={acting === rejectFor.id || !rejectReason.trim()}
              >
                {acting === rejectFor.id ? <Spinner /> : "Reject & teach the system"}
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
