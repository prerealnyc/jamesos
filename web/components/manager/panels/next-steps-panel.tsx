"use client";

/**
 * Next steps — the ordered setup checklist from GET /manager/next-steps.
 * Done steps are subdued, running steps spin, and the first pending step is
 * "You are here" (ported from bm2.0 NextStepsCard; the inline interview lives
 * on /intake here, so known steps deep-link instead of expanding in place).
 */

import Link from "next/link";

import { Card, Spinner } from "@/components/ui";
import { ErrorBox, Loading, useLoad } from "@/components/manager/primitives";
import { goToSection } from "@/components/manager/section-nav";
import { managerApi, type NextStep } from "@/lib/manager-api";
import { cx, fmtNum } from "./bits";

/** Known step keys -> where the work happens. Unknown keys render without an
 * action (the contract says new steps may appear). */
const STEP_TARGET: Record<string, { label: string; href?: string; section?: string }> = {
  research_profile: { label: "Open intake", href: "/intake" },
  answer_questions: { label: "Answer now", href: "/intake" },
  connect_accounts: { label: "Open settings", href: "/settings" },
  build_voice: { label: "Go to Brand Voice", section: "voice" },
  track_peers: { label: "Go to Competitors", section: "peers" },
  weekly_prescription: { label: "Draft the plan", href: "/manager/plan" },
  review_queue: { label: "Go to The Work", section: "work" },
};

export function NextStepsPanel({ refreshKey }: { refreshKey: number }) {
  const steps = useLoad<NextStep[]>(() => managerApi.nextSteps(), [refreshKey]);

  const list = steps.data ?? [];
  const hereIndex = list.findIndex((s) => s.state === "pending");

  return (
    <Card variant="compact">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Next steps</h3>
        {steps.loading && steps.data && <Spinner />}
      </div>

      {steps.error && <ErrorBox message={steps.error} onRetry={steps.reload} />}
      {steps.loading && !steps.data && <Loading label="Loading the checklist…" />}
      {steps.data && list.length === 0 && (
        <p className="text-sm text-muted-foreground">All caught up — nothing on the setup list.</p>
      )}

      {list.length > 0 && (
        <ol className="m-0 list-none space-y-0.5 p-0">
          {list.map((s, i) => {
            const running = s.state === "running";
            const done = s.state === "done";
            const here = !running && i === hereIndex;
            const target = STEP_TARGET[s.step];
            return (
              <li
                key={s.step}
                className={cx(
                  "flex items-start gap-2.5 rounded-md px-2 py-1.5",
                  here && "border border-border bg-background",
                  done && "opacity-60",
                )}
              >
                <span
                  className={cx(
                    "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                    done ? "bg-accent/20 text-accent" : "bg-secondary text-muted-foreground",
                  )}
                  aria-hidden
                >
                  {done ? "✓" : running ? <Spinner /> : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <span className="text-sm font-medium">{s.label}</span>
                  {here && (
                    <span className="ml-2 rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[.4px] text-primary">
                      You are here
                    </span>
                  )}
                  {s.count !== null && s.count > 0 && (
                    <span className="ml-2 text-xs font-semibold text-muted-foreground">{fmtNum(s.count)}</span>
                  )}
                  {s.hint && <div className="text-xs text-muted-foreground">{s.hint}</div>}
                </div>
                {target && !done && (
                  <span className="shrink-0">
                    {target.href ? (
                      <Link
                        href={target.href}
                        className={cx(
                          "inline-flex items-center rounded-md px-2.5 py-1 text-xs font-semibold transition-colors",
                          here
                            ? "bg-primary text-primary-foreground hover:bg-primary/90"
                            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                        )}
                      >
                        {target.label}
                      </Link>
                    ) : (
                      <button
                        type="button"
                        onClick={() => target.section && goToSection(target.section)}
                        className={cx(
                          "inline-flex items-center rounded-md px-2.5 py-1 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          here
                            ? "bg-primary text-primary-foreground hover:bg-primary/90"
                            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                        )}
                      >
                        {target.label}
                      </button>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
