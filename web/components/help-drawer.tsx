"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { TUTORIALS, type Tutorial } from "@/lib/tutorials";

/**
 * Self-documenting tutorial button. Drops a "How it works" button into
 * every tool's header (via PageHeader) and, on the home/landing routes,
 * can be rendered standalone. It looks the current route up in the
 * TUTORIALS map and renders nothing when there's no entry — so adding a
 * tutorial is a one-line edit to lib/tutorials.ts, never a page change.
 */
export function HelpButton({ routeOverride }: { routeOverride?: string }) {
  const path = usePathname();
  const key = routeOverride ?? path ?? "/";
  const tut = TUTORIALS[key];
  const [open, setOpen] = useState(false);

  // Close on Escape; lock body scroll while the drawer is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!tut) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/60 px-3 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        title={`How ${tut.title} works`}
        aria-label={`How ${tut.title} works`}
      >
        <span className="grid h-4 w-4 place-items-center rounded-full bg-primary/15 text-[10px] font-bold text-primary">
          ?
        </span>
        How it works
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-[1px]"
          onClick={() => setOpen(false)}
        >
          <aside
            className="h-full w-full max-w-md overflow-y-auto border-l border-border bg-background shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 flex items-start justify-between gap-4 border-b border-border bg-background px-6 py-4">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[1.2px] text-primary">
                  How it works
                </div>
                <h2 className="mt-0.5 text-lg font-semibold leading-tight">{tut.title}</h2>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="rounded-md px-2 py-1 text-lg leading-none text-muted-foreground hover:bg-secondary hover:text-foreground"
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div className="space-y-6 px-6 py-5">
              <p className="text-[14px] leading-relaxed text-foreground">{tut.what}</p>

              {tut.when && (
                <div className="rounded-md border border-border bg-secondary/40 p-3 text-[12.5px] leading-relaxed text-muted-foreground">
                  <span className="font-semibold text-foreground">When to use it: </span>
                  {tut.when}
                </div>
              )}

              <div>
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-[1px] text-muted-foreground">
                  Steps
                </div>
                <ol className="space-y-2.5">
                  {tut.steps.map((s, i) => (
                    <li key={i} className="flex gap-3 text-[13.5px] leading-relaxed">
                      <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary/15 text-[11px] font-bold text-primary">
                        {i + 1}
                      </span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ol>
              </div>

              {tut.tips && tut.tips.length > 0 && (
                <div>
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-[1px] text-muted-foreground">
                    Good to know
                  </div>
                  <ul className="space-y-2">
                    {tut.tips.map((t, i) => (
                      <li key={i} className="flex gap-2 text-[12.5px] leading-relaxed text-muted-foreground">
                        <span className="text-primary">•</span>
                        <span>{t}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

export type { Tutorial };
