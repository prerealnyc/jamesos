"use client";

import { useEffect, useState } from "react";
import { api, type QueueItem, type QueueStats } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);

  async function load() {
    try {
      const [q, s] = await Promise.all([api.queue(), api.queueStats()]);
      setItems(q);
      setStats(s);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function decide(id: string, kind: "approve" | "reject") {
    setActing(id);
    try {
      await (kind === "approve" ? api.approve(id) : api.reject(id));
      await load();
    } finally {
      setActing(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Approval Queue"
        sub="Every AI-proposed action waits here. Nothing goes out without a human yes — live on Supabase."
      />

      {stats && (
        <div className="grid grid-cols-4 gap-3">
          {(["pending", "approved", "rejected", "total"] as const).map((k) => (
            <Card key={k} className="py-4">
              <div className="text-2xl font-semibold">{stats[k]}</div>
              <div className="text-xs text-muted-foreground uppercase tracking-wide mt-1">{k}</div>
            </Card>
          ))}
        </div>
      )}

      {loading ? (
        <Card>
          <Spinner /> <span className="text-muted-foreground text-sm ml-2">Loading queue…</span>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <p className="text-muted-foreground text-sm">
            Queue is empty. When an agent proposes a post, it lands here for review.
          </p>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {items.map((it) => (
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
                {it.status === "pending" && (
                  <span className="ml-auto flex gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => decide(it.id, "reject")}
                      disabled={acting === it.id}
                    >
                      Reject
                    </Button>
                    <Button onClick={() => decide(it.id, "approve")} disabled={acting === it.id}>
                      {acting === it.id ? <Spinner /> : "Approve"}
                    </Button>
                  </span>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
