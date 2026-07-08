"use client";

/**
 * Sticky pill jump-nav with scroll-spy + BackToTop — ported from bm2.0
 * frontend/components/section-nav.tsx, restyled onto this app's Tailwind
 * HSL-var tokens (bg-card / border-border / text-muted-foreground with a
 * quiet bg-secondary active state).
 *
 * Section targets must be elements with matching ids and tabIndex={-1} so
 * focus can land on them (WCAG 2.4.3 Focus Order).
 */

import { useEffect, useState } from "react";

export type NavSection = { id: string; label: string };

// height of the sticky section-nav, so a jumped-to heading clears it
const NAV_OFFSET = 56;

/** Move to a section: scroll it just below the sticky nav AND move focus
 * there, so keyboard and screen-reader users land on it too. Instant,
 * motion-free scrolling (WCAG 2.3.3-friendly, reliable across browsers). */
export function goToSection(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  const top = Math.max(0, el.getBoundingClientRect().top + window.scrollY - NAV_OFFSET);
  window.scrollTo(0, top);
  // el has tabIndex={-1}; move focus so keyboard/SR users land here
  el.focus({ preventScroll: true });
}

/** Sticky in-page jump bar with scroll-spy. Turns a long dashboard into
 * something you can navigate — and get back from — in one click or key press. */
export function SectionNav({ sections }: { sections: NavSection[] }) {
  const [active, setActive] = useState<string>(sections[0]?.id ?? "");

  useEffect(() => {
    const els = sections
      .map((s) => document.getElementById(s.id))
      .filter((e): e is HTMLElement => e !== null);
    if (els.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      // a band across the middle of the viewport decides the "current" section
      { rootMargin: "-45% 0px -50% 0px", threshold: 0 },
    );
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [sections]);

  return (
    <nav
      aria-label="Page sections"
      className="sticky top-0 z-30 -mx-1 border-b border-border bg-card/95 px-1 py-2 backdrop-blur supports-[backdrop-filter]:bg-card/80"
    >
      <ul className="flex list-none flex-wrap items-center gap-1.5 overflow-x-auto p-0 m-0">
        {sections.map((s) => {
          const isActive = active === s.id;
          return (
            <li key={s.id}>
              <button
                type="button"
                aria-current={isActive ? "true" : undefined}
                onClick={() => goToSection(s.id)}
                className={[
                  "whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "border-border bg-secondary text-foreground"
                    : "border-transparent text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                ].join(" ")}
              >
                {s.label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** A persistent, keyboard-accessible way back to the top — appears once the
 * page has scrolled, and returns focus to the top so you don't lose your
 * place. Give the page's top landmark id="main-content" and tabIndex={-1}. */
export function BackToTop() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > 600);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!show) return null;

  const toTop = () => {
    window.scrollTo(0, 0);
    const anchor = document.getElementById("main-content");
    if (anchor instanceof HTMLElement) anchor.focus({ preventScroll: true });
  };

  return (
    <button
      type="button"
      onClick={toTop}
      aria-label="Back to top of page"
      className={[
        "fixed bottom-5 right-5 z-40 inline-flex items-center gap-1.5 rounded-full border border-border",
        "bg-card px-3.5 py-2 text-xs font-semibold text-muted-foreground shadow-lg transition-colors",
        "hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      ].join(" ")}
    >
      <span aria-hidden="true">↑</span> Top
    </button>
  );
}
