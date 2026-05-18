"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Ask the memory", icon: "◆" },
  { href: "/brand", label: "Brand voice & rules", icon: "✦" },
  { href: "/queue", label: "Approval queue", icon: "▣", soon: true },
  { href: "/settings", label: "Settings", icon: "⚙", soon: true },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="flex min-h-screen">
      <aside className="w-64 shrink-0 bg-sidebar text-sidebar-foreground border-r border-sidebar-border flex flex-col">
        <div className="px-5 py-5 border-b border-sidebar-border">
          <div className="text-[15px] font-bold tracking-[.5px] text-sidebar-primary">
            JAMES OS
          </div>
          <div className="text-xs opacity-70 mt-0.5">Brand Manager</div>
        </div>
        <nav className="p-3 flex flex-col gap-1">
          {NAV.map((n) => {
            const active = path === n.href;
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors ${
                  active
                    ? "bg-sidebar-accent text-sidebar-primary font-medium"
                    : "opacity-75 hover:opacity-100 hover:bg-sidebar-accent/60"
                }`}
              >
                <span className={`w-4 text-center ${active ? "text-sidebar-primary" : ""}`}>
                  {n.icon}
                </span>
                <span className="flex-1">{n.label}</span>
                {n.soon && (
                  <span className="text-[10px] uppercase tracking-wide opacity-60 border border-sidebar-border rounded px-1.5 py-0.5">
                    soon
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto p-4 text-[11px] opacity-60 border-t border-sidebar-border leading-relaxed">
          Memory · Voice · Rules · QA
          <br />
          tenant zero — James
        </div>
      </aside>
      <main className="flex-1 min-w-0 bg-background">
        <div className="max-w-4xl mx-auto px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
