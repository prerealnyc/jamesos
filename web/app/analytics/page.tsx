"use client";

/**
 * Analytics — social-performance dashboard for the BRAND's own accounts.
 *
 * Redesigned layout, top → bottom:
 *   1. Control bar      — time window + account filter + refresh
 *   2. KPI hero         — followers / posts / engagement rate / median outlier
 *   3. Daily views      — full-width area chart (SVG, themed)
 *   4. Platform share + Breakout post (two columns)
 *   5. Top posts        — sortable table
 *   6. Accounts side-by-side — cohort leaderboard with ranked bars
 *   7. Connected accounts — live per-account followers / engagement
 *   8. Connected-accounts post browser
 *   9. Manage tracked handles — Apify config drawer (add/remove + scrape)
 *
 * Scoped to handles configured in /analytics/accounts; peer / competitor
 * data on the watchlist is intentionally excluded — this answers "how is
 * OUR content doing." Cohort comparison lives in Market Research instead.
 *
 * Honest scope: live follower counts may be partial (not every provider
 * exposes them); totals are labelled "≥ N" when so. Apify scrapes
 * IG / TikTok / YouTube; no LinkedIn / X yet.
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, CardTitle, PageHeader, Badge, Spinner } from "@/components/ui";
import { ConnectedAccounts } from "@/components/connected-accounts";
import { FilterChip } from "@/components/filter-chip";
import { StatCard, ViewsAreaChart, PlatformBars, RankBar } from "@/components/analytics-charts";

type Handle = { platform: string; handle: string; name?: string; posts: number; last_post_at: string | null };
type BrandAccount = { platform: string; handle: string; name?: string };
type Post = {
  platform: string; handle: string; url: string; caption: string;
  thumbnail: string; views: number; likes: number; comments: number;
  shares: number; engagement_rate: number; outlier_score: number;
  velocity: number; posted_at: string;
};

const DAYS_OPTIONS = [7, 30, 90, 365];
const SORT_OPTIONS: { key: string; label: string }[] = [
  { key: "outlier", label: "Outlier score" },
  { key: "views", label: "Views" },
  { key: "engagement", label: "Engagement" },
  { key: "engagement_rate", label: "Engagement rate" },
  { key: "velocity", label: "Velocity (views/hr)" },
  { key: "recent", label: "Most recent" },
];

function fmtNum(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toString();
}
function fmtPct(x: number): string {
  if (!Number.isFinite(x) || x <= 0) return "—";
  return `${(x * 100).toFixed(2)}%`;
}
function fmtDate(iso: string): string {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
  catch { return iso; }
}
function platformChip(p: string): string {
  return { instagram: "IG", tiktok: "TT", youtube: "YT", linkedin: "LI", twitter: "X" }[p] || p;
}
const PLATFORM_LABEL: Record<string, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  linkedin: "LinkedIn",
  twitter: "X / Twitter",
};
function platformLabel(p: string): string {
  return PLATFORM_LABEL[p] || (p ? p[0].toUpperCase() + p.slice(1) : "—");
}

export default function AnalyticsPage() {
  const [handles, setHandles] = useState<Handle[]>([]);
  const [handle, setHandle] = useState("");
  const [platform, setPlatform] = useState("");
  const [days, setDays] = useState(30);
  const [sort, setSort] = useState("outlier");

  type Summary = Awaited<ReturnType<typeof api.analyticsSummary>>;
  type Cohort = Awaited<ReturnType<typeof api.analyticsCohort>>["rows"];
  type Timeline = Awaited<ReturnType<typeof api.analyticsTimeline>>["timeline"];
  type LiveSummary = Awaited<ReturnType<typeof api.analyticsLiveSummary>>;
  type LiveBreakdown = Awaited<ReturnType<typeof api.analyticsLiveBreakdown>>;

  const [summary, setSummary] = useState<Summary | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [timeline, setTimeline] = useState<Timeline>([]);
  const [cohort, setCohort] = useState<Cohort>([]);
  const [accounts, setAccounts] = useState<BrandAccount[]>([]);
  const [newPlatform, setNewPlatform] = useState("instagram");
  const [newHandle, setNewHandle] = useState("");
  const [newName, setNewName] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [savingAccounts, setSavingAccounts] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");

  // Live dashboard (aggregated across connected accounts) — independent
  // of the brand-handle / Apify pipeline below.
  const [live, setLive] = useState<LiveSummary | null>(null);
  const [breakdown, setBreakdown] = useState<LiveBreakdown | null>(null);
  const [liveLoading, setLiveLoading] = useState(true);
  const [liveErr, setLiveErr] = useState("");

  // Apify-scraped handle management lives in a collapsed drawer.
  const [manageOpen, setManageOpen] = useState(false);

  async function loadLive() {
    setLiveLoading(true); setLiveErr("");
    try {
      const [s, b] = await Promise.all([
        api.analyticsLiveSummary(),
        api.analyticsLiveBreakdown(),
      ]);
      setLive(s);
      setBreakdown(b);
    } catch (e) {
      setLiveErr(e instanceof Error ? e.message : "could not load live analytics");
    } finally {
      setLiveLoading(false);
    }
  }

  async function loadEverything() {
    setLoading(true); setErr("");
    try {
      const [h, s, p, t, c, a] = await Promise.all([
        api.listAnalyticsHandles(),
        api.analyticsSummary({ handle, platform, days }),
        api.analyticsPosts({ handle, platform, days, sort, limit: 30 }),
        api.analyticsTimeline({ handle, platform, days }),
        api.analyticsCohort({ platform, days }),
        api.listBrandAccounts(),
      ]);
      setHandles(h.handles);
      setSummary(s);
      setPosts(p.posts);
      setTimeline(t.timeline);
      setCohort(c.rows);
      setAccounts(a.accounts);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadLive(); }, []);
  useEffect(() => { loadEverything(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [handle, platform, days, sort]);

  async function addAccount() {
    const handle = newHandle.trim().replace(/^@/, "").toLowerCase();
    if (!handle) return;
    if (accounts.some((a) => a.platform === newPlatform && a.handle === handle)) {
      setErr(`@${handle} on ${newPlatform} is already tracked`);
      return;
    }
    setSavingAccounts(true); setErr("");
    try {
      const next = [
        ...accounts,
        { platform: newPlatform, handle, name: newName.trim() },
      ];
      const r = await api.setBrandAccounts(next);
      setAccounts(r.accounts);
      setNewHandle(""); setNewName("");
      await loadEverything();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not add");
    } finally {
      setSavingAccounts(false);
    }
  }

  async function removeAccount(target: BrandAccount) {
    setSavingAccounts(true); setErr("");
    try {
      const next = accounts.filter(
        (a) => !(a.platform === target.platform && a.handle === target.handle),
      );
      const r = await api.setBrandAccounts(next);
      setAccounts(r.accounts);
      if (handle === target.handle) setHandle("");
      await loadEverything();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not remove");
    } finally {
      setSavingAccounts(false);
    }
  }

  async function refreshFromSocial() {
    if (accounts.length === 0) {
      setErr("Add at least one brand account first.");
      return;
    }
    setRefreshing(true); setErr(""); setNotice("");
    try {
      const r = await api.refreshAnalytics(30);
      setNotice(
        r.note
          ? r.note
          : `Scraped ${r.scraped} posts, stored ${r.stored} new${r.provider ? ` via ${r.provider}` : ""}.`,
      );
      await loadEverything();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  const liveEmpty =
    !liveLoading && !liveErr && (live?.account_count ?? 0) === 0;

  // ── Derived view-model for the redesigned dashboard ──
  const platformRows = (live?.per_platform || []).map((r) => ({
    platform: r.platform,
    label: platformLabel(r.platform),
    followers: r.followers_known ? r.followers : null,
    posts: r.posts,
  }));
  const cohortMaxViews = Math.max(1, ...cohort.map((r) => r.views));
  const hasApify =
    !!summary && (summary.post_count > 0 || timeline.length > 0 || posts.length > 0);
  const timelineTotal = timeline.reduce((s, t) => s + t.views, 0);
  const timelineAvg = timeline.length ? Math.round(timelineTotal / timeline.length) : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        sub="Performance across the brand's connected social accounts. Peer / competitor data lives in Market Research."
      />

      {/* ── 1. Control bar ─────────────────────────────────────── */}
      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Window</span>
            <div className="flex items-center gap-1">
              {DAYS_OPTIONS.map((d) => (
                <FilterChip key={d} active={days === d} onClick={() => setDays(d)}>{d}d</FilterChip>
              ))}
            </div>
          </div>
          {accounts.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Account</span>
              <select
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                className="text-[12px] px-2 py-1 rounded border border-border bg-background min-w-[150px]"
              >
                <option value="">All my accounts</option>
                {accounts.map((a) => (
                  <option key={`${a.platform}:${a.handle}`} value={a.handle}>@{a.handle} ({platformChip(a.platform)})</option>
                ))}
              </select>
            </div>
          )}
          <div className="ml-auto">
            <Button
              variant="secondary"
              onClick={() => { loadLive(); loadEverything(); }}
              className="text-[12px] !px-3 !py-1.5"
            >
              {loading || liveLoading ? <Spinner /> : "↻ Refresh"}
            </Button>
          </div>
        </div>
      </Card>

      {liveErr && (
        <Card className="border-destructive/40 bg-destructive/10">
          <p className="text-destructive text-[13px]">✗ {liveErr}</p>
        </Card>
      )}

      {/* Loading skeleton for the KPI hero */}
      {liveLoading && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i} className="!p-5">
              <div className="h-3 w-20 bg-muted rounded animate-pulse" />
              <div className="h-8 w-24 bg-muted rounded mt-3 animate-pulse" />
            </Card>
          ))}
        </div>
      )}

      {/* Empty state — nothing connected and nothing tracked */}
      {liveEmpty && accounts.length === 0 && (
        <Card className="!p-8 text-center space-y-3">
          <p className="text-[15px] font-medium">No accounts connected yet</p>
          <p className="text-[13px] text-muted-foreground max-w-md mx-auto">
            Connect the brand&apos;s social accounts to see followers, posts and
            engagement aggregated here — or add public handles below to scrape
            post performance.
          </p>
          <div className="flex items-center justify-center gap-3 pt-1">
            <a href="/settings" className="text-[13px] text-primary hover:underline">Connect in Settings ↗</a>
            <span className="text-muted-foreground">·</span>
            <button onClick={() => setManageOpen(true)} className="text-[13px] text-primary hover:underline">Add tracked handles ↓</button>
          </div>
        </Card>
      )}

      {/* ── 2. KPI hero ────────────────────────────────────────── */}
      {!liveLoading && live && !liveEmpty && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Total followers"
            value={`${live.followers_partial ? "≥ " : ""}${fmtNum(live.total_followers)}`}
            sub={`${live.account_count} account${live.account_count === 1 ? "" : "s"} · ${live.platform_count} platform${live.platform_count === 1 ? "" : "s"}`}
            tone="primary"
          />
          <StatCard
            label="Total posts"
            value={fmtNum(live.total_posts)}
            sub={hasApify ? `${summary!.post_count} in last ${days}d` : "all-time, where reported"}
            tone="neutral"
          />
          {hasApify ? (
            <>
              <StatCard
                label="Engagement rate"
                value={fmtPct(summary!.engagement_rate)}
                sub={`${fmtNum(summary!.likes)} likes · ${fmtNum(summary!.comments)} comments`}
                tone="accent"
              />
              <StatCard
                label="Median outlier"
                value={summary!.median_outlier > 0 ? `${summary!.median_outlier.toFixed(2)}×` : "—"}
                sub={`avg ${summary!.avg_outlier.toFixed(2)}× · 1.0× = on-trend`}
                tone="primary"
              />
            </>
          ) : (
            <>
              <StatCard label="Accounts" value={`${live.account_count}`} sub="connected profiles" tone="accent" />
              <StatCard label="Platforms" value={`${live.platform_count}`} sub="distinct networks" tone="primary" />
            </>
          )}
        </div>
      )}

      {/* ── 3. Daily views ─────────────────────────────────────── */}
      {timeline.length > 0 && (
        <Card>
          <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
            <CardTitle>Daily views — last {days} days</CardTitle>
            <p className="text-[11px] text-muted-foreground">
              <span className="tabular-nums font-medium text-foreground">{fmtNum(timelineTotal)}</span> total
              <span className="opacity-50"> · </span>
              <span className="tabular-nums">{fmtNum(timelineAvg)}</span>/day avg
            </p>
          </div>
          <ViewsAreaChart data={timeline} />
          <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
            <span>{fmtDate(timeline[0]?.date)}</span>
            <span>{fmtDate(timeline[timeline.length - 1]?.date)}</span>
          </div>
        </Card>
      )}

      {/* ── 4. Platform share + Breakout post ──────────────────── */}
      {!liveLoading && live && !liveEmpty && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {platformRows.length > 0 && (
            <Card>
              <CardTitle>Followers by platform</CardTitle>
              <PlatformBars rows={platformRows} />
            </Card>
          )}
          {summary?.best_post ? (
            <Card>
              <CardTitle>Breakout — top outlier</CardTitle>
              <div className="flex gap-4 items-start">
                {summary.best_post.thumbnail && (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={summary.best_post.thumbnail} alt="" className="w-20 h-20 object-cover rounded-md bg-muted shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] line-clamp-2 mb-2">{summary.best_post.caption || "(no caption)"}</p>
                  <div className="flex flex-wrap gap-1.5 items-center">
                    <Badge tone="ok">{summary.best_post.outlier_score.toFixed(2)}× outlier</Badge>
                    <Badge tone="primary">{fmtNum(summary.best_post.views)} views</Badge>
                    <Badge tone="muted">{platformChip(summary.best_post.platform)}</Badge>
                    <a href={summary.best_post.url} target="_blank" rel="noopener noreferrer" className="text-[12px] text-primary hover:underline ml-auto">Open ↗</a>
                  </div>
                </div>
              </div>
            </Card>
          ) : (
            <Card className="flex items-center justify-center text-center">
              <p className="text-[12px] text-muted-foreground max-w-xs">
                Add brand handles and run a refresh to surface your breakout post,
                top content and engagement trends.
              </p>
            </Card>
          )}
        </div>
      )}

      {/* ── 5. Top posts ───────────────────────────────────────── */}
      {posts.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <p className="text-[13px] font-semibold">Top posts</p>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">Sort by</span>
              <select value={sort} onChange={(e) => setSort(e.target.value)} className="text-[11px] px-2 py-1 rounded border border-border bg-background">
                {SORT_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">Post</th>
                  <th className="text-left px-3 py-2 font-medium">Handle</th>
                  <th className="text-right px-3 py-2 font-medium">Views</th>
                  <th className="text-right px-3 py-2 font-medium">ER</th>
                  <th className="text-right px-3 py-2 font-medium">Outlier</th>
                  <th className="text-right px-3 py-2 font-medium">Velocity</th>
                  <th className="text-left px-3 py-2 font-medium">Posted</th>
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => (
                  <tr key={p.url} className="border-t border-border hover:bg-muted/20">
                    <td className="px-3 py-2">
                      <div className="flex gap-2 items-start max-w-[360px]">
                        {p.thumbnail && (
                          /* eslint-disable-next-line @next/next/no-img-element */
                          <img src={p.thumbnail} alt="" className="w-10 h-10 object-cover rounded bg-muted shrink-0" />
                        )}
                        <a href={p.url} target="_blank" rel="noopener noreferrer" className="line-clamp-2 hover:underline" title={p.caption}>{p.caption || "(no caption)"}</a>
                      </div>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap"><Badge tone="muted">{platformChip(p.platform)}</Badge> @{p.handle}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(p.views)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtPct(p.engagement_rate)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <span className={p.outlier_score >= 1.5 ? "text-accent font-semibold" : ""}>{p.outlier_score.toFixed(2)}×</span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Math.round(p.velocity))}/hr</td>
                    <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{fmtDate(p.posted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── 6. Accounts side-by-side (cohort) ──────────────────── */}
      {cohort.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-[13px] font-semibold">Accounts side-by-side</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">Ranked by total views in the window. Click a row to filter the page to that account.</p>
          </div>
          <table className="w-full text-[12px]">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Handle</th>
                <th className="text-left px-3 py-2 font-medium w-[30%]">Views</th>
                <th className="text-right px-3 py-2 font-medium">Posts</th>
                <th className="text-right px-3 py-2 font-medium">Engagement</th>
                <th className="text-right px-3 py-2 font-medium">Median outlier</th>
              </tr>
            </thead>
            <tbody>
              {cohort.map((r) => (
                <tr
                  key={`${r.platform}:${r.handle}`}
                  className={`border-t border-border hover:bg-muted/20 cursor-pointer ${handle === r.handle ? "bg-primary/5" : ""}`}
                  onClick={() => { setHandle(r.handle); setPlatform(r.platform); }}
                >
                  <td className="px-3 py-2 whitespace-nowrap"><Badge tone="muted">{platformChip(r.platform)}</Badge> @{r.handle}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="tabular-nums w-12 shrink-0">{fmtNum(r.views)}</span>
                      <RankBar value={r.views} max={cohortMaxViews} />
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.posts}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(r.engagement)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    <span className={r.median_outlier >= 1.2 ? "text-accent font-semibold" : ""}>{r.median_outlier > 0 ? `${r.median_outlier.toFixed(2)}×` : "—"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* ── 7. Per-account live breakdown ──────────────────────── */}
      {!liveLoading && breakdown && !liveEmpty && breakdown.rows.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-[13px] font-semibold">Connected accounts — live</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="text-left px-4 py-2.5 font-medium">Account</th>
                  <th className="text-right px-4 py-2.5 font-medium">Posts</th>
                  <th className="text-right px-4 py-2.5 font-medium">Followers</th>
                  <th className="text-right px-4 py-2.5 font-medium">Engagement</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.rows.map((r) => (
                  <tr key={`${r.platform}:${r.handle}:${r.id}`} className="border-t border-border hover:bg-muted/20">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <Badge tone="muted">{platformChip(r.platform)}</Badge>
                        <span className="font-medium">@{r.handle}</span>
                        {r.name && r.name !== r.handle && <span className="text-muted-foreground text-[11px]">· {r.name}</span>}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtNum(r.posts)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{r.followers === null ? "—" : fmtNum(r.followers)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{r.recent_engagement === null ? "—" : fmtNum(r.recent_engagement)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {((breakdown.notes && breakdown.notes.length > 0) || (live?.notes && live.notes.length > 0)) && (
            <div className="px-4 py-3 border-t border-border space-y-1">
              {[...(breakdown.notes || []), ...(live?.notes || [])].map((n, i) => (
                <p key={i} className="text-[11px] text-muted-foreground">· {n}</p>
              ))}
            </div>
          )}
        </Card>
      )}

      {notice && (
        <Card className="border-primary/40 bg-primary/5 !p-3"><p className="text-[12px]">{notice}</p></Card>
      )}
      {err && (
        <Card className="border-destructive/40 bg-destructive/10"><p className="text-destructive text-[13px]">✗ {err}</p></Card>
      )}

      {/* ── 8. Connected accounts (live post-browsing component) ── */}
      <ConnectedAccounts />

      {/* ── 9. Manage tracked handles (Apify config drawer) ────── */}
      <Card className="!p-0 overflow-hidden">
        <button
          onClick={() => setManageOpen((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/20"
        >
          <div>
            <p className="text-[13px] font-medium">Manage tracked handles</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Apify public scrape — add brand handles, then pull recent posts to power the charts above.
            </p>
          </div>
          <span className="text-muted-foreground text-[13px]">{manageOpen ? "▲" : "▼"}</span>
        </button>

        {manageOpen && (
          <div className="border-t border-border p-4 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[13px] font-medium">Brand accounts</p>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Only these handles are scraped. Add the brand&apos;s own Instagram / TikTok / YouTube.
                </p>
              </div>
              <Button
                variant="secondary"
                onClick={refreshFromSocial}
                disabled={refreshing || accounts.length === 0}
                className="text-[12px] !px-3 !py-1"
                title={accounts.length === 0
                  ? "Add at least one brand account first — there's nothing to scrape yet."
                  : "Scrape recent posts from your configured handles via Apify."}
              >
                {refreshing ? <Spinner /> : "Refresh from social"}
              </Button>
            </div>
            {accounts.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {accounts.map((a) => (
                  <span
                    key={`${a.platform}:${a.handle}`}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted border border-border text-[12px]"
                  >
                    <Badge tone="muted">{platformChip(a.platform)}</Badge>
                    <span>@{a.handle}</span>
                    {a.name && <span className="text-muted-foreground text-[11px]">· {a.name}</span>}
                    <button
                      onClick={() => removeAccount(a)}
                      disabled={savingAccounts}
                      className="text-muted-foreground hover:text-destructive ml-1"
                      title="Remove this account"
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border">
              <select
                value={newPlatform}
                onChange={(e) => setNewPlatform(e.target.value)}
                className="text-[12px] px-2 py-1.5 rounded border border-border bg-background"
              >
                <option value="instagram">Instagram</option>
                <option value="tiktok">TikTok</option>
                <option value="youtube">YouTube</option>
              </select>
              <input
                placeholder="handle (no @)"
                value={newHandle}
                onChange={(e) => setNewHandle(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addAccount(); }}
                className="text-[12px] px-2 py-1.5 rounded border border-border bg-background min-w-[160px]"
              />
              <input
                placeholder="display name (optional)"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addAccount(); }}
                className="text-[12px] px-2 py-1.5 rounded border border-border bg-background min-w-[180px]"
              />
              <Button
                onClick={addAccount}
                disabled={!newHandle.trim() || savingAccounts}
                className="text-[12px] !px-3 !py-1.5"
              >
                {savingAccounts ? <Spinner /> : "+ Add account"}
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
