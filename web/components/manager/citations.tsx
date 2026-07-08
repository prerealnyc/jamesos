"use client";

/**
 * Citation chips with favicons — ported from bm2.0
 * frontend/components/citations.tsx (+ the domainOf/normalizeCitations
 * helpers from its lib/format.ts), restyled onto this app's Tailwind
 * HSL-var tokens. Citations arrive as {url, ref, note} dicts or bare URL
 * strings depending on which agent wrote them.
 */

import type { Citation, CitationLike } from "@/lib/manager-api";

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function normalizeCitations(list: CitationLike[] | null | undefined): Citation[] {
  if (!list) return [];
  return list
    .map((c): Citation => {
      if (typeof c === "string") return { url: c, ref: "", note: "" };
      return { url: c.url ?? "", ref: c.ref ?? "", note: c.note ?? "" };
    })
    .filter((c) => c.url || c.ref);
}

function Favicon({ domain }: { domain: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`https://www.google.com/s2/favicons?sz=32&domain=${encodeURIComponent(domain)}`}
      alt=""
      width={14}
      height={14}
      loading="lazy"
      className="rounded-sm"
      onError={(e) => {
        e.currentTarget.style.display = "none";
      }}
    />
  );
}

const CHIP =
  "inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 " +
  "text-[11px] font-medium text-muted-foreground";

/** Citations as favicon+domain chips; internal refs render as plain chips. */
export function CitationChips({ citations }: { citations: CitationLike[] | null | undefined }) {
  const list = normalizeCitations(citations);
  if (list.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {list.map((c, i) =>
        c.url ? (
          <a
            key={`${c.url}-${i}`}
            className={`${CHIP} transition-colors hover:border-ring hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
            href={c.url}
            target="_blank"
            rel="noreferrer"
            title={c.note || c.url}
          >
            <Favicon domain={domainOf(c.url)} />
            <span>{domainOf(c.url)}</span>
          </a>
        ) : (
          <span key={`${c.ref}-${i}`} className={CHIP} title={c.note || c.ref}>
            <span>{c.ref}</span>
          </span>
        ),
      )}
    </span>
  );
}
