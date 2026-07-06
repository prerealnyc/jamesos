# Brand Manager OS — Product Requirements Document

**Version:** 1.0 · **Date:** 2026-07-07 · **Author:** Roy (drafted from the
2026-07-06 product-alignment meeting with James, Shahd, McGinn, Bernadette)
**Status:** Draft for Thursday regroup

---

## 1. Vision

> "It's a brand manager. It has to know it's a brand manager. It has to know
> where to look, how often to look, how to vet if it's accurate information…
> and how to produce these videos for this brand. That's the identity." — James

Brand Manager OS is an **autonomous brand manager**: software that thinks,
plans, and produces the way a six-figure human brand team does. For any brand
identity — a person, a physical asset, an institution, a politician — it:

1. **Knows who the brand is** (intake + ingested corpus + learned voice),
2. **Does the homework** (researches topics, peers, algorithms, press; vets
   what it finds),
3. **Prescribes the strategy** (how much to post, in what formats, on what
   topics, to reach stated goals),
4. **Produces the content** (posts, reels, long-form cuts, white papers,
   podcasts, lessons) in the brand's authentic voice,
5. **Learns from every human decision** (approvals, rejections, edits become
   permanent rules),
6. **Never publishes without a human gate.**

The commercial thesis: brands pay staff hundreds of thousands of dollars for
fragmented specialist work (shooting, cutting, captioning, trend-watching,
platform expertise). This product replaces the coordination layer with one
system, sold per brand seat, ultimately licensed to a distribution platform
for royalties.

## 2. Test cases & rollout narrative

| Brand | Type | Role |
|---|---|---|
| **James Prendamano** | Personal brand (real-estate authority) | Test case #1 — live today, richest corpus |
| **Spaceport America** | Public institution / physical asset | Test case #2 — demo target (governor's office, week of Jul 12). "One asset, one set of research" → daily space-exploration content |
| **Turtleback** | Physical asset (golf resort) | Test case #3 — proof it generalizes to any asset |

Sales narrative: *"Look what it did for a person, a spaceport, and a golf
course"* → sell to every golf course / asset / influencer / politician via a
platform partner.

## 3. Personas

- **Brand Operator** ("the individual that runs the program" — one per brand;
  Mike for James, a state employee for Spaceport). Uses intake, reviews the
  approval queue, follows prescriptions. NOT a video editor or strategist.
- **Brand Principal** (James; the Spaceport board). Reviews outputs, sets
  goals, records source material.
- **Platform Owner** (us). Provisions brands, monitors costs, ships
  improvements.

## 4. What exists today (baseline — do not rebuild)

The meeting demo predated a shipping wave. Current live capability:

- **Memory substrate:** multi-tenant Postgres (every table tenant-scoped,
  RLS-forced), 13k+ embedded knowledge chunks, hybrid vector+FTS retrieval,
  cite-or-refuse Ask with a second verification pass, conversational
  follow-ups.
- **Intelligence layer (native, NOT a separate platform):** document ingest
  (every format incl. audio/OCR), AI auto-filing to a naming convention,
  silos/entities, 4-tier sensitivity with NDA hard-gates, topic intelligence
  (web research → briefs filed back into memory), cross-silo synthesis,
  commitments mining, **white papers written in the brand voice**.
- **The brain:** 1,344 voice exemplars + 106 self-learned "never again" rules
  (every rejection/edit becomes a permanent guardrail) + brand kit.
- **Production:** bulk post generation (voice engine + independent voice-QA
  gate), designed image posts with quality gates (sharpness, reuse ledger,
  crop safety, text-fit), long-form cutter (transcribe → find engaging
  moments → auto-clip top-N), Content Library with cross-footage topic
  suggestions, two locked video templates, cinematic B-roll mode, speaker
  name-tags, trim, **podcast narration in the cloned voice (ElevenLabs)**.
