"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/icons";

type Item = {
  href: string; label: string; sub: string; icon: string; live?: boolean;
  // Extra routes that should keep this sidebar entry highlighted —
  // used by hub entries (Video Studio, Media Library) whose
  // sub-routes live at different URLs.
  match?: string[];
  // Visually nest under the entry above (e.g. Content Studio under
  // Autopilot) — adds left indent + a connector dot.
  indent?: boolean;
};
type Group = { title: string; items: Item[] };

// Sidebar groups — one entry per CONCEPT, not per page. Several pages
// share a single sidebar entry (e.g. all 7 video makers live under one
// "Video Studio" hub at /video; Reference + Hero share "Media Library"
// at /jp-clips with sub-tabs). The active-route logic at render time
// highlights the matching sidebar entry for any of its sub-routes.
const VIDEO_SUBROUTES = [
  "/video", "/long-form", "/engaging-video", "/story-mix",
  "/heygen-video", "/story-video", "/pipeline", "/editor",
];
const MEDIA_SUBROUTES = ["/jp-clips", "/hero"];

const NAV: Group[] = [
  {
    title: "Memory",
    items: [
      { href: "/", label: "Ask the memory", sub: "Grounded, cited Q&A", icon: "ask", live: true },
    ],
  },
  {
    // Autopilot is the headline — one-click content at scale. Content
    // Studio nests under it for the manual path (build one piece by hand).
    title: "Create",
    items: [
      { href: "/autopilot", label: "Autopilot", sub: "One-click multi-day content batches", icon: "queue", live: true },
      { href: "/design-studio", label: "Content Studio", sub: "Manual: write one draft by hand", icon: "design", live: true, indent: true },
      { href: "/video", label: "Video Studio", sub: "All 7 video makers in one place", icon: "pipeline", live: true, match: VIDEO_SUBROUTES, indent: true },
      { href: "/images", label: "Post Images", sub: "AI hero images for posts", icon: "images", live: true, indent: true },
    ],
  },
  {
    title: "Review & Library",
    items: [
      { href: "/queue", label: "Approval Queue", sub: "Videos + posts split, ready to ship", icon: "queue", live: true },
      { href: "/library", label: "Output Library", sub: "Finished content — download & share", icon: "clips", live: true },
      { href: "/updates", label: "What's Next", sub: "Feedback → changes: live + queued", icon: "design", live: true },
      { href: "/jp-clips", label: "Media Library", sub: "Reference clips & hero footage", icon: "clips", live: true, match: MEDIA_SUBROUTES },
      { href: "/style-templates", label: "Style Templates", sub: "Trending styles, reverse-engineered to replicate", icon: "pipeline", live: true },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/analytics", label: "Analytics", sub: "Your brand-account performance", icon: "social", live: true },
      // Social Companion folded in here — peer/competitor research is
      // part of "Social Research" alongside the trend radar.
      { href: "/market-research", label: "Social Research", sub: "Influencers, trends & peer intel", icon: "market", live: true, match: ["/market-research", "/social-companion"] },
    ],
  },
  {
    // Voice & brand health — reference material, not daily-driver. Bottom.
    title: "Brand Reference",
    items: [
      { href: "/voice-studio", label: "Voice Studio", sub: "Feed the voice from Drive (transcribe & learn)", icon: "voice", live: true },
      { href: "/brand", label: "Voice Rules", sub: "Brand voice & guidelines", icon: "voice", live: true },
      { href: "/jp-live", label: "JP Live", sub: "Live brand health", icon: "live", live: true },
    ],
  },
];

// Auth pages render full-bleed without the sidebar chrome — the
// signed-in shell is for the workspace, not the front door.
const AUTH_PATHS = ["/login", "/signup"];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (AUTH_PATHS.includes(path)) {
    return <>{children}</>;
  }
  return (
    <div className="flex min-h-screen">
      <aside className="w-64 shrink-0 bg-sidebar text-sidebar-foreground border-r border-sidebar-border flex flex-col">
        <div className="px-5 py-5 border-b border-sidebar-border">
          <div className="text-[15px] font-bold tracking-[.5px] text-sidebar-primary">JAMES OS</div>
          <div className="text-xs opacity-70 mt-0.5">Brand Manager</div>
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          {NAV.map((g) => (
            <div key={g.title} className="mb-4">
              <div className="px-5 mb-1.5 text-[10px] font-semibold uppercase tracking-[1.3px] opacity-50">
                {g.title}
              </div>
              {g.items.map((n) => {
                const active =
                  path === n.href ||
                  (n.match?.some((m) => path === m || path.startsWith(m + "/")) ?? false);
                return (
                  <Link
                    key={n.href}
                    href={n.href}
                    className={`flex items-center gap-3 py-2.5 text-sm transition-colors border-l-2 ${
                      n.indent ? "pl-9 pr-5" : "px-5"
                    } ${
                      active
                        ? "bg-sidebar-accent border-sidebar-primary text-sidebar-primary"
                        : "border-transparent opacity-75 hover:opacity-100 hover:bg-sidebar-accent/50"
                    }`}
                  >
                    {n.indent && <span className="text-[10px] opacity-40 -ml-3">└</span>}
                    <Icon name={n.icon} className={active ? "text-sidebar-primary" : "opacity-80"} />
                    <span className="flex-1 leading-tight">
                      <span className="block font-medium">{n.label}</span>
                      <span className="block text-[11px] opacity-60">{n.sub}</span>
                    </span>
                    {!n.live && (
                      <span className="text-[9px] uppercase tracking-wide opacity-50 border border-sidebar-border rounded px-1 py-0.5">
                        soon
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <Link
          href="/profile"
          className={`flex items-center gap-3 px-5 py-3 text-sm border-t border-sidebar-border transition-colors ${
            path === "/profile" ? "text-sidebar-primary" : "opacity-70 hover:opacity-100"
          }`}
        >
          <Icon name="design" />
          <span>Profile</span>
        </Link>
        <Link
          href="/settings"
          className={`flex items-center gap-3 px-5 py-3 text-sm border-t border-sidebar-border transition-colors ${
            path === "/settings" ? "text-sidebar-primary" : "opacity-70 hover:opacity-100"
          }`}
        >
          <Icon name="settings" />
          <span>API connections</span>
        </Link>
      </aside>

      <main className="flex-1 min-w-0 bg-background">
        <div className="max-w-4xl mx-auto px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
