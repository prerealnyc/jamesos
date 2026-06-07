"use client";

import { PageHeader } from "@/components/ui";
import { WatchlistEditor } from "@/components/watchlist-editor";

// Social Companion now lives INSIDE Social Research (the watchlist editor is
// rendered on /market-research right under the influencer roster). This page
// is kept as a thin alias so any old bookmarks / deep links still resolve to
// the same editor instead of 404-ing.
export default function SocialCompanionPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Social Companion"
        sub="Track a cohort of creators in your space. Refresh to pull their recent posts, ranked by what's breaking out — then turn any of it into an on-voice script. (Also available under Social Research.)"
      />
      <WatchlistEditor />
    </div>
  );
}