- **The one button:** weekly thesis (voice memo) → research → white paper
  arguing the thesis → 3 posts + 2 reels + podcast episode → approval queue.
- **Approval queue:** the universal human gate; rejections feed the learning
  loop. Scheduler + publishing integrations.
- **Analytics (beta):** connected-account metrics + tracked-handle scraping +
  influencer peer list. *Known gap: doesn't yet close the loop into strategy.*

## 5. Requirements

Priorities: **P0** = required for the Spaceport demo + packaging story;
**P1** = required for "it thinks" to be credibly true; **P2** = authority/
scale phase.

### R1 — Brand identity & tenancy (P0)
1. One login = one brand. Operators authenticate into exactly one brand
   tenant; no cross-brand visibility. (Substrate exists; provisioning UX does
   not.)
2. **Brand Intake**: a templatized onboarding walkthrough capturing: who the
   brand is (person/asset/institution), mission & positioning, goals (e.g.
   "top search authority in X", "public consensus for Y"), topics & taboo
   list, audiences, platforms, competitors/peer set, assets on hand, posting
   constraints. Output = a structured `brand_profile` that seeds every
   engine (voice, ideation, strategy, research).
3. Intake must accept **bulk asset drop** (footage, docs, press links) and
   auto-build the initial corpus (transcribe/OCR/file/embed) without manual
   metadata.
4. A brand must be provisionable end-to-end in **< 1 day of operator effort**.

