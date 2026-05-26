"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type QueueItem, type QueueStats, type Guardrail } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";

const VIDEO_FORMATS = new Set(["reel_script", "video_script", "video"]);

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
  const [copiedId, setCopiedId] = useState<string | null>(null);

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
    setActing(id);
    try {
      await api.approve(id);
      await load();
    } finally {
      setActing(null);
    }
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

      {loading ? (
        <Card>
          <Spinner /> <span className="text-muted-foreground text-sm ml-2">Loading queue…</span>
        </Card>
      ) : (() => {
        const visible = filter === "total" ? items : items.filter((it) => it.status === filter);
        if (visible.length === 0) {
          return (
            <Card>
              <p className="text-muted-foreground text-sm">
                {filter === "pending" && "No pending items. When an agent proposes a post, it lands here for review."}
                {filter === "approved" && "Nothing approved yet. Approved items will appear here with copy/export actions."}
                {filter === "rejected" && "Nothing rejected. Rejections become guardrails (see below)."}
                {filter === "total" && "Queue is empty."}
              </p>
            </Card>
          );
        }
        return (
        <div className="flex flex-col gap-3">
          {visible.map((it) => (
            <Card key={it.id}>
              <div className="flex items-center gap-2 mb-2">
                <Badge tone="primary">{it.platform}</Badge>
                <Badge tone="accent">{it.pillar}</Badge>
                <span className="text-xs text-muted-foreground">{it.format}</span>
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
              <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{it.content}</p>
              <div className="mt-3 pt-3 border-t border-border flex items-center gap-4 text-[12px] text-muted-foreground">
                {it.voiceScore != null && (
                  <span>
                    voice <b className="text-foreground">{it.voiceScore}</b>
                  </span>
                )}
                <span>by {it.proposedBy}</span>
                {it.status === "pending" && rejecting !== it.id && (
                  <span className="ml-auto flex gap-2">
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
                    {VIDEO_FORMATS.has(it.format) && (
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
                  </span>
                )}
                {it.status === "rejected" && it.reason && (
                  <span className="ml-auto text-muted-foreground text-[11px] italic max-w-[60%] truncate" title={it.reason}>
                    rejection: {it.reason}
                  </span>
                )}
              </div>

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
          ))}
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
    </div>
  );
}
