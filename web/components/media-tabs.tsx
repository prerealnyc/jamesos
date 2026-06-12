"use client";

/**
 * MediaTabs — shared header tab strip rendered at the top of every
 * Media Library sub-page. Visually unifies Reference + Hero libraries
 * so they feel like one library with two views, not two pages.
 *
 * Active state is purely path-based — drop this component at the top
 * of any /jp-clips or /hero page and it lights up the right tab.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  {
    href: "/jp-clips",
    label: "Reference Library",
    sub: "Style refs, James clips, B-roll",
  },
  {
    href: "/hero",
    label: "Hero Library",
    sub: "Photos + videos of the brand's hero",
  },
  {
    href: "/broll",
    label: "B-roll Library",
    sub: "Reusable cutaway clips — generated (Runway/Higgsfield) + uploads",
  },
  {
    href: "/style-templates",
    label: "Style Templates",
    sub: "Reference styles, reverse-engineered to replicate",
  },
];

export function MediaTabs() {
  const path = usePathname();
  return (
    <div className="flex items-center gap-1.5 border-b border-border pb-3">
      {TABS.map((t) => {
        const active = path === t.href || path.startsWith(t.href + "/");
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`text-[13px] px-3.5 py-2 rounded-md border transition-colors ${
              active
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background border-border hover:bg-muted"
            }`}
            title={t.sub}
          >
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}
