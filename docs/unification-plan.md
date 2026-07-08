# Unification Plan — Brand Manager 2.0 → james-os ("Brand Manager")

**Decision proposed:** merge, no API bridge. Port bm2.0's intelligence + execution
natively into james-os, exactly the way PreReal Intelligence was absorbed
(see james-os `docs/INTELLIGENCE_PARITY_PLAN.md` — the precedent this plan copies).
One codebase, one Postgres, one login, one approval queue, one brain.

**Direction of the merge:** bm2.0 is the donor, james-os is the substrate. Not the
other way around, because james-os already has everything bm2.0 lacks and cannot
cheaply grow: multi-tenant Postgres + FORCE RLS, auth/sessions, Supabase storage,
credit metering, encryption, deployment, and the media production stack (video/
image/TTS/podcast) with 21k+ events of real learned data. bm2.0 brings the parts
james-os has only as dormant 0-row stubs: the LIVE intelligence layer, onboarding,
voice harvesting, text hands + actual publishing, and the measurement→strategy loop.

**Naming:** the merged product is "Brand Manager" (james-os repo). bm2.0 feature-
freezes on merge start (bug fixes only) and retires after the data migration.

---

## Component mapping — every bm2.0 piece and its james-os landing zone

Legend: **PORT** = rewrite natively on james-os's asyncpg/RLS pattern ·
**FILL** = lands in an existing dormant james-os table/module ·
**REPLACE** = bm2.0's live version supersedes a james-os stub ·
**KEEP-THEIRS** = james-os already has the stronger version; bm2.0's is dropped.

### A. Onboarding (the front door)
| bm2.0 piece | james-os landing | How |
|---|---|---|
| Two-agent onboarding (interviewer + answerer, "is this your brand?", batch auto-answer + review) | `intake_agent.py` + `brand_questions` (0 rows) | REPLACE — ours is live/tested; port onto their tables |
| Researcher (D11 parallel lanes: web/news/wiki/youtube/places/reddit/deep) | `brand_research.py` / `research.py` | REPLACE (theirs is a single Perplexity pass; ours is multi-lane with provenance rules) |
| ProfileField append-only envelope (computed confidence, citations, contradiction states) | NEW migration `profile_fields` (their `brand_profiles` keeps the flat snapshot as a VIEW/projection) | PORT — the envelope is bm2.0's core IP; don't flatten it |
| Voice Harvester (own YouTube/podcast → transcripts → cited exemplars + voice profile) | alongside `voice_ingest.py` (their manual upload door) | PORT — complementary: theirs uploads, ours auto-pulls; both feed one corpus |
| PostProxy connect flow (white-label group binding) | `postproxy.py` + `connections.py` | MERGE — they have read/analytics; we add connect-URL flow + PUBLISH (theirs is read-only) |

### B. Intelligence (the eyes + the brain)
| bm2.0 piece | james-os landing | How |
|---|---|---|
| 5 eyes (content radar, trends, press, questions/AEO, appearances) | `content_suggestions` (0 rows) + their scheduler registry | FILL — each eye becomes a registered job; findings land as suggestions/actions |
| Algorithm briefs (cadence-refreshed, cited) | `platform_playbooks` (1 row) | FILL — same concept, their table |
| Peer discover→approve→track + snapshots | `research_roster` + `peer_snapshots` (0 rows) | FILL — keep the human-approval gate semantics |
| Goal agent / growth / daily plan / weekly strategist | `strategy.py` + `prescriptions` (0 rows) | REPLACE — ours is live and grounded in baselines+algorithm+what-worked; keep their prescription evidence[] format |
| Goal-miss replanning, promote-spend scan | scheduler steps | PORT (no james-os equivalent) |
| Daily cycle (measure→goal-check→promote→algorithm→eyes→autopilot→digest) | their `scheduled_jobs` table-driven scheduler | PORT — register as jobs; kill their 5 dormant intelligence jobs to avoid a second brain |

