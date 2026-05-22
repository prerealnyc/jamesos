"use client";

import { useState } from "react";
import { api, type Trend, type ContentDraft } from "@/lib/api";
import { Badge, Button, Spinner } from "@/components/ui";

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n ?? 0);
}

const PLATFORM_TONE: Record<string, "primary" | "accent" | "muted"> = {
  instagram: "accent",
  tiktok: "primary",
  youtube: "destructive" as "primary",
};

/** One viral post, with metrics, outlier score, and a "Make script" action
 *  that runs the brand-voice script generator off this trend. */
export function TrendCard({ trend }: { trend: Trend }) {
  const [draft, setDraft] = useState<ContentDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function makeScript() {
    if (!trend.event_id) {
      setErr("This trend isn't saved to memory yet — run a discovery first.");
      return;
    }
    setLoading(true);
    setErr(null);
    try {
      setDraft(await api.generateScript(trend.event_id, trend.platform));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "script generation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="border border-border rounded-lg p-4 bg-card flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone={PLATFORM_TONE[trend.platform] || "muted"}>{trend.platform}</Badge>
          <span className="text-sm font-semibold">@{trend.handle || "unknown"}</span>
        </div>
        {trend.outlier_score > 0 && (
          <span
            className="text-[12px] font-bold text-primary"
            title="Views ÷ this creator's median — how much it broke out"
          >
            {trend.outlier_score}× outlier
          </span>
        )}
      </div>

      {trend.caption && (
        <p className="text-[13px] leading-relaxed text-foreground/90 line-clamp-3">
          {trend.caption}
        </p>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted-foreground">
        <span>▶ {fmt(trend.views)} views</span>
        <span>♥ {fmt(trend.likes)}</span>
        <span>💬 {fmt(trend.comments)}</span>
        {trend.shares > 0 && <span>↗ {fmt(trend.shares)}</span>}
        {trend.velocity > 0 && <span>{fmt(trend.velocity)}/hr</span>}
        {trend.has_transcript && <Badge tone="primary">transcript</Badge>}
      </div>

      <div className="flex items-center gap-2 pt-1">
        {trend.url && (
          <a
            href={trend.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[12px] text-muted-foreground hover:text-primary"
          >
            view original ↗
          </a>
        )}
        <div className="ml-auto">
          <Button variant="secondary" onClick={makeScript} disabled={loading} className="px-3 py-1.5 text-[12px]">
            {loading ? <Spinner /> : "Make script"}
          </Button>
        </div>
      </div>

      {err && <div className="text-[12px] text-destructive">{err}</div>}

      {draft && <ScriptResult draft={draft} />}
    </div>
  );
}

/** Renders the generated brand-voice script + its independent voice-QA. */
export function ScriptResult({ draft }: { draft: ContentDraft }) {
  const passed = draft.qa?.passed;
  return (
    <div className="mt-1 rounded-md border border-border bg-background p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[12px]">
        <Badge tone={passed ? "ok" : "destructive"}>
          voice {draft.voice_score?.toFixed(2) ?? "—"}
        </Badge>
        <span className="text-muted-foreground">
          {draft.status === "not_generated"
            ? "not generated"
            : passed
              ? "on-voice → queued"
              : "flagged → queued for revision"}
        </span>
      </div>

      {draft.draft ? (
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{draft.draft}</p>
      ) : (
        <p className="text-[12px] text-muted-foreground">{draft.note}</p>
      )}

      {draft.qa?.drift && draft.qa.drift.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[.4px]">voice-QA flags: </span>
          {draft.qa.drift.join(" · ")}
        </div>
      )}
      {draft.draft && (
        <div className="text-[11px] text-muted-foreground">
          Saved to the Approval Queue — a human approves before anything ships.
        </div>
      )}
    </div>
  );
}

export const ALL_PLATFORMS = ["instagram", "tiktok", "youtube"] as const;
