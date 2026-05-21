"use client";

import { useState } from "react";
import { api, type ResearchResponse } from "@/lib/api";
import { Button, Card, Input, Spinner, Badge } from "@/components/ui";

const EXAMPLES = [
  "Staten Island commercial real estate market 2026",
  "Spaceport America economic impact New Mexico",
  "what real estate creators are posting that goes viral",
];

export default function MarketResearchPage() {
  const [subject, setSubject] = useState("");
  const [focus, setFocus] = useState("");
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<ResearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run(q?: string) {
    const s = (q ?? subject).trim();
    if (!s) return;
    if (q) setSubject(q);
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
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Market Research</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Live internet intelligence via Perplexity. Every result is ingested into
          memory with its sources, so you can cite it from Ask and build content on it.
        </p>
      </header>

      <Card>
        <div className="flex flex-col gap-3">
          <Input
            placeholder="Subject — a topic, company, person, or trend to research"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            autoFocus
          />
          <div className="flex gap-3">
            <Input
              placeholder="Optional focus — e.g. 'their content style and what's trending'"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
            <Button onClick={() => run()} disabled={loading}>
              {loading ? <Spinner /> : "Research"}
            </Button>
          </div>

          <div className="flex flex-wrap gap-2 pt-1">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => run(ex)}
                disabled={loading}
                className="text-[12px] text-muted-foreground border border-border rounded-full px-3 py-1 hover:border-primary hover:text-foreground transition-colors disabled:opacity-50"
              >
                {ex}
              </button>
            ))}
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
                <b>Settings</b> to get real internet intelligence. The text below is a
                placeholder, not real research.
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

            <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-4 text-[12px] text-muted-foreground">
              <span>
                {res.ingested_into_memory ? (
                  <>
                    saved to memory ·{" "}
                    <b className="text-foreground">{res.stored_event_ids.length}</b> events
                  </>
                ) : (
                  "not saved to memory"
                )}
              </span>
              {res.ingested_into_memory && (
                <span>now citable from Ask the memory + Content Studio</span>
              )}
            </div>
          </div>
        )}
      </Card>

      <div className="text-xs text-muted-foreground flex items-center gap-2">
        <Badge tone="primary">ingest-on-research</Badge>
        <span>Findings are stored with source provenance and reused across the system.</span>
      </div>
    </div>
  );
}
