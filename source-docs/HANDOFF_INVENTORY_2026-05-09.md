# JP Brand Manager — Complete Inventory & Handoff Document

**Generated:** 2026-05-09 11:42 AM ET
**Purpose:** Complete inventory of every artifact built for the JP Brand Manager project, organized by portability to a new architecture (LangGraph/CrewAI/LangSmith stack per Grok recommendation).

**Authorization context:** James, 2026-05-09 11:37 AM ET — "inventory all that has been done." Driven by today's recognition that the Perplexity Computer substrate is mismatched to a 24/7 production brand-manager use case, and the right path is migrating to a stateful-graph orchestrator while preserving the high-value artifacts already built.

---

## TL;DR — what to take, what to leave

| Category | Take | Leave |
|---|---|---|
| Voice profile / training corpora | ✅ ALL | — |
| Founder's thesis / strategy docs | ✅ ALL | — |
| 75-protocol registry | ✅ as system-prompt + rules-engine seed | retire `enforcement/` Python wrappers |
| Voice fidelity scoring code | ✅ portable Python | — |
| Peer creators / market research / brand voice DB | ✅ data | — |
| API integrations (xpoz, postproxy, Meta, OpenAI, etc.) | ✅ credentials, plumbing patterns | — |
| Dashboard (Vite/Express/SQLite) | ✅ as a viewer that points at new backend | — |
| Enforcement Python modules | logic only | substrate-bound wiring assumptions |
| daily_report.py monolith | message templates only | architecture |

---

## A. Knowledge & Voice Layer (highest portability)

These ARE the brand manager's memory. Copy verbatim into the new system's vector store + structured DB.

| File | Size | Portable as |
|---|---|---|
| `jp_brand_voice_profile.json` | 95KB | RAG profile, scorer reference |
| `jp_brand_voice_profile.md` | 37KB | Human-readable voice spec |
| `prereal_transcripts.docx` | 1.2MB | YouTube/Academy verbatim corpus → vector embedding |
| `academy_training_text.txt` | 727KB | Academy modules → vector store |
| `podcast_full_context.txt` | 6.7MB | Podcast speaker-tagged transcripts → primary RAG source |
| `neville_goddard_first_person.txt` | 16KB | Voice declarations (first-person POV) |
| `Predictions.docx` | 1.7MB | Predictions archive |
| `VOICE_FIDELITY_SCORING_SPEC.md` | 4KB | Scoring methodology |
| `VOICE_LEARNING_AUDIT_TRAIL.md` | 4KB | Audit trail spec |

**~10MB of distilled voice and content.** This took weeks to build and is irreplaceable.

---

## B. Founder's Thesis & Strategy (portable verbatim)

| File | Notes |
|---|---|
| `MAX_OPERATING_PROTOCOL.md` (392KB) | The full operating protocol — read as system-prompt seed, not enforcement code |
| `JP_Brand_Manager_Roadmap.md` | Multi-phase roadmap |
| `JP_Search_Categories_and_Health_Check.md` | Search categories + health check definitions |

**Note:** No file currently named `FOUNDERS_THESIS.md` — recommendation is to extract James's thesis from `MAX_OPERATING_PROTOCOL.md` + `JP_Brand_Manager_Roadmap.md` into a clean 1-2 page founders thesis document during handoff prep.

---

## C. Protocol Registry — the rules engine seed

`enforcement/registry/protocols.json` — 75 protocols, status:
- **30 code-enforced** (with module pointers)
- **16 monitor-only** (judgment-bound, with rationale)
- **29 retired** (DEAD or consolidated)

**Portable as:** structured JSON input to the new system's rules engine + system prompt augmentation. Each protocol has number, title, body, trigger, required_action, failure_condition, priority, owner, applies_to.

**NOT portable as:** the Python enforcement modules. Those are libraries that need a caller, and the caller didn't exist in this substrate. In LangGraph, the "caller" is the graph itself — every node has pre/post conditions and the graph enforces them automatically.

---

## D. Voice Fidelity Scoring (high-value portable code)

These work and are battle-tested. Lift them as-is.

| File | Lines | Purpose |
|---|---|---|
| `jp-brand-manager/server/voice_fidelity.py` | 789 | Scores any text against the voice profile (0-100) |
| `jp-brand-manager/server/voice_fidelity_trend.py` | 219 | 7-day rolling fidelity trend per platform |
| `jp-brand-manager/server/voice_learning_audit.py` | 273 | Generates daily audit trail |
| `jp-brand-manager/server/positive_signal_connector.py` | 268 | Ingests captured social as positive voice signal |
| `jp-brand-manager/server/tests/test_voice_fidelity.py` | 430 | 25/25 passing |

