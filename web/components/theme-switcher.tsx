"use client";

import { useEffect, useState } from "react";

/**
 * Floating Classic ⇄ Mission Control switcher — the P5 rollout vehicle
 * (repurposed from the retired Iris preview, per the merge decision):
 * every restyled page can be verified against the pre-merge look until
 * full cutover, at which point this control retires with Roy's sign-off.
 * Toggles the `theme-manager` class on <html> (CSS-var overrides in
 * globals.css do the rest) and remembers the choice in localStorage.
 * Default = Mission Control (the unified bm2.0-discipline design).
 */
const KEY = "jos-theme-preview";

export function ThemeSwitcher() {
  const [mc, setMc] = useState(true);

  useEffect(() => {
    const on = localStorage.getItem(KEY) !== "classic";
    setMc(on);
    document.documentElement.classList.toggle("theme-manager", on);
  }, []);

  function set(on: boolean) {
    setMc(on);
    document.documentElement.classList.toggle("theme-manager", on);
    localStorage.setItem(KEY, on ? "manager" : "classic");
  }

  return (
    <div
      style={{ position: "fixed", right: 16, bottom: 16, zIndex: 60 }}
      className="flex items-center gap-1 rounded-full border border-border bg-card/95 p-1 shadow-lg backdrop-blur"
    >
      <span className="px-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Theme
      </span>
      <button
        onClick={() => set(false)}
        aria-pressed={!mc}
        className={`rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors ${
          !mc ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Classic
      </button>
      <button
        onClick={() => set(true)}
        aria-pressed={mc}
        className={`rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors ${
          mc ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Mission Control
      </button>
    </div>
  );
}
