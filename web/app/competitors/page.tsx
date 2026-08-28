"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Competitor,
  type CompetitorPost,
  type NicheState,
} from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  CardTitle,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";

function num(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}
function rate(r: number | null | undefined): string {
  return r == null ? "—" : `${(r * 100).toFixed(2)}%`;
}

/** Poll a background job until it stops running.
 *
 * Every long step here (discovery, sync, media, vision) is a background job,
 * and the shape is always {status}. The cancel ref matters: without it a
 * poll started on this screen keeps firing after the user has navigated
 * away, and then sets state on an unmounted component. */
function useJobPoller() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  return useCallback(
    async (
      get: (id: string) => Promise<Record<string, unknown>>,
      id: string,
      onDone: (r: Record<string, unknown>) => void,
      everyMs = 4000,
    ) => {
      for (let i = 0; i < 200; i++) {
        await new Promise((r) => setTimeout(r, everyMs));
        if (!alive.current) return;
        let r: Record<string, unknown>;
        try {
          r = await get(id);
        } catch {
          continue;
        }
        if (r.status !== "running") {
          if (alive.current) onDone(r);
          return;
        }
      }
    },
    [],
  );
}

export default function CompetitorsPage() {
  const [niche, setNiche] = useState<NicheState | null>(null);
  const [nicheDraft, setNicheDraft] = useState("");
  const [savingNiche, setSavingNiche] = useState(false);

  const [comps, setComps] = useState<Competitor[]>([]);
  const [posts, setPosts] = useState<CompetitorPost[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState<string>("");     // competitor_id
  const [view, setView] = useState<"all" | "saved">("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const poll = useJobPoller();

  const loadNiche = useCallback(async () => {
    try {
      const n = await api.getNiche();
      setNiche(n);
      setNicheDraft(n.niche || n.proposal?.niche || n.proposed || "");
    } catch { /* the card shows its own empty state */ }
  }, []);

  const loadCompetitors = useCallback(async () => {
    try {
      const r = await api.listCompetitors();
      setComps(r.competitors || []);
    } catch { /* ignore */ }
  }, []);

  const loadGallery = useCallback(async () => {
    try {
      const g = await api.competitorGallery({
        competitor_id: filter,
        replicate: view === "saved" ? "saved" : "",
        sort: view === "saved" ? "picked" : "engagement",
        limit: 90,
      });
      setPosts(g.posts || []);
      setCounts(g.counts || {});
    } catch { /* ignore */ }
  }, [filter, view]);

  useEffect(() => {
    Promise.all([loadNiche(), loadCompetitors(), loadGallery()])
      .finally(() => setLoading(false));
  }, [loadNiche, loadCompetitors, loadGallery]);

  async function saveNiche() {
    const v = nicheDraft.trim();
    if (!v) return;
    setSavingNiche(true);
    try {
      const terms = niche?.proposal?.terms || niche?.terms || [];
      setNiche(await api.confirmNiche(v, terms));
      setNote("Niche confirmed. Discovery will use this from now on.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not save the niche.");
    } finally {
      setSavingNiche(false);
    }
  }

  async function run(
    label: string,
    start: () => Promise<{ job_id: string }>,
    get: (id: string) => Promise<Record<string, unknown>>,
    done: string,
  ) {
    setBusy(label);
    setNote("");
    try {
      const { job_id } = await start();
      poll(get, job_id, (r) => {
        setBusy("");
        setNote(r.status === "failed" ? `${label} failed: ${r.error}` : done);
        loadCompetitors();
        loadGallery();
      });
    } catch (e) {
      setBusy("");
      setNote(e instanceof Error ? e.message : `${label} failed`);
    }
  }

  async function pick(p: CompetitorPost, status: string) {
    const next = p.replicate_status === status ? "" : status;
    setPosts((list) =>
      list.map((x) => (x.id === p.id ? { ...x, replicate_status: next } : x)));
    try {
      await api.setReplicate(p.id, next);
      const g = await api.competitorGallery({
        competitor_id: filter, replicate: "", sort: "engagement", limit: 1 });
      setCounts(g.counts || {});
    } catch {
      // Put the card back the way it was — a pick that did not persist must
      // not keep looking like it did.
      setPosts((list) =>
        list.map((x) =>
          (x.id === p.id ? { ...x, replicate_status: p.replicate_status } : x)));
      setNote("Could not save that pick.");
    }
  }

  const tracked = comps.filter((c) => c.status === "tracked");
  const candidates = comps.filter((c) => c.status === "candidate");
  const nicheConfirmed = !!niche?.confirmed;

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground text-sm p-6">
        <Spinner /> Loading competitors…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Competitors"
        sub="Who owns your niche, what they post, and which of it you want made for you. Confirm the niche, confirm the competitors, then pick the posts worth replicating."
      />

      {note && (
        <div className="text-[13px] border border-primary/40 bg-primary/5 rounded-md p-3">
          {note}
        </div>
      )}

      {/* 1 — the niche. Everything downstream runs on this answer. */}
      <Card className={nicheConfirmed ? "" : "border-accent/50 bg-accent/5"}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle>1 · Your niche</CardTitle>
          {nicheConfirmed
            ? <Badge tone="ok">confirmed</Badge>
            : <Badge tone="accent">needs your confirmation</Badge>}
        </div>
        <p className="text-[12px] text-muted-foreground mt-1">
          This decides which accounts we go and study, so we don&apos;t guess it.
          {niche?.proposal?.reasoning && !nicheConfirmed && (
            <> {niche.proposal.reasoning}</>
          )}
        </p>
        <div className="flex gap-2 mt-3">
          <Input
            value={nicheDraft}
            onChange={(e) => setNicheDraft(e.target.value)}
            placeholder="e.g. public golf course in Kohler, Wisconsin"
            onKeyDown={(e) => e.key === "Enter" && saveNiche()}
          />
          <Button onClick={saveNiche} disabled={savingNiche || !nicheDraft.trim()}>
            {savingNiche ? <Spinner /> : nicheConfirmed ? "Update" : "Confirm"}
          </Button>
        </div>
        {(niche?.terms?.length || niche?.proposal?.terms?.length) ? (
          <div className="flex flex-wrap gap-1 mt-3">
            <span className="text-[11px] text-muted-foreground mr-1">
              searching for peers with:
            </span>
            {(niche.terms.length ? niche.terms : niche.proposal?.terms || [])
              .map((t) => (
                <span key={t}
                  className="text-[11px] rounded-full px-2 py-0.5 border border-border text-muted-foreground">
                  {t}
                </span>
              ))}
          </div>
        ) : null}
      </Card>

      {/* 2 — confirm who the competitors are. */}
      <Card>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle>2 · Your competitors</CardTitle>
          <div className="flex gap-2">
            <Button
              onClick={() => run("Discovery", () => api.discoverCompetitors("", 14),
                api.discoverJob, "Found candidates — confirm the real ones below.")}
              disabled={!nicheConfirmed || !!busy}>
              {busy === "Discovery" ? <Spinner /> : "Find competitors"}
            </Button>
            <Button
              onClick={() => run("Sync", () => api.syncCompetitors(), api.syncJob,
                "Posts pulled. Fetching media next gets you the pictures.")}
              disabled={!tracked.length || !!busy}>
              {busy === "Sync" ? <Spinner /> : "Pull their posts"}
            </Button>
            <Button
              onClick={() => run("Media", () => api.fetchCompetitorMedia(),
                api.mediaJob, "Media downloaded and stored.")}
              disabled={!tracked.length || !!busy}>
              {busy === "Media" ? <Spinner /> : "Download media"}
            </Button>
          </div>
        </div>
        {!nicheConfirmed && (
          <p className="text-[12px] text-accent mt-2">
            Confirm your niche first — discovery is only as good as the niche it
            starts from.
          </p>
        )}

        {candidates.length > 0 && (
          <>
            <div className="text-[12px] text-muted-foreground mt-4 mb-2">
              Suggested — are these your competitors?
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {candidates.map((c) => (
                <div key={c.id}
                  className="border border-border rounded-md p-3 flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <Badge tone="muted">{c.platform.slice(0, 2).toUpperCase()}</Badge>
                    <span className="text-[13px] font-semibold truncate">@{c.handle}</span>
                    <span className="text-[11px] text-muted-foreground ml-auto">
                      {num(c.followers)}
                    </span>
                  </div>
                  {c.why && (
                    <p className="text-[11px] text-muted-foreground line-clamp-2">{c.why}</p>
                  )}
                  <div className="flex gap-2 mt-auto">
                    <button
                      onClick={async () => {
                        await api.setCompetitorStatus(c.id, "tracked");
                        loadCompetitors();
                      }}
                      className="text-[11px] text-primary hover:underline">
                      ✓ Yes, track them
                    </button>
                    <button
                      onClick={async () => {
                        await api.setCompetitorStatus(c.id, "rejected");
                        loadCompetitors();
                      }}
                      className="text-[11px] text-muted-foreground hover:text-destructive">
                      ✕ Not a competitor
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {tracked.length > 0 && (
          <>
            <div className="text-[12px] text-muted-foreground mt-5 mb-2">
              Tracking {tracked.length} — ranked by engagement against audience size
            </div>
            <div className="flex flex-col divide-y divide-border">
              {tracked
                .slice()
                .sort((a, b) => (b.rank_score || 0) - (a.rank_score || 0))
                .map((c) => (
                  <div key={c.id} className="py-2 flex items-center gap-3 text-[13px]">
                    <button
                      onClick={() => setFilter(filter === c.id ? "" : c.id)}
                      className={`font-medium truncate text-left flex-1 hover:underline ${
                        filter === c.id ? "text-primary" : ""
                      }`}>
                      @{c.handle}
                    </button>
                    <span className="text-muted-foreground">{num(c.followers)}</span>
                    <span className="text-muted-foreground w-20 text-right">
                      {c.measured_posts ? rate(c.median_engagement_rate) : "—"}
                    </span>
                    <span className="w-14 text-right font-semibold">
                      {c.rank_score ? c.rank_score.toFixed(1) : "—"}
                    </span>
                  </div>
                ))}
            </div>
          </>
        )}
      </Card>

      {/* 3 — the gallery: look at what they post, pick what to replicate. */}
      <Card>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle>3 · What they&apos;re posting</CardTitle>
          <div className="flex items-center gap-2">
            {filter && (
              <button onClick={() => setFilter("")}
                className="text-[11px] text-muted-foreground hover:underline">
                clear filter
              </button>
            )}
            <button onClick={() => setView("all")}
              className={`text-[12px] rounded px-2.5 py-1 border ${
                view === "all" ? "border-primary bg-primary/10" : "border-border text-muted-foreground"
              }`}>All</button>
            <button onClick={() => setView("saved")}
              className={`text-[12px] rounded px-2.5 py-1 border ${
                view === "saved" ? "border-primary bg-primary/10" : "border-border text-muted-foreground"
              }`}>
              Saved{counts.saved ? ` (${counts.saved})` : ""}
            </button>
            <Button
              onClick={() => run("Analysis", () => api.analyzeCompetitors(),
                api.analyzeJob, "Analysed — hooks and formats are on the cards.")}
              disabled={!tracked.length || !!busy}>
              {busy === "Analysis" ? <Spinner /> : "Analyse posts"}
            </Button>
          </div>
        </div>
        <p className="text-[12px] text-muted-foreground mt-1">
          {counts.with_media ?? 0} of {counts.total ?? 0} posts have media saved.
          Pick the ones you want made for you.
        </p>

        {posts.length === 0 ? (
          <div className="mt-5 text-[13px] text-muted-foreground border border-dashed border-border rounded-md p-5">
            {view === "saved"
              ? "Nothing saved yet. Switch to All and pick the posts you like."
              : tracked.length
                ? "No posts with media yet — run “Pull their posts”, then “Download media”."
                : "Confirm some competitors above, then pull their posts."}
          </div>
        ) : (
          <div className="grid gap-4 mt-5 sm:grid-cols-2 lg:grid-cols-3">
            {posts.map((p) => {
              const saved = p.replicate_status === "saved";
              const src = p.thumbnail_url || p.stored_media_url;
              const isVideo = p.media_type === "video";
              return (
                <div key={p.id}
                  className={`border rounded-lg overflow-hidden flex flex-col ${
                    saved ? "border-primary ring-1 ring-primary/40" : "border-border"
                  }`}>
                  <div className="relative bg-secondary aspect-[4/5] overflow-hidden">
                    {src ? (
                      isVideo && p.stored_media_url && !p.thumbnail_url ? (
                        <video src={p.stored_media_url} muted playsInline
                          className="w-full h-full object-cover" />
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={src} alt="" loading="lazy"
                          className="w-full h-full object-cover" />
                      )
                    ) : (
                      <div className="w-full h-full grid place-items-center text-[11px] text-muted-foreground">
                        no media
                      </div>
                    )}
                    {isVideo && (
                      <span className="absolute top-2 left-2 text-[10px] bg-black/70 text-white rounded px-1.5 py-0.5">
                        ▶ reel
                      </span>
                    )}
                    <span className="absolute top-2 right-2 text-[10px] bg-black/70 text-white rounded px-1.5 py-0.5">
                      {rate(p.engagement_rate)}
                    </span>
                  </div>

                  <div className="p-3 flex flex-col gap-1.5 flex-1">
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="font-medium text-foreground">@{p.handle}</span>
                      <span className="ml-auto">♥ {num(p.likes)} · 💬 {num(p.comments)}</span>
                    </div>
                    {p.hook && (
                      <p className="text-[12px] font-medium line-clamp-2">“{p.hook}”</p>
                    )}
                    {!p.hook && p.caption && (
                      <p className="text-[12px] text-muted-foreground line-clamp-2">
                        {p.caption}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {p.format && (
                        <span className="text-[10px] rounded-full px-2 py-0.5 border border-border text-muted-foreground">
                          {p.format}
                        </span>
                      )}
                      {p.hook_pattern && (
                        <span className="text-[10px] rounded-full px-2 py-0.5 border border-border text-muted-foreground">
                          {p.hook_pattern}
                        </span>
                      )}
                      {p.eye_score != null && (
                        <span className="text-[10px] rounded-full px-2 py-0.5 border border-border text-muted-foreground">
                          design {p.eye_score.toFixed(0)}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-auto pt-1.5">
                      <button onClick={() => pick(p, "saved")}
                        className={`text-[11px] font-medium ${
                          saved ? "text-primary" : "text-muted-foreground hover:text-primary"
                        }`}>
                        {saved ? "✓ Saved to replicate" : "♡ Replicate this"}
                      </button>
                      {p.url && (
                        <a href={p.url} target="_blank" rel="noopener noreferrer"
                          className="text-[11px] text-muted-foreground hover:underline ml-auto">
                          original →
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
