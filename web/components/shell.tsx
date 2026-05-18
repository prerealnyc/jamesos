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
      <aside className="w-64 shrink-0 border-r border-border bg-panel flex flex-col">
        <div className="px-5 py-5 border-b border-border">
          <div className="text-[15px] font-bold tracking-[.5px]">JAMES OS</div>
          <div className="text-xs text-muted mt-0.5">Brand Manager</div>
        </div>
        <nav className="p-3 flex flex-col gap-1">
          {NAV.map((n) => {
            const active = path === n.href;
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                  active ? "bg-panel2 text-ink" : "text-muted hover:text-ink hover:bg-panel2/60"
                }`}
              >
                <span className="text-accent w-4 text-center">{n.icon}</span>
                <span className="flex-1">{n.label}</span>
                {n.soon && (
                  <span className="text-[10px] uppercase tracking-wide text-muted border border-border rounded px-1.5 py-0.5">
                    soon
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto p-4 text-[11px] text-muted border-t border-border">
          Memory · Voice · Rules · QA
          <br />
          tenant zero — James
        </div>
      </aside>
      <main className="flex-1 min-w-0">
        <div className="max-w-4xl mx-auto px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