**Use case in new system:** the voice fidelity scorer is the **hard gate** before any content publishes. Below threshold X = retry. This is the kind of deterministic check LangGraph/CrewAI enforces structurally.

---

## E. Operational Data (portable rows)

`jp-brand-manager/database.sqlite` (868KB) — extract these tables:

| Table | Rows | Use in new system |
|---|---|---|
| `peer_creators` | 8 | Peer comparison cohort seed |
| `market_research` | 6 | Trend / gap research seed |
| `brand_voice` | 5 | Voice attributes (already in profile JSON; cross-reference) |
| `social_content` | 95 | Historical post baseline |
| `publish_queue` | 43 | Pending content (review before migrating — may be stale) |
| `element_review` | 162 | Review feedback log — feeds learning loop |
| `jp_clip_library` | 5 | Video clip metadata |
| `image_library` | 4 | Image metadata |
| `scan_logs` | 36 | Scan history |

---

## F. API Integrations (credentials + patterns)

All wired and working today (with one critical caveat — see "open issues" section).

| Service | Purpose | Status |
|---|---|---|
| `xpoz` | Read engagement data (IG/TT/X/Reddit only) | **WIRED but possibly throttled — see open issues** |
| `postproxy` | Publish to TT/FB/LinkedIn/YT/Threads | WIRED, 5 platforms active |
| `twitter` (tweepy direct) | Twitter publish | WIRED |
| `instagram` (Meta Graph direct) | IG publish | WIRED |
| `meta` / `meta_credentials_path` | Meta Graph (Facebook + Threads + IG insights) | WIRED — read scope needs Meta review |
| `anthropic` | Claude (LLM) | WIRED |
| `openai` | GPT (LLM + Whisper + Sora) | WIRED |
| `elevenlabs` | Voice synthesis | WIRED |
| `heygen` | Avatar video | WIRED |
| `descript` | Audio/video editing | WIRED |
| `runway` | Video generation | WIRED |
| `minimax` | Video generation | WIRED |
| `google_drive_service_account` | Drive R/W | WIRED |

**Migration note:** all of these become standard API calls in any orchestrator. Move credentials to env vars / secrets manager. The patterns for each are documented in the existing daily_report.py and jp-brand-manager/server/ code.

---

## G. Dashboard — re-pointable

`jp-brand-manager/` is a Vite + Express + SQLite + React app. Pages:
- `jp-live.tsx` — main dashboard (KPIs, content feed, peer comparison)
- `approval-queue.tsx` — content approval gate
- `design-studio.tsx` — content design
- `image-library.tsx`, `jp-clip-library.tsx` — asset libraries
- `market-research.tsx` — trends + gaps
- `pipeline.tsx` — content pipeline
- `social-companion.tsx` — engagement responder
- `voice-amendments.tsx` — voice profile amendment workflow

**Portability:** the frontend is React/Tailwind/shadcn — drop-in to any backend. Keep the UI, swap the backend to LangGraph-orchestrated endpoints.

**Known dashboard issues today:**
- KPI cards are lifetime-cumulative totals, not rolling-window — needs redesign
- Sidebar says "8 platforms tracked" (hardcoded incorrect)
- Older bug: `social_content` ordered by scrapedAt not publishedAt — fixed today

---

## H. Enforcement Modules Built Today (logic portable, wiring not)

Built today during attempted "complete fix":

| Module | Lines | Logic value |
|---|---|---|
| `enforcement/action_gates.py` | 22KB | 10 deterministic action gates — patterns translate directly to LangGraph node pre-conditions |
| `enforcement/audit_watchdogs.py` | 35KB | 12 post-hoc audits — translate to LangGraph supervisor checks |
| `enforcement/p46_credential_scanner.py` | 11KB | Pre-send credential scan regex |
| `enforcement/p47_corpus_signoff.py` | 10KB | Voice corpus signoff gate |
| `enforcement/p71_security_audit.py` | 16KB | File-mode + plaintext-token scan |
| `enforcement/validate_registry.py` | 11KB | Registry validator (P75 enforcement) |
| `enforcement/qa/mechanism_6/` | 18KB+10KB+23KB | Response-text verifier + structural extractors + test set |
| `operational_readiness.py` | 30KB | System self-awareness module |
| `daily_report.py` | 83KB | Daily report generator (refactor candidate) |

**Honest assessment:** Each of these is a **library that needed a caller**. In Perplexity Computer, the caller was supposed to be me, by discipline. That didn't work. In LangGraph, the caller is the graph itself — every node fires its pre-conditions automatically. So the LOGIC of these modules is high-value (regex patterns, gate conditions, scoring rubrics), but the wiring assumption is wrong substrate. Port the logic, throw away the wiring.

