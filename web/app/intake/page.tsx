"use client";

/**
 * Brand Setup — bm2.0's 5-step onboarding wizard on the merged /manager
 * routes (this REPLACES the old free-form intake page; the ledger decision).
 *
 *   1. Basics                — name / entity type / website / socials → research seed
 *   2. Is this your brand?   — sync discover → candidate picker → confirm
 *   3. What we found         — confirmed 7-lane fan-out (202 + status poll) → review
 *   4. Interview             — question batches, ✨ suggest, ✨ draft-all batch review
 *   5. Brand Voice           — harvest (202 + poll) → the distilled voice reveal
 *
 * Every step is resumable: on load the wizard reads the current state
 * (seed present → research running/done → questions open → voice ready) and
 * lands a returning user on the right step. A 409 from any background kicker
 * means "already running" — we resume the poll instead of erroring.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CitationChips } from "@/components/manager/citations";
import { AnswerReview } from "@/components/manager/onboard/answer-review";
import { ENTITY_OPTIONS, isEntityType, isUrl } from "@/components/manager/onboard/format";
import { QuestionCard } from "@/components/manager/onboard/question-card";
import { ResearchReview } from "@/components/manager/onboard/research-review";
import { Stepper } from "@/components/manager/onboard/steps";
import { VoiceReveal } from "@/components/manager/onboard/voice-reveal";
import { EmptyState, ErrorBox, Loading } from "@/components/manager/primitives";
import { Badge, Button, Card, Input, PageHeader, Spinner } from "@/components/ui";
import {
  errorMessage,
  isConflict,
  managerApi,
  type AutoAnswerDraft,
  type EntityCandidate,
  type EntityType,
  type InterviewQuestion,
} from "@/lib/manager-api";

const INTERVIEW_BUDGET = 10; // interviewer ranks + caps server-side (≤25 during onboarding)
const INTERVIEW_CAP = 25;
const STATUS_POLL_MS = 2500;

type ResearchPhase = "idle" | "running" | "succeeded" | "failed";

/** Primary-button styling for plain <Link>s (ui.tsx Button, link edition). */
const BTN_LINK =
  "inline-flex items-center justify-center rounded-md px-4 py-2.5 text-sm font-semibold " +
  "bg-primary text-primary-foreground hover:bg-primary/90 transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export default function IntakePage() {
  const [booted, setBooted] = useState(false);
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── step 1: basics ────────────────────────────────────────────────────────
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState<EntityType>("person");
  const [website, setWebsite] = useState("");
  const [socials, setSocials] = useState("");

  // ── step 2: discover ──────────────────────────────────────────────────────
  const [candidates, setCandidates] = useState<EntityCandidate[] | null>(null);
  const [discoverFailures, setDiscoverFailures] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const discoverStarted = useRef(false);

  // ── step 3: confirmed research (202 + poll) ──────────────────────────────
  const [research, setResearch] = useState<ResearchPhase>("idle");
  const [researchError, setResearchError] = useState<string | null>(null);
  const [confirmedUrls, setConfirmedUrls] = useState<string[]>([]);
  const [laneStats, setLaneStats] = useState<Record<string, number>>({});
  const [failures, setFailures] = useState<string[]>([]);
  const [profileFields, setProfileFields] = useState<Record<string, unknown> | null>(null);

  // ── step 4: interview ─────────────────────────────────────────────────────
  const [questions, setQuestions] = useState<InterviewQuestion[] | null>(null);
  const [onboardingStatus, setOnboardingStatus] = useState<string>("");
  const [qIndex, setQIndex] = useState(0);
  const [answered, setAnswered] = useState(0);
  const [qError, setQError] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);
  const [drafts, setDrafts] = useState<AutoAnswerDraft[] | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const interviewStarted = useRef(false);

  const parsedSocials = useMemo(
    () =>
      socials
        .split(/[\n,;]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    [socials],
  );

  // ── resumability: land a returning user on the right step ────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [seedR, statusR, voiceR] = await Promise.allSettled([
        managerApi.getResearchSeed(),
        managerApi.researchStatus(),
        managerApi.voiceProfile(),
      ]);
      if (cancelled) return;

      const seed = seedR.status === "fulfilled" ? seedR.value.research_seed : {};
      const hasSeed = !!String(seed?.name ?? "").trim();
      if (hasSeed) {
        setName(String(seed.name));
        if (isEntityType(seed.entity_type)) setEntityType(seed.entity_type);
        if (typeof seed.website === "string") setWebsite(seed.website);
        if (Array.isArray(seed.socials)) setSocials(seed.socials.join(", "));
      }

      const rs = statusR.status === "fulfilled" ? statusR.value : null;
      const vp = voiceR.status === "fulfilled" ? voiceR.value : null;

      if (vp && (vp.state === "running" || vp.state === "ready")) {
        // Voice already harvesting/harvested — the reveal is the live step.
        setStep(5);
      } else if (rs?.state === "running") {
        setResearch("running"); // the status poller takes over
        setStep(3);
      } else if (rs?.state === "succeeded") {
        setStep(4); // research done, questions still open — straight to the interview
      } else if (rs?.state === "failed") {
        setResearchError(rs.error || "Research failed — please try again.");
        setResearch("failed");
        setStep(3);
      } else if (hasSeed) {
        setStep(2); // seed saved but never confirmed — re-run "is this your brand?"
      }
      setBooted(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── step 1 → 2 ────────────────────────────────────────────────────────────
  async function submitBasics(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await managerApi.setResearchSeed({
        name: name.trim(),
        entity_type: entityType,
        website: website.trim() || null,
        socials: parsedSocials,
      });
      setCandidates(null);
      discoverStarted.current = false; // fresh seed → fresh discover
      setStep(2);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const runDiscover = useCallback(async () => {
    if (!name.trim()) return;
    setDiscovering(true);
    setError(null);
    try {
      const report = await managerApi.discover({
        name: name.trim(),
        entity_type: entityType,
        website: website.trim() || null,
        socials: parsedSocials,
      });
      setCandidates([...report.candidates].sort((a, b) => b.score - a.score));
      setDiscoverFailures(report.failures);
    } catch (err) {
      setError(errorMessage(err));
      setCandidates((c) => c ?? []); // show the empty/retry state, not an endless spinner
    } finally {
      setDiscovering(false);
    }
  }, [name, entityType, website, parsedSocials]);

  // Entering step 2 without candidates runs discover once (fresh or resumed).
  useEffect(() => {
    if (!booted || step !== 2 || discoverStarted.current) return;
    discoverStarted.current = true;
    void runDiscover();
  }, [booted, step, runDiscover]);

  function candidateUrls(c: EntityCandidate): string[] {
    if (c.urls.length > 0) return c.urls;
    return website.trim() ? [website.trim()] : [];
  }

  // ── step 2 → 3 ────────────────────────────────────────────────────────────

  /** POST confirmed expects 202 {state:"running"}; a 409 means a run is
   * already in flight — either way the status poller takes over. */
  async function startConfirmedResearch(urls: string[]) {
    try {
      await managerApi.confirmResearch(urls);
    } catch (err) {
      if (isConflict(err)) return; // already running — just poll
      throw err;
    }
  }

  async function confirmCandidate(c: EntityCandidate) {
    if (busy) return;
    const urls = candidateUrls(c);
    if (urls.length === 0) return; // button is disabled in this case
    setBusy(true);
    setError(null);
    try {
      await startConfirmedResearch(urls);
      setConfirmedUrls(urls);
      setResearchError(null);
      setResearch("running");
      setStep(3);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  /** On resume the confirmed urls are gone from state — fall back to the
   * seed's own urls; with none, the way back is picking a candidate again. */
  const retryUrls = useMemo(() => {
    if (confirmedUrls.length > 0) return confirmedUrls;
    return [website.trim(), ...parsedSocials].filter(isUrl);
  }, [confirmedUrls, website, parsedSocials]);

  async function retryResearch() {
    if (busy) return;
    if (retryUrls.length === 0) {
      setCandidates(null);
      discoverStarted.current = false;
      setStep(2);
      return;
    }
    setBusy(true);
    setResearchError(null);
    try {
      await startConfirmedResearch(retryUrls);
      setResearch("running");
      setError(null);
    } catch (err) {
      setResearchError(errorMessage(err));
      setResearch("failed");
    } finally {
      setBusy(false);
    }
  }

  // Poll research/status while the background fan-out runs; transient poll
  // failures are tolerated (the run itself is unaffected) but give up after
  // ~30s of consecutive misses rather than spinning forever.
  useEffect(() => {
    if (research !== "running") return;
    let cancelled = false;
    let misses = 0;
    const tick = async () => {
      try {
        const status = await managerApi.researchStatus();
        if (cancelled) return;
        misses = 0;
        setLaneStats(status.lane_stats ?? {});
        if (status.state === "succeeded") {
          setFailures(status.failures ?? []);
          const profile = await managerApi.brandProfile();
          if (cancelled) return;
          setProfileFields(profile.fields ?? {});
          setResearch("succeeded");
          setResearchError(null);
        } else if (status.state === "failed") {
          setResearchError(status.error || "Research failed — please try again.");
          setResearch("failed");
        } else if (status.state === "none") {
          setResearchError("No research run on record — pick your brand again.");
          setResearch("failed");
        }
        // "running": keep polling
      } catch (err) {
        if (cancelled) return;
        misses += 1;
        if (misses >= 12) {
          setResearchError(`Lost contact with the research run (${errorMessage(err)}).`);
          setResearch("failed");
        }
      }
    };
    void tick();
    const interval = setInterval(tick, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [research]);

  // ── step 4: the interview ─────────────────────────────────────────────────
  const loadInterview = useCallback(async () => {
    setQuestions(null);
    setQIndex(0);
    setQError(null);
    setFinished(false);
    setDrafts(null);
    setDraftNote(null);
    try {
      const r = await managerApi.interviewNext(INTERVIEW_BUDGET);
      setQuestions(r.questions);
      setOnboardingStatus(r.onboarding_status);
      setFinished(r.questions.length === 0);
    } catch (err) {
      setQuestions([]);
      setFinished(false);
      setQError(errorMessage(err));
    }
  }, []);

  async function goToInterview() {
    if (busy) return;
    setBusy(true);
    setError(null);
    interviewStarted.current = true;
    setStep(4);
    await loadInterview();
    setBusy(false);
  }

  // Resuming straight onto step 4 (or arriving without goToInterview).
  useEffect(() => {
    if (!booted || step !== 4 || interviewStarted.current) return;
    interviewStarted.current = true;
    void loadInterview();
  }, [booted, step, loadInterview]);

  // No skip endpoint on the backend: fetched questions are already "asked",
  // so skipping is simply advancing to the next one.
  async function submitAnswer(answer: string | null) {
    if (!questions || busy) return;
    const q = questions[qIndex];
    if (!q) return;
    setQError(null);
    if (answer !== null) {
      setBusy(true);
      try {
        await managerApi.answerQuestion(q.id, answer);
        setAnswered((n) => n + 1);
      } catch (err) {
        setQError(errorMessage(err));
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    if (qIndex + 1 >= questions.length) setFinished(true);
    else setQIndex(qIndex + 1);
  }

  /** "✨ Draft all with AI": kick the background batch (202; a 409 means one
   * is already running — resume its poll), wait for it, review the drafts. */
  async function draftAll() {
    if (drafting || busy) return;
    setDrafting(true);
    setDraftError(null);
    setDraftNote(null);
    try {
      try {
        await managerApi.startAutoAnswer();
      } catch (err) {
        if (!isConflict(err)) throw err; // already running — poll the one in flight
      }
      const status = await managerApi.awaitAutoAnswer({ intervalMs: STATUS_POLL_MS });
      if (status.state === "failed") {
        setDraftError(status.error || "The AI drafting run failed — you can answer by hand.");
      } else if (status.state === "succeeded" && status.drafts.length > 0) {
        setDrafts(status.drafts);
      } else {
        setDraftNote("Nothing to draft — the remaining questions need you.");
      }
    } catch (err) {
      setDraftError(errorMessage(err));
    } finally {
      setDrafting(false);
    }
  }

  function onReviewDone(saved: number) {
    if (saved > 0) setAnswered((n) => n + saved);
    void loadInterview(); // settled questions are swept; fetch the next batch
  }

  const currentQuestion = questions && !finished && !drafts ? questions[qIndex] : null;

  // ─────────────────────────────────────────────────────────────── render ──
  if (!booted) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner /> loading…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <PageHeader
        title="Brand Setup"
        sub="A few basics from you — the Researcher does the heavy lifting. Teach the brand manager who this brand is; everything here steers every engine."
      />

      <Stepper current={step} />
      {error && <ErrorBox message={error} />}

      {/* ------------------------------------------------ step 1: basics */}
      {step === 1 && (
        <form onSubmit={submitBasics}>
          <Card className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-[12px] font-medium" htmlFor="brand-name">
                Brand name
              </label>
              <Input
                id="brand-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Turtleback Golf Course"
                autoFocus
              />
              <p className="text-[11px] text-muted-foreground">
                The name people search for. We&apos;ll find the rest.
              </p>
            </div>

            <div className="flex flex-col gap-1">
              <span className="text-[12px] font-medium">What kind of brand is this?</span>
              <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Entity type">
                {ENTITY_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    role="radio"
                    aria-checked={entityType === o.value}
                    onClick={() => setEntityType(o.value)}
                    className={`rounded-md border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      entityType === o.value
                        ? "border-primary/60 bg-primary/10"
                        : "border-border bg-background hover:bg-secondary"
                    }`}
                  >
                    <span className="block text-[13px] font-semibold">{o.label}</span>
                    <span className="block text-[11px] text-muted-foreground">{o.hint}</span>
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-muted-foreground">
                The interview is tailored to this — a golf course gets different questions than a person.
              </p>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-[12px] font-medium" htmlFor="brand-website">
                Website <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <Input
                id="brand-website"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                placeholder="https://…"
                inputMode="url"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-[12px] font-medium" htmlFor="brand-socials">
                Social handles{" "}
                <span className="text-muted-foreground font-normal">(optional, comma-separated)</span>
              </label>
              <Input
                id="brand-socials"
                value={socials}
                onChange={(e) => setSocials(e.target.value)}
                placeholder="@handle, youtube.com/@channel"
              />
              <p className="text-[11px] text-muted-foreground">
                One or two is plenty — they anchor the research.
              </p>
            </div>

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={!name.trim() || busy}>
                {busy ? (
                  <span className="inline-flex items-center gap-2">
                    <Spinner /> Saving…
                  </span>
                ) : (
                  "Start research"
                )}
              </Button>
              <Link href="/manager" className="text-sm text-muted-foreground hover:text-foreground">
                Cancel
              </Link>
            </div>
          </Card>
        </form>
      )}

      {/* ------------------------------------- step 2: is this your brand */}
      {step === 2 && (
        <>
          {candidates === null || discovering ? (
            <Loading label={`Searching the public web for “${name}”…`} />
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[13px] text-muted-foreground">
                {candidates.length > 0
                  ? `We found ${candidates.length === 1 ? "one match" : `${candidates.length} possible matches`}. Pick yours and we'll research it in depth.`
                  : "Nothing confident came back for this name."}
              </p>
              {discoverFailures.length > 0 && (
                <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-[12px] text-warning">
                  Some sources were unavailable: {discoverFailures.join("; ")}
                </div>
              )}

              {candidates.map((c, i) => (
                <Card key={`${c.name}-${i}`} className="flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold">{c.name}</div>
                      <p className="text-[13px] text-muted-foreground mt-0.5">{c.description}</p>
                      <div className="mt-2">
                        <CitationChips citations={c.urls} />
                      </div>
                    </div>
                    <Badge tone={c.score >= 0.6 ? "primary" : "muted"}>
                      {Math.round(c.score * 100)}% match
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3">
                    <Button
                      disabled={busy || candidateUrls(c).length === 0}
                      onClick={() => confirmCandidate(c)}
                    >
                      {busy ? (
                        <span className="inline-flex items-center gap-2">
                          <Spinner /> Starting deep research…
                        </span>
                      ) : (
                        "Yes, this is my brand"
                      )}
                    </Button>
                    {candidateUrls(c).length === 0 && (
                      <span className="text-[11px] text-muted-foreground">
                        No source URLs to confirm against — add a website in step 1.
                      </span>
                    )}
                  </div>
                </Card>
              ))}

              {candidates.length === 0 && (
                <EmptyState
                  title="No confident matches"
                  body="We couldn't find this brand on the public web yet — no problem, the interview covers it."
                >
                  <Button variant="secondary" disabled={discovering} onClick={() => void runDiscover()}>
                    Search again
                  </Button>
                </EmptyState>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Button variant="ghost" disabled={busy} onClick={goToInterview}>
                  {busy ? "Preparing questions…" : "None of these — skip to the interview"}
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setStep(1);
                  }}
                >
                  ← Edit basics
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ------------------------------------ step 3: confirmed research */}
      {step === 3 && research === "running" && (
        <Card className="py-10 text-center">
          <div className="flex justify-center">
            <Loading label="Researching across the intelligence lanes — usually 1–2 minutes" />
          </div>
          {Object.entries(laneStats).filter(([, n]) => n > 0).length > 0 && (
            <div className="mt-2 flex flex-wrap justify-center gap-1.5">
              {Object.entries(laneStats)
                .filter(([, n]) => n > 0)
                .map(([lane, n]) => (
                  <span
                    key={lane}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-1 text-[11px] font-medium text-muted-foreground"
                  >
                    {lane} <b className="text-foreground">{n}</b>
                  </span>
                ))}
            </div>
          )}
          <p className="mt-3 text-[12px] text-muted-foreground max-w-md mx-auto">
            The Researcher is reading your site, socials, news, and more. You can keep this tab
            open — we&apos;ll move on automatically when it&apos;s done.
          </p>
        </Card>
      )}

      {step === 3 && research === "failed" && (
        <div className="flex flex-col gap-3">
          <ErrorBox
            message={researchError || "Research failed — please try again."}
            onRetry={busy ? undefined : () => void retryResearch()}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={() => void retryResearch()}>
              {busy ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner /> Restarting research…
                </span>
              ) : retryUrls.length > 0 ? (
                "Retry research"
              ) : (
                "Pick your brand again"
              )}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={goToInterview}>
              Skip to the interview
            </Button>
          </div>
        </div>
      )}

      {step === 3 && research === "succeeded" && profileFields && (
        <div className="flex flex-col gap-4">
          <ResearchReview fields={profileFields} laneStats={laneStats} failures={failures} />
          <div>
            <Button disabled={busy} onClick={goToInterview}>
              {busy ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner /> Preparing questions…
                </span>
              ) : (
                "Looks right — continue"
              )}
            </Button>
          </div>
        </div>
      )}

      {/* ------------------------------------------- step 4: interview */}
      {step === 4 && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="primary">
              {answered} answered · caps at {INTERVIEW_CAP} during onboarding
            </Badge>
            {onboardingStatus && <Badge>{onboardingStatus.replace(/_/g, " ")}</Badge>}
            {!drafts && !finished && questions !== null && (
              <span className="ml-auto">
                <Button
                  variant="secondary"
                  className="!px-3 !py-1.5 text-xs"
                  disabled={drafting || busy}
                  onClick={() => void draftAll()}
                  title="The Answerer agent drafts a researched answer for every open question — you review each one before anything is saved"
                >
                  {drafting ? (
                    <span className="inline-flex items-center gap-2">
                      <Spinner /> Drafting…
                    </span>
                  ) : (
                    "✨ Draft all with AI"
                  )}
                </Button>
              </span>
            )}
          </div>

          {draftError && <ErrorBox message={draftError} />}
          {draftNote && <p className="text-[12px] text-muted-foreground">{draftNote}</p>}

          {questions === null && <Loading label="Preparing your questions…" />}

          {qError && questions !== null && questions.length === 0 && (
            <ErrorBox message={qError} onRetry={() => void loadInterview()} />
          )}

          {drafts && <AnswerReview drafts={drafts} onDone={onReviewDone} />}

          {currentQuestion && questions && (
            <QuestionCard
              question={currentQuestion}
              counter={`Question ${qIndex + 1} of ${questions.length}`}
              busy={busy}
              error={qError}
              onAnswer={(answer) => void submitAnswer(answer)}
              onSkip={() => void submitAnswer(null)}
              onFinishLater={() => setFinished(true)}
            />
          )}

          {finished && !drafts && (
            <Card className="py-10 text-center">
              <h2 className="text-lg font-semibold">The interview is caught up</h2>
              <p className="mt-2 text-[13px] text-muted-foreground max-w-md mx-auto">
                {answered > 0
                  ? `You answered ${answered} question${answered === 1 ? "" : "s"} — every answer is permanent brand memory. `
                  : ""}
                The interview keeps going quietly in the background; you can always answer more
                from Mission Control.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                <Button onClick={() => setStep(5)}>Hear your brand voice</Button>
                <Button variant="ghost" onClick={() => void loadInterview()}>
                  Ask me more
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* --------------------------------------- step 5: brand voice */}
      {step === 5 && (
        <div className="flex flex-col gap-5">
          <VoiceReveal />
          <div className="flex flex-wrap items-center justify-center gap-3 pb-4">
            <Link href="/manager" className={BTN_LINK}>
              Go to Mission Control
            </Link>
            <Link href="/manager" className="text-sm text-muted-foreground hover:text-foreground">
              Back to home
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
