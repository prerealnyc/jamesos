"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const STORAGE_KEY = "jos.onboarding.dismissed.v1";

type StepKey = "keys" | "accounts" | "hero" | "render";

type Step = {
  key: StepKey;
  label: string;
  href: string;
  done: boolean;
  note: string;
};

export function OnboardingChecklist() {
  const [ready, setReady] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      if (window.localStorage.getItem(STORAGE_KEY) === "1") {
        setDismissed(true);
      }
    } catch {
      /* ignore storage errors */
    }

    let cancelled = false;
    (async () => {
      const [accountsR, productionsR, heroR] = await Promise.allSettled([
        api.listBrandAccounts(),
        api.listProductions(),
        api.getHeroContext(),
      ]);

      const accountsCount =
        accountsR.status === "fulfilled" ? (accountsR.value.accounts?.length ?? 0) : 0;
      const productionsCount =
        productionsR.status === "fulfilled" ? (productionsR.value?.length ?? 0) : 0;
      const heroPhotos =
        heroR.status === "fulfilled" ? (heroR.value.photo_count ?? 0) : 0;

      const next: Step[] = [
        {
          key: "keys",
          label: "Configure API keys",
          href: "/settings",
          done: productionsCount >= 1,
          note:
            productionsCount >= 1
              ? "OpenAI · Anthropic · Creatomate · HeyGen · Runway · Apify"
              : "OpenAI · Anthropic · Creatomate · HeyGen · Runway · Apify",
        },
        {
          key: "accounts",
          label: "Add your brand accounts",
          href: "/analytics",
          done: accountsCount >= 1,
          note:
            accountsCount >= 1
              ? `${accountsCount} configured`
              : "Track your own social handles for analytics",
        },
        {
          key: "hero",
          label: "Upload hero photos",
          href: "/hero",
          done: heroPhotos >= 1,
          note:
            heroPhotos >= 1
              ? `${heroPhotos} uploaded`
              : "Optional — used by story modes for consistent character rendering",
        },
        {
          key: "render",
          label: "Render your first video",
          href: "/video",
          done: productionsCount >= 1,
          note:
            productionsCount >= 1
              ? `${productionsCount} rendered`
              : "Pick a maker from Video Studio",
        },
      ];

      if (!cancelled) {
        setSteps(next);
        setReady(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (typeof window === "undefined") return null;
  if (dismissed) return null;
  if (!ready) return null;

  const remaining = steps.filter((s) => !s.done).length;
  if (remaining === 0) return null;

  function dismiss() {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  }

  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">Get started</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            {remaining} {remaining === 1 ? "step" : "steps"} remaining
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          Dismiss
        </button>
      </div>
      <ul className="mt-3 space-y-2">
        {steps.map((s) => (
          <li key={s.key} className="flex items-start gap-3">
            <span
              aria-hidden
              className={`mt-0.5 text-sm ${s.done ? "text-accent" : "text-muted-foreground"}`}
            >
              {s.done ? "✓" : "○"}
            </span>
            <div className="flex-1">
              <Link
                href={s.href}
                className={`text-sm font-medium hover:underline ${s.done ? "text-muted-foreground line-through" : "text-foreground"}`}
              >
                {s.label}
              </Link>
              <p className="text-xs text-muted-foreground mt-0.5">{s.note}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
