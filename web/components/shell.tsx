"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/icons";

type Item = { href: string; label: string; sub: string; icon: string; live?: boolean };
type Group = { title: string; items: Item[] };

const NAV: Group[] = [
  {
    title: "Memory",
    items: [
      { href: "/", label: "Ask the memory", sub: "Grounded, cited Q&A", icon: "ask", live: true },
    ],
  },
  {
    title: "Voice & Content",
    items: [
      { href: "/jp-live", label: "JP Live", sub: "Live brand health", icon: "live", live: true },
      { href: "/brand", label: "Voice Rules", sub: "Brand voice & guidelines", icon: "voice", live: true },
    ],
  },
  {
    title: "Production",
    items: [
      { href: "/design-studio", label: "Content Studio", sub: "On-voice draft generation", icon: "design", live: true },
      { href: "/long-form", label: "Long Form Cutter", sub: "50-60 min podcast → standalone reels", icon: "pipeline", live: true },
      { href: "/engaging-video", label: "Engaging Reel", sub: "James full video + B-roll cutaways", icon: "pipeline", live: true },
      { href: "/story-mix", label: "Story Reel (mix)", sub: "James on camera + AI B-roll · 1 HeyGen render", icon: "pipeline", live: true },
      { href: "/heygen-video", label: "HeyGen Video", sub: "Avatar-only · one HeyGen render", icon: "pipeline", live: true },
      { href: "/story-video", label: "Story Video", sub: "Voice + AI image story (faceless)", icon: "pipeline", live: true },
      { href: "/pipeline", label: "Video Studio", sub: "Mixed: B-roll + avatar + clips", icon: "pipeline", live: true },
      { href: "/editor", label: "Timeline Editor", sub: "Stitch clips, add text & music", icon: "pipeline", live: true },
      { href: "/jp-clips", label: "Reference Library", sub: "Clips, style refs & B-roll", icon: "clips", live: true },
      { href: "/hero", label: "Hero Library", sub: "Photos + videos of the brand's hero", icon: "clips", live: true },
      { href: "/images", label: "Post Images", sub: "AI hero images for posts", icon: "images", live: true },
    ],
  },
  {
    title: "Distribution",
    items: [
      { href: "/autopilot", label: "Autopilot", sub: "Autonomous daily content", icon: "queue", live: true },
      { href: "/queue", label: "Approval Queue", sub: "Review & publish", icon: "queue", live: true },
    ],
  },
  {
    title: "Engagement",
    items: [
      { href: "/social-companion", label: "Social Companion", sub: "Creator watchlist & trends", icon: "social", live: true },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/market-research", label: "Market Research", sub: "Trend radar & intel", icon: "market", live: true },
    ],
  },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
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
                const active = path === n.href;
                return (
                  <Link
                    key={n.href}
                    href={n.href}
                    className={`flex items-center gap-3 px-5 py-2.5 text-sm transition-colors border-l-2 ${
                      active
                        ? "bg-sidebar-accent border-sidebar-primary text-sidebar-primary"
                        : "border-transparent opacity-75 hover:opacity-100 hover:bg-sidebar-accent/50"
                    }`}
                  >
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
          href="/settings"
          className={`flex items-center gap-3 px-5 py-3 text-sm border-t border-sidebar-border transition-colors ${
            path === "/settings" ? "text-sidebar-primary" : "opacity-70 hover:opacity-100"
          }`}
        >
          <Icon name="settings" />
          <span>Settings</span>
        </Link>
        <div className="px-5 py-3 text-[11px] opacity-50 border-t border-sidebar-border">
          Last scan: — · 8 platforms tracked
        </div>
      </aside>

      <main className="flex-1 min-w-0 bg-background">
        <div className="max-w-4xl mx-auto px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
