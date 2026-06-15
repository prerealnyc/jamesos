"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/icons";
import { HubTabs, BRAND_TABS, RESEARCH_TABS } from "@/components/hub-tabs";

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
// One "Create" hub over every maker (batch, post, video, image).
const CREATE_SUBROUTES = ["/create", "/autopilot", "/design-studio", "/images", ...VIDEO_SUBROUTES];
// Assets = the 4 media libraries + style templates, already tab-unified.
const ASSETS_SUBROUTES = ["/jp-clips", "/hero", "/broll", "/audio", "/style-templates"];
// Research = listen + trends + people, tab-unified via HubTabs.
const RESEARCH_SUBROUTES = ["/social-listening", "/market-research", "/social-companion"];
// Brand = rules + voice studio + health, tab-unified via HubTabs.
const BRAND_SUBROUTES = ["/brand", "/voice-studio", "/jp-live"];

const NAV: Group[] = [
  {
    title: "Memory",
    items: [
      { href: "/", label: "Ask the memory", sub: "Grounded, cited Q&A", icon: "ask", live: true },
    ],
  },
  {
    title: "Create",
    items: [
      { href: "/create", label: "Create", sub: "Posts, videos, images & batches — one place", icon: "design", live: true, match: CREATE_SUBROUTES },
    ],
  },
  {
    title: "Review & Library",
    items: [
      { href: "/queue", label: "Approval Queue", sub: "Review, edit & approve every piece", icon: "queue", live: true },
      { href: "/library", label: "Output Library", sub: "Finished content — download & share", icon: "clips", live: true },
      { href: "/updates", label: "What's Next", sub: "Feedback → changes: live + queued", icon: "design", live: true },
      { href: "/jp-clips", label: "Assets", sub: "Footage, hero, B-roll, audio & styles", icon: "clips", live: true, match: ASSETS_SUBROUTES },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/analytics", label: "Analytics", sub: "Your brand-account performance", icon: "social", live: true },
      { href: "/social-listening", label: "Research", sub: "Listen, trends & people across socials", icon: "market", live: true, match: RESEARCH_SUBROUTES },
    ],
  },
  {
    title: "Brand",
    items: [
      { href: "/brand", label: "Brand", sub: "Voice rules, voice studio & health", icon: "voice", live: true, match: BRAND_SUBROUTES },
    ],
  },
];

// Auth pages render full-bleed without the sidebar chrome — the
// signed-in shell is for the workspace, not the front door. `/preview`
// is the self-contained redesign preview (its own sidebar + content).
const AUTH_PATHS = ["/login", "/signup"];
const FULL_BLEED = ["/preview"];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (AUTH_PATHS.includes(path) || FULL_BLEED.some((p) => path.startsWith(p))) {
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
        <div className="max-w-4xl mx-auto px-8 py-10">
          {/* Hub tab strips unify a cluster of pages into one surface.
              Media/Assets pages render their own MediaTabs in-page. */}
          {BRAND_SUBROUTES.some((p) => path === p || path.startsWith(p + "/")) && (
            <div className="mb-6"><HubTabs tabs={BRAND_TABS} /></div>
          )}
          {RESEARCH_SUBROUTES.some((p) => path === p || path.startsWith(p + "/")) && (
            <div className="mb-6"><HubTabs tabs={RESEARCH_TABS} /></div>
          )}
          {children}
        </div>
      </main>
    </div>
  );
}
