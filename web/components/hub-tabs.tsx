"use client";

/**
 * HubTabs — a shared header tab strip that visually unifies a group of
 * related pages into one "hub" (e.g. Brand = Voice + Rules + Health;
 * Research = Listen + Trends + People). Drop it at the top of every
 * sub-page in the group; active state is purely path-based. Generalizes
 * the MediaTabs pattern so any cluster of pages feels like one surface.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

export type HubTab = { href: string; label: string; sub?: string };

export function HubTabs({ tabs }: { tabs: HubTab[] }) {
  const path = usePathname();
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border pb-3">
      {tabs.map((t) => {
        const active = path === t.href || path.startsWith(t.href + "/");
        return (
          <Link
            key={t.href}
            href={t.href}
            title={t.sub}
            className={`text-[13px] px-3.5 py-2 rounded-md border transition-colors ${
              active
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background border-border hover:bg-muted"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}

export const BRAND_TABS: HubTab[] = [
  { href: "/brand", label: "Voice Rules", sub: "Brand voice & strict guidelines" },
  { href: "/voice-studio", label: "Voice Studio", sub: "Feed the voice from Drive (transcribe & learn)" },
  { href: "/jp-live", label: "Brand Health", sub: "Live status of what's connected" },
];

export const RESEARCH_TABS: HubTab[] = [
  { href: "/social-listening", label: "Listen", sub: "Brand mentions across X/IG/TikTok/Reddit" },
  { href: "/market-research", label: "Trends & Research", sub: "Viral trends + topic research" },
  { href: "/social-companion", label: "People", sub: "Creators & competitors to watch" },
];
