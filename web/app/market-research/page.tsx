"use client";

import { useEffect, useState } from "react";
import { api, type ResearchResponse, type Trend } from "@/lib/api";
import { Button, Card, Input, Spinner, Badge, PageHeader } from "@/components/ui";
import { TrendCard, ALL_PLATFORMS } from "@/components/trends";

type Tab = "trends" | "topic";

export default function MarketResearchPage() {
  const [tab, setTab] = useState<Tab>("trends");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Market Research"
        sub="Find what's going viral in your space and turn it into on-voice scripts — plus live internet intelligence, all saved to memory."
      />
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
