"use client";

/**
 * Analytics — performance for the BRAND's own social accounts.
 *
 * Scoped to handles configured in /analytics/accounts; peer /
 * competitor data on the watchlist is intentionally excluded so the
 * dashboard answers "how is OUR content doing." Cohort comparison
 * lives in Market Research instead.
 *
 * Sections (only render when ≥1 brand account is configured):
 *   1. Accounts management — add / remove brand handles
 *   2. Filter bar   — account / platform / days
 *   3. Summary cards — totals + engagement rate + median outlier
 *   4. Best post    — the breakout, by outlier score
 *   5. Timeline     — daily views (lightweight inline SVG bar chart)
 *   6. Top posts    — sortable table
 *   7. Accounts leaderboard — side-by-side when the brand owns
 *      multiple accounts (personal IG + brand IG + TikTok)
 *
 * Honest scope: scrapes IG / TikTok / YouTube via Apify; no LinkedIn
 * / X yet; no follower-count growth (we don't capture follower counts).
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, PageHeader, Badge, Spinner } from "@/components/ui";

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

export default function AnalyticsPage() {
  const [handles, setHandles] = useState<Handle[]>([]);
  const [handle, setHandle] = useState("");
  const [platform, setPlatform] = useState("");
  const [days, setDays] = useState(30);
  const [sort, setSort] = useState("outlier");

  type Summary = Awaited<ReturnType<typeof api.analyticsSummary>>;
  type Cohort = Awaited<ReturnType<typeof api.analyticsCohort>>["rows"];
  type Timeline = Awaited<ReturnType<typeof api.analyticsTimeline>>["timeline"];

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

  const maxViews = useMemo(
    () => Math.max(1, ...timeline.map((t) => t.views)),
    [timeline],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        sub="Performance for the brand's own social accounts. Peer / competitor data lives in Market Research."
      />

      {/* ── Accounts management ────────────────────────────────── */}
      <Card className="!p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[13px] font-medium">Brand accounts</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Only these handles are tracked. Add the brand's own Instagram / TikTok / YouTube here.
            </p>
          </div>
          <Button
            variant="secondary"
            onClick={refreshFromSocial}
            disabled={refreshing || accounts.length === 0}
            className="text-[12px] !px-3 !py-1"
            title={accounts.length === 0 ? "Add at least one account first" : "Scrape recent posts"}
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
      </Card>

      {notice && (
        <Card className="border-primary/40 bg-primary/5 !p-3">
          <p className="text-[12px]">{notice}</p>
        </Card>
      )}

      {accounts.length === 0 ? (
        <Card className="!p-6 text-center space-y-2">
          <p className="text-[14px] font-medium">No brand accounts yet</p>
          <p className="text-[12px] text-muted-foreground max-w-md mx-auto">
            Add the brand's own Instagram / TikTok / YouTube handles above. Then click <b>Refresh from social</b> to pull their recent posts.
          </p>
        </Card>
      ) : (
        <>
      {/* ── Filter bar ─────────────────────────────────────────── */}
      <Card className="flex flex-wrap items-center gap-3 !p-3">
        <div className="flex items-center gap-2">
          <span className="text-[12px] text-muted-foreground">Account</span>
          <select
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            className="text-[12px] px-2 py-1 rounded border border-border bg-background min-w-[160px]"
          >
            <option value="">All my accounts</option>
            {accounts.map((a) => (
              <option key={`${a.platform}:${a.handle}`} value={a.handle}>
                @{a.handle} ({platformChip(a.platform)})
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[12px] text-muted-foreground">Platform</span>
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            className="text-[12px] px-2 py-1 rounded border border-border bg-background"
          >
            <option value="">All</option>
            <option value="instagram">Instagram</option>
            <option value="tiktok">TikTok</option>
            <option value="youtube">YouTube</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[12px] text-muted-foreground">Window</span>
          <div className="flex items-center gap-1">
            {DAYS_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`text-[12px] px-2.5 py-1 rounded border transition-colors ${
                  days === d
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-background border-border hover:bg-muted"
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <Button
          variant="secondary"
          onClick={loadEverything}
          className="text-[12px] !px-3 !py-1 ml-auto"
        >
          Refresh
        </Button>
      </Card>

      {err && (
        <Card className="border-destructive/40 bg-destructive/10">
          <p className="text-destructive text-[13px]">✗ {err}</p>
        </Card>
      )}

      {loading && !summary ? (
        <div className="flex items-center gap-2 text-muted-foreground"><Spinner /> Loading…</div>
      ) : (
        <>
          {/* ── Summary cards ───────────────────────────────────── */}
          {summary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Card className="!p-4">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Posts</p>
                <p className="text-2xl font-semibold mt-1">{summary.post_count}</p>
                <p className="text-[11px] text-muted-foreground mt-1">in last {days}d</p>
              </Card>
              <Card className="!p-4">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Total views</p>
                <p className="text-2xl font-semibold mt-1">{fmtNum(summary.views)}</p>
                <p className="text-[11px] text-muted-foreground mt-1">
                  {summary.post_count > 0 ? `~${fmtNum(Math.round(summary.views / summary.post_count))} / post` : "—"}
                </p>
              </Card>
              <Card className="!p-4">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Engagement rate</p>
                <p className="text-2xl font-semibold mt-1">{fmtPct(summary.engagement_rate)}</p>
                <p className="text-[11px] text-muted-foreground mt-1">
                  {fmtNum(summary.likes)} likes · {fmtNum(summary.comments)} comments
                </p>
              </Card>
              <Card className="!p-4">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Median outlier</p>
                <p className="text-2xl font-semibold mt-1">
                  {summary.median_outlier > 0 ? `${summary.median_outlier.toFixed(2)}×` : "—"}
                </p>
                <p className="text-[11px] text-muted-foreground mt-1">
                  avg {summary.avg_outlier.toFixed(2)}× · 1.0× = on-trend
                </p>
              </Card>
            </div>
          )}

          {/* ── Best post highlight ────────────────────────────── */}
          {summary?.best_post && (
            <Card className="!p-4">
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-2">
                Breakout — highest outlier score
              </p>
              <div className="flex gap-4 items-start">
                {summary.best_post.thumbnail && (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={summary.best_post.thumbnail}
                    alt="thumbnail"
                    className="w-24 h-24 object-cover rounded bg-muted shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] line-clamp-2 mb-1">
                    {summary.best_post.caption || "(no caption)"}
                  </p>
                  <div className="flex flex-wrap gap-1.5 items-center">
                    <Badge tone="ok">
                      {summary.best_post.outlier_score.toFixed(2)}× outlier
                    </Badge>
                    <Badge tone="accent">{fmtNum(summary.best_post.views)} views</Badge>
                    <Badge tone="muted">{platformChip(summary.best_post.platform)}</Badge>
                    <Badge tone="muted">{fmtDate(summary.best_post.posted_at)}</Badge>
                    <a
                      href={summary.best_post.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[12px] text-primary hover:underline ml-auto"
                    >
                      Open ↗
                    </a>
                  </div>
                </div>
              </div>
            </Card>
          )}

          {/* ── Timeline chart (inline SVG) ────────────────────── */}
          {timeline.length > 0 && (
            <Card className="!p-4">
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-3">
                Daily views — last {days} days
              </p>
              <div className="flex items-end gap-[2px] h-24">
                {timeline.map((t) => {
                  const h = t.views > 0 ? Math.max(2, (t.views / maxViews) * 100) : 0;
                  return (
                    <div
                      key={t.date}
                      className="flex-1 bg-primary/30 hover:bg-primary/60 rounded-t transition-colors"
                      style={{ height: `${h}%` }}
                      title={`${t.date}: ${fmtNum(t.views)} views, ${t.posts} posts`}
                    />
                  );
                })}
              </div>
              <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                <span>{timeline[0]?.date}</span>
                <span>{timeline[timeline.length - 1]?.date}</span>
              </div>
            </Card>
          )}

          {/* ── Top posts table ─────────────────────────────────── */}
          <Card className="!p-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <p className="text-[13px] font-medium">Top posts</p>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-muted-foreground">Sort by</span>
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                  className="text-[11px] px-2 py-1 rounded border border-border bg-background"
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </div>
            </div>
            {posts.length === 0 ? (
              <p className="text-muted-foreground text-[13px] p-4">
                No posts in this window. Try a longer time range or run
                a refresh from Market Research.
              </p>
            ) : (
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
                              <img
                                src={p.thumbnail}
                                alt=""
                                className="w-10 h-10 object-cover rounded bg-muted shrink-0"
                              />
                            )}
                            <a
                              href={p.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="line-clamp-2 hover:underline"
                              title={p.caption}
                            >
                              {p.caption || "(no caption)"}
                            </a>
                          </div>
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <Badge tone="muted">{platformChip(p.platform)}</Badge>{" "}
                          @{p.handle}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(p.views)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtPct(p.engagement_rate)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          <span className={p.outlier_score >= 1.5 ? "text-emerald-500 font-medium" : ""}>
                            {p.outlier_score.toFixed(2)}×
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Math.round(p.velocity))}/hr</td>
                        <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{fmtDate(p.posted_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* ── Cohort leaderboard ──────────────────────────────── */}
          {cohort.length > 0 && (
            <Card className="!p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <p className="text-[13px] font-medium">Accounts side-by-side</p>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Every brand account ranked by total views in the window. Click a row to filter the rest of the page to just that account.
                </p>
              </div>
              <table className="w-full text-[12px]">
                <thead className="bg-muted/40 text-muted-foreground">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Handle</th>
                    <th className="text-right px-3 py-2 font-medium">Posts</th>
                    <th className="text-right px-3 py-2 font-medium">Views</th>
                    <th className="text-right px-3 py-2 font-medium">Engagement</th>
                    <th className="text-right px-3 py-2 font-medium">Median outlier</th>
                  </tr>
                </thead>
                <tbody>
                  {cohort.map((r) => (
                    <tr
                      key={`${r.platform}:${r.handle}`}
                      className={`border-t border-border hover:bg-muted/20 cursor-pointer ${
                        handle === r.handle ? "bg-primary/5" : ""
                      }`}
                      onClick={() => { setHandle(r.handle); setPlatform(r.platform); }}
                    >
                      <td className="px-3 py-2">
                        <Badge tone="muted">{platformChip(r.platform)}</Badge>{" "}
                        @{r.handle}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{r.posts}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmtNum(r.views)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmtNum(r.engagement)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        <span className={r.median_outlier >= 1.2 ? "text-emerald-500 font-medium" : ""}>
                          {r.median_outlier > 0 ? `${r.median_outlier.toFixed(2)}×` : "—"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </>
      )}
        </>
      )}
    </div>
  );
}
