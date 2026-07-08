"use client";

import { useMemo } from "react";

import { CitationChips } from "@/components/manager/citations";
import { EmptyState, SourceBadge } from "@/components/manager/primitives";
import { Card } from "@/components/ui";
import { prettyKey, prettySection, SECTION_ORDER, urlsIn, valueToText } from "./format";

interface FieldRow {
  key: string;
  item: string | null;
  value: unknown;
}

/** What confirmed research filled in — the flat `fields` map off
 * GET /manager/brand-profile, grouped by profile section (the key's prefix;
 * item-keyed rows arrive as "field_key[item]"). Lane pills show which
 * intelligence lanes contributed; every section carries its provenance badge
 * (everything on this screen was researched — the interview upgrades what
 * you confirm to user-stated). URL values render as citation chips. */
export function ResearchReview({
  fields,
  laneStats,
  failures,
}: {
  fields: Record<string, unknown>;
  laneStats: Record<string, number>;
  failures: string[];
}) {
  const sections = useMemo(() => {
    const groups = new Map<string, FieldRow[]>();
    for (const [rawKey, value] of Object.entries(fields)) {
      const m = rawKey.match(/^(.*?)\[(.*)\]$/);
      const fieldKey = m ? m[1] : rawKey;
      const item = m ? m[2] : null;
      const section = fieldKey.includes(".") ? fieldKey.slice(0, fieldKey.indexOf(".")) : "identity";
      const list = groups.get(section) ?? [];
      list.push({ key: fieldKey, item, value });
      groups.set(section, list);
    }
    return Array.from(groups.entries()).sort((a, b) => {
      const ia = SECTION_ORDER.indexOf(a[0]);
      const ib = SECTION_ORDER.indexOf(b[0]);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
  }, [fields]);

  const laneEntries = useMemo(
    () => Object.entries(laneStats).filter(([, n]) => n > 0),
    [laneStats],
  );

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-muted-foreground">
        Here&apos;s what research filled in. You can correct anything later — the interview
        handles what we couldn&apos;t settle.
      </p>

      {laneEntries.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {laneEntries.map(([lane, n]) => (
            <span
              key={lane}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-1 text-[11px] font-medium text-muted-foreground"
            >
              {prettyKey(lane)} <b className="text-foreground">{n} {n === 1 ? "field" : "fields"}</b>
            </span>
          ))}
        </div>
      )}

      {failures.length > 0 && (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-[12px] text-warning">
          Some sources were unavailable: {failures.join("; ")}
        </div>
      )}

      {sections.length === 0 && (
        <EmptyState
          title="Nothing written yet"
          body="Research came back empty — the interview will build your profile instead."
        />
      )}

      {sections.map(([section, rows]) => (
        <section key={section}>
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold">
              {prettySection(section)}
            </h2>
            <SourceBadge source="researched" />
          </div>
          <Card variant="flush" className="divide-y divide-border">
            {rows.map((f, i) => {
              const urls = urlsIn(f.value);
              const urlOnly =
                urls.length > 0 &&
                urls.length === (Array.isArray(f.value) ? (f.value as unknown[]).length : 1);
              return (
                <div
                  key={`${f.key}:${f.item ?? ""}:${i}`}
                  className="grid grid-cols-1 gap-1 px-4 py-3 sm:grid-cols-[180px_1fr]"
                >
                  <div className="text-[12px] font-medium text-muted-foreground">
                    {prettyKey(f.key)}
                    {f.item ? <span className="opacity-70"> · {f.item}</span> : null}
                  </div>
                  <div className="min-w-0 text-[13px] leading-relaxed">
                    {!urlOnly && <span className="break-words">{valueToText(f.value)}</span>}
                    {urls.length > 0 && (
                      <div className={!urlOnly ? "mt-1.5" : undefined}>
                        <CitationChips citations={urls} />
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </Card>
        </section>
      ))}
    </div>
  );
}
