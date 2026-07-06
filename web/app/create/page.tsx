"use client";

/**
 * Create — the single front door for making anything. Replaces the four
 * separate sidebar entries (Autopilot / Content Studio / Video Studio /
 * Post Images) that caused "I didn't know where to click". You pick WHAT
 * you want; this routes you to the right maker.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type ContentSuggestion } from "@/lib/api";
import { Button, Card, CardTitle, PageHeader, Badge, Spinner } from "@/components/ui";
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
    title: "A post (text + image)",
    oneLiner: "One topic → on-voice post + matching image",
    detail: "Write a single on-brand post in your voice AND generate a cinematic image of James (from your hero library) to match it — composed together and queued as one item.",
    icon: "design",
  },
];

const TEMPLATES: Option[] = [
  {
    href: "/long-form",
    title: "Template 1 — Your clip, full-frame",
    oneLiner: "Upload a clip · B-roll on top · magenta-on-black",
    detail: "Upload your own 1-minute talking-head clip; we cut it, layer cinematic B-roll over it, and burn magenta-on-black captions. Real you, full-frame 9:16.",
    icon: "pipeline",
  },
  {
    href: "/engaging-video?t=split",
    title: "Template 2 — Avatar split 50/50",
    oneLiner: "Script → avatar top, B-roll bottom · magenta-on-white",
    detail: "Write or auto-generate a script; the HeyGen avatar fills the TOP half (face framed), B-roll fills the BOTTOM half (vertical 9:16), with magenta-on-white captions on the seam.",
    icon: "queue",
    tag: "New",
  },
];

function OptionCard({ o }: { o: Option }) {
  return (
    <Link href={o.href}>
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
  );
}

function SuggestionsRail() {
  const [items, setItems] = useState<ContentSuggestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.listSuggestions();
      setItems(r.suggestions);
    } catch { /* no profile yet → rail stays empty */ }
  }
  useEffect(() => { load(); }, []);

  async function accept(id: string) {
    setBusy(id);
    setErr(null);
    try {
      await api.acceptSuggestion(id);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy((p) => (p === id ? null : p));
    }
  }

  async function dismiss(id: string) {
    setBusy(id);
    try {
      await api.dismissSuggestion(id);
      setItems((prev) => prev.filter((s) => s.id !== id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy((p) => (p === id ? null : p));
    }
  }

  async function refresh() {
    setRefreshing(true);
    setErr(null);
    try {
      const start = (await api.listSuggestions()).suggestions.length;
      await api.refreshSuggestions();
      // Research + ideation take a couple of minutes; poll until new rows land.
      for (let i = 0; i < 24; i++) {
        await new Promise((r) => setTimeout(r, 10000));
        const r2 = await api.listSuggestions();
        setItems(r2.suggestions);
        if (r2.suggestions.length > start) break;
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <CardTitle>Today&apos;s suggestions</CardTitle>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            The brand manager&apos;s own picks — fresh research every morning,
            filtered through your goals. Accept one and it&apos;s made for you.
          </div>
        </div>
        <Button
          variant="secondary"
          onClick={refresh}
          disabled={refreshing}
          className="text-[12px] !px-3 !py-1"
          title="Run the research pass now"
        >
          {refreshing ? <span className="inline-flex items-center gap-1"><Spinner /> researching…</span> : "↻ Research now"}
        </Button>
      </div>
      {err && <p className="text-[12px] text-destructive">✗ {err}</p>}
      {items.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No suggestions yet. Complete the{" "}
          <Link href="/intake" className="text-primary hover:underline">Brand Setup</Link>{" "}
          and the brand manager starts proposing content every morning — or hit
          “Research now”.
        </p>
      ) : (
        <div className="flex flex-col divide-y divide-border">
          {items.map((s) => (
            <div key={s.id} className="flex items-start gap-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium leading-snug">{s.title}</div>
                {s.why && <div className="text-[11px] text-muted-foreground line-clamp-1">{s.why}</div>}
              </div>
              <Badge tone="muted">{s.format}</Badge>
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => accept(s.id)}
                  disabled={busy === s.id}
                  className="text-[12px] !px-3 !py-1"
                >
                  {busy === s.id ? <Spinner /> : "Make it"}
                </Button>
                <button
                  onClick={() => dismiss(s.id)}
                  disabled={busy === s.id}
                  className="text-[12px] text-muted-foreground hover:text-destructive"
                  title="Dismiss"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function CreatePage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Create"
        sub="What do you want to make? Pick one — everything you create lands in the Approval Queue for review before it goes anywhere."
      />

      <SuggestionsRail />

      <div className="grid grid-cols-2 gap-4">
        {OPTIONS.map((o) => <OptionCard key={o.href} o={o} />)}
      </div>

      <section>
        <h2 className="text-[13px] font-medium text-muted-foreground uppercase tracking-wider mb-3">
          Reel templates
        </h2>
        <div className="grid grid-cols-2 gap-4">
          {TEMPLATES.map((o) => <OptionCard key={o.href} o={o} />)}
        </div>
      </section>

      <p className="text-[12px] text-muted-foreground">
        Not sure? Start with <Link href="/autopilot" className="text-primary underline">Batch</Link> — it does the thinking for you.
      </p>
    </div>
  );
}
