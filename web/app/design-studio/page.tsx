"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type ContentDraft, type TopicIdea } from "@/lib/api";
import {
  Button,
  Card,
  CardTitle,
  Input,
  Textarea,
  Select,
  Label,
  Badge,
  Spinner,
  PageHeader,
} from "@/components/ui";

const MULTI_PLATFORMS = ["linkedin", "facebook", "instagram", "x", "tiktok"];
const PLATFORMS = ["instagram", "linkedin", "x", "youtube", "tiktok", "threads"];

function scoreTone(s: number): "ok" | "accent" | "destructive" {
  if (s >= 0.7) return "ok";
  if (s >= 0.4) return "accent";
  return "destructive";
}

/** Split a carousel draft ("Slide 1: ... Slide 2: ...") into slides. */
function parseSlides(draft: string): string[] | null {
  if (!/slide\s*1\s*[:.\-]/i.test(draft)) return null;
  const parts = draft
    .split(/\n?\s*slide\s*\d+\s*[:.\-]\s*/i)
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length > 1 ? parts : null;
}

export default function ContentStudio() {
  const [mode, setMode] = useState<"post" | "multi">("post");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Post — text + image"
        sub="One topic → an on-voice written post AND a real photo of James you pick from the hero library, queued together as one item. Grounded in voice + thesis + research + the learned guardrails; voice-QA scores every draft; nothing ships without approval."
      />
      <div className="flex gap-2">
        <TabBtn active={mode === "post"} onClick={() => setMode("post")}>
          Post + image
        </TabBtn>
        <TabBtn active={mode === "multi"} onClick={() => setMode("multi")}>
          Multi-platform (text)
        </TabBtn>
      </div>
      {mode === "post" ? <PostImageMode /> : <MultiMode />}
    </div>
  );
}

// ── Post + image: one topic → on-voice post + a matching hero image ──


