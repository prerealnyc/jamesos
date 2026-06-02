"use client";

/**
 * Video Studio — one landing page for every way to make a video.
 *
 * Replaces the 7 separate sidebar entries (Long Form Cutter, Engaging
 * Reel, Story Reel Mix, HeyGen Video, Story Video, Video Composer,
 * Timeline Editor) with a single hub. The pages themselves still live
 * at their existing routes — the hub just gives them organization +
 * use-case copy so the user picks the right tool first.
 *
 * Grouping is by INPUT, not by technology:
 *   1. From existing footage — you have a podcast / clip / interview
 *   2. From a script — no footage, just words
 *   3. Power tools — full control / manual stitching
 */

import Link from "next/link";
import { Card, PageHeader, Badge } from "@/components/ui";

type Maker = {
  href: string;
  title: string;
  oneLiner: string;
  detail: string;
  best: string;
  tag?: string;
};

const FROM_FOOTAGE: Maker[] = [
  {
    href: "/long-form",
    title: "Long Form Cutter",
    oneLiner: "Podcast / interview → standalone reel candidates",
    detail:
      "Drop a 50-60 min Drive video; the LLM picks 5-15 reel-worthy 30s windows. Render each as an engaging-avatar reel.",
    best: "Best for podcasts, interviews, IG Live recordings.",
    tag: "Most common",
  },
  {
    href: "/engaging-video",
    title: "Engaging Reel",
    oneLiner: "Real talking-head + AI B-roll cutaways",
    detail:
      "Your James footage stays on camera; AI fills 5s-cadence inserts with photoreal B-roll the LLM picks from the script.",
    best: "Best when you have James footage and want energy without re-shooting.",
  },
  {
    href: "/story-mix",
    title: "Story Reel (Mix)",
    oneLiner: "Talking-head + AI B-roll, one HeyGen render",
    detail:
      "Like Engaging Reel but uses one HeyGen voice track end-to-end. Cheaper, slightly less dynamic.",
    best: "Best when you have only a script (no real footage) but want a story-driven reel.",
  },
];

const FROM_SCRIPT: Maker[] = [
  {
    href: "/heygen-video",
    title: "HeyGen Avatar",
    oneLiner: "Avatar speaks your script, captions baked in",
    detail:
      "One HeyGen render. No Creatomate, no B-roll, no extra steps. Captions auto-burned.",
    best: "Best for talking-head announcements when speed matters more than visuals.",
  },
  {
    href: "/story-video",
    title: "Story Video (Faceless)",
    oneLiner: "Voice + AI image story — no face",
    detail:
      "HeyGen voice over caption-pinned AI stills. Faceless content for cases where the brand owner doesn't want to be on camera.",
    best: "Best for educational / explainer / faceless content.",
  },
];

const POWER_TOOLS: Maker[] = [
  {
    href: "/pipeline",
    title: "Video Composer",
    oneLiner: "Full scene plan: avatar + B-roll + clips",
    detail:
      "The original mixed-mode composer. Edit the scene plan, choose per-scene transitions and music, then render.",
    best: "Best for power users who want scene-by-scene control.",
  },
  {
    href: "/editor",
    title: "Timeline Editor",
    oneLiner: "Manually stitch clips, add text & music",
    detail:
      "Drag clips into a timeline. No LLM picks, no automation — just an editor.",
    best: "Best when you already know exactly what you want to assemble.",
  },
];


function MakerCard({ m }: { m: Maker }) {
  return (
    <Link href={m.href} className="block">
      <Card className="!p-4 h-full hover:border-primary transition-colors space-y-2 cursor-pointer">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[14px] font-semibold leading-tight">{m.title}</p>
          {m.tag && <Badge tone="ok">{m.tag}</Badge>}
        </div>
        <p className="text-[12px] text-primary">{m.oneLiner}</p>
        <p className="text-[12px] text-muted-foreground leading-relaxed">{m.detail}</p>
        <p className="text-[11px] text-muted-foreground italic pt-1 border-t border-border">
          {m.best}
        </p>
      </Card>
    </Link>
  );
}

function Section({ title, sub, makers }: { title: string; sub: string; makers: Maker[] }) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-[13px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</p>
        <p className="text-[12px] text-muted-foreground mt-0.5">{sub}</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {makers.map((m) => <MakerCard key={m.href} m={m} />)}
      </div>
    </div>
  );
}

export default function VideoStudioPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="Video Studio"
        sub="Every way to make a video, organized by what you're starting with. Pick the maker that matches your input."
      />

      <Section
        title="From existing footage"
        sub="You have a podcast, interview, or recorded clip. The maker chops it and/or layers visuals on top."
        makers={FROM_FOOTAGE}
      />

      <Section
        title="From a script"
        sub="No real footage — just words. The maker handles voice + visuals end-to-end."
        makers={FROM_SCRIPT}
      />

      <Section
        title="Power tools"
        sub="Manual control. Use when the automated makers above aren't precise enough."
        makers={POWER_TOOLS}
      />
    </div>
  );
}
