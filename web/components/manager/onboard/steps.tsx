"use client";

/**
 * Progress stepper across the top of the onboarding wizard — the bm2.0
 * onboard page's Steps strip (numbered pills, ✓ once passed), restyled onto
 * this app's Tailwind HSL-var tokens.
 */

export const STEPS = ["Basics", "Is this your brand?", "What we found", "Interview", "Brand Voice"];

function cx(...c: (string | false | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Stepper({ current }: { current: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-2" aria-label="Onboarding progress">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const active = n === current;
        const done = n < current;
        return (
          <li
            key={label}
            aria-current={active ? "step" : undefined}
            className={cx(
              "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium",
              active && "border-primary/50 bg-primary/10 text-foreground",
              done && "border-border bg-secondary text-muted-foreground",
              !active && !done && "border-border text-muted-foreground",
            )}
          >
            <span
              className={cx(
                "inline-flex h-[18px] w-[18px] items-center justify-center rounded-full text-[10px] font-semibold",
                active && "bg-primary text-primary-foreground",
                done && "bg-accent/20 text-accent",
                !active && !done && "bg-secondary text-muted-foreground",
              )}
            >
              {done ? "✓" : n}
            </span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}
