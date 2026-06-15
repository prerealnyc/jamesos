"use client";

import { useEffect, useState } from "react";

/**
 * Redesigned dashboard — STANDALONE PREVIEW. Renders the new command-center
 * experience (Iris look, 4-zone vault sidebar, Ask/Do bar, pulse cards, gold
 * "grounded in memory" provenance) so the new design can be evaluated as a
 * real built screen — not just a recolor of the old pages. Login-free and
 * non-destructive: it forces the `theme-iris` tokens while mounted and
 * restores the prior theme on unmount. Representative data.
 */

type NavItem = { label: string; icon: string; active?: boolean; badge?: string };
const NAV: { zone: string; items: NavItem[] }[] = [
  { zone: "Pulse", items: [{ label: "Dashboard", icon: "sparkles", active: true }] },
  { zone: "Memory", items: [
    { label: "Brand Memory", icon: "brain" },
    { label: "Knowledge Base", icon: "book" },
  ] },
  { zone: "Create", items: [
    { label: "Content", icon: "wand" },
    { label: "Video Studio", icon: "movie" },
    { label: "Campaigns", icon: "calendar" },
    { label: "Approvals", icon: "check", badge: "3" },
    { label: "Asset Library", icon: "photo" },
  ] },
  { zone: "Grow", items: [{ label: "Analytics", icon: "chart" }] },
];

function I({ name, size = 16 }: { name: string; size?: number }) {
  const p: Record<string, React.ReactNode> = {
    sparkles: <path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5 10.1 7.6z" />,
    brain: <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 4 2.8M15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V15a3 3 0 0 1-4 2.8M9 4v14M15 4v14" />,
    book: <path d="M5 4h11a2 2 0 0 1 2 2v13H7a2 2 0 0 1-2-2V4zM5 17a2 2 0 0 1 2-2h11" />,
    wand: <path d="M15 4V2M15 10V8M12 7h-2M18 7h2M6 18L18 6M5 21l2-2" />,
    movie: <path d="M4 5h16v14H4zM4 9h16M9 5v14M15 5v14" />,
    calendar: <path d="M4 5h16v15H4zM4 9h16M8 3v4M16 3v4" />,
    check: <path d="M5 12l5 5 9-11" />,
    photo: <path d="M4 5h16v14H4zM4 15l4-4 5 5M14 12l2-2 4 4" />,
    chart: <path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-7" />,
    play: <path d="M8 5v14l11-7z" />,
    quote: <path d="M7 7h4v6H7zM13 7h4v6h-4zM7 13c0 2-1 3-3 3M13 13c0 2-1 3-3 3" />,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {p[name]}
    </svg>
  );
}

