"use client";

/**
 * RenderTracker — a live "where is my video render" view: a stage stepper,
 * what it's doing right now, a progress bar, elapsed time, and an approximate
 * ETA. Videos only. Reads the server-computed `progress` on a Production
 * (video_pipeline._progress), so it stays consistent everywhere it's shown.
 */

import type { Production } from "@/lib/api";
import { Spinner } from "@/components/ui";

const STAGES: { key: Production["status"]; label: string }[] = [
  { key: "queued", label: "Queued" },
  { key: "planning", label: "Planning" },
  { key: "rendering_clips", label: "Rendering clips" },
  { key: "assembling", label: "Assembling" },
  { key: "succeeded", label: "Done" },
];

function fmtDur(s: number): string {
  if (!s || s <= 0) return "0s";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export function RenderTracker({ prod, compact = false }: { prod: Production; compact?: boolean }) {
  const p = prod.progress;
  const failed = prod.status === "failed";
  const done = prod.status === "succeeded";
  const pct = failed ? 100 : p?.pct ?? (done ? 100 : 4);
  const curIdx = STAGES.findIndex((s) => s.key === prod.status);

  return (
    <div className="flex flex-col gap-2">
      {/* Stage stepper */}
      <div className="flex items-end gap-1.5">
        {STAGES.map((s, i) => {
          const active = i === curIdx && !done && !failed;
          const passed = done || (curIdx >= 0 && i < curIdx);
          return (
            <div key={s.key} className="flex-1 flex flex-col items-center gap-1">
              <div
                className={`h-1.5 w-full rounded-full transition-colors ${
                  failed
                    ? "bg-destructive/40"
                    : passed
                    ? "bg-primary"
                    : active
                    ? "bg-primary/70 animate-pulse"
                    : "bg-secondary"
                }`}
              />
              {!compact && (
                <span
                  className={`text-[10px] leading-tight text-center ${
                    active ? "text-foreground font-medium" : "text-muted-foreground"
                  }`}
                >
                  {s.label}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* What it's doing + ETA / elapsed */}
      {!done && !failed && (
        <div className="flex items-center justify-between gap-3">
          <span className="text-[12px] text-foreground flex items-center gap-1.5 min-w-0">
            <Spinner />
            <span className="truncate">{p?.label || prod.status}</span>
          </span>
          <span className="text-[11px] text-muted-foreground whitespace-nowrap">
            {p?.eta_s ? `~${fmtDur(p.eta_s)} left` : ""}
            {p?.elapsed_s ? ` · ${fmtDur(p.elapsed_s)} in` : ""}
          </span>
        </div>
      )}

      {/* Progress bar */}
      <div className="h-2 w-full rounded-full bg-secondary overflow-hidden">
        <div
          className={`h-full transition-all duration-500 ${failed ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {failed && prod.error && (
        <p className="text-[12px] text-destructive">✗ {prod.error}</p>
      )}
      {done && (
        <p className="text-[12px] text-primary">✓ Render complete — find it in the Output Library.</p>
      )}
    </div>
  );
}
