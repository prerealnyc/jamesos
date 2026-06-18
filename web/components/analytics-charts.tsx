"use client";

/**
 * Presentational chart primitives for the Analytics dashboard. All SVG —
 * no chart dependency — themed via the app's CSS tokens (hsl(var(--…))).
 * Kept dumb: they take already-shaped data and render. Number/percent
 * formatting is duplicated here (tiny) so the components are self-contained.
 */

import React from "react";

function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return `${Math.round(n)}`;
}

/** Premium KPI stat card with a colored accent rail and optional context. */
export function StatCard({
  label,
  value,
  sub,
  hint,
  tone = "primary",
}: {
  label: string;
  value: string;
  sub?: string;
  hint?: string;
  tone?: "primary" | "accent" | "neutral";
}) {
  const rail =
    tone === "accent"
      ? "bg-accent"
      : tone === "neutral"
        ? "bg-muted-foreground/40"
        : "bg-primary";
  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-card p-5 shadow-sm">
      <span className={`absolute left-0 top-0 h-full w-1 ${rail}`} />
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1.5 text-[28px] leading-none font-bold tabular-nums">{value}</p>
      {sub && <p className="mt-2 text-[11px] text-muted-foreground">{sub}</p>}
      {hint && <p className="mt-0.5 text-[10px] text-muted-foreground/70">{hint}</p>}
    </div>
  );
}

/**
 * Responsive area+line chart of a daily series. preserveAspectRatio="none"
 * lets it fill the card width; the line uses non-scaling-stroke so it stays
 * crisp, and axis labels live in HTML around the SVG (not inside it) so text
 * is never distorted. Hover columns expose per-day values via <title>.
 */
export function ViewsAreaChart({
  data,
  height = 200,
}: {
  data: { date: string; views: number; posts: number }[];
  height?: number;
}) {
  const W = 720;
  const H = height;
  const padT = 10;
  const padB = 8;
  const n = data.length;
  const max = Math.max(1, ...data.map((d) => d.views));
  const innerH = H - padT - padB;
  const x = (i: number) => (n <= 1 ? W / 2 : (i / (n - 1)) * W);
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const pts = data.map((d, i) => `${x(i).toFixed(1)},${y(d.views).toFixed(1)}`);
  const line = pts.length ? `M ${pts.join(" L ")}` : "";
  const area = pts.length
    ? `M ${x(0).toFixed(1)},${(padT + innerH).toFixed(1)} L ${pts.join(" L ")} L ${x(n - 1).toFixed(1)},${(padT + innerH).toFixed(1)} Z`
    : "";
  const grid = [0, 0.5, 1].map((f) => padT + innerH - f * innerH);
  const colW = n > 1 ? W / (n - 1) : W;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      style={{ height }}
      preserveAspectRatio="none"
      aria-hidden
    >
      <defs>
        <linearGradient id="viewsFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.32" />
          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {grid.map((gy, i) => (
        <line
          key={i}
          x1={0}
          y1={gy}
          x2={W}
          y2={gy}
          stroke="hsl(var(--border))"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
          strokeDasharray={i === 0 ? "" : "3 4"}
          opacity={0.6}
        />
      ))}
      {area && <path d={area} fill="url(#viewsFill)" />}
      {line && (
        <path
          d={line}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={2.2}
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {data.map((d, i) => (
        <rect
          key={d.date}
          x={x(i) - colW / 2}
          y={padT}
          width={colW}
          height={innerH}
          fill="transparent"
          className="hover:fill-primary/10"
        >
          <title>{`${d.date}: ${fmtNum(d.views)} views · ${d.posts} post${d.posts === 1 ? "" : "s"}`}</title>
        </rect>
      ))}
    </svg>
  );
}

/** Horizontal follower-share bars, one row per platform. */
export function PlatformBars({
  rows,
}: {
  rows: {
    platform: string;
    label: string;
    followers: number | null;
    posts: number;
  }[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.followers || 0));
  return (
    <div className="space-y-3.5">
      {rows.map((r) => {
        const f = r.followers || 0;
        const pct = r.followers === null ? 0 : (f / max) * 100;
        return (
          <div key={r.platform}>
            <div className="mb-1 flex items-center justify-between text-[12px]">
              <span className="font-medium">{r.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {r.followers === null ? "— followers" : `${fmtNum(f)} followers`}
                <span className="opacity-50"> · </span>
                {fmtNum(r.posts)} posts
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${r.followers === null ? 0 : Math.max(pct, 4)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Compact ranked bar — used in the per-account comparison leaderboard. */
export function RankBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-accent"
        style={{ width: `${Math.max(pct, value > 0 ? 3 : 0)}%` }}
      />
    </div>
  );
}