function PostImageMode() {
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [pillar, setPillar] = useState("");
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [imgBusy, setImgBusy] = useState(false);
  const [draft, setDraft] = useState<ContentDraft | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageErr, setImageErr] = useState<string | null>(null);
  const [err, setErr] = useState("");

  // Image source: a REAL hero photo (default, guaranteed likeness) OR a NEW
  // AI render from the trained Higgsfield Soul ID.
  const [imageMode, setImageMode] = useState<"photo" | "soul">("photo");
  const [heroPhotos, setHeroPhotos] = useState<string[]>([]);
  const [selectedPhoto, setSelectedPhoto] = useState<string>("");

  useEffect(() => {
    api
      .getHeroContext()
      .then((r) => {
        const urls = r.photo_urls || [];
        setHeroPhotos(urls);
        if (urls.length) setSelectedPhoto(urls[0]); // default to the first
      })
      .catch(() => setHeroPhotos([]));
  }, []);

  // Suggested topics — same data-steered ideation as the video flow.
  // Auto-loaded on arrival so picks are ready before you type anything.
  const [ideas, setIdeas] = useState<TopicIdea[]>([]);
  const [ideasBusy, setIdeasBusy] = useState(false);
  const [ideasErr, setIdeasErr] = useState<string | null>(null);

  // Topic ideation is a background job (intel is 30-60s) — start, then poll.
  // A token guards against a superseding regenerate / unmount.
  const pollRef = useRef<{ cancelled: boolean } | null>(null);

  async function loadIdeas() {
    if (pollRef.current) pollRef.current.cancelled = true;
    const token = { cancelled: false };
    pollRef.current = token;

    setIdeasBusy(true);
    setIdeasErr(null);
    try {
      const { batch_id } = await api.startPostIdeas(10);
      for (let i = 0; i < 45; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        if (token.cancelled) return;
        const r = await api.getPostIdeas(batch_id);
        if (r.status === "done") {
          setIdeas(r.ideas || []);
          if ((!r.ideas || r.ideas.length === 0) && r.error) setIdeasErr(r.error);
          return;
        }
        if (r.status === "failed") {
          setIdeasErr(r.error || "topic generation failed");
          return;
        }
      }
      setIdeasErr("Timed out generating topics — hit regenerate to retry.");
    } catch (e) {
      if (!token.cancelled)
        setIdeasErr(e instanceof Error ? e.message : "could not load topics");
    } finally {
      if (!token.cancelled) setIdeasBusy(false);
    }
  }

  // On arrival, show the SAVED suggestions (no regen). Only ideate when
  // there are none saved yet. "regenerate" forces a fresh batch.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.getSavedIdeas();
        if (cancelled) return;
        if (r.ideas && r.ideas.length) {
          setIdeas(r.ideas);
          return;
        }
      } catch {
        /* fall through to generate */
      }
      if (!cancelled) loadIdeas();
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) pollRef.current.cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function setIdeaStatus(id: string, status: "accepted" | "rejected" | "pending") {
    // Optimistic — reject drops it, accept pins it, pending un-keeps.
    setIdeas((prev) =>
      status === "rejected"
        ? prev.filter((i) => i.id !== id)
        : prev.map((i) => (i.id === id ? { ...i, status } : i))
    );
    try {
      const r = await api.setIdeaStatus(id, status);
      setIdeas(r.ideas);
    } catch {
      /* keep optimistic state */
    }
  }

  function pickIdea(i: TopicIdea) {
    setTopic(i.topic);
    if (i.pillar) setPillar(i.pillar);
    // Bring the form into view on smaller screens.
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setDraft(null);
    setImageUrl(null);
    setImageErr(null);
    // Write the post first (queued), show it, THEN attach the chosen photo.
    let d: ContentDraft;
    try {
      d = await api.generate({
        platform,
        format: "post",
        pillar,
        topic,
        extra_instructions: extra,
      });
      setDraft(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
      setBusy(false);
      return;
    }
    setBusy(false);

    // Image step. "photo" = attach the real hero photo (instant, exact
    // likeness). "soul" = render a NEW James from the Higgsfield Soul ID.
    if (imageMode === "photo" && !selectedPhoto) return;
    if (!d.action_id) {
      setImageErr("post wasn't queued, so no image was attached");
      return;
    }
    setImgBusy(true);
    try {
      if (imageMode === "photo") {
        await api.setPostImage({ action_id: d.action_id, image_url: selectedPhoto });
        setImageUrl(selectedPhoto);
      } else {
        const { job_id } = await api.startSoulImage({
          action_id: d.action_id,
          topic,
          draft_text: d.draft || topic,
          aspect: "9:16",
        });
        let done = false;
        for (let i = 0; i < 60 && !done; i++) {
          await new Promise((r) => setTimeout(r, 3000));
          const r = await api.getSoulImage(job_id);
          if (r.status === "done") {
            setImageUrl(r.image_url);
            if (!r.image_url && r.error) setImageErr(r.error);
            done = true;
          } else if (r.status === "failed") {
            setImageErr(r.error || "Soul render failed");
            done = true;
          }
        }
        if (!done) setImageErr("Soul render timed out — try again.");
      }
    } catch (e) {
      setImageErr(e instanceof Error ? e.message : "could not attach image");
    } finally {
      setImgBusy(false);
    }
  }

  return (
    <>
      <Card>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Suggested topics</CardTitle>
          <button
            onClick={loadIdeas}
            disabled={ideasBusy}
            className="text-[12px] text-primary hover:underline disabled:opacity-50"
          >
            {ideasBusy ? "thinking…" : "↻ regenerate"}
          </button>
        </div>
        <p className="text-[12px] text-muted-foreground -mt-1 mb-2">
          Steered from live data — tracked creators + trends + James&apos;s real
          topics, balanced to your brand pillars. These are saved, so they stay
          until you change them. Click a topic to load it · ✓ keep · ✕ reject ·
          ↻ regenerate (kept ones stay).
        </p>
        {ideasBusy && ideas.length === 0 ? (
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-2">
            <Spinner /> Pulling trends + ideating 10 topics… ~15–25s
          </div>
        ) : ideasErr ? (
          <p className="text-[12px] text-muted-foreground py-1">
            {ideasErr}{" "}
            <button onClick={loadIdeas} className="text-primary hover:underline">
              retry
            </button>
          </p>
        ) : ideas.length === 0 ? (
          <p className="text-[12px] text-muted-foreground py-1">No topics yet.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {ideas.map((i, idx) => {
              const accepted = i.status === "accepted";
              return (
                <div
                  key={i.id || idx}
                  className={`rounded-lg border p-3 transition-colors ${
                    accepted
                      ? "border-primary bg-primary/10"
                      : topic === i.topic
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/60"
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <button
                      onClick={() => pickIdea(i)}
                      className="text-left text-[13px] font-medium leading-snug flex-1"
                      title="Load this topic into the composer"
                    >
                      {i.topic}
                    </button>
                    {accepted && <Badge tone="ok">kept</Badge>}
                    {i.pillar && <Badge tone="muted">{i.pillar}</Badge>}
                  </div>
                  {i.trend_basis && (
                    <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
                      {i.trend_basis}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-2">
                    <button
                      onClick={() => pickIdea(i)}
                      className="text-[11px] text-primary hover:underline"
                    >
                      use this →
                    </button>
                    {i.id && (
                      <div className="ml-auto flex items-center gap-1">
                        <button
                          onClick={() => setIdeaStatus(i.id!, accepted ? "pending" : "accepted")}
                          title={accepted ? "Kept — click to un-keep" : "Keep this suggestion"}
                          aria-label={accepted ? "Un-keep" : "Keep"}
                          className={`h-6 w-6 grid place-items-center rounded-md border text-[12px] transition-colors ${
                            accepted
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-border text-muted-foreground hover:border-primary hover:text-primary"
                          }`}
                        >
                          ✓
                        </button>
                        <button
                          onClick={() => setIdeaStatus(i.id!, "rejected")}
                          title="Reject (remove this suggestion)"
                          aria-label="Reject"
                          className="h-6 w-6 grid place-items-center rounded-md border border-border text-[12px] text-muted-foreground transition-colors hover:border-destructive hover:text-destructive"
                        >
                          ✕
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card>
        <CardTitle>One topic → post + James&apos;s photo</CardTitle>
        <p className="text-[12px] text-muted-foreground -mt-1 mb-2">
          Pick a suggestion above or type your own. Writes the post in
          James&apos;s voice and attaches the real hero photo you pick below.
          Both land together in the Approval Queue.
        </p>
        <Label>Topic</Label>
        <Textarea
          rows={2}
          placeholder="what the post is about"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-4 mt-1">
          <div>
            <Label>Platform</Label>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Pillar (optional)</Label>
            <Input value={pillar} onChange={(e) => setPillar(e.target.value)} placeholder="which brand pillar" />
          </div>
        </div>
        <Label>Extra instructions (optional)</Label>
        <Input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="a one-off steer" />
        <div className="mt-4">
          <Label>Image for this post</Label>
          <div className="flex gap-2 mb-2">
            <button
              type="button"
              onClick={() => setImageMode("photo")}
              className={`text-[12px] px-3 py-1.5 rounded-full border transition-colors ${
                imageMode === "photo"
                  ? "border-primary text-primary bg-primary/10"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              Real photo
            </button>
            <button
              type="button"
              onClick={() => setImageMode("soul")}
              className={`text-[12px] px-3 py-1.5 rounded-full border transition-colors ${
                imageMode === "soul"
                  ? "border-primary text-primary bg-primary/10"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              ✨ AI (Soul ID)
            </button>
          </div>

          {imageMode === "photo" ? (
            heroPhotos.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">
                No hero photos yet. Upload some on the{" "}
                <Link href="/hero" className="text-primary underline">
                  Hero
                </Link>{" "}
                page, then they&apos;ll show here to pick from.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {heroPhotos.map((url) => (
                  <button
                    key={url}
                    type="button"
                    onClick={() => setSelectedPhoto(selectedPhoto === url ? "" : url)}
                    className={`relative h-20 w-20 overflow-hidden rounded-md border-2 transition-colors ${
                      selectedPhoto === url
                        ? "border-primary"
                        : "border-transparent hover:border-border"
                    }`}
                    title="Use this photo"
                  >
                    <img src={mediaUrl(url)} alt="hero" className="h-full w-full object-cover" />
                    {selectedPhoto === url && (
                      <span className="absolute bottom-0 right-0 bg-primary text-primary-foreground text-[10px] px-1 rounded-tl">
                        ✓
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )
          ) : (
            <p className="text-[12px] text-muted-foreground">
              Renders a fresh, on-topic image of James from your trained
              Higgsfield Soul ID (~30–60s). Same face every time. Trained on the{" "}
              <Link href="/hero" className="text-primary underline">
                Hero
              </Link>{" "}
              page.
            </p>
          )}
        </div>
        <div className="mt-4">
          <Button onClick={run} disabled={busy || imgBusy || !topic.trim()}>
            {busy ? (
              <Spinner />
            ) : imageMode === "soul" ? (
              "Generate post + Soul image"
            ) : selectedPhoto ? (
              "Generate post + attach photo"
            ) : (
              "Generate post"
            )}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
        {busy && (
          <p className="text-[12px] text-muted-foreground mt-2">
            Writing the post + voice-QA — ~15–30s.
          </p>
        )}
      </Card>

      {draft && (
        <>
          <div className="text-[12px] text-muted-foreground flex items-center gap-2">
            {draft.action_id ? (
              <>
                <Badge tone="primary">queued for approval</Badge>
                <Link href="/queue" className="text-primary hover:underline">
                  Review in the queue →
                </Link>
              </>
            ) : (
              <Badge tone="accent">not queued</Badge>
            )}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <DraftCard draft={draft} />
            {((imageMode === "photo" && selectedPhoto) || imageUrl || imgBusy || imageErr) && (
              <Card>
                <CardTitle>{imageMode === "soul" ? "James (Soul ID)" : "James's photo"}</CardTitle>
                {imgBusy ? (
                  <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-3">
                    <Spinner />{" "}
                    {imageMode === "soul"
                      ? "Rendering James from your Soul ID — ~30–60s…"
                      : "Attaching the photo…"}
                  </div>
                ) : imageUrl ? (
                  <>
                    <a href={mediaUrl(imageUrl)} target="_blank" rel="noopener noreferrer">
                      <img
                        src={mediaUrl(imageUrl)}
                        alt="post image"
                        className="w-full rounded-md border border-border mt-2 bg-background"
                      />
                    </a>
                    <p className="text-[11px] text-muted-foreground mt-2">
                      {imageMode === "soul"
                        ? "Fresh render from your Higgsfield Soul ID, attached to the queued post."
                        : "Your real hero photo, attached to the queued post."}
                    </p>
                  </>
                ) : (
                  <p className="text-[12px] text-muted-foreground mt-2">
                    {imageErr || "No image attached."}
                  </p>
                )}
              </Card>
            )}
          </div>
        </>
      )}
    </>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-semibold rounded-md transition-colors ${
        active ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

// ── Multi-platform: one idea → posts per platform + a carousel ──

function MultiMode() {
  const [topic, setTopic] = useState("");
  const [pillar, setPillar] = useState("");
  const [platforms, setPlatforms] = useState<string[]>(["linkedin", "facebook", "instagram"]);
  const [carousel, setCarousel] = useState(true);
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<ContentDraft[] | null>(null);
  const [queued, setQueued] = useState(0);
  const [err, setErr] = useState("");

  function toggle(p: string) {
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  async function run() {
    if (!topic.trim()) return;
    setBusy(true);
    setErr("");
    setDrafts(null);
    try {
      const r = await api.generateMulti({
        topic,
        pillar,
        platforms,
        carousel,
        extra_instructions: extra,
      });
      setDrafts(r.drafts);
      setQueued(r.queued);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardTitle>One idea → many channels</CardTitle>
        <Label>Topic</Label>
        <Textarea
          rows={2}
          placeholder="what the content is about"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <Label>Pillar (optional)</Label>
        <Input value={pillar} onChange={(e) => setPillar(e.target.value)} placeholder="which brand pillar" />
        <Label>Channels</Label>
        <div className="flex flex-wrap gap-2">
          {MULTI_PLATFORMS.map((p) => (
            <button
              key={p}
              onClick={() => toggle(p)}
              className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
                platforms.includes(p)
                  ? "border-primary text-foreground bg-primary/10"
                  : "border-border text-muted-foreground"
              }`}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => setCarousel((c) => !c)}
            className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
              carousel ? "border-accent text-foreground bg-accent/10" : "border-border text-muted-foreground"
            }`}
          >
            + carousel
          </button>
        </div>
        <Label>Extra instructions (optional)</Label>
        <Input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="a one-off steer" />
        <div className="mt-3">
          <Button onClick={run} disabled={busy || !topic.trim()}>
            {busy ? <Spinner /> : `Generate ${platforms.length + (carousel ? 1 : 0)} pieces`}
          </Button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
        {busy && (
          <p className="text-[12px] text-muted-foreground mt-2">
            Generating + voice-QA on each in parallel — this takes ~30–60s.
          </p>
        )}
      </Card>

      {drafts && (
        <>
          <div className="text-[12px] text-muted-foreground flex items-center gap-2">
            <Badge tone="primary">{queued} queued for approval</Badge>
            <Link href="/queue" className="text-primary hover:underline">
              Review in the queue →
            </Link>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {drafts.map((d, i) => (
              <DraftCard key={i} draft={d} />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function DraftCard({ draft }: { draft: ContentDraft }) {
  const [copied, setCopied] = useState(false);
  const slides = draft.format === "carousel" ? parseSlides(draft.draft) : null;

  function copy() {
    navigator.clipboard.writeText(draft.draft);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  function download() {
    const blob = new Blob([draft.draft], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${draft.platform}-${draft.format}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="border border-border rounded-lg p-4 bg-card flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone="primary">{draft.platform}</Badge>
          <span className="text-[12px] text-muted-foreground">{draft.format}</span>
        </div>
        <Badge tone={scoreTone(draft.voice_score)}>voice {draft.voice_score.toFixed(2)}</Badge>
      </div>

      {draft.status === "not_generated" ? (
        <p className="text-[12px] text-muted-foreground">{draft.note}</p>
      ) : slides ? (
        <div className="flex flex-col gap-2">
          {slides.map((s, i) => (
            <div key={i} className="bg-background border border-border rounded-md p-2 text-[13px]">
              <span className="text-[10px] uppercase tracking-[.4px] text-muted-foreground">
                Slide {i + 1}
              </span>
              <div>{s}</div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap bg-background border border-border rounded-md p-3">
          {draft.draft}
        </p>
      )}

      {draft.qa && draft.qa.drift.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[.4px]">voice-QA: </span>
          {draft.qa.drift.join(" · ")}
        </div>
      )}

      {draft.draft && (
        <div className="flex items-center gap-3 text-[12px] pt-1">
          <button onClick={copy} className="text-primary hover:underline">
            {copied ? "copied!" : "copy"}
          </button>
          <button onClick={download} className="text-muted-foreground hover:text-foreground">
            export .txt
          </button>
          {draft.action_id && <span className="ml-auto text-muted-foreground">queued ✓</span>}
        </div>
      )}
    </div>
  );
}
