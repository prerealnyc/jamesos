"use client";

/**
 * Sources — the D12 insight-stream inventory (GET /system/sources: one row per
 * stream with its live/mock/idle status) joined with this tenant's per-stream
 * contribution counts (GET /manager/sources). Ported from the bm2.0 dashboard
 * "Intelligence sources" card, as a dense table with right-aligned counts.
 */

import { Card } from "@/components/ui";
import { ErrorBox, Loading, useLoad } from "@/components/manager/primitives";
import { managerApi, type SystemSource } from "@/lib/manager-api";
import { cx, fmtDate, fmtNum, prettyKey } from "./bits";

// D12: one label per insight stream (sources_api inventory order).
const STREAM_LABELS: Record<string, string> = {
  web_search: "Web search",
  page_scraping: "Page scraping",
  news: "News",
  places: "Places",
  wikipedia: "Wikipedia",
  youtube: "YouTube",
  reddit: "Reddit",
  deep_research: "Deep research",
  llm: "LLM",
  peer_tracking: "Peer tracking",
  social_publishing: "Social publishing",
  transcription: "Transcription",
};

const DOT_CLS: Record<SystemSource["status"], string> = {
  live: "bg-accent",
  mock: "bg-muted-foreground/50",
  idle: "bg-warning",
};

/** Contributing streams first (insight count desc); stable sort keeps the
 * inventory order for the rest. */
function orderStreams(streams: SystemSource[], contributions: Record<string, number>): SystemSource[] {
  return [...streams].sort((a, b) => (contributions[b.stream] ?? 0) - (contributions[a.stream] ?? 0));
}

export function SourcesPanel() {
  const data = useLoad(() => Promise.all([managerApi.systemSources(), managerApi.tenantSources()]), []);

  return (
    <Card variant="compact">
      {data.loading && !data.data && <Loading label="Loading the source inventory…" />}
      {data.error && <ErrorBox message={data.error} onRetry={data.reload} />}

      {data.data &&
        (() => {
          const [inventory, contrib] = data.data;
          const sections = Object.entries(contrib.profile_sections ?? {}).filter(([, n]) => n > 0);
          return (
            <>
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="m-0 text-xs text-muted-foreground">
                  Every stream feeding the brand's intelligence — one key, one job, additive (D12).
                </p>
                <span className="shrink-0 text-[11px] uppercase tracking-[.4px] text-muted-foreground">
                  env · {inventory.env}
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-[11px] uppercase tracking-[.4px] text-muted-foreground">
                      <th className="py-1.5 pr-3 font-semibold" aria-label="Status" />
                      <th className="py-1.5 pr-3 font-semibold">Stream</th>
                      <th className="py-1.5 pr-3 font-semibold">Provider</th>
                      <th className="py-1.5 text-right font-semibold">Insights</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orderStreams(inventory.sources, contrib.contributions).map((s) => {
                      const count = contrib.contributions[s.stream];
                      return (
                        <tr key={s.stream} className="border-b border-border/60" title={s.note || undefined}>
                          <td className="w-6 py-1.5 pr-3">
                            <span
                              className={cx("inline-block h-2 w-2 rounded-full", DOT_CLS[s.status] ?? "bg-muted-foreground/50")}
                              aria-label={s.status}
                              title={s.status}
                            />
                          </td>
                          <td className="py-1.5 pr-3 font-medium">{STREAM_LABELS[s.stream] ?? prettyKey(s.stream)}</td>
                          <td className="max-w-[320px] truncate py-1.5 pr-3 text-muted-foreground">
                            {s.stream === "llm" && s.chain && s.chain.length > 0 ? s.chain.join(" → ") : s.provider_impl}
                          </td>
                          <td className="py-1.5 text-right font-semibold">
                            {count !== undefined ? fmtNum(count) : <span className="font-normal text-muted-foreground">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {sections.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
                    Profile sections
                  </span>
                  {sections.map(([k, n]) => (
                    <span
                      key={k}
                      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
                    >
                      {prettyKey(k)} <b className="text-foreground">{fmtNum(n)}</b>
                    </span>
                  ))}
                </div>
              )}

              <p className="m-0 mt-2 text-xs text-muted-foreground">
                {contrib.last_run
                  ? `Last research run ${fmtDate(contrib.last_run)} · green live, grey mock, amber idle.`
                  : "No research run yet — insight counts appear after the first confirmed research."}
              </p>
            </>
          );
        })()}
    </Card>
  );
}
