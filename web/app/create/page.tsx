"use client";

/**
 * Create — the single front door for making anything. Replaces the four
 * separate sidebar entries (Autopilot / Content Studio / Video Studio /
 * Post Images) that caused "I didn't know where to click". You pick WHAT
 * you want; this routes you to the right maker.
 */

import Link from "next/link";
import { Card, PageHeader, Badge } from "@/components/ui";
import { Icon } from "@/components/icons";

type Option = {
  href: string;
  title: string;
  oneLiner: string;
  detail: string;
  icon: string;
  tag?: string;
};

const OPTIONS: Option[] = [
  {
    href: "/autopilot",
    title: "Batch (Autopilot)",
    oneLiner: "One click → a week of content",
    detail: "Pick how many videos and/or posts; it invents on-brand ideas, drafts them, and drops them in the Approval Queue. The fastest way to fill your calendar.",
    icon: "queue",
    tag: "Most common",
  },
  {
    href: "/video",
    title: "A video",
    oneLiner: "Reels from your footage or a script",
    detail: "The Video Studio: turn a long recording into reels, put your talking-head with B-roll, or generate a faceless story video. Grouped by what you have.",
    icon: "pipeline",
  },
  {
    href: "/design-studio",
    title: "A written post",
    oneLiner: "One text post, by hand",
    detail: "Write a single on-brand post or caption with the content engine and your voice — for when you want to craft one piece deliberately.",
    icon: "design",
  },
  {
    href: "/images",
    title: "An image",
    oneLiner: "AI hero image for a post",
    detail: "Generate a clean, on-brand image for a post — no text or logos baked in, so you add your own headline.",
    icon: "images",
  },
];

export default function CreatePage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Create"
        sub="What do you want to make? Pick one — everything you create lands in the Approval Queue for review before it goes anywhere."
      />

      <div className="grid grid-cols-2 gap-4">
        {OPTIONS.map((o) => (
          <Link key={o.href} href={o.href}>
            <Card className="h-full transition-colors hover:border-primary/60 cursor-pointer">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-md bg-primary/10 text-primary">
                  <Icon name={o.icon} />
                </span>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{o.title}</span>
                    {o.tag && <Badge tone="primary">{o.tag}</Badge>}
                  </div>
                  <div className="text-[12px] text-muted-foreground">{o.oneLiner}</div>
                </div>
              </div>
              <p className="text-[13px] text-muted-foreground mt-3 leading-relaxed">{o.detail}</p>
            </Card>
          </Link>
        ))}
      </div>

      <p className="text-[12px] text-muted-foreground">
        Not sure? Start with <Link href="/autopilot" className="text-primary underline">Batch</Link> — it does the thinking for you.
      </p>
    </div>
  );
}
