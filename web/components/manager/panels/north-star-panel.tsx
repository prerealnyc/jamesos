"use client";

/**
 * North Star — the negotiated growth targets that frame everything below.
 * The merged /manager surface has no goal-negotiation route, so targets are
 * shown READ-ONLY from GET /manager/brand-profile: `fields` entries keyed
 * `goals.{platform}.{metric}` render as the donor's target cards (baseline →
 * target span + rationale) with the negotiated provenance badge, and the
 * plain-text `goals` list renders as the qualitative frame.
 */

import { Card } from "@/components/ui";
import { EmptyState, ErrorBox, Loading, SourceBadge, useLoad } from "@/components/manager/primitives";
import { managerApi } from "@/lib/manager-api";
import { fmtNum, prettyKey } from "./bits";

interface GoalValue {
  baseline?: number | null;
  target?: number | null;
  timeframe_days?: number | null;
  rationale?: string | null;
  [key: string]: unknown;
}

interface GoalTarget {
  fieldKey: string;
  platform: string;
  metric: string;
  value: GoalValue;
}

function asGoalValue(v: unknown): GoalValue | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.target !== "number" && typeof o.baseline !== "number") return null;
  return o as GoalValue;
}

/** Pull the `goals.*` fields out of the profile projection. */
function extractTargets(fields: Record<string, unknown>): GoalTarget[] {
  return Object.entries(fields)
    .filter(([k]) => k.startsWith("goals."))
    .map(([fieldKey, raw]): GoalTarget | null => {
      const value = asGoalValue(raw);
      if (!value) return null;
      const parts = fieldKey.split(".");
      return {
        fieldKey,
        platform: parts[1] ?? "overall",
        metric: parts.slice(2).join(".") || "target",
        value,
      };
    })
    .filter((t): t is GoalTarget => t !== null);
}

export function NorthStarPanel() {
  const profile = useLoad(() => managerApi.brandProfile(), []);

  const targets = profile.data ? extractTargets(profile.data.fields ?? {}) : [];
  const textGoals = profile.data?.goals ?? [];
  const hasAnything = targets.length > 0 || textGoals.length > 0;

  return (
    <div className="space-y-3">
      {profile.loading && !profile.data && <Loading label="Loading your north star…" />}
      {profile.error && <ErrorBox message={profile.error} onRetry={profile.reload} />}

      {profile.data && !hasAnything && (
        <EmptyState
          title="No north star yet"
          body="Targets are negotiated from your baseline and peer benchmarks during onboarding and planning — none on file for this brand yet. Draft a weekly plan and the goals snapshot starts here."
        />
      )}

      {textGoals.length > 0 && (
        <Card variant="compact">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="text-sm font-semibold">Goals</h3>
            <SourceBadge source="user_stated" />
          </div>
          <ul className="m-0 list-disc space-y-1 pl-5 text-sm">
            {textGoals.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </Card>
      )}

      {targets.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {targets.map((t) => (
            <TargetCard key={t.fieldKey} target={t} />
          ))}
        </div>
      )}
    </div>
  );
}

/** platform·metric with the baseline → target span, a progress hint when both
 * are known, and the negotiated rationale (donor NorthStarPanel TargetCard). */
function TargetCard({ target }: { target: GoalTarget }) {
  const v = target.value;
  const hasBaseline = typeof v.baseline === "number";

  // A thin bar from baseline toward target — a visual anchor of where the
  // baseline sits on the way to the goal, not a claim of progress made.
  let pct: number | null = null;
  if (hasBaseline && typeof v.target === "number" && v.target !== 0) {
    pct = Math.max(0, Math.min(100, ((v.baseline as number) / v.target) * 100));
  }

  return (
    <Card variant="compact">
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold capitalize">
            {target.platform} · {prettyKey(target.metric).toLowerCase()}
          </div>
          {typeof v.timeframe_days === "number" && (
            <div className="text-xs text-muted-foreground">in {v.timeframe_days} days</div>
          )}
        </div>
        <SourceBadge source="negotiated" />
      </div>

      <div className="flex items-baseline gap-2 text-lg">
        <span className="text-muted-foreground">{hasBaseline ? fmtNum(v.baseline) : "—"}</span>
        <span aria-hidden className="text-muted-foreground">
          →
        </span>
        <b>{fmtNum(v.target)}</b>
      </div>

      {pct !== null && (
        <div aria-hidden className="mt-2 h-1 overflow-hidden rounded-full bg-secondary">
          <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
        </div>
      )}

      {v.rationale && <p className="mt-2 text-xs text-muted-foreground">{v.rationale}</p>}
    </Card>
  );
}