### C. Execution + learning
| bm2.0 piece | james-os landing | How |
|---|---|---|
| Hands (blog/email/social drafting in strict brand voice) + rewrite door | their content engine + voice-QA gate | MERGE — their voice engine is stronger (1,344 exemplars, 106 rules); our formats (blog/email) and rewrite door are new |
| WorkOrder→Artifact→ApprovalEvent + D5 state machine (…→published→measured) | their `actions` queue (pending→approved→executed) | MERGE — extend their status vocabulary with published/measured; their queue UI survives |
| Publish providers (blog webhook, Resend email, PostProxy social) | their dormant `outbox.execute_action` | FILL — our publish code IS the missing outbox executor |
| Reviewer 4-leg gate (lint / guardrails / voice / fact-check) | their voice-QA gate | MERGE — they have voice-QA; add our lint + learned-guardrails + fact-check legs |
| Learning loop (auto-measure, what_worked, approve→exemplar, reject→guardrail) | their `learning.py` + `feedback_changes` | MERGE — theirs learns from decisions already; ours adds performance→strategy (their PRD's known gap R7) |
| Brand memory (exemplars/insights) | their `events` substrate (embedded, RAG-ready) | PORT chunks into events — they get retrievable via Ask for free |
| Video/image production | their pipelines | KEEP-THEIRS (this was always the plan) |

### D. Frontend (the unified design)
| bm2.0 piece | james-os landing | How |
|---|---|---|
| Onboarding flow (5 steps incl. Brand Voice step) | their `web/app/intake` + `onboarding-checklist` | REPLACE their intake with our flow, restyled to the unified design |
| Dashboard sections (Today/North Star/Voice/Radar/Competitors/The Work/Follow-ups) + WCAG nav | new top-level "Manager" area in their shell | PORT — their web app keeps its production surfaces (create/editor/library/queue); ours becomes the strategy home |
| Design language | ONE system, decided up front | **DECIDED (Roy, 2026-07-08): bm2.0's design language wins.** The merged app adopts bm2.0's token set (stone/paper palette, typography, calm cards, pill section-nav, WCAG patterns) across ALL surfaces — james-os's production pages get restyled to it during P5. |

### E. Infrastructure decisions
- **Data layer:** james-os is raw asyncpg + SQL migrations; bm2.0 is SQLAlchemy.
  Port bm2.0 services to THEIR pattern (native, per their "no bolt-on" rule).
  This is the bulk of the mechanical work.
- **Providers:** bm2.0's D8 provider layer (mock/live per key) ports as a james-os
  module; it's how the merged product stays demo-able without keys.
- **Auth/tenancy:** solved by the merge — james-os's login/tenants/RLS covers the
  front door. bm2.0 never builds auth. brand_id → tenant_id mapping happens once
  at data migration.
- **Intelligence ownership:** bm2.0's brain replaces james-os's dormant jobs.
  One scheduler, one set of eyes, one strategist. No duplicate spend.

---

## Phases (each independently shippable; mirrors the PreReal parity-plan style)

- **P1 — Substrate + schema (≈1 wk):** new migrations (profile_fields, action_items,
  work-order status extensions, algorithm briefs into playbooks); provider layer
  module; feature-flag `manager_v2` per tenant.
- **P2 — Eyes + heartbeat (≈1 wk):** port the 5 eyes + algorithm agent + daily cycle
  onto their scheduler; retire their dormant intelligence jobs; suggestions flow
  into their existing queue UI.
- **P3 — Brain + learning (≈1 wk):** goal/growth/daily/strategist + goal-miss replan;
  auto-measure + what_worked + promote scan; approve/reject learning merged into
  their learning.py.
- **P4 — Onboarding + voice (≈1 wk):** intake flow + researcher lanes + voice
  harvester; brand-profile projection; PostProxy publish path (their outbox).
- **P5 — Unified frontend (≈1–1.5 wk):** the Manager area (our dashboard sections)
  in their shell; onboarding UI; ONE design token set; WCAG nav patterns.
- **P6 — Data migration + retirement (≈2–3 d):** *(script DONE 2026-07-09 —
  scripts/migrate_bm2.py, dry-run-first, single-tx, idempotent, additive-only;
  rehearsed + adversarially verified on the local cluster: 839 profile fields,
  832 memory chunks, 127 action items, 96 questions, all counts 1:1. The
  PRODUCTION run awaits Roy: Supabase DATABASE_URL + James's tenant uuid for
  --map, dry-run read-through, then --execute in a quiet window.)* James / Spaceport / Turtleback
  brands migrated (profile fields, peers, actions, work orders, voice exemplars →
  events); bm2.0 archived. James's existing james-os tenant is ENRICHED, never
  overwritten (his 1,344 exemplars + 106 rules are the moat — additive only).

**Total: ~5–6 weeks single-threaded; ~3–4 with parallel agents on P2/P3/P4.**

## Zero-feature-loss rule

No feature from EITHER system may be dropped silently. `docs/feature-ledger.md`
(generated by exhaustive sweeps of both codebases) is the merge contract: every
feature has its origin (repo + source file, so it can be backtracked), its
disposition (KEEP / PORT / FILL / REPLACE / MERGE), its landing zone, and its
phase. A phase is done only when its ledger rows are checked off; anything
proposed for DROP requires Roy's explicit sign-off first.

## Risks & mitigations
- **Two data models colliding** → the append-only ProfileField envelope is
  non-negotiable (it's the provenance/confidence story); everything else adapts
  to james-os shapes.
- **James's live tenant** → all merge writes behind the `manager_v2` flag; his
  current production flows untouched until cutover.
- **Voice corpus conflict** → one corpus, tagged by origin (harvested / uploaded /
  approved); the reviewer reads all three.
- **SQLite→Postgres surprises** → bm2.0's tests port with the code and run against
  a local Docker Postgres (james-os already has this pattern).
