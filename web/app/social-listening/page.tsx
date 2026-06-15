"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, CardTitle, Input, Badge, Spinner, PageHeader } from "@/components/ui";

type Post = {
  platform: string; id: string; text: string; author: string | null;
  likes: number | null; comments: number | null; shares?: number | null;
  views?: number | null; subreddit?: string | null; created: string; url: string | null;
};

const PLATFORMS = [
  { key: "twitter", label: "X" },
  { key: "instagram", label: "Instagram" },
  { key: "tiktok", label: "TikTok" },
  { key: "reddit", label: "Reddit" },
];
const PLATFORM_LABEL: Record<string, string> = {
  x: "X", instagram: "Instagram", tiktok: "TikTok", reddit: "Reddit",
};

function num(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}


export default function SocialListeningPage() {
  const [acct, setAcct] = useState<Awaited<ReturnType<typeof api.xpozAccount>> | null>(null);
  const [query, setQuery] = useState("Staten Island real estate");
  const [sel, setSel] = useState<string[]>(PLATFORMS.map((p) => p.key));
  const [limit, setLimit] = useState(10);
  const [busy, setBusy] = useState(false);
  const [posts, setPosts] = useState<Post[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [ran, setRan] = useState(false);
  const [err, setErr] = useState("");
  const [drafting, setDrafting] = useState<string | null>(null);
  const [draftMsg, setDraftMsg] = useState<{ id: string; text: string; ok: boolean } | null>(null);

  useEffect(() => { api.xpozAccount().then(setAcct).catch(() => {}); }, []);

  function toggle(p: string) {
    setSel((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]));
  }

  async function run() {
    if (!query.trim() || sel.length === 0) return;
    setBusy(true); setErr(""); setRan(true);
    try {
      const r = await api.xpozSearch(query.trim(), sel, limit);
      if (r.error) { setErr(r.error); setPosts([]); setErrors({}); return; }
      setPosts(r.results || []);
      setErrors(r.errors || {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : "search failed");
    } finally { setBusy(false); }
  }

  async function draftFrom(p: Post) {
    const key = p.platform + p.id;
    setDrafting(key); setDraftMsg(null);
    try {
      const d = await api.xpozDraftFromPost({
        text: p.text, platform: "instagram", author: p.author,
        source_platform: p.platform, format: "reel_script",
      });
      const ok = d.status === "generated";
      setDraftMsg({
        id: key, ok,
        text: ok
          ? `On-brand draft created (voice ${d.voice_score}). Review it in the Approval Queue.`
          : d.note || `Draft flagged by voice-QA (score ${d.voice_score}). Check the queue.`,
      });
    } catch (e) {
      setDraftMsg({ id: key, ok: false, text: e instanceof Error ? e.message : "draft failed" });
    } finally { setDrafting(null); }
  }

  // Aggregate analytics over the result set.
  const totalEng = posts.reduce((a, p) => a + (p.likes || 0) + (p.comments || 0), 0);
  const byPlatform = PLATFORMS.map((pl) => {
    const tag = pl.key === "twitter" ? "x" : pl.key;
    const rows = posts.filter((p) => p.platform === tag);
    return { ...pl, tag, count: rows.length, eng: rows.reduce((a, p) => a + (p.likes || 0) + (p.comments || 0), 0) };
  }).filter((p) => p.count > 0);
  const top = posts.length ? posts.reduce((a, b) => ((b.likes || 0) > (a.likes || 0) ? b : a)) : null;

  const notConfigured = acct && acct.configured === false;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Social Listening"
        sub="Find the top trending content in your niche across X, Instagram, TikTok and Reddit — powered by Xpoz (1.5B+ posts). Search a topic, competitor, or your brand; see what's working; then turn any post into an on-brand draft with one click. Autopilot also rides these live trends automatically."
      />

      {/* Account / quota */}
      <Card className={notConfigured ? "border-destructive/40 bg-destructive/5" : ""}>
        <div className="flex items-center justify-between">
          <CardTitle>Xpoz account</CardTitle>
          {acct?.configured && !acct.error && <Badge tone="ok">connected</Badge>}
          {notConfigured && <Badge tone="destructive">no key</Badge>}
        </div>
        {notConfigured ? (
          <p className="text-[13px] text-muted-foreground mt-2">
            Add your Xpoz API key under <a href="/settings" className="underline">Settings → API connections</a> to enable social listening.
          </p>
        ) : acct?.error ? (
          <p className="text-[13px] text-destructive mt-2">{acct.error}</p>
        ) : acct ? (
          <div className="flex flex-wrap gap-6 mt-2 text-[13px]">
            <span><span className="text-muted-foreground">Plan: </span><b>{acct.plan_name || "—"}</b></span>
            <span>
              <span className="text-muted-foreground">Credits left: </span>
              <b>{acct.credits_remaining != null ? acct.credits_remaining.toLocaleString() : "—"}</b>
              {acct.credits != null && <span className="text-muted-foreground"> / {acct.credits.toLocaleString()} {acct.reset_frequency || ""}</span>}
            </span>
            {acct.tracked_items != null && (
              <span><span className="text-muted-foreground">Tracked items: </span><b>{acct.tracked_items}</b></span>
            )}
            {acct.next_renewal && (
              <span><span className="text-muted-foreground">Renews: </span><b>{acct.next_renewal.slice(0, 10)}</b></span>
            )}
          </div>
        ) : (
          <div className="text-muted-foreground text-sm mt-2 flex items-center gap-2"><Spinner /> checking…</div>
        )}
      </Card>

      {/* Search */}
      <Card>
        <CardTitle>Search the conversation</CardTitle>
        <div className="flex gap-2 mt-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. Staten Island real estate, your brand, a competitor…"
            onKeyDown={(e) => e.key === "Enter" && run()} />
          <Button onClick={run} disabled={busy || !query.trim() || sel.length === 0}>
            {busy ? <Spinner /> : "Search"}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-3">
          {PLATFORMS.map((p) => (
            <button key={p.key} onClick={() => toggle(p.key)}
              className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
                sel.includes(p.key) ? "border-primary text-foreground bg-primary/10" : "border-border text-muted-foreground"
              }`}>{p.label}</button>
          ))}
          <span className="text-[11px] text-muted-foreground ml-1">per platform:</span>
          {[5, 10, 20].map((n) => (
            <button key={n} onClick={() => setLimit(n)}
              className={`text-[12px] rounded px-2 py-1 border transition-colors ${
                limit === n ? "border-primary text-foreground bg-primary/10" : "border-border text-muted-foreground"
              }`}>{n}</button>
          ))}
        </div>
        {err && <p className="text-destructive text-[12px] mt-2">✗ {err}</p>}
      </Card>

      {/* Aggregate analytics */}
      {ran && !busy && posts.length > 0 && (
        <>
          <div className="grid grid-cols-3 gap-4">
            <Card><div className="text-[12px] text-muted-foreground">Mentions found</div><div className="text-[26px] font-semibold mt-1">{posts.length}</div></Card>
            <Card><div className="text-[12px] text-muted-foreground">Total engagement</div><div className="text-[26px] font-semibold mt-1">{num(totalEng)}</div></Card>
            <Card><div className="text-[12px] text-muted-foreground">Top platform</div><div className="text-[26px] font-semibold mt-1">{byPlatform.length ? byPlatform.reduce((a, b) => (b.eng > a.eng ? b : a)).label : "—"}</div></Card>
          </div>

          <Card>
            <CardTitle>By platform</CardTitle>
            <div className="flex flex-col gap-2 mt-3">
              {byPlatform.sort((a, b) => b.eng - a.eng).map((p) => {
                const max = Math.max(...byPlatform.map((x) => x.eng), 1);
                return (
                  <div key={p.key} className="flex items-center gap-3 text-[13px]">
                    <span className="w-20 shrink-0">{p.label}</span>
                    <div className="flex-1 h-2.5 rounded-full bg-secondary overflow-hidden">
                      <div className="h-full bg-primary/60 rounded-full" style={{ width: `${(p.eng / max) * 100}%` }} />
                    </div>
                    <span className="w-28 shrink-0 text-right text-muted-foreground">{p.count} posts · {num(p.eng)} eng</span>
                  </div>
                );
              })}
            </div>
          </Card>

          {top && (
            <Card className="border-primary/30 bg-primary/5">
              <CardTitle>Top mention</CardTitle>
              <div className="flex items-center gap-2 mt-2">
                <Badge tone="primary">{PLATFORM_LABEL[top.platform] || top.platform}</Badge>
                <span className="text-[12px] text-muted-foreground">@{top.author || "unknown"}</span>
                <span className="text-[12px] ml-auto">♥ {num(top.likes)} · 💬 {num(top.comments)}</span>
              </div>
              <p className="text-[14px] mt-2">{top.text}</p>
              {top.url && <a href={top.url} target="_blank" rel="noopener noreferrer" className="text-[12px] text-primary hover:underline mt-2 inline-block">View original →</a>}
            </Card>
          )}
        </>
      )}

      {/* Results */}
      {ran && !busy && (
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Mentions</CardTitle>
            {Object.keys(errors).length > 0 && (
              <span className="text-[11px] text-muted-foreground">
                {Object.entries(errors).map(([k, v]) => `${k}: ${v}`).join(" · ")}
              </span>
            )}
          </div>
          {posts.length === 0 ? (
            <p className="text-muted-foreground text-sm mt-2">No mentions found for “{query}”. Try a broader term or more platforms.</p>
          ) : (
            <div className="flex flex-col divide-y divide-border mt-1">
              {posts.map((p) => (
                <div key={p.platform + p.id} className="py-3 flex gap-3">
                  <Badge tone="muted">{PLATFORM_LABEL[p.platform] || p.platform}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] text-muted-foreground flex items-center gap-2">
                      <span className="font-medium text-foreground">@{p.author || "unknown"}</span>
                      {p.subreddit && <span>· r/{p.subreddit}</span>}
                      <span className="ml-auto">♥ {num(p.likes)} · 💬 {num(p.comments)}{p.views != null ? ` · ▶ ${num(p.views)}` : ""}</span>
                    </div>
                    <p className="text-[13px] mt-1">{p.text}</p>
                    <div className="flex items-center gap-3 mt-1.5">
                      {p.url && <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-[11px] text-muted-foreground hover:underline">View original →</a>}
                      <button
                        onClick={() => draftFrom(p)}
                        disabled={drafting === p.platform + p.id}
                        className="text-[11px] text-primary hover:underline disabled:opacity-50 inline-flex items-center gap-1"
                      >
                        {drafting === p.platform + p.id ? <Spinner /> : "✦ Draft content from this →"}
                      </button>
                    </div>
                    {draftMsg?.id === p.platform + p.id && (
                      <div className={`mt-2 rounded-md border p-2 text-[12px] ${draftMsg.ok ? "border-primary/40 bg-primary/10 text-foreground" : "border-destructive/40 bg-destructive/10 text-foreground"}`}>
                        {draftMsg.text}{" "}
                        {draftMsg.ok && <a href="/queue" className="text-primary underline font-medium">Open Approval Queue →</a>}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
