"use client";

import { useEffect, useState } from "react";

/**
 * Floating Classic ⇄ Iris theme switcher — a PREVIEW control so the new
 * "Iris" redesign can be evaluated live against the shipped "Classic"
 * look on every page, without committing to it. Toggles the `theme-iris`
 * class on <html> (CSS-var overrides in globals.css do the rest) and
 * remembers the choice in localStorage. Default = Classic.
 */
const KEY = "jos-theme-preview";

export function ThemeSwitcher() {
  const [iris, setIris] = useState(false);

  useEffect(() => {
    const on = localStorage.getItem(KEY) === "iris";
    setIris(on);
    document.documentElement.classList.toggle("theme-iris", on);
  }, []);

  function set(on: boolean) {
    setIris(on);
    document.documentElement.classList.toggle("theme-iris", on);
    localStorage.setItem(KEY, on ? "iris" : "classic");
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
        aria-pressed={!iris}
        className={`rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors ${
          !iris ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Classic
      </button>
      <button
        onClick={() => set(true)}
        aria-pressed={iris}
        className={`rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors ${
          iris ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Iris (new)
      </button>
    </div>
  );
}
