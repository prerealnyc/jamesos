"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, type QueueItem, type QueueStats, type Guardrail } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";
import { Toast } from "@/components/toast";
import { FilterChip } from "@/components/filter-chip";

// "Script" formats can be turned INTO a video via the Composer — they
// still belong on the Posts tab (they're written-content drafts), but
// they get a "Make video →" action when approved. The Videos tab is
// for finished renders only.
const SCRIPT_FORMATS = new Set(["reel_script", "video_script"]);

// True for finished video renders that have a playable URL. We check
// the URL too because a row could have format='video' but no media
// (e.g. a render that's still in flight or failed and somehow snuck in).
function isVideoItem(it: QueueItem): boolean {
  return it.format === "video" && !!it.mediaUrl;
}

export default function QueuePage() {
  const router = useRouter();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [guardrails, setGuardrails] = useState<Guardrail[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [learned, setLearned] = useState<string | null>(null);
  const [filter, setFilter] = useState<"pending" | "approved" | "rejected" | "total">("pending");
  // Content-kind tab: videos = finished renders with mediaUrl; posts =
  // scripts + captions + image-only content. Default to "videos" since
  // that's the new flow we're highlighting for marketing review.
  const [kind, setKind] = useState<"videos" | "posts" | "all">("videos");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; href?: string; hrefLabel?: string } | null>(null);
  // Edit-in-place + batch approve.
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [scheduling, setScheduling] = useState<string | null>(null);
  const [scheduleVal, setScheduleVal] = useState("");
  const [backfill, setBackfill] = useState<{ running: boolean; done: number; total: number } | null>(null);
  // Click a post image to view it full-size in an overlay.
  const [lightbox, setLightbox] = useState<string | null>(null);
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox]);

  // Generate a designed image for every queued post that's missing one (with a
  // hero-photo fallback so none is left blank). Re-runnable; retries failures.
  // Drains in rounds of 60 so a backlog larger than one batch is fully cleared.
  async function runBackfillImages() {
    if (backfill?.running) return;
    setBackfill({ running: true, done: 0, total: 0 });
    let generated = 0;
    let failed = 0;
    let stillRunning = false;
    let errored = "";
    try {
      for (let round = 0; round < 12; round++) {
        const { job_id } = await api.startBackfillImages({ mode: "designed", limit: 60 });
        let r: Awaited<ReturnType<typeof api.getBackfillImages>> | null = null;
        let timedOut = true;
        // ~20 min/round — a designed render with a Soul image can run ~3 min.
        for (let i = 0; i < 400; i++) {
          await new Promise((res) => setTimeout(res, 3000));
          r = await api.getBackfillImages(job_id);
          setBackfill({ running: true, done: r.done, total: r.total });
          if (r.status !== "running") {
            timedOut = false;
            break;
          }
        }
        if (!r || timedOut) {
          stillRunning = true;
          break;
        }
        if (r.status === "failed") {
          errored = r.error || "backfill failed";
          break;
        }
        generated += r.generated;
        failed += r.failed;
        // Stop when nothing was left, or a round made no progress (avoids a
        // loop when the only remaining posts can't get an image).
        if (r.total === 0 || r.generated === 0) break;
      }
      setToast({
        message: errored
          ? errored
          : stillRunning
          ? "Still generating images in the background — refresh in a minute."
          : generated === 0 && failed === 0
          ? "Every queued post already has an image."
          : `Generated images for ${generated} post${generated === 1 ? "" : "s"}` +
            (failed ? ` · ${failed} couldn't be generated` : ""),
      });
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "backfill failed" });
    } finally {
      setBackfill((b) => (b ? { ...b, running: false } : null));
      load();
    }
  }

  async function load() {
    try {
      const [q, s, g] = await Promise.all([
        api.queue(),
        api.queueStats(),
        api.guardrails().catch(() => ({ guardrails: [] })),
      ]);
      setItems(q);
      setStats(s);
      setGuardrails(g.guardrails);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function approve(id: string) {
    const item = items.find((i) => i.id === id);
    // Flagged = failed voice-QA. Require an explicit confirm before overriding
    // the gate, so a "mandatory" rule isn't bypassed with a stray click.
    let override = false;
    if (item?.flagged) {
      const drift = (item.qaDrift || []).join(" · ");
      if (
        !window.confirm(
          "This draft FAILED voice-QA" +
            (drift ? ` (${drift})` : "") +
            ".\n\nApprove it anyway?"
        )
      )
        return;
      override = true;
    }
    setActing(id);
    try {
      try {
        await api.approve(id, "approved via dashboard", override);
      } catch (e) {
        // Backend gate fired (e.g. flag set after load) — offer the override.
        const msg = e instanceof Error ? e.message : "";
        if (msg.includes("qa_flagged") && window.confirm("This draft failed voice-QA. Approve anyway?")) {
          await api.approve(id, "approved via dashboard", true);
        } else {
          throw e;
        }
      }
      if (item && isVideoItem(item)) {
        setToast({
          message: "Approved — find the download in Output Library.",
          href: "/library",
          hrefLabel: "Output Library",
        });
      } else {
        setToast({ message: "Approved.", href: undefined, hrefLabel: undefined });
      }
      await load();
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "Approve failed" });
    } finally {
      setActing(null);
    }
  }

  // Hard delete — removes the row entirely (vs reject which keeps it +
  // learns from the reason). Confirmed inline so a stray click doesn't
  // nuke an item.
  async function deleteItem(id: string) {
    if (!window.confirm("Delete this item permanently? This can't be undone.")) return;
    setActing(id);
    try {
      await api.deleteQueueItem(id);
      setToast({ message: "Deleted.", href: undefined, hrefLabel: undefined });
      await load();
    } catch (e) {
      setToast({
        message: e instanceof Error ? e.message : "Delete failed",
        href: undefined, hrefLabel: undefined,
      });
    } finally {
      setActing(null);
    }
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  async function approveSelected() {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBatchBusy(true);
    try {
      // Bulk approve can't safely override the voice-QA gate, so flagged items
      // 409 and are skipped — count real successes instead of claiming all N.
      let ok = 0;
      for (const id of ids) {
        try { await api.approve(id); ok++; } catch { /* flagged → skipped */ }
      }
      setSelected(new Set());
      setToast({
        message:
          ok === ids.length
            ? `Approved ${ids.length} item${ids.length === 1 ? "" : "s"}.`
            : `Approved ${ok} of ${ids.length} — ${ids.length - ok} flagged, approve those individually.`,
      });
      await load();
    } finally { setBatchBusy(false); }
  }

  // Bulk hard-delete — removes the selected rows entirely (vs reject, which
  // keeps them and teaches the engine). Destructive, so confirm first.
  async function deleteSelected() {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (
      !window.confirm(
        `Delete ${ids.length} selected item${ids.length === 1 ? "" : "s"} permanently? ` +
          "This can't be undone."
      )
    )
      return;
    setBatchBusy(true);
    try {
      let ok = 0;
      for (const id of ids) {
        try { await api.deleteQueueItem(id); ok++; } catch { /* skip failures */ }
      }
      setSelected(new Set());
      setToast({
        message:
          ok === ids.length
            ? `Deleted ${ids.length} item${ids.length === 1 ? "" : "s"}.`
            : `Deleted ${ok} of ${ids.length} — ${ids.length - ok} could not be deleted.`,
      });
      await load();
    } finally { setBatchBusy(false); }
  }

  // Edit the draft text, optionally approving in the same click.
  async function saveEdit(id: string, thenApprove: boolean) {
    setActing(id);
    try {
      await api.editQueueItem(id, editText.trim());
      setItems((list) => list.map((x) => (x.id === id ? { ...x, content: editText.trim() } : x)));
      setEditing(null);
      if (thenApprove) {
        await api.approve(id);
        setToast({ message: "Edited & approved." });
        await load();
      } else {
        setToast({ message: "Saved." });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "Save failed" });
    } finally { setActing(null); }
  }

  async function saveSchedule(id: string) {
    setActing(id);
    try {
      await api.scheduleQueueItem(id, scheduleVal);
      setItems((list) => list.map((x) => (x.id === id ? { ...x, scheduledFor: scheduleVal } : x)));
      setScheduling(null);
      setScheduleVal("");
      setToast({ message: scheduleVal ? "Scheduled." : "Schedule cleared." });
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "Schedule failed" });
    } finally { setActing(null); }
  }

  async function confirmReject(id: string) {
    setActing(id);
    try {
      const r = await api.reject(id, reason.trim() || "rejected");
      setRejecting(null);
      setReason("");
      if (r.learned) {
        setLearned("Logged as a guardrail — the engine won't repeat this.");
        setTimeout(() => setLearned(null), 4000);
      }
      await load();
    } finally {
      setActing(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Approval Queue"
        sub="Every AI-proposed action waits here. Nothing goes out without a human yes. Rejections teach the engine — it learns from each 'why'."
      />

      {learned && (
        <div className="text-[13px] text-accent border border-accent/40 rounded-md p-3 bg-accent/10">
          ✓ {learned}
        </div>
      )}

      {stats && (
        <div className="grid grid-cols-4 gap-3">
          {(["pending", "approved", "rejected", "total"] as const).map((k) => {
            const active = filter === k;
            return (
              <button
                key={k}
                onClick={() => setFilter(k)}
                className={`text-left rounded-lg border bg-card py-4 px-4 transition-colors ${
                  active
                    ? "border-primary ring-1 ring-primary"
                    : "border-border hover:border-muted-foreground"
                }`}
              >
                <div className={`text-2xl font-semibold ${active ? "text-primary" : ""}`}>
                  {stats[k]}
                </div>
                <div className="text-xs text-muted-foreground uppercase tracking-wide mt-1">{k}</div>
              </button>
            );
          })}
        </div>
      )}

      {/* Content-kind tabs — splits the queue into Videos vs Posts so
          the marketing manager can scan whichever surface matters. Counts
          reflect the current status filter so "Videos · 4" means
          "4 video items in the pending bucket," not "4 videos ever." */}
      {(() => {
        const inStatus = filter === "total" ? items : items.filter((it) => it.status === filter);
        const nVideos = inStatus.filter(isVideoItem).length;
        const nPosts = inStatus.length - nVideos;
        // Pending posts with no image yet — the backfill targets these.
        const missingImages = items.filter(
          (it) => !isVideoItem(it) && it.status === "pending" && !it.imageUrl
        ).length;
        return (
          <div className="flex items-center gap-2 flex-wrap">
            {(["videos", "posts", "all"] as const).map((k) => {
              const active = kind === k;
              const count = k === "videos" ? nVideos : k === "posts" ? nPosts : inStatus.length;
              return (
                <FilterChip
                  key={k}
                  active={active}
                  onClick={() => setKind(k)}
                  count={count}
                >
                  {k === "videos" ? "🎬 Videos" : k === "posts" ? "📝 Posts" : "All"}
                </FilterChip>
              );
            })}
            {(missingImages > 0 || backfill?.running) && (
              <Button
                className="ml-auto"
                onClick={runBackfillImages}
                disabled={backfill?.running}
                title="Generate a designed image for every queued post that's missing one"
              >
                {backfill?.running ? (
                  <>
                    <Spinner /> Generating images {backfill.done}/{backfill.total || "…"}
                  </>
                ) : (
                  `🎨 Generate ${missingImages} missing image${missingImages === 1 ? "" : "s"}`
                )}
              </Button>
            )}
          </div>
        );
      })()}

      {selected.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 px-4 py-2.5">
          <span className="text-[13px] font-medium">{selected.size} selected</span>
          <button onClick={() => setSelected(new Set())} className="text-[12px] text-muted-foreground hover:text-foreground">clear</button>
          <button
            onClick={deleteSelected}
            disabled={batchBusy}
            className="ml-auto text-[13px] text-red-400 hover:text-red-300 disabled:opacity-50"
            title="Delete the selected items permanently"
          >
            🗑 Delete {selected.size}
          </button>
          <Button onClick={approveSelected} disabled={batchBusy}>
            {batchBusy ? <Spinner /> : `Approve ${selected.size} selected`}
          </Button>
        </div>
      )}

      {loading ? (
        <Card>
          <Spinner /> <span className="text-muted-foreground text-sm ml-2">Loading queue…</span>
        </Card>
      ) : (() => {
        const visibleStatus = filter === "total" ? items : items.filter((it) => it.status === filter);
        const visible = kind === "all"
          ? visibleStatus
          : kind === "videos"
            ? visibleStatus.filter(isVideoItem)
            : visibleStatus.filter((it) => !isVideoItem(it));
        if (visible.length === 0) {
          const kindLabel = kind === "videos" ? "videos" : kind === "posts" ? "posts" : "items";
          const videoRenderers = (
            <>
              Render some from{" "}
              <Link href="/long-form" className="text-primary hover:underline">
                Long Form Cutter
              </Link>{" "}
              or{" "}
              <Link href="/engaging-video" className="text-primary hover:underline">
                Engaging Reel
              </Link>
              .
            </>
          );
          const postSources = (
            <>
              Drafts from{" "}
              <Link href="/design-studio" className="text-primary hover:underline">
                Content Studio
              </Link>{" "}
              or{" "}
              <Link href="/autopilot" className="text-primary hover:underline">
                Autopilot
              </Link>{" "}
              show up here for review.
            </>
          );
          return (
            <Card>
              <p className="text-muted-foreground text-sm">
                {filter === "pending" && kind === "videos" && (
                  <>No pending videos. {videoRenderers}</>
                )}
                {filter === "pending" && kind === "posts" && (
                  <>No pending posts. {postSources}</>
                )}
                {filter === "pending" && kind === "all" && (
                  <>
                    No pending items. {videoRenderers} {postSources}
                  </>
                )}
                {filter === "approved" && `No approved ${kindLabel} yet.`}
                {filter === "rejected" &&
                  "Nothing rejected. When you reject something, the reason teaches the system."}
                {filter === "total" && "Queue is empty."}
              </p>
            </Card>
          );
        }
        return (
        <div className="flex flex-col gap-3">
          {visible.map((it) => {
            const video = isVideoItem(it);
            return (
            <Card key={it.id}>
              <div className="flex items-center gap-2 mb-2">
                {it.status === "pending" && (
                  <input
                    type="checkbox"
                    checked={selected.has(it.id)}
                    onChange={() => toggleSelect(it.id)}
                    aria-label="Select for batch approve"
                    className="h-4 w-4 accent-[hsl(var(--primary))]"
                  />
                )}
                <Badge tone="primary">{it.platform}</Badge>
                <Badge tone="accent">{it.pillar}</Badge>
                <span className="text-xs text-muted-foreground">
                  {video ? "🎬 video" : it.format}
                </span>
                {it.flagged && it.status === "pending" && (
                  <span title={(it.qaDrift || []).join(" · ") || "failed voice-QA"}>
                    <Badge tone="destructive">⚠ failed voice-QA</Badge>
                  </span>
                )}
                <span className="ml-auto">
                  <Badge
                    tone={
                      it.status === "pending"
                        ? "muted"
                        : it.status === "approved"
                          ? "ok"
                          : "destructive"
                    }
                  >
                    {it.status}
                  </Badge>
                </span>
              </div>
              {/* The COMPLETE piece in one row: its media (video render or
                  generated image) beside the text it'll post with — so the
                  manager reviews everything at a glance, not text alone. */}
              <div className="flex gap-4">
                {video && it.mediaUrl ? (
                  <div className="shrink-0 self-start bg-black rounded-md overflow-hidden w-[150px]" style={{ aspectRatio: "9 / 16" }}>
                    <video src={it.mediaUrl} controls preload="metadata" className="w-full h-full" />
                  </div>
                ) : it.imageUrl ? (
                  // Portrait (4:5) box, top-aligned cover so the subject's head
                  // is always visible — a square crop used to lop it off.
                  // self-start keeps the 4:5 ratio: without it the flex row
                  // stretches the box to the full text height, and object-cover
                  // then slices a designed card's centered text off both sides.
                  <button
                    type="button"
                    onClick={() => setLightbox(it.imageUrl!)}
                    className="shrink-0 self-start bg-black rounded-md overflow-hidden border border-border w-[150px] cursor-zoom-in group relative"
                    style={{ aspectRatio: "4 / 5" }}
                    title="Click to expand"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={it.imageUrl}
                      alt="generated post image"
                      className="w-full h-full object-cover object-top"
                    />
                    <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white opacity-0 group-hover:opacity-100 transition-opacity">
                      ⤢ expand
                    </span>
                  </button>
                ) : null}
                <div className="min-w-0 flex-1">
                  {editing === it.id ? (
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      autoFocus
                      className="w-full bg-background border border-input rounded-md px-3 py-2 text-[14px] leading-relaxed resize-y min-h-[120px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                  ) : (
                    <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{it.content}</p>
                  )}
                </div>
              </div>
              <div className="mt-3 pt-3 border-t border-border flex items-center gap-4 text-[12px] text-muted-foreground">
                {it.voiceScore != null && (
                  <span>
                    voice <b className="text-foreground">{it.voiceScore}</b>
                  </span>
                )}
                <span>by {it.proposedBy}</span>
                {it.status === "pending" && rejecting !== it.id && editing === it.id && (
                  <span className="ml-auto flex gap-2 items-center">
                    <Button variant="ghost" onClick={() => setEditing(null)} disabled={acting === it.id}>Cancel</Button>
                    <Button variant="secondary" onClick={() => saveEdit(it.id, false)} disabled={acting === it.id || !editText.trim()}>
                      {acting === it.id ? <Spinner /> : "Save"}
                    </Button>
                    <Button onClick={() => saveEdit(it.id, true)} disabled={acting === it.id || !editText.trim()}>
                      {acting === it.id ? <Spinner /> : "Save & approve"}
                    </Button>
                  </span>
                )}
                {it.status === "pending" && rejecting !== it.id && editing !== it.id && (
                  <span className="ml-auto flex gap-2 items-center">
                    <button
                      onClick={() => deleteItem(it.id)}
                      disabled={acting === it.id}
                      className="text-[12px] text-muted-foreground hover:text-destructive disabled:opacity-40 px-1"
                      title="Delete permanently (no learning)"
                    >
                      🗑 Delete
                    </button>
                    {!video && (
                      <button
                        onClick={() => { setEditing(it.id); setEditText(it.content || ""); }}
                        disabled={acting === it.id}
                        className="text-[12px] text-muted-foreground hover:text-foreground disabled:opacity-40 px-1"
                        title="Tweak the text before approving"
                      >
                        ✎ Edit
                      </button>
                    )}
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setRejecting(it.id);
                        setReason("");
                      }}
                      disabled={acting === it.id}
                    >
                      Reject
                    </Button>
                    <Button onClick={() => approve(it.id)} disabled={acting === it.id}>
                      {acting === it.id ? <Spinner /> : "Approve"}
                    </Button>
                  </span>
                )}
                {it.status === "approved" && (
                  <span className="ml-auto flex gap-3 items-center">
                    {it.scheduledFor ? (
                      <span className="text-[12px] text-foreground" title={it.scheduledFor}>
                        📅 {new Date(it.scheduledFor).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                        <button onClick={() => { setScheduling(it.id); setScheduleVal(""); }} className="ml-1 text-muted-foreground hover:text-foreground underline">edit</button>
                      </span>
                    ) : (
                      <button onClick={() => { setScheduling(it.id); setScheduleVal(""); }} className="text-[12px] text-muted-foreground hover:text-foreground">📅 Schedule</button>
                    )}
                    {video && it.mediaUrl && (
                      <a
                        href={it.mediaUrl}
                        download={`reel-${it.id.slice(0, 8)}.mp4`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary font-semibold hover:underline"
                      >
                        ⬇ Download
                      </a>
                    )}
                    {!video && SCRIPT_FORMATS.has(it.format) && (
                      <button
                        className="text-primary font-semibold hover:underline"
                        onClick={() => {
                          sessionStorage.setItem("pipeline.from_script", it.content || "");
                          router.push("/pipeline");
                        }}
                        title="Plan + edit this script as a video in the Composer"
                      >
                        Make video →
                      </button>
                    )}
                    <button
                      className="text-primary hover:underline"
                      onClick={async () => {
                        await navigator.clipboard.writeText(it.content || "");
                        setCopiedId(it.id);
                        setTimeout(() => setCopiedId((c) => (c === it.id ? null : c)), 1500);
                      }}
                    >
                      {copiedId === it.id ? "copied!" : "copy"}
                    </button>
                    <button
                      className="text-muted-foreground hover:text-foreground"
                      onClick={() => {
                        const blob = new Blob([it.content || ""], { type: "text/plain" });
                        const a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = `${it.platform || "post"}-${it.id.slice(0, 8)}.txt`;
                        a.click();
                        URL.revokeObjectURL(a.href);
                      }}
                    >
                      download .txt
                    </button>
                    <button
                      onClick={() => deleteItem(it.id)}
                      disabled={acting === it.id}
                      className="text-muted-foreground hover:text-destructive disabled:opacity-40"
                      title="Delete permanently"
                    >
                      🗑
                    </button>
                  </span>
                )}
                {it.status === "rejected" && (
                  <span className="ml-auto flex items-center gap-2">
                    {it.reason && (
                      <span className="text-muted-foreground text-[11px] italic max-w-[40%] truncate" title={it.reason}>
                        rejection: {it.reason}
                      </span>
                    )}
                    <button
                      onClick={() => deleteItem(it.id)}
                      disabled={acting === it.id}
                      className="text-[12px] text-muted-foreground hover:text-destructive disabled:opacity-40"
                      title="Delete permanently"
                    >
                      🗑
                    </button>
                  </span>
                )}
              </div>

              {scheduling === it.id && (
                <div className="mt-3 pt-3 border-t border-border flex flex-wrap items-center gap-2">
                  <label className="text-[12px] text-muted-foreground">Publish at:</label>
                  <input
                    type="datetime-local"
                    value={scheduleVal}
                    onChange={(e) => setScheduleVal(e.target.value)}
                    className="bg-background border border-input rounded-md px-2 py-1 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  <span className="text-[11px] text-muted-foreground">records the time — actual auto-posting needs a connected account</span>
                  <span className="ml-auto flex gap-2">
                    <Button variant="ghost" onClick={() => { setScheduling(null); setScheduleVal(""); }}>Cancel</Button>
                    {it.scheduledFor && <Button variant="ghost" onClick={() => { setScheduleVal(""); saveSchedule(it.id); }}>Clear</Button>}
                    <Button variant="secondary" onClick={() => saveSchedule(it.id)} disabled={acting === it.id || !scheduleVal}>
                      {acting === it.id ? <Spinner /> : "Save schedule"}
                    </Button>
                  </span>
                </div>
              )}

              {rejecting === it.id && (
                <div className="mt-3 pt-3 border-t border-border flex flex-col gap-2">
                  <label className="text-[12px] text-muted-foreground">
                    Why are you rejecting this? The engine learns from your reason and won't repeat it.
                  </label>
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    autoFocus
                    placeholder="e.g. too salesy · wrong tone · don't use hashtags · claim isn't true to us…"
                    className="w-full bg-background border border-input rounded-md px-3 py-2 text-[13px] resize-y min-h-[60px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  <div className="flex gap-2 justify-end">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setRejecting(null);
                        setReason("");
                      }}
                    >
                      Cancel
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => confirmReject(it.id)}
                      disabled={acting === it.id}
                    >
                      {acting === it.id ? <Spinner /> : "Confirm rejection"}
                    </Button>
                  </div>
                </div>
              )}
            </Card>
            );
          })}
        </div>
        );
      })()}

      {guardrails.length > 0 && (
        <Card>
          <div className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold mb-3">
            What the engine has learned ({guardrails.length})
          </div>
          <ul className="flex flex-col gap-2">
            {guardrails.map((g) => (
              <li
                key={g.id}
                className="text-[13px] border border-border rounded-md p-3 flex items-start gap-2"
              >
                <Badge tone="destructive">avoid</Badge>
                <span className="flex-1">
                  {g.reason}
                  {(g.platform || g.topic) && (
                    <span className="text-muted-foreground">
                      {" "}— {[g.platform, g.topic].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-muted-foreground mt-3">
            Each rejection reason becomes a hard guardrail fed into every future draft — and the
            voice-QA gate fails anything that violates it.
          </p>
        </Card>
      )}

      {toast && (
        <Toast
          message={toast.message}
          kind="success"
          href={toast.href}
          hrefLabel={toast.hrefLabel}
          onClose={() => setToast(null)}
        />
      )}

      {lightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-6"
          onClick={() => setLightbox(null)}
          role="dialog"
          aria-modal="true"
        >
          <button
            type="button"
            onClick={() => setLightbox(null)}
            className="absolute top-4 right-5 text-white/80 hover:text-white text-2xl leading-none"
            aria-label="Close"
          >
            ✕
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={lightbox}
            alt="expanded post image"
            className="max-h-[90vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