export default function PreviewDashboard() {
  const [mode, setMode] = useState<"ask" | "do">("ask");

  useEffect(() => {
    const had = document.documentElement.classList.contains("theme-iris");
    document.documentElement.classList.add("theme-iris");
    return () => { if (!had) document.documentElement.classList.remove("theme-iris"); };
  }, []);

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {/* ── Vault sidebar (Archive Slate) ── */}
      <aside className="flex w-60 shrink-0 flex-col bg-sidebar text-sidebar-foreground">
        <div className="border-b border-sidebar-border px-5 py-5">
          <div className="text-[15px] font-bold tracking-[.5px] text-sidebar-primary">JAMES OS</div>
          <div className="mt-0.5 text-xs opacity-70">Brand Manager</div>
        </div>

        <div className="px-4 pb-1 pt-4">
          <button className="flex w-full items-center justify-center gap-2 rounded-md bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90">
            <I name="wand" /> Create content
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          {NAV.map((g) => (
            <div key={g.zone} className="mb-3">
              <div className="mb-1 px-5 text-[10px] font-semibold uppercase tracking-[1.3px] opacity-50">
                {g.zone}
              </div>
              {g.items.map((n) => (
                <div key={n.label}
                  className={`mx-2 flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                    n.active
                      ? "bg-sidebar-accent text-sidebar-primary border-l-2 border-sidebar-primary"
                      : "opacity-75 hover:opacity-100"
                  }`}>
                  <I name={n.icon} />
                  <span className="flex-1">{n.label}</span>
                  {n.badge && (
                    <span className="rounded-full bg-memory px-1.5 py-0.5 text-[10px] font-semibold text-memory-foreground">
                      {n.badge}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-sidebar-border px-5 py-3 text-xs opacity-60">PreReal · owner</div>
      </aside>

      {/* ── Command center ── */}
      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-5xl px-8 py-8">
          <div className="mb-1 text-2xl font-semibold">Good evening, James.</div>
          <p className="mb-5 text-sm text-muted-foreground">
            3 drafts are waiting — each cites the memory it was built from.
          </p>

          {/* Ask / Do bar */}
          <div className="mb-6 flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-sm">
            <span className="text-primary"><I name="sparkles" size={18} /></span>
            <input
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder={mode === "ask"
                ? "Ask your brand memory… I answer only from what I remember."
                : "Tell me to do something — render a reel, refresh analytics, approve a draft."}
            />
            <div className="flex gap-1 rounded-md bg-secondary p-0.5">
              {(["ask", "do"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)}
                  className={`rounded px-3 py-1 text-xs font-medium capitalize transition-colors ${
                    mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground"
                  }`}>
                  {m}
                </button>
              ))}
            </div>
          </div>

          {/* Pulse cards */}
          <div className="mb-6 grid grid-cols-3 gap-4">
            {[
              { label: "Pending approvals", value: "3", tone: "text-foreground" },
              { label: "Queued this week", value: "12", tone: "text-foreground" },
              { label: "Voice match", value: "0.91", tone: "text-[hsl(var(--success))]" },
            ].map((c) => (
              <div key={c.label} className="rounded-md border border-border bg-card p-4 shadow-sm">
                <div className="text-[12px] text-muted-foreground">{c.label}</div>
                <div className={`mt-1 text-[26px] font-semibold ${c.tone}`}>{c.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-3 gap-5">
            {/* Latest draft */}
            <div className="col-span-2 rounded-lg border border-border bg-card p-4 shadow-sm">
              <div className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                Latest draft · ready for review
              </div>
              <div className="flex gap-4">
                <div className="flex h-[96px] w-[72px] shrink-0 items-center justify-center rounded-md bg-sidebar text-sidebar-foreground">
                  <I name="play" size={22} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-medium">“Why most Staten Island investors overpay”</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="inline-flex items-center gap-1.5 rounded-md bg-memory px-2 py-1 text-[11px] font-medium text-memory-foreground">
                      <I name="quote" size={12} /> grounded in 3 memories
                    </span>
                    <span className="rounded-md border border-[hsl(var(--success)/0.4)] px-2 py-1 text-[11px] font-medium text-[hsl(var(--success))]">
                      1080×1920 ✓
                    </span>
                    <span className="rounded-md bg-secondary px-2 py-1 text-[11px] text-muted-foreground">
                      Reflective · Higgsfield
                    </span>
                  </div>
                  <div className="mt-4 flex gap-2">
                    <button className="rounded-md bg-primary px-4 py-1.5 text-[13px] font-semibold text-primary-foreground hover:bg-primary/90">
                      Approve
                    </button>
                    <button className="rounded-md border border-border px-4 py-1.5 text-[13px] font-medium hover:bg-secondary">
                      Reject
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* What's changing next */}
            <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
              <div className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                What's changing next
              </div>
              <ul className="space-y-3 text-[13px]">
                <li className="flex gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span>One whoosh per video <span className="text-muted-foreground">— applied live</span></span>
                </li>
                <li className="flex gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-memory" />
                  <span>B-roll pulls real-estate imagery <span className="text-muted-foreground">— queued</span></span>
                </li>
                <li className="flex gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[hsl(var(--success))]" />
                  <span>Full 1080×1920 output <span className="text-muted-foreground">— shipped</span></span>
                </li>
              </ul>
            </div>
          </div>

          {/* Memory recall strip */}
          <div className="mt-5 rounded-lg border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
              <span className="text-primary"><I name="brain" size={14} /></span> Recalled from your brand memory
            </div>
            <div className="grid grid-cols-3 gap-3 text-[12px]">
              {[
                ["Voice corpus", "James — 41 transcripts"],
                ["Frustration ledger", "“no stock-photo skylines”"],
                ["Guideline", "Lead with the deal, not the disclaimer"],
              ].map(([k, v]) => (
                <div key={k} className="rounded-md border border-[hsl(var(--memory)/0.35)] bg-[hsl(var(--memory)/0.08)] p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--memory-foreground))] opacity-70">{k}</div>
                  <div className="mt-1 text-foreground">{v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