### R2 — The strategy engine: "it has to think" (P1)
1. **Prescriptions**: on a schedule (weekly default) the system produces a
   concrete, quantified content plan per platform: volumes by format ("5
   statics + 10 videos + 5 reels / week"), topic mix vs. the brand's pillars
   ("underweight on mindset"), specific suggested pieces, and growth actions
   (next-tier podcast targets, promote-spend candidates, collab suggestions).
2. Prescriptions must be **grounded in three inputs**: (a) platform-algorithm
   research refreshed on a cadence and re-checked when change is detected,
   (b) peer-set benchmarking (what accounts at the target tier actually do),
   (c) own analytics (what performed).
3. **One-click execution**: accepting a prescription hands it to autopilot;
   the queue fills accordingly. Partial acceptance allowed.
4. The engine states **why** for every prescription line (evidence: algorithm
   note, peer stat, or analytics datum) — no oracle claims.
5. Algorithm knowledge is stored, versioned, and surfaced ("algorithm brief"
   per platform with last-refreshed date); operators are notified on change.

### R3 — Research & vetting (P1)
1. Topic research runs inside the brand tenant (exists — topic intelligence).
2. **Vetting rule**: information not directly fed by the brand must carry a
   source + confidence; low-confidence claims never enter published copy
   (extends the existing cite-or-refuse machinery to generation).
3. Asset-class research packs: for a physical asset, "do the same homework"
   — ingest its media, statistics, history; daily topical sweep (e.g.
   "most compelling space-exploration stories today") producing N content
   candidates/day (Spaceport: 5–10).

### R4 — Press monitor (P1)
1. Scheduled scrape of designated press pages (Pat's press page) + web
   mention search for the brand and its named holdings.
2. LLM triage: **content-worthy?** (credibility signals — board seat,
   featured guest, award → yes; passing mention → no) with reasons.
3. Worthy items generate suggested content into the approval queue and are
   filed into memory as credibility evidence for the strategy engine
   (trust-signal ladder: Meltzer → next tier).

### R5 — Content production (P0 — largely exists)
1. All formats from one brief: posts, designed images, reels (both locked
   templates), long-form cuts, white papers, podcasts. (Exists.)
2. **Authentic-voice rewrite**: drop any external draft (e.g. a Perplexity
   white paper) → rewritten so "it sounds like James wrote it, not AI" —
   grounded in the voice corpus, passing the voice-QA gate. (Engine exists;
   needs a first-class "rewrite this" door.)
3. **Academy lessons**: ingest lesson transcripts → segment into lessons →
   generate lesson videos where the "gold" is narrated over auto-generated
   visuals (calculations like NOI/IRR rendered as graphics), with shot
   intro/outro slots.
4. Everything lands in the approval queue; nothing auto-publishes.

### R6 — Learning loop (P0 — exists; protect it)
1. Every approval → voice exemplar; every rejection/edit → permanent
   guardrail; visible on the Brand page ("the brain behind the voice").
2. The loop must remain per-tenant (one brand's lessons never leak to
   another).

### R7 — Analytics maturity (P1)
1. Per-post performance capture across connected platforms.
2. Attribution back to content attributes (format, topic, hook style,
   template) so the strategy engine can learn what works **for this brand**.
3. Brand-identity inference: analytics + corpus → a living model of "what
   this brand is best at" feeding prescriptions.

### R8 — Authority & distribution (P2)
1. **SEO/AEO/GEO publishing**: transcripts and white papers published to a
   crawlable surface (blog/Substack) with schema markup, so the brand
   surfaces in generative search.
2. Wikipedia-readiness workflow: notability evidence assembly (press ledger
   feeds this); page drafting for human submission.
3. Prediction-ledger feature: timestamped past claims (e.g. James's market
   predictions) packaged as authority content.

### R9 — Packaging & monetization (P0 decision, P1 build)
1. Per-brand **credit metering**: every generation records its provider cost;
   tenants have caps; usage visible to the operator.
2. Pricing model (recommendation, decision open): subscription tiers with
   included content quotas + metered overage credits. Avoid raw pass-through
   (exposes vendor pricing, no margin) and upfront-only (no recurring value
   for an acquirer).
3. Outward-facing demo mode: a clean tenant showcasing the product without
   exposing James's data.

## 6. Non-functional requirements

- **Isolation:** tenant RLS everywhere (exists); per-tenant storage paths;
  no shared learning across brands.
- **Human gate:** no content leaves the system without explicit approval.
- **Honesty:** cite-or-refuse retained for Ask & vetting; sensitivity/NDA
  tiers enforced in every generated artifact (exists).
- **Cost ceilings:** per-tenant daily/monthly spend caps with graceful
  degradation (queue the request, notify) rather than silent failure.
- **Ops:** deploys verified (typecheck + deployment status + live probe);
  renders never killed by deploys; migrations idempotent.

## 7. Success metrics

- **Demo (week of Jul 12):** Spaceport tenant live: intake done, corpus
  ingested, 5+ daily content suggestions generated, 3+ approved pieces
  produced end-to-end in its own visual identity.
- **James (30/60/90 days):** prescription adherence possible (queue always
  full to prescription), % of prescription auto-produced, follower/engagement
  growth trend, zero internal-label/off-voice rejections recurring.
- **Product:** new-brand provisioning < 1 day; cost per generated piece
  tracked per tenant; 3 live tenants (James, Spaceport, Turtleback).

## 8. Open questions & constraints (from the meeting)

1. **Monetization model** — recommendation in R9; needs James's sign-off.
2. **Licensing/third-party costs** — Claude/OpenAI/ElevenLabs/HeyGen/
   Creatomate/Voyage/Cohere per-tenant costs must be measured (R9.1) before
   pricing is set.
3. **Analytics maturity** — R7 is the long pole for credible prescriptions;
   until then prescriptions lean on algorithm + peer research.
4. **Team growth** — James offered resources; the roadmap flags where a
   second engineer or a designer changes timelines.
5. **Publishing surface** for SEO/AEO (own blog vs Substack vs both) — needs
   a decision before R8.1.
6. **Politician vertical** (district comment-scanning) — deliberately
   deferred; raises platform-ToS and ethics review requirements.

## 9. Out of scope (for now)

Auto-publishing without approval; selling one-on-one to end customers
(platform/royalty path chosen); the politician comment-scanning vertical;
paid-ads execution (promote *suggestions* are in scope, ad buying is not).
