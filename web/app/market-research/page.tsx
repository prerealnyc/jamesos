"use client";

import { useEffect, useState } from "react";
import { api, type ResearchResponse, type Trend } from "@/lib/api";
import { Button, Card, Input, Spinner, Badge, PageHeader } from "@/components/ui";
import { TrendCard, ALL_PLATFORMS } from "@/components/trends";

type Tab = "trends" | "topic";

// Roster creator shape (from api.researchRoster()).
type RosterCreator = {
  platform: string;
  handle: string;
  name?: string;
  interests?: string[];
  post_count: number;
  last_post_at: string | null;
};

function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  const days = Math.floor(ms / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Short platform badge — IG / TT / YT etc. from a platform string.
function platformBadge(platform: string): string {
  const p = platform.trim().toLowerCase();
  const map: Record<string, string> = {
    instagram: "IG",
    ig: "IG",
    tiktok: "TT",
    tt: "TT",
    youtube: "YT",
    yt: "YT",
    twitter: "X",
    x: "X",
    linkedin: "LI",
    facebook: "FB",
    threads: "TH",
  };
  return map[p] || platform.slice(0, 2).toUpperCase();
}

export default function MarketResearchPage() {
  const [tab, setTab] = useState<Tab>("trends");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Social Research"
        sub="The creators and influencers we track, what's going viral in your space, and live peer intel — all saved to memory."
      />
      <InfluencerRoster />
      <div className="flex gap-2">
        <TabButton active={tab === "trends"} onClick={() => setTab("trends")}>
          Trend radar
        </TabButton>
        <TabButton active={tab === "topic"} onClick={() => setTab("topic")}>
          Topic research
        </TabButton>
      </div>
      {tab === "trends" ? <TrendRadar /> : <TopicResearch />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-semibold rounded-md transition-colors ${
        active
          ? "bg-primary text-primary-foreground"
          : "bg-secondary text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

// ── Influencer roster: creators we scrape weekly via Apify ──

function InfluencerRoster() {
  const [creators, setCreators] = useState<RosterCreator[]>([]);
  const [status, setStatus] = useState<{ last_refresh: string | null; due: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.researchRoster().catch(() => ({ creators: [] as RosterCreator[] })),
      api.researchRosterStatus().catch(() => null),
    ])
      .then(([roster, st]) => {
        setCreators(roster.creators || []);
        setStatus(st);
      })
      .finally(() => setLoading(false));
  }, []);

  async function refresh() {
    setRefreshing(true);
    setNote(null);
    try {
      await api.refreshResearchRoster();
      setNote("Scraping latest posts… check back in a minute.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not start refresh.");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-semibold text-[15px]">Influencers we track</h2>
            {status?.due && <Badge tone="accent">due for refresh</Badge>}
          </div>
          <p className="text-[12px] text-muted-foreground mt-1">
            Last scraped {status ? fmtRelative(status.last_refresh) : "…"} · auto-refreshes weekly.
          </p>
        </div>
        <Button onClick={refresh} disabled={refreshing}>
          {refreshing ? <Spinner /> : "Refresh now"}
        </Button>
      </div>

      {note && (
        <div className="mt-4 text-[12px] text-primary border border-primary/40 rounded-md p-3">
          {note}
        </div>
      )}

      {loading ? (
        <div className="mt-5 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Spinner /> Loading roster…
        </div>
      ) : creators.length === 0 ? (
        <div className="mt-5 text-[13px] text-muted-foreground border border-dashed border-border rounded-md p-4">
          No influencers tracked yet. Add creators in the watchlist below and they&apos;ll be scraped weekly.
        </div>
      ) : (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {creators.map((c, i) => (
            <div
              key={`${c.platform}:${c.handle}:${i}`}
              className="bg-background border border-border rounded-md p-3 flex flex-col gap-2"
            >
              <div className="flex items-center gap-2">
                <Badge tone="primary">{platformBadge(c.platform)}</Badge>
                <span className="text-[13px] font-semibold truncate">@{c.handle}</span>
              </div>
              {c.name && (
                <div className="text-[12px] text-muted-foreground truncate">{c.name}</div>
              )}
              {c.interests && c.interests.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {c.interests.slice(0, 4).map((tag) => (
                    <span
                      key={tag}
                      className="text-[11px] rounded-full px-2 py-0.5 border border-border text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between text-[11px] text-muted-foreground mt-auto pt-1">
                <span>{c.post_count} posts tracked</span>
                <span>{c.last_post_at ? fmtRelative(c.last_post_at) : "no posts yet"}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── Trend radar: discover viral posts by topic, score, ingest, scriptify ──

function TrendRadar() {
  const [topic, setTopic] = useState("");
  const [platforms, setPlatforms] = useState<string[]>([...ALL_PLATFORMS]);
  const [loading, setLoading] = useState(false);
  const [trends, setTrends] = useState<Trend[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Show whatever's already in memory on load.
  useEffect(() => {
    api
      .listTrends()
      .then((r) => {
        setTrends(r.trends);
        setNote(r.note);
      })
      .catch(() => {});
  }, []);

  function togglePlatform(p: string) {
    setPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    );
  }

  async function discover() {
    const t = topic.trim();
    if (!t || platforms.length === 0) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await api.discoverTrends(t, platforms);
      setTrends(r.trends);
      setNote(r.note);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "discovery failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-col gap-3">
          <Input
            placeholder="Topic or hashtag — e.g. 'real estate tips', 'home staging', 'NYC apartments'"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && discover()}
            autoFocus
          />
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex gap-2">
              {ALL_PLATFORMS.map((p) => (
                <button
                  key={p}
                  onClick={() => togglePlatform(p)}
                  className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
                    platforms.includes(p)
                      ? "border-primary text-foreground bg-primary/10"
                      : "border-border text-muted-foreground"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <Button onClick={discover} disabled={loading}>
              {loading ? <Spinner /> : "Discover viral content"}
            </Button>
          </div>
        </div>

        {err && (
          <div className="mt-4 text-sm text-destructive border border-destructive/40 rounded-md p-3">
            Error: {err}
          </div>
        )}
        {note && (
          <div className="mt-4 text-[12px] text-primary border border-primary/40 rounded-md p-3">
            {note}
          </div>
        )}
      </Card>

      {trends.length > 0 && (
        <>
          <div className="text-[12px] text-muted-foreground flex items-center gap-2">
            <Badge tone="primary">{trends.length} ranked by outlier score</Badge>
            <span>Highest break-out first. “Make script” writes it in your brand voice.</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {trends.map((t, i) => (
              <TrendCard key={t.event_id || t.url || i} trend={t} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Topic research: Perplexity live intelligence (unchanged behavior) ──

function TopicResearch() {
  const [subject, setSubject] = useState("");
  const [focus, setFocus] = useState("");
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<ResearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    const s = subject.trim();
    if (!s) return;
    setLoading(true);
    setErr(null);
    setRes(null);
    try {
      setRes(await api.research(s, focus.trim()));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "research failed");
    } finally {
      setLoading(false);
    }
  }

  const isStub = res?.provider === "stub";

  return (
    <Card>
      <div className="flex flex-col gap-3">
        <Input
          placeholder="Subject — a topic, company, or person to research"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <div className="flex gap-3">
          <Input
            placeholder="Optional focus — e.g. 'their content style and what's trending'"
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <Button onClick={run} disabled={loading}>
            {loading ? <Spinner /> : "Research"}
          </Button>
        </div>
      </div>

      {err && (
        <div className="mt-4 text-sm text-destructive border border-destructive/40 rounded-md p-3">
          Error: {err}
        </div>
      )}

      {res && (
        <div className="mt-5 rounded-lg border border-border p-4 bg-background">
          {isStub && (
            <div className="mb-3 text-[12px] text-primary border border-primary/40 rounded-md p-3">
              No live research provider connected. Add a Perplexity API key in{" "}
              <b>Settings</b> to get real internet intelligence.
            </div>
          )}
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-semibold text-[15px]">{res.subject}</h2>
            <Badge tone={isStub ? "muted" : "primary"}>via {res.provider}</Badge>
          </div>
          <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{res.summary}</p>

          {res.findings.length > 0 && (
            <div className="mt-4">
              <div className="text-xs uppercase tracking-[.5px] text-muted-foreground mb-2">
                Findings
              </div>
              <ul className="flex flex-col gap-2">
                {res.findings.map((f, i) => (
                  <li
                    key={i}
                    className="bg-card border border-border rounded-md p-3 text-[13px] leading-relaxed"
                  >
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {res.sources.length > 0 && (
            <div className="mt-4">
              <div className="text-xs uppercase tracking-[.5px] text-muted-foreground mb-2">
                Sources ({res.sources.length})
              </div>
              <div className="flex flex-col gap-1">
                {res.sources.map((s, i) => (
                  <a
                    key={i}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[12px] text-muted-foreground hover:text-primary truncate"
                  >
                    {s.title ? `${s.title} — ` : ""}
                    {s.url}
                  </a>
                ))}
              </div>
            </div>
          )}

          <div className="mt-4 pt-3 border-t border-border text-[12px] text-muted-foreground">
            {res.ingested_into_memory ? (
              <>
                saved to memory · <b className="text-foreground">{res.stored_event_ids.length}</b>{" "}
                events · citable from Ask + Content Studio
              </>
            ) : (
              "not saved to memory"
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
