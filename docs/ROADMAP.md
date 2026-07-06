# Brand Manager OS — Development Roadmap

**Version:** 1.0 · **Date:** 2026-07-07 · Companion to [PRD.md](PRD.md) and
[ARCHITECTURE.md](ARCHITECTURE.md). Requirement IDs (R1–R9) refer to the PRD.

Guiding constraint: **Spaceport demo the week of Jul 12** (governor's
office). Everything before that date serves the demo; everything after
serves "it thinks" and packaging.

---

## M0 — Baseline (DONE, as of Jul 7)

Shipped and verified in production; the demo should lead with these:
- Intelligence layer native in BM + James's full corpus migrated (13k chunks)
- ⚡ Full-week button: thesis → research → white paper (his voice) → 3 posts
  + 2 reels → podcast (cloned voice) → approval queue
- Conversational Ask; brain card (1,344 exemplars / 106 learned rules)
- Content Library topic suggestions incl. cross-footage stitched edits
- All 5 feedback-fix clusters from the Jul-6 review (labels, image gates,
  hook timing, b-roll dedupe, split blanks)
- Per-page "How it works" tutorials

## M1 — Spaceport demo ready (target: Jul 11) — R1, R3.3

**Goal:** walk into the governor's-office meeting with a second brand living
in the product, provably distinct from James.

| # | Deliverable | Notes |
|---|---|---|
| 1.1 | Tenant provisioning: create-brand flow (admin) + operator login scoped to the brand | substrate exists; needs provisioning script/UI + invite |
| 1.2 | **Brand Intake form** → `brand_profile` (identity, goals, topics, peers, platforms, constraints) | seeds voice engine + ideation + Ask context |
| 1.3 | Intake bulk-drop: assets/docs/press links → auto-ingest into the tenant corpus | reuses knowledge ZIP/upload doors |
| 1.4 | **Daily asset research job**: "read about space every day" → 5–10 content candidates/day into a Suggestions rail | reuses topic-intelligence + autopilot ideation, driven by brand_profile |
| 1.5 | Spaceport visual identity: brand kit (logo/colors/sign-off), templates render in its identity, no James bleed-through | brand kit exists; verify template theming |
| 1.6 | Outward demo script + clean walkthrough (no James data on screen) | with Shahd |

**Exit test:** provision Spaceport in <1 day; generate day-one suggestions;
approve 3 pieces end-to-end in Spaceport's identity.

## M2 — The strategy engine v1: "it thinks" (target: Jul 25) — R2

| # | Deliverable | Notes |
|---|---|---|
| 2.1 | `platform_playbooks`: per-platform algorithm briefs, research-refreshed on cadence + change detection, versioned, surfaced in UI | Perplexity/web-research providers exist |
| 2.2 | Peer-set benchmarking job: tracked peer accounts → cadence/format/topic stats | influencer tracking + Xpoz exist; add aggregation |
| 2.3 | **Weekly Prescription**: quantified plan (volumes per format/platform, topic-mix gaps vs pillars, 10+ suggested pieces, growth actions incl. podcast ladder) with per-line evidence | the flagship feature |
| 2.4 | One-click "Accept plan" → autopilot fills the queue to prescription; partial accept | autopilot batch exists |
| 2.5 | Prescription review UI (the operator's Monday morning page) | |

**Exit test:** James's tenant receives a Monday prescription whose every
line cites evidence; accepting it queues the week.

## M3 — Inputs that feed the brain (target: Aug 8) — R4, R5.2, R5.3

| # | Deliverable | Notes |
|---|---|---|
| 3.1 | **Press monitor**: scheduled scrape (Pat's page + mention search) → content-worthy triage → queue suggestions + credibility ledger | cron + scrape provider |
| 3.2 | **Authentic-voice rewrite door**: paste/upload any draft → rewritten in brand voice through voice-QA | engine exists; add the door |
| 3.3 | **Academy lessons pipeline v1**: transcript → lesson segmentation → narrated lesson video with auto visuals (calculations as graphics) + intro/outro slots | biggest new render mode |
| 3.4 | Trust-signal ladder: press/appearance events ranked; "next tier" targets feed prescriptions (Meltzer → Bett-David) | uses 3.1 ledger |

## M4 — Analytics maturity + metering (target: Aug 22) — R7, R9.1

| # | Deliverable | Notes |
|---|---|---|
| 4.1 | Per-post performance capture across connected accounts (scheduled pulls) | analytics beta exists |
| 4.2 | Content-attribute attribution (format/topic/hook/template → performance) | payload already records attributes |
| 4.3 | Performance feedback into prescriptions ("your carousels outperform 3:1") | closes the R2 loop |
| 4.4 | **Credit metering**: per-generation provider-cost records, per-tenant caps + usage dashboard | prereq for pricing |
| 4.5 | Promote-spend suggestions (top performers flagged with budget rationale) | suggestions only |

## M5 — Packaging & authority (target: Sep 15) — R8, R9

| # | Deliverable | Notes |
|---|---|---|
| 5.1 | Pricing v1 implemented (tiers + included quotas + overage credits) after James signs off on model | decision gate |
| 5.2 | Self-serve-ish onboarding: intake templates per vertical (person / asset / institution) | "templatize the inputs" |
| 5.3 | SEO/AEO publishing: crawlable transcript/white-paper surface with schema markup | needs surface decision |
| 5.4 | Wikipedia-readiness workflow (notability dossier from the press ledger) | human submits |
| 5.5 | Prediction-ledger authority feature | James's prediction videos |
| 5.6 | Turtleback tenant onboarded via the templated flow (proof of generalization) | third test case |

## Dependencies & critical path

```
M1.2 brand_profile ──► M1.4 daily research ──► M2.3 prescriptions ──► M4.3 perf loop
M1.1 provisioning ──► M5.2 templated onboarding ──► 5.6 Turtleback
M3.1 press ledger ──► M3.4 trust ladder ──► M2.3 growth actions (enriched)
M4.4 metering ──► M5.1 pricing
```

## Resourcing flags (James asked "tell me what you need")

- **Now:** current pace sustains M1–M2 solo (with AI-assisted development).
- **M3.3 (lessons render mode)** and **M4.1–4.3 (analytics)** run in
  parallel only with a **second engineer**; otherwise M4 slips ~3 weeks.
- **M5.3 SEO surface** benefits from a **designer/content ops** hire for the
  publishing templates.
- Budget note: every prescription/research job spends provider credits;
  M4.4 metering should land before Turtleback onboards.

## Standing risks

1. **Analytics platform APIs** (Meta etc.) rate-limit and change — R7 dates
   are the least certain.
2. **AI-video aesthetics** for brand-critical footage (James flagged in the
   demo) — mitigated by real-footage-first modes; keep cinematic mode
   optional per render.
3. **Algorithm research quality** — prescriptions must always show evidence
   (R2.4) so weak research is visible, not silently wrong.
4. **Scope gravity** — the vision expands every meeting; this roadmap is the
   contract. New asks slot into M-numbers, not into "this week."
