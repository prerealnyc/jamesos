"use client";

/**
 * Connected accounts — live read of every brand profile reachable
 * across Meta Graph (Pages + IG Business) + PostProxy (11 platforms).
 *
 * Shown on /analytics. Distinct from the "Brand accounts" Apify-
 * scraping panel right above it — those are public handles we
 * scrape. THIS one is authenticated-API connected: Meta token,
 * PostProxy API key, real numbers.
 *
 * Click a profile to expand recent posts inline. No re-render of
 * the parent — useState + a tiny fetcher per profile.
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Spinner } from "@/components/ui";

type Connections = Awaited<ReturnType<typeof api.listConnections>>;
type Profile = Connections["profiles"][number];
type PostList = Awaited<ReturnType<typeof api.listProfilePosts>>;
type Post = PostList["posts"][number];

function fmtNum(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

const PLATFORM_LABEL: Record<string, { icon: string; tone: "muted" | "accent" | "ok" | "destructive" }> = {
  instagram: { icon: "IG", tone: "accent" },
  facebook: { icon: "FB", tone: "muted" },
  tiktok: { icon: "TT", tone: "accent" },
  youtube: { icon: "YT", tone: "destructive" },
  linkedin: { icon: "LI", tone: "muted" },
  twitter: { icon: "X", tone: "muted" },
  x: { icon: "X", tone: "muted" },
  threads: { icon: "TH", tone: "muted" },
  pinterest: { icon: "PI", tone: "destructive" },
  bluesky: { icon: "BS", tone: "muted" },
  telegram: { icon: "TG", tone: "muted" },
  google_business: { icon: "GB", tone: "muted" },
};

const PROVIDER_LABEL: Record<string, string> = {
  meta: "Meta Graph",
  postproxy: "PostProxy",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  } catch { return iso; }
}

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  try {
    const ms = new Date(iso).getTime() - Date.now();
    return Math.floor(ms / 86_400_000);
  } catch { return null; }
}

export function ConnectedAccounts() {
  const [data, setData] = useState<Connections | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [postsErr, setPostsErr] = useState("");
  const [postsLoading, setPostsLoading] = useState(false);

  async function load() {
    setLoading(true); setErr("");
    try {
      setData(await api.listConnections());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function openProfile(p: Profile) {
    const key = `${p.provider}:${p.id}`;
    if (expanded === key) { setExpanded(null); setPosts(null); setPostsErr(""); return; }
    setExpanded(key); setPosts(null); setPostsErr(""); setPostsLoading(true);
    try {
      const r = await api.listProfilePosts(p.provider, p.id, p.platform, 12);
      if (r.error) setPostsErr(r.error);
      setPosts(r.posts || []);
    } catch (e) {
      setPostsErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setPostsLoading(false);
    }
  }

  if (loading && !data) {
    return (
      <Card variant="compact" className="!p-3">
        <div className="flex items-center gap-2 text-muted-foreground text-[12px]">
          <Spinner /> Probing Meta + PostProxy…
        </div>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card variant="compact" className="!p-3">
        <p className="text-[12px] text-destructive">✗ {err || "Could not load connections."}</p>
      </Card>
    );
  }

  const profiles = data.profiles;
  const summary = data.by_platform;

  return (
    <Card className="!p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium">Connected accounts · live</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {data.total} profile{data.total === 1 ? "" : "s"} reachable via Meta Graph + PostProxy. Click any to load recent posts.
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={load}
          disabled={loading}
          className="text-[12px] !px-3 !py-1"
        >
          {loading ? <Spinner /> : "Refresh"}
        </Button>
      </div>

      {/* Provider health row */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {Object.entries(data.providers).map(([name, p]) => {
          const tone =
            !p.configured ? "muted" :
            p.ok ? "ok" : "destructive";
          return (
            <span key={name} className="inline-flex items-center gap-1.5">
              <Badge tone={tone}>
                {PROVIDER_LABEL[name] || name}{" "}
                {p.configured ? (p.ok ? "✓" : "✗") : "—"}
              </Badge>
              {p.error && (
                <span className="text-destructive truncate max-w-[280px]" title={p.error}>
                  {p.error}
                </span>
              )}
            </span>
          );
        })}
      </div>

      {/* Per-platform counts */}
      {Object.keys(summary).length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          {Object.entries(summary).sort(([, a], [, b]) => b - a).map(([pl, n]) => (
            <Badge key={pl} tone="muted">
              {PLATFORM_LABEL[pl]?.icon || pl} · {n}
            </Badge>
          ))}
        </div>
      )}

      {/* Profile rows */}
      {profiles.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No profiles connected. Paste a Meta token or PostProxy key in /settings, then connect accounts on the provider's dashboard.
        </p>
      ) : (
        <ul className="divide-y divide-border border border-border rounded-md">
          {profiles.map((p) => {
            const key = `${p.provider}:${p.id}`;
            const pl = PLATFORM_LABEL[p.platform] || { icon: p.platform.slice(0, 2).toUpperCase(), tone: "muted" as const };
            const expires = daysUntil(p.expires_at);
            const expiresSoon = expires !== null && expires <= 14;
            const isOpen = expanded === key;
            return (
              <li key={key} className="text-[12px]">
                <button
                  onClick={() => openProfile(p)}
                  className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-muted/40 transition-colors text-left"
                >
                  <Badge tone={pl.tone}>{pl.icon}</Badge>
                  <span className="font-medium">{p.name || p.handle || "(no name)"}</span>
                  <span className="text-muted-foreground">@{p.handle}</span>
                  {p.post_count > 0 && (
                    <Badge tone="muted">{p.post_count} posts</Badge>
                  )}
                  {p.status !== "active" && (
                    <Badge tone="destructive">{p.status}</Badge>
                  )}
                  {expiresSoon && (
                    <Badge tone="destructive">
                      expires {expires! < 0 ? "expired" : `in ${expires}d`}
                    </Badge>
                  )}
                  <span className="ml-auto text-[11px] text-muted-foreground">
                    via {PROVIDER_LABEL[p.provider] || p.provider} {isOpen ? "▼" : "▶"}
                  </span>
                </button>
                {isOpen && (
                  <div className="px-3 pb-3 pt-2 border-t border-border bg-muted/20">
                    {postsLoading ? (
                      <p className="text-[11px] text-muted-foreground flex items-center gap-2 py-2">
                        <Spinner /> Fetching posts…
                      </p>
                    ) : postsErr ? (
                      <p className="text-[11px] text-destructive py-2">✗ {postsErr}</p>
                    ) : posts && posts.length > 0 ? (
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {posts.map((post) => (
                          <a
                            key={post.id}
                            href={post.permalink || "#"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block rounded-md border border-border bg-background overflow-hidden hover:border-primary transition-colors"
                          >
                            {post.thumbnail ? (
                              /* eslint-disable-next-line @next/next/no-img-element */
                              <img
                                src={post.thumbnail}
                                alt=""
                                className="w-full aspect-video object-cover bg-muted"
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-full aspect-video bg-muted flex items-center justify-center text-muted-foreground text-[10px]">
                                no thumbnail
                              </div>
                            )}
                            <div className="p-2 space-y-1">
                              {post.title && (
                                <p className="text-[11px] font-medium line-clamp-1">{post.title}</p>
                              )}
                              <p className="text-[10px] text-muted-foreground line-clamp-2 leading-snug">
                                {post.caption || "(no caption)"}
                              </p>
                              <div className="flex items-center gap-2 text-[10px] text-muted-foreground pt-0.5">
                                {post.views != null && <span>▶ {fmtNum(post.views)}</span>}
                                {post.likes != null && <span>♥ {fmtNum(post.likes)}</span>}
                                {post.comments != null && <span>💬 {fmtNum(post.comments)}</span>}
                                <span className="ml-auto">{fmtDate(post.posted_at)}</span>
                              </div>
                            </div>
                          </a>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[11px] text-muted-foreground py-2">No posts found.</p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