---

## I. Chat Backups (BROKEN — needs rebuild)

Current state:
- `chat_backups/HISTORICAL_BACKFILL_2026-05-08/` — 6 verbatim conversations + 40 summaries from 2026-03-30 to 2026-05-08 (one-time backfill)
- `chat_backups/2026-05-08_session_521e8d85_live_snapshot.md` — yesterday's snapshot
- `chat_backups/SESSION_SNAPSHOTS_2026-05-09/session_521e8d85_partial_2026-05-09T154143Z.md` — today's partial (just written)

**Critical gap:** the May-8 design said Max writes the conversation at session-end, "Honest constraint: this depends on Max remembering to run the capture each session. There is no platform-level auto-walk hook." Max didn't run it. Same failure pattern as Mechanism 6.

**Real fix:** in any orchestrator that's not the Perplexity chat substrate, conversation capture is automatic — the orchestrator IS the conversation runtime, so it logs everything by definition. This goes away in the new architecture.

---

## J. Scheduled Tasks (running)

- **Cron 1b1990bf** — Daily report at 12:00 UTC (8 AM ET). Runs `python3 daily_report.py`. Sends email to recipients.
- **Cron 4f7522a8** — Weekly scan + system audit, Mondays 11:00 UTC.

Both are tied to this Perplexity session's cron infrastructure. **They migrate as standard cron jobs in the new deployment** (Vercel Cron, Modal scheduled functions, GCP Cloud Scheduler, etc.).

---

## K. HeyGen / Video / Audio Assets (in Drive)

Referenced in the shared assets list (verify in your Drive `JP Brand Manager` folder):
- `heygen_avatar_4_james`, `heygen_voice_V4_james`
- `heygen_jp_first_test_generation`, `heygen_studio_mic_98c34473_keep`
- Multiple Peter Gambino avatar versions
- Voice clones V1-V4

**These live in Google Drive, not workspace** — they survive any sandbox migration.

---

## OPEN ISSUES (must address before / during handoff)

### CRITICAL — credential/key state

1. **xpoz API key** — same key from before May 7 chat-paste incident. James emailed `hello@xpoz.ai` 2026-05-07 10:33 AM ET asking for support-side revocation. **Status of that ticket: unknown.** The key still works for auth but xpoz may have throttled scope (IG view counts return 0 for new posts despite working historically). **Action:** check xpoz support inbox; rotate properly via OAuth re-auth path.

2. **xpoz key plaintext exposure** — `jp-brand-manager/server/fetch_social.py:11` hardcodes the key. Pre-existing P71 violation. Migrate to env var / secrets manager during handoff.

3. **P71 audit found 21 findings** — plaintext tokens in `daily_report_config.json`, 2 files in `secrets/` at 0644 instead of 0600. Address during migration.

### HIGH — data flow

4. **IG view counts go to zero for posts after some date** — xpoz API regression OR field-name change OR account-specific gap. Older DB rows have view counts; newer ones don't. Real fix is reading view counts via Meta Graph API directly (Peter has the publishing scope; needs `pages_read_engagement` added).

5. **6 watchdog FAILs from today's audit:** P14, P4, P5, P56, P65, P66 — all real behavior-vs-protocol gaps that surfaced when audit_watchdogs.py started running. Each needs human action; none auto-fix.

### MEDIUM — design

6. **Dashboard KPI cards show lifetime cumulative totals**, not rolling windows. Headline numbers don't move when you'd expect them to. Redesign as part of dashboard re-point.

7. **Read-side data missing for facebook/linkedin/youtube/threads** — publish-connected (postproxy), not read-ingested (no compatible API). YouTube Data API v3 is the cheapest unblock (~2 hrs once key obtained).

---

## Recommended next step (strongly held)

1. **Hand this document + the artifacts to a developer** standing up the LangGraph + CrewAI + LangSmith stack per Grok's recommendation, OR engage me in **architect-only mode** drafting the spec and node-level design for that stack (no more Python in this sandbox claiming to enforce things it can't).

2. **In parallel:** James + Peter resolve the xpoz key situation (email reply check + proper rotation via OAuth).

3. **Three-week target** for the new stack to be running with this knowledge layer ingested, voice fidelity scoring as a hard gate, peer comparison + content holes feeding daily, approval queue on rails, and the daily/weekly reports flowing.

The protocols, voice profile, and content corpus are 90% of the value of what was built. The substrate beneath them is what needs replacing.
