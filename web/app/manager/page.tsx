"use client";

/**
 * Mission Control — the strategy home of the unified Brand Manager: the bm2.0
 * dashboard ported onto the merged /manager backend. One dense scrolling page:
 * Today (brief + digest + cycle) → Next steps → North Star → Radar (the five
 * eyes) → Competitors → The Work → Follow-ups → Brand Voice → Sources, with a
 * sticky jump-nav. Follow-up mutations share one refresh key across Today /
 * The Work / Follow-ups so a change in one is reflected in the others.
 */

import Link from "next/link";
import { useCallback, useState } from "react";

import { PageHeader } from "@/components/ui";
import { BackToTop, SectionNav } from "@/components/manager/section-nav";
import { CompetitorsPanel } from "@/components/manager/panels/competitors-panel";
import { FollowupsPanel } from "@/components/manager/panels/followups-panel";
import { NextStepsPanel } from "@/components/manager/panels/next-steps-panel";
import { NorthStarPanel } from "@/components/manager/panels/north-star-panel";
import { RadarPanel } from "@/components/manager/panels/radar-panel";
import { SourcesPanel } from "@/components/manager/panels/sources-panel";
import { TodayPanel } from "@/components/manager/panels/today-panel";
import { VoicePanel } from "@/components/manager/panels/voice-panel";
import { WorkPanel } from "@/components/manager/panels/work-panel";

// Module-level so the reference is stable and the scroll-spy observer isn't
// rebuilt each render.
const SECTIONS = [
  { id: "today", label: "Today" },
  { id: "next-steps", label: "Next steps" },
  { id: "north-star", label: "North Star" },
  { id: "radar", label: "Radar" },
  { id: "peers", label: "Competitors" },
  { id: "work", label: "The Work" },
  { id: "followups", label: "Follow-ups" },
  { id: "voice", label: "Brand Voice" },
  { id: "sources", label: "Sources" },
];

function Section({
  id,
  title,
  sub,
  action,
  children,
}: {
  id: string;
  title: string;
  sub?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section id={id} tabIndex={-1} aria-label={title} className="scroll-mt-16 outline-none">
      <div className="mb-2 mt-6 flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold uppercase tracking-[1px] text-muted-foreground">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-muted-foreground/80">{sub}</p>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      {children}
    </section>
  );
}

export default function ManagerPage() {
  // Follow-up action items are shared across Today / The Work / Follow-ups:
  // bumping this key refetches all three so a mutation in one shows in the rest.
  const [actionsKey, setActionsKey] = useState(0);
  const bumpActions = useCallback(() => setActionsKey((k) => k + 1), []);

  // A digest item deep-links to its follow-up card (id="action-{id}").
  const scrollToAction = useCallback((actionId: string) => {
    const el = document.getElementById(`action-${actionId}`);
    if (!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    el.setAttribute("tabindex", "-1");
    window.setTimeout(() => el.focus({ preventScroll: true }), reduce ? 0 : 320);
  }, []);

  return (
    <div id="main-content" tabIndex={-1} className="outline-none">
      <PageHeader
        title="Mission Control"
        sub="The brand manager's cockpit — today's cycle, the radar, the peers, the work, and the follow-ups."
      />

      <div className="mt-4">
        <SectionNav sections={SECTIONS} />
      </div>

      <Section id="today" title="Today" sub="The morning brief and the daily heartbeat.">
        <TodayPanel refreshKey={actionsKey} onCycleRan={bumpActions} onScrollToAction={scrollToAction} />
      </Section>

      <Section id="next-steps" title="Next steps" sub="The setup checklist — done, running, and where you are.">
        <NextStepsPanel refreshKey={actionsKey} />
      </Section>

      <Section id="north-star" title="North Star" sub="The negotiated targets everything below plans toward.">
        <NorthStarPanel />
      </Section>

      <Section id="radar" title="Radar" sub="Five eyes scanning for opportunities; findings land in Follow-ups.">
        <RadarPanel onScanned={bumpActions} />
      </Section>

      <Section id="peers" title="Competitors & peers" sub="Discover, approve, track — then plays matched to each relationship.">
        <CompetitorsPanel onChange={bumpActions} />
      </Section>

      <Section
        id="work"
        title="The Work"
        sub="What the manager did with each opportunity."
        action={
          <Link href="/manager/plan" className="text-xs font-semibold text-primary hover:underline">
            Weekly plan →
          </Link>
        }
      >
        <WorkPanel refreshKey={actionsKey} onMutated={bumpActions} />
      </Section>

      <Section id="followups" title="Follow-ups" sub="Every play and finding as a trackable action item.">
        <FollowupsPanel refreshKey={actionsKey} onMutated={bumpActions} />
      </Section>

      <Section id="voice" title="Brand Voice" sub="Learned from your own words; every draft is written in it.">
        <VoicePanel />
      </Section>

      <Section id="sources" title="Sources" sub="The insight streams feeding this brand, and what each contributed.">
        <SourcesPanel />
      </Section>

      <BackToTop />
    </div>
  );
}
