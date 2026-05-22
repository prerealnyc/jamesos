"use client";

import { useEffect, useState } from "react";
import { api, type Creator, type Trend } from "@/lib/api";
import { PageHeader, Card, Button, Input, Select, Spinner, Badge } from "@/components/ui";
import { TrendCard, ALL_PLATFORMS } from "@/components/trends";

export default function SocialCompanionPage() {
  const [creators, setCreators] = useState<Creator[]>([]);
  const [platform, setPlatform] = useState<string>("instagram");
  const [handle, setHandle] = useState("");
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [trends, setTrends] = useState<Trend[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.getWatchlist().then((r) => setCreators(r.creators)).catch(() => {});
  }, []);

  async function persist(next: Creator[]) {
    setCreators(next);
    setSaving(true);
    try {
      const r = await api.setWatchlist(next);
      setCreators(r.creators);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  function add() {
    const h = handle.trim().replace(/^@/, "");
    if (!h) return;
    if (creators.some((c) => c.platform === platform && c.handle === h)) return;
    persist([...creators, { platform, handle: h }]);
    setHandle("");
  }

  function remove(c: Creator) {
    persist(creators.filter((x) => !(x.platform === c.platform && x.handle === c.handle)));
  }

  async function refresh() {
    setRefreshing(true);
    setErr(null);
    try {
      const r = await api.refreshWatchlist();
      setTrends(r.trends);
      setNote(r.note);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Social Companion"
        sub="Track a cohort of creators in your space. Refresh to pull their recent posts, ranked by what's breaking out — then turn any of it into an on-voice script."
      />

      <Card>
        <div className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold mb-3">
          Watchlist
        </div>
        <div className="flex gap-2 flex-wrap">
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)} className="max-w-[140px]">
            {ALL_PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
          <Input
            placeholder="@handle or channel"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            className="max-w-[260px]"
          />
          <Button variant="secondary" onClick={add} disabled={saving}>
            Add
          </Button>
          <div className="ml-auto">
            <Button onClick={refresh} disabled={refreshing || creators.length === 0}>
              {refreshing ? <Spinner /> : "Refresh feed"}
            </Button>
          </div>
        </div>

        {creators.length > 0 ? (
          <div className="flex flex-wrap gap-2 mt-4">
            {creators.map((c) => (
              <span
                key={`${c.platform}:${c.handle}`}
                className="inline-flex items-center gap-2 text-[12px] border border-border rounded-full pl-3 pr-2 py-1"
              >
                <Badge tone="muted">{c.platform}</Badge>
                @{c.handle}
                <button
                  onClick={() => remove(c)}
                  className="text-muted-foreground hover:text-destructive"
                  aria-label="remove"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-muted-foreground mt-4">
            No creators tracked yet. Add competitors or creators whose format you want to learn from.
            (Or use <b>Market Research → Trend radar</b> to discover by topic.)
          </p>
        )}

        {err && <div className="mt-4 text-sm text-destructive">{err}</div>}
        {note && (
          <div className="mt-4 text-[12px] text-primary border border-primary/40 rounded-md p-3">
            {note}
          </div>
        )}
      </Card>

      {trends.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {trends.map((t, i) => (
            <TrendCard key={t.event_id || t.url || i} trend={t} />
          ))}
        </div>
      )}
    </div>
  );
}
