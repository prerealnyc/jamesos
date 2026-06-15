"use client";

/**
 * "What's changing next" — the feedback → change roadmap.
 *
 * Your rejections/notes on videos & captions are interpreted into changes:
 * LIVE tweaks (e.g. B-roll insert duration) apply instantly and show under
 * "Applied live"; bigger changes are queued for the next build and struck off
 * when shipped. Hit Refresh to re-read the latest feedback.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, type ChangeItem } from "@/lib/api";
import { PageHeader, Card, Button, Badge, Spinner } from "@/components/ui";

type Board = { applied_live: ChangeItem[]; queued: ChangeItem[]; proposed: ChangeItem[]; done: ChangeItem[] };

export default function UpdatesPage() {
  const [board, setBoard] = useState<Board>({ applied_live: [], queued: [], proposed: [], done: [] });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await api.listChanges());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  // Keep the board live without a manual click: re-fetch every 15s, and
  // immediately when the tab regains focus (e.g. after rejecting a piece
  // in another tab). The backend writes change rows the instant feedback
  // lands; this is the frontend pull that surfaces them in real time.
  useEffect(() => {
    const t = setInterval(load, 15000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    setErr(null);
    try {
      const r = await api.refreshChanges();
      setBoard({ applied_live: r.applied_live, queued: r.queued, proposed: r.proposed, done: r.done });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="What's changing next"
        sub="Your feedback on videos & captions, turned into changes. Live tweaks apply instantly; bigger changes are queued for the next build and struck off when shipped."
      />
      <div>
        <Button onClick={refresh} disabled={refreshing}>
          {refreshing ? (
            <span className="flex items-center gap-2"><Spinner /> reading your feedback…</span>
          ) : (
            "↻ Refresh from latest feedback"
          )}
        </Button>
      </div>
      {err && <div className="text-sm text-destructive">{err}</div>}

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2"><Spinner /> loading…</div>
      ) : (
        <>
          <Section
            title="✓ Applied live"
            count={board.applied_live.length}
            empty="Nothing applied yet — hit Refresh to turn feedback into instant tweaks."
          >
            {board.applied_live.map((c) => (
              <Row key={c.id} c={c} struck note="applied — live now" />
            ))}
          </Section>

          <Section
            title="⧗ Queued for the next build"
            count={board.queued.length}
            empty="No code changes queued."
          >
            {board.queued.map((c) => (
              <Row
                key={c.id}
                c={c}
                actions={
                  <>
                    <button onClick={() => api.markChangeDone(c.id).then(load)} className="text-accent hover:underline">
                      mark shipped
                    </button>
                    <button onClick={() => api.dismissChange(c.id).then(load)} className="text-muted-foreground hover:text-destructive">
                      dismiss
                    </button>
                  </>
                }
              />
            ))}
          </Section>

          {board.proposed.length > 0 && (
            <Section
              title="◇ Proposed — PR ready for your review"
              count={board.proposed.length}
              empty=""
            >
              {board.proposed.map((c) => (
                <Row
                  key={c.id}
                  c={c}
                  note="the agent wrote a fix — review & merge to ship"
                  actions={
                    c.pr_url ? (
                      <a href={c.pr_url} target="_blank" rel="noopener noreferrer" className="text-primary font-medium hover:underline">
                        Review PR →
                      </a>
                    ) : null
                  }
                />
              ))}
            </Section>
          )}

          {board.done.length > 0 && (
            <Section title="✓ Shipped" count={board.done.length} empty="">
              {board.done.map((c) => (
                <Row key={c.id} c={c} struck note="shipped" />
              ))}
            </Section>
          )}
        </>
      )}
    </div>
  );
}

function Section({
  title, count, empty, children,
}: { title: string; count: number; empty: string; children: ReactNode }) {
  return (
    <Card>
      <div className="text-[15px] font-semibold mb-2">
        {title} <span className="text-muted-foreground text-[12px]">({count})</span>
      </div>
      {count === 0 ? (
        <p className="text-[12px] text-muted-foreground">{empty}</p>
      ) : (
        <div className="flex flex-col gap-1.5">{children}</div>
      )}
    </Card>
  );
}

function Row({
  c, struck, note, actions,
}: { c: ChangeItem; struck?: boolean; note?: string; actions?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border border-border rounded-md px-3 py-2 bg-background">
      <div className="min-w-0">
        <div className={`text-[13px] ${struck ? "line-through text-muted-foreground" : "font-medium"}`}>
          {c.plain_english}
        </div>
        <div className="text-[11px] text-muted-foreground mt-0.5 flex flex-wrap items-center gap-1.5">
          <Badge tone="muted">{c.area}</Badge>
          {c.diagnosis && <span>· from: “{c.diagnosis}”</span>}
          {note && <span>· {note}</span>}
          {(c.production_id || c.source_event_id) && (
            <a href="/queue?status=rejected" className="text-primary hover:underline" title="See the rejected item this came from">
              · ↳ from a rejected {c.production_id ? "video" : "post"}
            </a>
          )}
        </div>
      </div>
      {actions && <div className="flex items-center gap-3 text-[11px] shrink-0">{actions}</div>}
    </div>
  );
}
