"use client";

import { useState } from "react";
import { api, type AskResponse } from "@/lib/api";
import { Button, Card, Input, Spinner, Badge } from "@/components/ui";

export default function AskPage() {
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<AskResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function ask() {
    const question = q.trim();
    if (!question) return;
    setLoading(true);
    setErr(null);
    setRes(null);
    try {
      setRes(await api.ask(question));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Ask the memory</h1>
        <p className="text-muted text-sm mt-1">
          Every answer is grounded in ingested events and cited. If it can&apos;t be grounded,
          it refuses — it does not guess.
        </p>
      </header>

      <Card>
        <div className="flex gap-3">
          <Input
            placeholder="e.g. What did we decide about Spaceport pricing?"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            autoFocus
          />
          <Button onClick={ask} disabled={loading}>
            {loading ? <Spinner /> : "Ask"}
          </Button>
        </div>

        {err && (
          <div className="mt-4 text-sm text-bad border border-bad/40 rounded-lg p-3">
            Error: {err}
          </div>
        )}

        {res && (
          <div
            className={`mt-5 rounded-xl border p-4 bg-panel2 ${
              res.refused ? "border-warn" : "border-border"
            }`}
          >
            {res.refused ? (
              <>
                <div className="text-warn font-semibold text-xs uppercase tracking-[.5px] mb-2">
                  ⚠ Refused — not grounded in memory
                </div>
                <p className="text-[15px] leading-relaxed">{res.refusal_reason}</p>
              </>
            ) : (
              <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{res.response}</p>
            )}

            {res.citations.length > 0 && (
              <div className="mt-4 flex flex-col gap-2">
                {res.citations.map((c, i) => (
                  <div key={i} className="bg-bg border border-border rounded-lg p-3 text-[13px]">
                    <div>&ldquo;{c.span}&rdquo;</div>
                    <div className="text-muted text-[11px] font-mono mt-1">
                      source {c.event_id.slice(0, 8)} · confidence {(c.confidence * 100).toFixed(0)}%
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-4 text-[12px] text-muted">
              <span>
                confidence <b className="text-ink">{(res.confidence * 100).toFixed(0)}%</b>
              </span>
              <span>
                retrieved <b className="text-ink">{res.retrieved_event_ids.length}</b> events
              </span>
              <span>
                model <b className="text-ink">{res.model}</b>
              </span>
              <span>
                latency <b className="text-ink">{res.latency_ms} ms</b>
              </span>
            </div>
          </div>
        )}
      </Card>

      <div className="text-xs text-muted flex items-center gap-2">
        <Badge tone="accent">cite-or-refuse</Badge>
        <span>+ independent verification pass before any answer is shown.</span>
      </div>
    </div>
  );
}
