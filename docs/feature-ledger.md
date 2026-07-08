# Feature Ledger — the merge contract (zero feature loss)

Generated 2026-07-08 by an exhaustive 10-agent sweep of BOTH codebases.
**431 features.** Every row: origin repo + exact source file (the backtrack path),
disposition, landing zone in the merged product (james-os repo), and phase.
A phase is done only when its rows are checked off. **No silent drops** — any DROP needs Roy's sign-off.

| Disposition | Count | Meaning |
|---|---|---|
| KEEP | 208 | james-os already has it; survives as-is (restyled in P5 where frontend) |
| PORT | 92 | rewritten natively onto james-os's asyncpg/RLS pattern |
| REPLACE | 56 | bm2.0's live version supersedes a james-os dormant stub |
| MERGE | 47 | capabilities folded into a stronger james-os twin — nothing lost |
| FILL | 25 | lands in a pre-shaped, empty james-os table/module |
| NEEDS-DECISION | 3 | Roy decides (listed first below) |

## ✔ Decisions resolved (Roy, 2026-07-08)

All 431 rows now have a final disposition — zero open items.

| Feature | Origin | Source | Decision |
|---|---|---|---|
| Persisted topic suggestions with keep/reject curation | james-os | `src/james_os/topic_suggestions.py` | **KEEP separate** — stays as the composer-side idea scratchpad, independent of the intelligence rail (Roy's call) |
| Iris redesign preview dashboard | james-os | `web/app/preview/page.tsx` | **REPURPOSE (P5)** — the Iris *design* is superseded by bm2.0's design language, but the preview page becomes the rollout vehicle for the NEW design: preview the bm2.0-token theme per page before cutover |
| ThemeSwitcher (Classic ⇄ Iris preview) | james-os | `web/components/theme-switcher.tsx` | **REPURPOSE (P5)** — becomes Classic ⇄ New-Design during the migration so every restyled page can be verified against the old one; retired only AFTER full cutover, with Roy's sign-off at that point |

## P1 — Substrate & schema — 174 rows


### bm2.0 · execution

- [ ] **Action lifecycle (status/notes/snooze/delete)** (PORT) — `backend/app/services/actions.py` → action_items module (statuses, snooze_until, timestamped update trail)
- [ ] **ActionItem upsert with dedupe keys** (PORT) — `backend/app/services/actions.py` → new action_items table/module (P1 migration) with dedupe_key upsert; dismissed/done never resurrected
- [ ] **D5 work-order state machine (single implementation)** (MERGE) — `backend/app/services/state_machine.py` → actions queue: status vocabulary extended with published/measured + validated edge sets (P1 migration)

### bm2.0 · platform

- [ ] **AgentRun bookkeeping around every agent** (PORT) — `backend/app/services/runs.py` → job-run records on their scheduled_jobs scheduler (agent_runs migration; asyncpg pattern removes the SQLite lock dance)
- [ ] **App shell: provider wiring, scheduler lifecycle, health** (MERGE) — `backend/app/main.py` → james-os app startup (their shell KEEP; provider wiring + scheduler job registration folded into their lifespan)
- [ ] **Brand CRUD + cascade delete + settings** (MERGE) — `backend/app/routers/brands.py` → their tenant/brand management (auth/tenancy/RLS KEEP-theirs; settings toggles + RLS-scoped cascade delete folded in)
- [ ] **Captions-first video transcription** (PORT) — `backend/app/adapters/live.py` → provider module transcription provider (captions -> AssemblyAI; feeds the ported voice harvester)
- [ ] **Computed confidence (D2 rubric)** (PORT) — `backend/app/schemas/contracts.py` → profile_fields envelope contracts module (deterministic rubric, never model-reported)
- [ ] **Contradiction detection + auto-queued interview question** (PORT) — `backend/app/services/profile.py` → profile_fields contradiction states; materialized question -> brand_questions
- [ ] **D12 intelligence-source inventory** (PORT) — `backend/app/routers/sources.py` → provider layer module: GET /system/sources ('more keys = more power' observability)
- [ ] **Hardened HTTP helper for all vendors** (PORT) — `backend/app/adapters/live.py` → provider module shared HTTP helper (timeout/retry/key-redaction/ok_404)
- [ ] **LLM availability chain (Anthropic -> Perplexity)** (PORT) — `backend/app/adapters/live.py` → provider module chain router (availability fall-through, tier-bug re-raise, no mock in live)
- [ ] **Per-provider incremental go-live with mock fallback** (PORT) — `backend/app/adapters/live.py` → provider module (per-key live-else-mock fallback; PostProxy primary / Ayrshare fallback routing)
- [ ] **Profile envelope: append-only versioned writes** (PORT) — `backend/app/services/profile.py` → NEW migration profile_fields; their brand_profiles becomes flat VIEW/projection (non-negotiable, never flattened)
- [ ] **Provider protocol layer (D8)** (PORT) — `backend/app/adapters/base.py` → new james-os provider module (13 Protocols, mock/live by env — keeps merged product demo-able without keys)
- [ ] **Settings: env-driven keys, voice caps, model tiers** (MERGE) — `backend/app/core/config.py` → james-os config/env system (vendor keys, LLM tier ids, voice caps, scheduler toggle, manager_v2 flag)
- [ ] **Staleness TTLs + nightly mark_stale** (PORT) — `backend/app/services/profile.py` → profile_fields TTL logic + nightly scheduled_jobs entry
- [ ] **Tiered LLM routing + JSON enforcement** (PORT) — `backend/app/adapters/live.py` → provider module LLM router (extract/content/strategy tiers, no native tools, complete_json retry)
- [ ] **Token accounting via active-run contextvar** (MERGE) — `backend/app/services/runs.py` → their credit metering (per-run tokens_in/tokens_out accrual feeds existing meter)
- [ ] **run_agent wrapper (failed runs survive as 502)** (PORT) — `backend/app/routers/brands.py` → API-layer job wrapper on their routes (failed job-run committed, 502 surfaced)

### bm2.0 · providers

- [ ] **AnthropicRouter — tiered LLM with token accounting** (PORT) — `backend/app/adapters/live.py` → james-os providers module — primary LLM router (usage accrual adapted to their credit metering)
- [ ] **AssemblyAITranscription + YouTube caption/audio chain** (PORT) — `backend/app/adapters/live.py` → james-os providers module — transcription chain consumed by the voice harvester alongside voice_ingest
- [ ] **AyrshareConnector (fallback social vendor)** (PORT) — `backend/app/adapters/live.py` → james-os providers module — fallback social vendor behind PostProxy in the vendor priority chain
- [ ] **CompositePeerData — per-platform resilient scraper chain** (PORT) — `backend/app/adapters/live.py` → james-os providers module — peer-data chain feeding research_roster/peer_snapshots fills
- [ ] **DeepResearchProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — supplementary research contract (source=researched SECONDARY rule preserved)
- [ ] **EmailProvider protocol (execution hand)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — email-hand contract consumed by the outbox.execute_action executor
- [ ] **GET /system/sources — D12 stream inventory** (PORT) — `backend/app/routers/sources.py` → new sources route in james-os API — provider-layer stream inventory (live/mock/idle observability)
- [ ] **Keyless mock provider suite (mock mode works with zero keys)** (PORT) — `backend/app/adapters/mocks.py` → james-os providers module — mock mode; how the merged product stays demo-able without keys
- [ ] **KnowledgeProvider protocol (Wikipedia lane)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — wiki lane contract (None-is-a-finding semantics preserved)
- [ ] **LLM availability chain: PerplexityRouter fallback + ChainLLMRouter** (PORT) — `backend/app/adapters/live.py` → james-os providers module — anthropic→perplexity availability chain
- [ ] **LLMRouter protocol (tiered LLM access)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — tiered LLM contract with token accounting (D8 no-tools rule preserved)
- [ ] **MockLLMRouter — prompt-routed deterministic completions for every agent** (PORT) — `backend/app/adapters/mocks.py` → james-os providers module — deterministic LLM mock backing every ported agent's tests and demos
- [ ] **News chain: GNewsProvider + SerperNewsProvider fallback** (PORT) — `backend/app/adapters/live.py` → james-os providers module — news chain (gnews>serper>mock)
- [ ] **NewsProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — news lane contract
- [ ] **PeerDataProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — non-owned-account data contract feeding peer_snapshots
- [ ] **PerplexityDeepResearch adapter** (PORT) — `backend/app/adapters/live.py` → james-os providers module — supplementary deep-research lane (supersedes their single-Perplexity-pass research role)
- [ ] **PlacesProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — places lane contract
- [ ] **PublishProvider protocol (blog hand)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — blog-hand contract consumed by the outbox.execute_action executor
- [ ] **Scrape chain: FirecrawlScrape + PlainScrape keyless fallback** (PORT) — `backend/app/adapters/live.py` → james-os providers module — scrape chain (firecrawl>plain, never mock in live)
- [ ] **ScrapeProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — scrape contract
- [ ] **SearchProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module (new, D8 mock/live-per-key) — sanctioned web access contract
- [ ] **SerperPlaces adapter** (PORT) — `backend/app/adapters/live.py` → james-os providers module — places lane riding the Serper key
- [ ] **SerperSearch adapter** (PORT) — `backend/app/adapters/live.py` → james-os providers module — live search (also carries the reddit stream)
- [ ] **Shared HTTP resilience helper (_http)** (PORT) — `backend/app/adapters/live.py` → james-os providers module — shared HTTP helper (timeout/retry/key-redaction/ok_404)
- [ ] **SocialConnector protocol (aggregator)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — typed aggregator contract over postproxy.py + connections.py
- [ ] **TranscriptionProvider protocol** (PORT) — `backend/app/adapters/base.py` → james-os providers module — transcription contract (voice harvester dependency)
- [ ] **VideoProvider protocol (YouTube lane)** (PORT) — `backend/app/adapters/base.py` → james-os providers module — YouTube lane contract (D9 no-search.list rule travels with it)
- [ ] **WikipediaKnowledge adapter (keyless)** (PORT) — `backend/app/adapters/live.py` → james-os providers module — always-live keyless wiki lane
- [ ] **YouTubeVideo adapter (D9-safe)** (PORT) — `backend/app/adapters/live.py` → james-os providers module — D9-safe YouTube lane (feeds voice harvester in P4)
- [ ] **get_providers() single wiring point** (PORT) — `backend/app/adapters/base.py` → james-os providers module — single wiring point (mock vs live-with-fallback)
- [ ] **live_providers() per-provider key-gated fallback rules** (PORT) — `backend/app/adapters/live.py` → james-os providers module — live wiring with per-provider key-gated fallback (D8 incremental go-live)

### james-os · intelligence

- [ ] **AI auto-filing classifier** (KEEP) — `src/james_os/classify.py` → stays: src/james_os/classify.py
- [ ] **Commitments extraction from meeting docs** (KEEP) — `src/james_os/commitments.py; src/james_os/main.py` → stays: src/james_os/commitments.py; src/james_os/main.py (bm2.0 action_items land as a separate new P1 migration, not merged here)

### james-os · memory

- [ ] **Append-only events memory substrate** (KEEP) — `src/james_os/models.py; src/james_os/main.py` → stays: src/james_os/models.py; src/james_os/main.py (also the landing zone for bm2.0 brand-memory/exemplar chunks PORTed as events)
- [ ] **Ask audit log with stage timings** (KEEP) — `src/james_os/ask.py` → stays: src/james_os/ask.py
- [ ] **Canonical 8-slot filename convention** (KEEP) — `src/james_os/naming.py` → stays: src/james_os/naming.py
- [ ] **Cohere Rerank v3.5 reranker with pass-through fallback** (KEEP) — `src/james_os/rerank.py` → stays: src/james_os/rerank.py
- [ ] **Collision-safe document versioning** (KEEP) — `src/james_os/knowledge.py` → stays: src/james_os/knowledge.py
- [ ] **Controlled vocabularies (PreReal Naming v1.0)** (KEEP) — `src/james_os/vocab.py` → stays: src/james_os/vocab.py
- [ ] **Conversational follow-ups with history guard** (KEEP) — `src/james_os/ask.py` → stays: src/james_os/ask.py
- [ ] **Document version supersession (append-only)** (KEEP) — `src/james_os/ingestion.py` → stays: src/james_os/ingestion.py
- [ ] **Entity registry with stable EntityIDs** (KEEP) — `src/james_os/entities.py` → stays: src/james_os/entities.py
- [ ] **Full-spectrum document text extraction** (KEEP) — `src/james_os/documents.py` → stays: src/james_os/documents.py
- [ ] **Hybrid retrieval (vector + full-text, parallel fan-out)** (KEEP) — `src/james_os/retrieval.py` → stays: src/james_os/retrieval.py
- [ ] **Idempotent event ingestion with embedding** (KEEP) — `src/james_os/ingestion.py` → stays: src/james_os/ingestion.py
- [ ] **Knowledge Base single-file + batch-ZIP ingest** (KEEP) — `src/james_os/knowledge.py; src/james_os/main.py` → stays: src/james_os/knowledge.py; src/james_os/main.py
- [ ] **Knowledge document management API** (KEEP) — `src/james_os/main.py` → stays: src/james_os/main.py (/knowledge/documents endpoints)
- [ ] **Paragraph-aware chunking** (KEEP) — `src/james_os/documents.py` → stays: src/james_os/documents.py
- [ ] **Plug-ins guidelines API** (KEEP) — `src/james_os/main.py` → stays: src/james_os/main.py (/plug-ins endpoints)
- [ ] **Private storage + signed-URL document downloads** (KEEP) — `src/james_os/knowledge.py` → stays: src/james_os/knowledge.py
- [ ] **Provider-agnostic embedder (Voyage + stub)** (KEEP) — `src/james_os/embedder.py` → stays: src/james_os/embedder.py
- [ ] **Rules-as-data system prompts (plug_ins slots)** (KEEP) — `src/james_os/prompts.py` → stays: src/james_os/prompts.py (bm2.0 learned guardrails feed its ACTIVE GUIDELINES / avoid slots as data in P3)
- [ ] **Sensitivity enforcement at answer time** (KEEP) — `src/james_os/sensitivity.py` → stays: src/james_os/sensitivity.py
- [ ] **Silos (project/topic corpus groupings)** (KEEP) — `src/james_os/silos.py; src/james_os/main.py` → stays: src/james_os/silos.py; src/james_os/main.py
- [ ] **Two-pass answer verification** (KEEP) — `src/james_os/ask.py; src/james_os/prompts.py` → stays: src/james_os/ask.py; src/james_os/prompts.py
- [ ] **ask() cite-or-refuse QA pipeline** (KEEP) — `src/james_os/ask.py` → stays: src/james_os/ask.py

### james-os · platform

- [ ] **Auth: bcrypt + JWT-in-httpOnly-cookie + CSRF** (KEEP) — `src/james_os/auth.py; src/james_os/main.py` → stays: src/james_os/auth.py; src/james_os/main.py (merge kills bm2.0's need to ever build auth)
- [ ] **Field-level Fernet encryption of stored keys** (KEEP) — `src/james_os/encryption.py` → stays: src/james_os/encryption.py
- [ ] **First-signup claims default tenant** (KEEP) — `src/james_os/auth.py` → stays: src/james_os/auth.py
- [ ] **Global auth + tenant middleware** (KEEP) — `src/james_os/main.py` → stays: src/james_os/main.py (all ported bm2.0 services inherit tenant scoping from it)
- [ ] **Login rate limiting + account lockout** (KEEP) — `src/james_os/auth.py` → stays: src/james_os/auth.py
- [ ] **Postgres RLS multi-tenancy** (KEEP) — `src/james_os/db.py` → stays: src/james_os/db.py (all bm2.0 code rewrites onto this asyncpg/RLS pattern; brand_id -> tenant_id maps once in P6)
- [ ] **Revocable DB-backed sessions** (KEEP) — `src/james_os/auth.py` → stays: src/james_os/auth.py
- [ ] **Supabase Storage backend (CDN URLs + TUS resumable upload)** (KEEP) — `src/james_os/storage_supabase.py` → stays: src/james_os/storage_supabase.py
- [ ] **Tenant-managed credentials store (Settings-driven keys)** (KEEP) — `src/james_os/credentials.py` → stays: src/james_os/credentials.py (supplies per-tenant keys to the PORTed bm2.0 D8 mock/live provider layer)
- [ ] **Warm asyncpg pool tuned for cloud Supabase** (KEEP) — `src/james_os/db.py` → stays: src/james_os/db.py

### james-os · production

- [ ] **<avoid> render steering block** (KEEP) — `src/james_os/video_feedback.py` → stays: src/james_os/video_feedback.py
- [ ] **Abstracted media storage layer** (KEEP) — `src/james_os/media.py` → stays: src/james_os/media.py MediaStorage (their storage abstraction survives per plan)
- [ ] **Auto-clip (one-click render top candidates)** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **Avatar vs B-roll beat classification** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **B-roll reuse library (generated clips become assets)** (KEEP) — `src/james_os/broll_library.py` → stays: src/james_os/broll_library.py (media_assets role='broll')
- [ ] **B-roll scene rendering (seed still -> image-to-video)** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **B-roll seed image generation** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **Beat visual-prompt writing + parallel still generation** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Brand kit (identity on every render)** (KEEP) — `src/james_os/brand_kit.py` → stays: src/james_os/brand_kit.py (tenants.config['brand_kit'] + brand_kit_api.py)
- [ ] **Branding overlay elements (watermark / nameplate / end card / progress bar)** (KEEP) — `src/james_os/assembly.py` → stays: src/james_os/assembly.py
- [ ] **Bulk one-click generation (N pieces)** (KEEP) — `src/james_os/autopilot_bulk.py` → stays: src/james_os/autopilot_bulk.py (+ autopilot_bulk_api.py POST /autopilot/bulk)
- [ ] **Candidate management (whole-source, re-analyze, dismiss)** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **Caption element builder (auto-fit, face-safe)** (KEEP) — `src/james_os/caption_styles.py` → stays: src/james_os/caption_styles.py
- [ ] **Caption preset library (14 presets)** (KEEP) — `src/james_os/caption_styles.py` → stays: src/james_os/caption_styles.py
- [ ] **Cinematic treatment storyboard (film through-line)** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Clip-topic mining across the library** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **Composition capability registry + build queue** (KEEP) — `src/james_os/compositions.py` → stays: src/james_os/compositions.py (build-request surface restyled in P5, logic unchanged)
- [ ] **Conservative B-roll reuse matching + provenance** (KEEP) — `src/james_os/broll_library.py` → stays: src/james_os/broll_library.py
- [ ] **Content library rollup** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **Creatomate final-cut assembly provider** (KEEP) — `src/james_os/assembly.py` → stays: src/james_os/assembly.py (registered in unified provider layer)
- [ ] **Crop-safety gate (subject never cut off)** (KEEP) — `src/james_os/image_compose.py` → stays: src/james_os/image_compose.py
- [ ] **Dead-air interval computation + timestamp remap** (KEEP) — `src/james_os/clip_tighten.py` → stays: src/james_os/clip_tighten.py
- [ ] **Design Inspector (whole-video style reverse-engineering)** (KEEP) — `src/james_os/design_inspector.py` → stays: src/james_os/design_inspector.py
- [ ] **Designed-card compositor (5 formats, Pillow)** (KEEP) — `src/james_os/image_compose.py` → stays: src/james_os/image_compose.py
- [ ] **Distinct style-template batch assignment** (KEEP) — `src/james_os/autopilot_templates.py` → stays: src/james_os/autopilot_templates.py
- [ ] **Durable video render state machine** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **Durable video-job store** (KEEP) — `src/james_os/video.py` → stays: src/james_os/video.py (video_jobs table)
- [ ] **ElevenLabs cloned-voice TTS** (KEEP) — `src/james_os/tts.py` → stays: src/james_os/tts.py (registered in unified provider layer)
- [ ] **Filler-word removal (optional gate)** (KEEP) — `src/james_os/clip_tighten.py` → stays: src/james_os/clip_tighten.py
- [ ] **Generated-clip persistence to owned storage** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py (_persist_clip_to_storage into their media storage)
- [ ] **Hero character context from photos** (KEEP) — `src/james_os/hero_context.py` → stays: src/james_os/hero_context.py
- [ ] **Hero-consistent insert stills (Soul / photo refs)** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Hero-photo reuse ledger** (KEEP) — `src/james_os/photo_pick.py` → stays: src/james_os/photo_pick.py (actions-table memory, zero DDL)
- [ ] **HeyGen avatar provider** (KEEP) — `src/james_os/heygen.py` → stays: src/james_os/heygen.py (registered in unified provider layer)
- [ ] **HeyGen talking photo** (KEEP) — `src/james_os/heygen.py` → stays: src/james_os/heygen.py
- [ ] **Higgsfield Soul ID (trained digital double)** (KEEP) — `src/james_os/higgsfield_souls.py` → stays: src/james_os/higgsfield_souls.py
- [ ] **Hook title cards** (KEEP) — `src/james_os/caption_styles.py` → stays: src/james_os/caption_styles.py
- [ ] **Image-to-video providers (Runway / Higgsfield / stub)** (KEEP) — `src/james_os/video.py` → stays: src/james_os/video.py (aligned with the unified D8-style provider layer landing in P1)
- [ ] **Insert animation (image-to-video motion)** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Insert scene dedupe gate** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Intra-clip tightening orchestration (kill the fluff)** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **LLM art director for designed cards** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **LLM image director (story -> cinematic scene prompt)** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **LLM reel-candidate mining** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py (reel_candidates)
- [ ] **Layout mislabel guard** (KEEP) — `src/james_os/compositions.py` → stays: src/james_os/compositions.py
- [ ] **Live-tunable render knobs** (KEEP) — `src/james_os/render_tuning.py` → stays: src/james_os/render_tuning.py (tenants.config['render_tuning'])
- [ ] **Long-form cutter pipeline (podcast -> reels)** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py (long_sources)
- [ ] **Mood-tagged music-bed library** (KEEP) — `src/james_os/audio_library.py` → stays: src/james_os/audio_library.py (media_assets role='music')
- [ ] **Motion and reframe props (Ken Burns / zoom punch / speaker crop)** (KEEP) — `src/james_os/assembly.py` → stays: src/james_os/assembly.py
- [ ] **Per-scene re-render** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **Photo sharpness gate (Laplacian variance)** (KEEP) — `src/james_os/photo_pick.py` → stays: src/james_os/photo_pick.py
- [ ] **Podcast engine (document -> spoken episode)** (KEEP) — `src/james_os/podcast.py` → stays: src/james_os/podcast.py (actions queue, action_type='podcast')
- [ ] **Post hero-image generation** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **Post image style library (6 styles)** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **Production lifecycle controls (cancel / trim / delete / progress)** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **Reference-conditioned hero image generation** (KEEP) — `src/james_os/imagegen.py` → stays: src/james_os/imagegen.py
- [ ] **Role-based media / reference library** (KEEP) — `src/james_os/media.py` → stays: src/james_os/media.py (9-role media_assets)
- [ ] **SFX library (transition sounds)** (KEEP) — `src/james_os/audio_library.py` → stays: src/james_os/audio_library.py (media_assets role='sfx')
- [ ] **Scene-plan generator (fixed shootable structure)** (KEEP) — `src/james_os/video_plan.py` → stays: src/james_os/video_plan.py
- [ ] **Sentence-aware beat segmentation** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Shot-size rotation gate** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Similar-style dedupe detection** (KEEP) — `src/james_os/templates.py` → stays: src/james_os/templates.py
- [ ] **Speaker detection + manual speaker tags** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **Speaker lower-third name tags** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Speaker-following crop (diarization-driven reframe)** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Split-screen modes (horizontal / vertical)** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **Style-specific caption treatments** (KEEP) — `src/james_os/caption_styles.py` → stays: src/james_os/caption_styles.py
- [ ] **Style-template library (trending video styles)** (KEEP) — `src/james_os/templates.py` → stays: src/james_os/templates.py (+ templates_api.py)
- [ ] **Template -> render parameter mapping (honest approximations)** (KEEP) — `src/james_os/template_apply.py` → stays: src/james_os/template_apply.py
- [ ] **Template B-roll-only reel** (KEEP) — `src/james_os/templates_api.py` → stays: src/james_os/templates_api.py (POST /templates/{id}/broll-reel)
- [ ] **Template replicate (render in a stored style)** (KEEP) — `src/james_os/templates_api.py` → stays: src/james_os/templates_api.py (POST /templates/{id}/replicate)
- [ ] **Trailing-silence detection + trim** (KEEP) — `src/james_os/audio_trim.py` → stays: src/james_os/audio_trim.py
- [ ] **Uniform caption style policy** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Video caption + hook generators + signoff** (KEEP) — `src/james_os/content.py` → stays: src/james_os/content.py
- [ ] **Video rejection learning loop** (KEEP) — `src/james_os/video_feedback.py` → stays: src/james_os/video_feedback.py (video_feedback events, deliberately separate from text frustrations)
- [ ] **Whisper-cap-aware chunked transcription** (KEEP) — `src/james_os/long_form.py` → stays: src/james_os/long_form.py
- [ ] **White paper -> content pack fan-out** (KEEP) — `src/james_os/content_pack.py` → stays: src/james_os/content_pack.py (into their Approval Queue)
- [ ] **White-paper generator (3-act, cited)** (KEEP) — `src/james_os/whitepaper.py` → stays: src/james_os/whitepaper.py (grounds on their KB/events substrate)
- [ ] **Word-anchored dense B-roll insert picker** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **Word-pinned caption phrase builder** (KEEP) — `src/james_os/story_video.py` → stays: src/james_os/story_video.py
- [ ] **avatar_only production mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **avatar_story_mix production mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **engaging_avatar production mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **ffmpeg toolbelt (slice / extract / probe / concat / tighten)** (KEEP) — `src/james_os/audio_trim.py` → stays: src/james_os/audio_trim.py
- [ ] **hero_clone talking-photo mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **long_form_reel production mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **mixed / timeline structured mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py
- [ ] **story_audio production mode** (KEEP) — `src/james_os/video_pipeline.py` → stays: src/james_os/video_pipeline.py

## P2 — Eyes & heartbeat — 54 rows


### bm2.0 · execution

- [ ] **Action follow-up endpoints** (PORT) — `backend/app/routers/actions.py` → actions API routes over action_items (status/note/snooze/delete, research-contact, manual daily-cycle, daily-digest)
- [ ] **Autopilot (opt-in drafting, human gate intact)** (PORT) — `backend/app/services/scheduler.py` → scheduler step -> their content engine + their approval queue (nothing auto-publishes)
- [ ] **Contact-research agent (public outreach paths)** (PORT) — `backend/app/agents/contact_research.py` → new job (no james-os equivalent); results -> action_items updates + meta.contact_paths
- [ ] **Daily digest ('chunk for the day') assembly** (PORT) — `backend/app/services/actions.py` → scheduler digest step over action_items (one upserted digest per brand/date)
- [ ] **Due-followups computation** (PORT) — `backend/app/services/actions.py` → action_items module feeding the daily digest scheduler step

### bm2.0 · intelligence

- [ ] **Algorithm staleness cadence (7-day)** (FILL) — `backend/app/agents/algorithm.py` → platform_playbooks + scheduled_jobs (refresh only when stale)
- [ ] **Appearances Agent (guest-appearance ladder)** (FILL) — `backend/app/agents/appearances.py` → content_suggestions + scheduled_jobs; items -> action_items appearance:{title}
- [ ] **Aspirational-tier benchmarks + own-vs-peer observations** (FILL) — `backend/app/agents/peer.py` → peer_snapshots + profile_fields competitors.benchmarks; snapshot_count on job-run output
- [ ] **Async peer discovery endpoints** (FILL) — `backend/app/routers/peers.py` → peers API routes over research_roster (202 + status poll + 409 in-flight)
- [ ] **Baseline audit per connected account** (PORT) — `backend/app/agents/auditor.py` → new audit job on their scheduled_jobs scheduler; reads postproxy.py analytics; writes profile_fields channels.* (source=audited)
- [ ] **Content Opportunity Radar (Reddit problem mining)** (FILL) — `backend/app/agents/opportunities.py` → content_suggestions (0 rows) + scheduled_jobs registry; items -> action_items radar:{title}
- [ ] **Degraded audit modes as notes, never failures** (PORT) — `backend/app/agents/auditor.py` → audit job report notes (per-platform degradation, no hard fail)
- [ ] **Discovery dedupe against every existing peer** (FILL) — `backend/app/agents/peer_discovery.py` → research_roster dedupe (handle + display-name keys across candidate/tracked/rejected)
- [ ] **Exemplar promotion seeds the voice corpus** (MERGE) — `backend/app/agents/auditor.py` → james-os voice exemplar corpus (one corpus, tagged origin=audited; additive to their 1,344 exemplars)
- [ ] **Intelligence trigger endpoints (one per eye/brain)** (PORT) — `backend/app/routers/planning.py` → james-os API layer: manual-trigger routes over the registered eye/brain jobs (commit-on-success/502-on-failure)
- [ ] **Peer approve/reject human gate endpoints** (FILL) — `backend/app/routers/peers.py` → peers API routes: research_roster candidate->tracked/rejected (rejected also inactive; stale-snapshot leak guard)
- [ ] **Peer discovery into 4 relationship buckets** (FILL) — `backend/app/agents/peer_discovery.py` → research_roster (status='candidate' + relationship kind + reason)
- [ ] **Peer metric writes as source=derived (D4 sole writer)** (FILL) — `backend/app/agents/peer.py` → profile_fields competitors.* via envelope with peer_snapshot citations (peer job = sole Section 6 writer)
- [ ] **Peer snapshotting of approved peers only** (FILL) — `backend/app/agents/peer.py` → research_roster + peer_snapshots (0 rows); registered scheduler job, human gate kept
- [ ] **Per-peer failure isolation + empty-digest degradation** (FILL) — `backend/app/agents/peer.py` → peer snapshot job (observation notes + honest empty digest)
- [ ] **Per-platform algorithm briefs (R2)** (FILL) — `backend/app/agents/algorithm.py` → platform_playbooks (1 row): cited briefs with confidence/freshness via envelope
- [ ] **Post-history import to brand memory** (PORT) — `backend/app/agents/auditor.py` → events substrate (kind='post' chunks, embedded and Ask-retrievable for free)
- [ ] **Press Agent (amplify content-worthy coverage)** (FILL) — `backend/app/agents/press.py` → content_suggestions + scheduled_jobs; items -> action_items press:{title}
- [ ] **Public-baseline fallback (honest provenance)** (PORT) — `backend/app/agents/auditor.py` → audit job fallback path; profile_fields source=researched data_basis='public_scrape'
- [ ] **Search-Questions Agent (AEO eye)** (FILL) — `backend/app/agents/questions.py` → content_suggestions + scheduled_jobs; items -> action_items question:{title}
- [ ] **Trend Agent (industry news to timely angles)** (FILL) — `backend/app/agents/trends.py` → content_suggestions + scheduled_jobs; items -> action_items trend:{title}
- [ ] **Unknown-platform peer resolution** (FILL) — `backend/app/agents/peer.py` → research_roster (platform/display-name backfill on first snapshot)

### bm2.0 · platform

- [ ] **Daily full-cycle heartbeat (sense-think-act-learn)** (PORT) — `backend/app/services/scheduler.py` → their scheduled_jobs table-driven scheduler (registered steps; their 5 dormant intelligence jobs retired — one brain)
- [ ] **Digest email delivery (opt-in)** (PORT) — `backend/app/services/scheduler.py` → scheduler step via ported email provider (Resend)

### james-os · analytics

- [ ] **Accounts leaderboard + platform performance** (KEEP) — `src/james_os/analytics.py; src/james_os/main.py` → stays: src/james_os/analytics.py; src/james_os/main.py
- [ ] **Apify trend-scraping provider** (KEEP) — `src/james_os/apify.py` → stays: src/james_os/apify.py (bm2.0 trends eye consumes this provider instead of shipping a second scraper)
- [ ] **Brand accounts registry (owned handles)** (KEEP) — `src/james_os/brand_accounts.py` → stays: src/james_os/brand_accounts.py (bm2.0 onboarding connect step writes into it)
- [ ] **Brand-scoped scraped-post analytics** (KEEP) — `src/james_os/analytics.py; src/james_os/main.py` → stays: src/james_os/analytics.py; src/james_os/main.py (bm2.0 auto-measure/what_worked step reads these aggregates)
- [ ] **Live connector-backed analytics dashboard** (KEEP) — `src/james_os/analytics_live.py` → stays: src/james_os/analytics_live.py (feeds bm2.0 north-star/goal-check baselines)
- [ ] **Meta Graph read-side client** (KEEP) — `src/james_os/meta_graph.py` → stays: src/james_os/meta_graph.py
- [ ] **Saved trending posts curation shelf** (KEEP) — `src/james_os/social_saved.py` → stays: src/james_os/social_saved.py
- [ ] **Social listening API + draft-from-post** (KEEP) — `src/james_os/xpoz_api.py` → stays: src/james_os/xpoz_api.py (draft path keeps routing through their content engine + voice-QA gate)
- [ ] **Trend layer: viral scoring + trends-as-memory** (KEEP) — `src/james_os/trends.py` → stays: src/james_os/trends.py (stronger substrate; bm2.0 trend eye files its findings as suggestions on top)
- [ ] **Watchlist (peer/competitor creators) + cohort trends** (KEEP) — `src/james_os/trends.py; src/james_os/main.py` → stays: src/james_os/trends.py; src/james_os/main.py (bm2.0 peer discover->approve->track fills research_roster/peer_snapshots alongside, human-approval gate kept)
- [ ] **Xpoz cross-platform social listening adapter** (KEEP) — `src/james_os/xpoz_intel.py` → stays: src/james_os/xpoz_intel.py (available as a provider to the bm2.0 trends/press/AEO eyes)

### james-os · intelligence

- [ ] **Background intelligence job runner** (KEEP) — `src/james_os/intelligence.py` → stays: src/james_os/intelligence.py
- [ ] **Content suggestions store + accept/dismiss rail** (FILL) — `src/james_os/brands.py; src/james_os/main.py` → content_suggestions table (0 rows) + /suggestions accept/dismiss rail — bm2.0's 5 eyes file findings here; accept still routes into their production + approval queue
- [ ] **Corpus coverage check (skip-the-sweep gate)** (KEEP) — `src/james_os/intelligence.py` → stays: src/james_os/intelligence.py
- [ ] **Cross-silo portfolio synthesis** (KEEP) — `src/james_os/intelligence.py; src/james_os/main.py` → stays: src/james_os/intelligence.py; src/james_os/main.py
- [ ] **Daily brand research job -> content suggestions** (REPLACE) — `src/james_os/brand_research.py` → src/james_os/brand_research.py — retired; bm2.0's 5 eyes + daily cycle produce the suggestions (each with a WHY, deduped), landing in content_suggestions via the scheduler
- [ ] **Strategy engine — versioned platform algorithm playbooks** (REPLACE) — `src/james_os/strategy.py` → src/james_os/strategy.py run_playbook_refresh — retired; bm2.0 cadence-refreshed cited algorithm briefs FILL platform_playbooks (versioned diff/change-detection semantics kept)
- [ ] **Topic Intelligence deep-research machine** (KEEP) — `src/james_os/intelligence.py` → stays: src/james_os/intelligence.py (its GATHER stage consumes the researcher that P4 replaces in research.py; interface unchanged)
- [ ] **Weekly thesis -> intelligence -> white paper pipeline** (KEEP) — `src/james_os/thesis.py; src/james_os/main.py` → stays: src/james_os/thesis.py; src/james_os/main.py (whitepapers are explicit KEEP-THEIRS production)

### james-os · platform

- [ ] **Scheduled job registry (5 kinds)** (REPLACE) — `src/james_os/scheduler.py` → scheduled_jobs table — the 5 dormant intelligence kinds (daily_brand_research, brand_interview, playbook_refresh, peer_snapshot, weekly_prescription) are retired; bm2.0 daily cycle + eyes jobs registered in their place (one brain, no duplicate spend)
- [ ] **Table-driven recurring-job scheduler** (KEEP) — `src/james_os/scheduler.py` → stays: src/james_os/scheduler.py (bm2.0 daily cycle + 5 eyes + goal-miss/promote scans register as jobs on this loop)

### james-os · production

- [ ] **Auto-compose video (research -> editable scene plan)** (KEEP) — `src/james_os/video_compose.py` → stays: src/james_os/video_compose.py (trending-intel input re-sourced from the unified bm2.0 eyes once they land — no duplicate research spend)
- [ ] **Autopilot daily autonomous batches** (KEEP) — `src/james_os/autopilot.py` → stays: src/james_os/autopilot.py (run_batch invoked as the 'autopilot' step of the unified daily cycle on their scheduled_jobs scheduler)
- [ ] **Autopilot per-tenant config slot** (KEEP) — `src/james_os/autopilot.py` → stays: tenants.config['autopilot'] (read by the unified daily cycle)
- [ ] **Virality-first intel gathering** (MERGE) — `src/james_os/autopilot.py` → src/james_os/autopilot.py _gather_intel rewired to consume bm2.0 5-eyes output (content_suggestions + scheduler jobs) — one set of eyes, no duplicate research spend; honest-None behavior kept

## P3 — Brain & learning — 38 rows


### bm2.0 · intelligence

- [ ] **Competitor growth trajectory computation** (REPLACE) — `backend/app/agents/growth.py` → strategy.py growth module over peer_snapshots deltas
- [ ] **Growth-driver reasoning (what to borrow)** (REPLACE) — `backend/app/agents/growth.py` → strategy.py growth module (grounded LLM reasoning via ported provider tiers)

### bm2.0 · learning

- [ ] **Approval teaches the voice corpus (R6.1)** (MERGE) — `backend/app/routers/queue.py` → learning.py approve hook -> shared voice corpus (tagged origin=approved, artifact:{id} dedupe)
- [ ] **Goal-miss replanning (rate-limited nag)** (PORT) — `backend/app/services/scheduler.py` → scheduler step (no equivalent): nag -> action_items per ISO week; corrective draft -> prescriptions; activation stays human
- [ ] **Measure step: predicted-vs-actual + insight memory** (MERGE) — `backend/app/services/execution.py` → learning.py + actions queue 'measured' status; insight chunks -> events substrate
- [ ] **Rejection distilled into permanent guardrails (R6.1)** (MERGE) — `backend/app/routers/queue.py` → learning.py + feedback_changes; avoid-terms -> profile_fields guardrails.learned_avoid (source=queue_signal)
- [ ] **auto_measure: analytics pulled for published posts** (MERGE) — `backend/app/services/learning.py` → learning.py + postproxy.py analytics (post-id matching, 24h delay, honest unmatched notes)
- [ ] **goal_gap pace math ('it notices it's missing the goal')** (PORT) — `backend/app/services/learning.py` → scheduler goal-check step (no james-os equivalent) reading profile_fields goals.* + audited baselines
- [ ] **promote_candidates (put spend behind winners)** (PORT) — `backend/app/services/learning.py` → scheduler promote-spend scan step (no james-os equivalent)
- [ ] **what_worked read-back block for planning** (MERGE) — `backend/app/services/learning.py` → learning.py (closes their PRD gap R7: performance -> strategy); feeds strategy.py prompts

### bm2.0 · strategy

- [ ] **Collab generate + persist as action items** (PORT) — `backend/app/routers/peers.py` → collab API routes over the ported collaboration job; plays/levers -> action_items; GET serves latest job-run report
- [ ] **Collaboration plays matched to relationship kind** (PORT) — `backend/app/agents/collaboration.py` → new strategy job (no james-os equivalent); plays -> action_items collab:{kind}:{handle}:{play_type}
- [ ] **D5 plan-item contract enforcement** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py post-parse validation -> prescriptions (rationale/evidence/predicted_metrics guaranteed)
- [ ] **Daily activities plan (content + non-content moves)** (REPLACE) — `backend/app/agents/daily_plan.py` → strategy.py daily module; activities -> action_items (dedupe daily:{day}:{title})
- [ ] **Morning brief (deterministic assembly)** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py brief assembly (reads actions queue, peer_snapshots, profile_fields staleness)
- [ ] **North-star goal negotiation** (REPLACE) — `backend/app/agents/goal.py` → strategy.py goal module; goals -> profile_fields goals.{platform}.{metric} (source=negotiated)
- [ ] **Partial plan acceptance + idempotent activation** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py activation path (item_indices, idempotent re-activate, 409 on superseded)
- [ ] **Peer digest recompute excludes rejected peers** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py digest over peer_snapshots (tracked+active only, gate honored at read time)
- [ ] **Plan activation + D5 replan semantics** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py activation; materialized orders -> actions queue (supersede/reattach semantics)
- [ ] **Planning endpoints (weekly plan / activate / brief)** (REPLACE) — `backend/app/routers/planning.py` → planning API routes over strategy.py + prescriptions (partial acceptance, 409 on superseded)
- [ ] **Visibility-plays floor (never dead-ends)** (PORT) — `backend/app/agents/collaboration.py` → collaboration job fallback; full report stored on the job-run record
- [ ] **Weekly plan generation (single strategy-tier call)** (REPLACE) — `backend/app/agents/strategist.py` → strategy.py; plans -> prescriptions (0 rows, keep their evidence[] format)
- [ ] **Weekly prescription cron (Monday strategist)** (REPLACE) — `backend/app/services/scheduler.py` → scheduled_jobs Monday entry -> strategy.py -> prescriptions (replaces their dormant weekly intelligence job; draft-only)

### james-os · intelligence

- [ ] **Strategy engine — peer benchmarking snapshot** (REPLACE) — `src/james_os/strategy.py` → src/james_os/strategy.py run_peer_snapshot — retired; bm2.0 peer discover->approve->track FILLs research_roster + peer_snapshots (human-approval gate semantics kept)
- [ ] **Strategy engine — weekly Prescription** (REPLACE) — `src/james_os/strategy.py; src/james_os/main.py` → src/james_os/strategy.py run_weekly_prescription — retired; bm2.0 weekly strategist (grounded in baselines + algorithm + what-worked) FILLs prescriptions, keeping their evidence[] line format and accept->production routing

### james-os · learning

- [ ] **Approval -> positive exemplar learning** (MERGE) — `src/james_os/learning.py` → src/james_os/learning.py — bm2.0 approve->exemplar folds in; one exemplar corpus tagged by origin (harvested/uploaded/approved)
- [ ] **Changes board API** (KEEP) — `src/james_os/feedback_changes_api.py` → stays: src/james_os/feedback_changes_api.py
- [ ] **Feedback -> change store ('What's changing next' roadmap)** (MERGE) — `src/james_os/feedback_changes.py` → feedback_changes table (src/james_os/feedback_changes.py) — bm2.0 auto-measure/what_worked performance->strategy learning folds in (their PRD's known gap R7)
- [ ] **Feedback interpreter (reason -> live knob or queued change)** (KEEP) — `src/james_os/feedback_interpreter.py` → stays: src/james_os/feedback_interpreter.py
- [ ] **Human-edit diff learning** (KEEP) — `src/james_os/learning.py` → stays: src/james_os/learning.py (no bm2.0 twin)
- [ ] **Recent guardrails feed** (KEEP) — `src/james_os/learning.py` → stays: src/james_os/learning.py (backs guardrail display in the P5 unified UI)
- [ ] **Rejection -> frustration guardrail learning loop** (MERGE) — `src/james_os/learning.py` → src/james_os/learning.py — bm2.0 reject->guardrail plus the reviewer learned-guardrails leg fold in; human decisions keep full confidence
- [ ] **SHIPPED_FIXES already-built ledger** (KEEP) — `src/james_os/feedback_changes.py` → stays: src/james_os/feedback_changes.py

### james-os · platform

- [ ] **Agent run API** (KEEP) — `src/james_os/main.py` → stays: src/james_os/main.py (/agent/* endpoints)
- [ ] **Agent tool suite (24 registered tools)** (KEEP) — `src/james_os/agent.py` → stays: src/james_os/agent.py (existing 24 tools survive as-is; registry extends, never shrinks)
- [ ] **Claude tool-use agent loop** (KEEP) — `src/james_os/agent.py` → stays: src/james_os/agent.py (gains new tools wrapping bm2.0 strategy/eyes modules as they land)

### james-os · production

- [ ] **Frustration-ledger guardrail injection** (KEEP) — `src/james_os/content.py` → stays: src/james_os/content.py (_recent_frustrations; reconciled with bm2.0 reject->guardrail loop merging into learning.py)
- [ ] **Story-framed ideation with pillar quotas** (MERGE) — `src/james_os/autopilot.py` → unified brain ideation: generate_ideas steered by bm2.0 daily plan/prescriptions (prescriptions + content_suggestions); pillar quotas, story-arc scoring and trend_steer folded in — one strategist

## P4 — Onboarding, voice & publish — 72 rows


### bm2.0 · execution

- [ ] **Approval queue view + human approve/reject gate** (MERGE) — `backend/app/routers/queue.py` → their actions queue + approval UI (KEEP-theirs UI; add D5 guards, ApprovalEvent trail, superseded-version 409, RLS scoping)
- [ ] **Creator v0 drafting pipeline** (MERGE) — `backend/app/agents/creator.py` → their content engine (queued-order drafting loop folded in; their engine drafts)
- [ ] **D5 guard on reviewer state moves** (MERGE) — `backend/app/agents/reviewer.py` → actions queue transition guards (rejected only from review/pending_approval)
- [ ] **Email placeholder-recipient + social profile-key guards** (FILL) — `backend/app/services/execution.py` → outbox.execute_action provider guards (honest errors, labelled placeholder sends)
- [ ] **Execution HTTP surface with layered error mapping** (MERGE) — `backend/app/routers/execution.py` → execution API routes over content engine + outbox + actions queue (404/409/502 mapping kept)
- [ ] **Hands: format-aware text drafting (blog/email/social)** (MERGE) — `backend/app/agents/hands.py` → their content engine (adds blog/email formats + media payloads they never built)
- [ ] **Lint-clean house style + em-dash stripping** (MERGE) — `backend/app/agents/hands.py` → content engine system prompt + deterministic post-pass
- [ ] **One automatic revise-and-rescore loop** (MERGE) — `backend/app/agents/reviewer.py` → voice-QA gate + content engine (single retry, new artifact version, annotations on their queue)
- [ ] **Owner work view (opportunity -> draft -> impact)** (MERGE) — `backend/app/services/execution.py` → actions queue views (their queue UI survives; join adds opportunity/routing/metrics columns)
- [ ] **Publish step with honest-failure semantics** (FILL) — `backend/app/services/execution.py` → outbox.execute_action (dormant) — bm2.0 publish code IS the missing executor
- [ ] **Reviewer gate leg 1: deterministic AI-ism lint** (MERGE) — `backend/app/agents/reviewer.py` → james-os voice-QA gate (new deterministic lint leg, zero tolerance)
- [ ] **Reviewer gate leg 2: guardrail substring check** (MERGE) — `backend/app/agents/reviewer.py` → voice-QA gate (learned-guardrails leg reading profile_fields guardrails.*)
- [ ] **Reviewer gate leg 3: voice-fidelity LLM judge** (MERGE) — `backend/app/agents/reviewer.py` → voice-QA gate (their voice engine is stronger; our threshold/cold-start semantics folded in)
- [ ] **Reviewer gate leg 4: fact-check on specific claims** (MERGE) — `backend/app/agents/reviewer.py` → voice-QA gate (new best-effort fact-check leg vs order evidence)
- [ ] **Rewrite door pipeline (R5.2)** (MERGE) — `backend/app/services/execution.py` → content engine (source='rewrite' orders through their queue + merged QA gates)
- [ ] **Rewrite-of prompt path (R5.2)** (MERGE) — `backend/app/agents/hands.py` → content engine (new rewrite path through the same QA gates)
- [ ] **Video/image routing to james-os** (MERGE) — `backend/app/services/execution.py` → their media production pipelines (cross-project hand-off becomes direct in-process dispatch; pipelines KEEP-theirs)
- [ ] **Voice-strict drafting context** (MERGE) — `backend/app/agents/hands.py` → their content engine + voice engine (theirs stronger; harvested-profile injection + no-fake-voice rule folded in)
- [ ] **execute_opportunity: ActionItem -> WorkOrder -> draft** (MERGE) — `backend/app/services/execution.py` → content engine + actions queue (opportunity -> source='opportunity' order -> engine draft; outcome noted on action_items)

### bm2.0 · onboarding

- [ ] **Account connect endpoints + one profile-key per brand** (MERGE) — `backend/app/routers/accounts.py` → postproxy.py + connections.py (they have read/analytics; adds connect flow + key healing on brand settings)
- [ ] **Aggregator group list/bind/sync** (MERGE) — `backend/app/routers/accounts.py` → postproxy.py + connections.py (group list/bind; sync upserts into their connections rows)
- [ ] **Answer intake as user_stated writes** (REPLACE) — `backend/app/services/onboarding.py` → intake_agent.py answers -> profile_fields (source=user_stated, list parsing kept)
- [ ] **Answerer: researched answer suggestion (draft-only)** (REPLACE) — `backend/app/agents/answerer.py` → intake_agent.py + brand_questions (draft suggestion; human accept -> profile_fields user_stated)
- [ ] **Aspirational-peer seeding from answers (pre-approved)** (FILL) — `backend/app/services/onboarding.py` → research_roster (status='tracked', platform='unknown'; existing candidates upgraded not duplicated)
- [ ] **Async confirmed-research flow with double-run guard** (REPLACE) — `backend/app/routers/onboarding.py` → research API routes over brand_research.py (202 + status poll + 409 in-flight, on their API layer)
- [ ] **Auto-answer batch endpoints with stale-draft filtering** (REPLACE) — `backend/app/routers/onboarding.py` → intake API routes + brand_questions (202 batch, stale-draft filtering, 409 concurrent)
- [ ] **Auto-answer sweep saves interview budget** (REPLACE) — `backend/app/agents/interviewer.py` → intake_agent.py + brand_questions (state=auto_answered)
- [ ] **Batch auto-answer over all open questions** (REPLACE) — `backend/app/agents/answerer.py` → intake_agent.py + brand_questions; drafts stored on job-run record for review screen
- [ ] **Brand creation + question materialization (D3)** (REPLACE) — `backend/app/services/onboarding.py` → intake_agent.py on their tenant/brand creation; templates materialize into brand_questions
- [ ] **Citation validation gate on every extraction** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py extraction path; citations enforced into profile_fields envelope
- [ ] **Competitor candidates -> PeerEntity inserts (no metrics)** (FILL) — `backend/app/agents/researcher.py` → research_roster (0 rows): status='candidate' rows, deduped by (platform, handle)
- [ ] **Computed next-steps checklist** (MERGE) — `backend/app/routers/next_steps.py` → their onboarding-checklist (recomputed from live DB state on every call, no stored flags)
- [ ] **Confirmed research 7-lane parallel fan-out** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py / research.py (multi-lane D11 engine replaces the stub)
- [ ] **Connect-URL passthrough with stale-key self-heal** (MERGE) — `backend/app/routers/accounts.py` → postproxy.py + connections.py (white-label OAuth URL + mint-and-retry self-heal)
- [ ] **Cross-lane field merge with citation union** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py merge step; multi-citation bonus computed by envelope rubric
- [ ] **Deep-research lane, provenance-guarded** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py deep lane (their Perplexity pass demoted to one guarded lane)
- [ ] **Entity discovery ('Is this your brand?')** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py / research.py (supersedes their single Perplexity pass; candidates stay human-confirmed)
- [ ] **Exemplar quality gates + cost caps** (PORT) — `backend/app/agents/voice_harvester.py` → voice harvester module; caps into james-os config; dedupe against the shared corpus
- [ ] **Interview endpoints (next/suggest/answer)** (REPLACE) — `backend/app/routers/onboarding.py` → intake API routes over intake_agent.py + brand_questions
- [ ] **Interview question selection (3-tier priority)** (REPLACE) — `backend/app/agents/interviewer.py` → intake_agent.py + brand_questions (0 rows)
- [ ] **News lane (90-day lookback extraction)** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py news lane; cited fields -> profile_fields
- [ ] **Onboarding question cap (<=25) + budget switch** (REPLACE) — `backend/app/agents/interviewer.py` → intake_agent.py + brand_questions (cap keyed to brand/tenant onboarding status)
- [ ] **Onboarding-to-active advancement rule (single source)** (REPLACE) — `backend/app/services/onboarding.py` → intake_agent.py + brand_questions settle check (must-asks settled OR cap consumed)
- [ ] **Places lane for physical assets/institutions** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py places lane; channels.local.* -> profile_fields
- [ ] **Reddit lane: SERP-level community signal** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py reddit lane (SERP-only, secondary confidence) -> profile_fields
- [ ] **Research seed persistence** (REPLACE) — `backend/app/routers/onboarding.py` → brand settings (research_seed) on their tenant brand record
- [ ] **Spoken-voice harvest (transcribe own uploads)** (PORT) — `backend/app/agents/voice_harvester.py` → alongside voice_ingest.py (theirs uploads, ours auto-pulls); exemplars -> shared voice corpus tagged origin=harvested
- [ ] **Voice harvest endpoints (background + poll)** (PORT) — `backend/app/routers/voice.py` → voice API routes alongside voice_ingest.py (harvest/add-source background + GET voice profile/status)
- [ ] **Voice-profile distillation to voice.* fields** (PORT) — `backend/app/agents/voice_harvester.py` → profile_fields voice.* (source=researched, cited); complements their voice rules
- [ ] **Web lane: confirmed pages + angle searches** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py web lane; fields -> profile_fields envelope
- [ ] **Wikipedia lane with absence-as-finding** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py wiki lane; positioning.wikipedia_presence -> profile_fields
- [ ] **Written-voice harvest from own posts** (PORT) — `backend/app/agents/voice_harvester.py` → alongside voice_ingest.py; connected accounts via postproxy.py, fallback public handles
- [ ] **YouTube lane: locate-then-stat, never guess handles** (REPLACE) — `backend/app/agents/researcher.py` → brand_research.py youtube lane via ported providers.video (D9 1-unit endpoints)

### bm2.0 · platform

- [ ] **AI-ism lint lexicon (deterministic, pure)** (PORT) — `backend/app/data/ai_isms.py` → new pure module under their content QA, consumed by the voice-QA lint leg
- [ ] **Per-brand source contribution report** (PORT) — `backend/app/routers/sources.py` → provider layer + researcher job-run records (per-lane field counts mapped to streams)
- [ ] **james-os handoff export** (MERGE) — `backend/app/routers/brands.py` → brand_profiles flat VIEW/projection (the export shape becomes the internal projection; cross-project import door retires)

### bm2.0 · providers

- [ ] **GET /brands/{id}/sources — per-brand contribution** (PORT) — `backend/app/routers/sources.py` → james-os API sources route (tenant-scoped) — reads researcher-run lane_stats + profile_fields section counts
- [ ] **PostProxyConnector (primary social vendor)** (MERGE) — `backend/app/adapters/live.py` → james-os postproxy.py + connections.py — theirs keeps read/analytics; fold in connect-URL flow, group binding, post-history paging, and publish
- [ ] **ResendEmailProvider (live email hand)** (FILL) — `backend/app/adapters/live.py` → james-os outbox.execute_action — the email leg of the missing outbox executor
- [ ] **WebhookBlogPublisher (live blog hand)** (FILL) — `backend/app/adapters/live.py` → james-os outbox.execute_action — the blog leg of the missing outbox executor (fills PRD R8.1)

### james-os · analytics

- [ ] **PostProxy unified social-API client (11 platforms)** (MERGE) — `src/james_os/postproxy.py` → src/james_os/postproxy.py — read/analytics side survives; bm2.0 adds the white-label connect-URL flow + PUBLISH path (theirs is read-only today), feeding the outbox executor
- [ ] **Unified connections view (Meta + PostProxy merged)** (MERGE) — `src/james_os/connections.py` → src/james_os/connections.py — bm2.0 connect flow folds into the unified profile shape; best-effort merged listing survives

### james-os · intelligence

- [ ] **Agentic intake — interviewer agent (10,000-question interview)** (REPLACE) — `src/james_os/intake_agent.py` → src/james_os/intake_agent.py — bm2.0 interviewer + seeded question bank (batch auto-answer + review) replaces the dormant 10-dimension generator; post-intake cadence re-registered on the scheduler
- [ ] **Agentic intake — researcher agent ('is this your brand?')** (REPLACE) — `src/james_os/intake_agent.py` → src/james_os/intake_agent.py — bm2.0's live/tested answerer + 'is this your brand?' research agent ports onto their tables (brand_questions); dormant stub retired
- [ ] **Brand profile store (who this tenant IS)** (MERGE) — `src/james_os/brands.py; src/james_os/main.py` → brand_profiles (src/james_os/brands.py) — becomes the flat snapshot projection/VIEW over the new PORTed profile_fields append-only envelope; existing readers (content/autopilot/Ask) keep working unchanged
- [ ] **Web research provider (Perplexity sonar + stub)** (REPLACE) — `src/james_os/research.py; src/james_os/main.py` → src/james_os/research.py — bm2.0 D11 multi-lane researcher (web/news/wiki/youtube/places/reddit/deep, provenance rules) lands here; findings stay category:research events; single Perplexity pass retired
- [ ] **brand_questions ledger -> memory filing** (FILL) — `src/james_os/intake_agent.py; src/james_os/main.py` → brand_questions table (0 rows) + /intake/questions confirm/dismiss endpoints — bm2.0 question bank and confirm/correct flow fill it; confirmed answers still file as citable events

### james-os · memory

- [ ] **Durable voice ingest jobs API** (KEEP) — `src/james_os/voice_ingest_api.py` → stays: src/james_os/voice_ingest_api.py
- [ ] **Voice Studio — Drive folder ingest to voice corpus** (KEEP) — `src/james_os/voice_ingest.py` → stays: src/james_os/voice_ingest.py (bm2.0 Voice Harvester PORTs alongside as the auto-pull door; one voice_corpus tagged by origin)

### james-os · platform

- [ ] **Whisper transcription service** (KEEP) — `src/james_os/transcription.py` → stays: src/james_os/transcription.py (ported bm2.0 voice harvester reuses it for its transcript path)

### james-os · production

- [ ] **Independent voice-QA gate (second LLM)** (KEEP) — `src/james_os/content.py` → stays: src/james_os/content.py voice-QA gate — merge TARGET: absorbs bm2.0 lint + learned-guardrails + fact-check reviewer legs
- [ ] **On-voice content engine (memory-grounded writing)** (KEEP) — `src/james_os/content.py` → stays: src/james_os/content.py — merge TARGET: absorbs bm2.0 blog/email hands + rewrite door (their voice engine is stronger, 1,344 exemplars)

## P5 — Unified frontend (bm2.0 design language) — 90 rows


### bm2.0 · frontend

- [ ] **5-step onboarding wizard with progress stepper** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — supersedes their intake flow, restyled to unified design
- [ ] **AnswerReview — batch review of AI-drafted answers** (REPLACE) — `frontend/components/answer-review.tsx` → james-os intake_agent review flow UI — drafted-answer review over brand_questions
- [ ] **Approval queue page — triage, draft, approve/reject** (MERGE) — `frontend/app/brand/[id]/queue/page.tsx` → james-os approval queue UI (theirs is stronger, survives restyled) — fold in D7 review badges, reject-reason capture, draft-content trigger
- [ ] **Async UI primitives + data-loading hook** (PORT) — `frontend/components/use-load.ts` → shared frontend lib in james-os web (useLoad + Loading/ErrorBox/EmptyState)
- [ ] **Auditor baseline per platform** (PORT) — `frontend/app/brand/[id]/page.tsx` → Manager area — Accounts section, audit baseline cards over connections.py analytics
- [ ] **Back-to-top control (BackToTop)** (PORT) — `frontend/components/section-nav.tsx` → Manager area in james-os shell — shared nav component
- [ ] **Batch AI auto-answer ('✨ Draft all with AI')** (REPLACE) — `frontend/app/brand/[id]/page.tsx` → james-os intake_agent answerer flow UI (intake + onboarding-checklist) — batch auto-answer is part of the intake replacement
- [ ] **Brand Voice panel — harvest, poll, add source** (PORT) — `frontend/components/voice-panel.tsx` → Manager area — Brand Voice section, alongside their voice_ingest upload door (one corpus, origin-tagged)
- [ ] **Brand layout with view tabs and status badges** (PORT) — `frontend/app/brand/[id]/layout.tsx` → new Manager area layout inside james-os web shell (their shell/routes survive restyled)
- [ ] **Citation chips with favicons** (PORT) — `frontend/components/citations.tsx` → shared design-system components in james-os web — citation rendering for suggestions/prescriptions/profile fields
- [ ] **Collaboration & growth panel** (PORT) — `frontend/components/collaboration-panel.tsx` → Manager area — Competitors/growth section; plays land as action_items (new P1 migration)
- [ ] **Competitors & peers panel — discover → approve → track** (PORT) — `frontend/components/competitor-panel.tsx` → Manager area — Competitors section over research_roster + peer_snapshots (0-row landing zones)
- [ ] **Connected accounts: OAuth connect, sync, and aggregator group picker** (MERGE) — `frontend/app/brand/[id]/page.tsx` → james-os connections UI over postproxy.py + connections.py — adds connect-URL/group-bind flow to their read/analytics layer
- [ ] **Daily brief panel — run/re-run today's cycle** (PORT) — `frontend/components/daily-brief-panel.tsx` → Manager area — Today section, reading the daily-cycle job registered on their scheduled_jobs scheduler
- [ ] **Follow-ups panel — trackable action items** (PORT) — `frontend/components/followups-panel.tsx` → Manager area — Follow-ups section over action_items (new P1 migration)
- [ ] **Growth intelligence + today's plan panel** (PORT) — `frontend/components/growth-plan-panel.tsx` → Manager area — growth/daily-plan section over strategy.py replacement + prescriptions
- [ ] **Inline dashboard interview inside Next steps** (REPLACE) — `frontend/app/brand/[id]/page.tsx` → james-os web onboarding-checklist — answer_questions step backed by brand_questions
- [ ] **Intelligence radar — 5 scanning lanes** (PORT) — `frontend/components/radar-panel.tsx` → Manager area — Radar section over the 5 eyes filling content_suggestions + their scheduler registry
- [ ] **Intelligence sources card (D12 stream inventory)** (PORT) — `frontend/app/brand/[id]/page.tsx` → Manager area — sources card reading the ported sources router (provider-layer observability)
- [ ] **Morning Brief card (Strategist)** (PORT) — `frontend/app/brand/[id]/page.tsx` → Manager area — Today section, strategist brief (strategy.py replacement output)
- [ ] **Next-steps setup checklist card** (REPLACE) — `frontend/app/brand/[id]/page.tsx` → james-os web onboarding-checklist component — superseded by our checklist in the Manager area
- [ ] **North Star panel — negotiate growth targets** (PORT) — `frontend/components/north-star-panel.tsx` → Manager area — North Star section over goal agent (strategy.py replacement / prescriptions)
- [ ] **Peer-Agent digest (benchmarks + observations)** (PORT) — `frontend/app/brand/[id]/page.tsx` → Manager area — Competitors section, benchmark digest over peer_snapshots
- [ ] **Provenance and status badge kit** (PORT) — `frontend/components/badges.tsx` → shared design-system components in james-os web — part of the winning bm2.0 token set
- [ ] **QuestionCard with '✨ Suggest an answer' agent** (REPLACE) — `frontend/components/question-card.tsx` → shared component in james-os web/app/intake + Manager area — interview UI over brand_questions
- [ ] **Step 1 basics form (name, entity type, website, socials)** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — step 1 of replacement intake flow (feeds intake_agent replacement)
- [ ] **Step 2 entity disambiguation candidate picker** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — 'is this your brand?' step (part of intake_agent replacement)
- [ ] **Step 3 background confirmed-research run with status polling** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — research step over the brand_research.py replacement (D11 lanes)
- [ ] **Step 3 research-results review grouped by profile section** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — research review step reading profile_fields envelope (new migration)
- [ ] **Step 4 onboarding interview** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — interview step backed by brand_questions (0-row table)
- [ ] **Step 5 brand-voice reveal** (REPLACE) — `frontend/app/onboard/page.tsx` → james-os web/app/intake — Brand Voice step (embeds ported VoicePanel)
- [ ] **Sticky section jump-nav with scroll-spy (SectionNav)** (PORT) — `frontend/components/section-nav.tsx` → Manager area in james-os shell — pill section-nav (part of winning design token set)
- [ ] **The Work — execution spine panel** (MERGE) — `frontend/components/execution-panel.tsx` → james-os actions queue UI (survives, restyled) — extended with publish/measure controls and published/measured statuses
- [ ] **Typed API client for the whole backend surface** (MERGE) — `frontend/lib/api.ts` → james-os web API client layer — fold in typed wrappers, ApiError, 202/409 background-job conventions against merged james-os routes
- [ ] **WCAG skip-link and focusable main content** (PORT) — `frontend/app/brand/[id]/layout.tsx` → james-os web shell layout — WCAG pattern applied across all surfaces per unified design
- [ ] **Weekly plan page — draft, review, activate** (PORT) — `frontend/app/brand/[id]/plan/page.tsx` → Manager area — weekly plan page over strategist (strategy.py replacement) + prescriptions evidence[] format

### james-os · frontend

- [ ] **Analytics chart primitives (SVG, no deps)** (KEEP) — `web/components/analytics-charts.tsx` → stays: web/components/analytics-charts.tsx (retokened to bm2.0 CSS variables, API unchanged)
- [ ] **Analytics dashboard** (KEEP) — `web/app/analytics/page.tsx` → stays: web/app/analytics/page.tsx (restyled; later reads published/measured impact data from the merged actions queue)
- [ ] **App shell + concept-grouped sidebar** (KEEP) — `web/components/shell.tsx` → stays: web/components/shell.tsx (explicitly: shell/routes survive restyled in bm2.0 design language; sidebar gains concept entries for ported bm2.0 dashboard/onboarding routes)
- [ ] **Approval Queue** (KEEP) — `web/app/queue/page.tsx` → stays: web/app/queue/page.tsx (their approval queue UI wins; status chips/filters extended with published/measured as the bm2.0 WorkOrder lifecycle merges into the actions queue; rejection-reason guardrails feed the merged voice-QA gate)
- [ ] **Ask/Do agent console (home)** (KEEP) — `web/app/page.tsx` → stays: web/app/page.tsx (restyled; remains the unified home over the surviving memory substrate and agent_runs)
- [ ] **Audio Library (music + SFX)** (KEEP) — `web/app/audio/page.tsx` → stays: web/app/audio/page.tsx
- [ ] **Autopilot batch content generation** (KEEP) — `web/app/autopilot/page.tsx` → stays: web/app/autopilot/page.tsx (drafts keep landing in the Approval Queue; content engine gains bm2.0 blog/email hands behind it)
- [ ] **B-roll Library** (KEEP) — `web/app/broll/page.tsx` → stays: web/app/broll/page.tsx
- [ ] **Brand Brain (voice rules & plug-ins)** (KEEP) — `web/app/brand/page.tsx` → stays: web/app/brand/page.tsx (their voice engine wins; bm2.0 reviewer legs merge in behind the voice-QA gate, not into this page)
- [ ] **Brand Setup intake form** (REPLACE) — `web/app/intake/page.tsx` → web/app/intake route — replaced by the ported bm2.0 onboarding flow (frontend/app/onboard) fronting the live intake/interview engine that supersedes the dormant intake_agent; brand_questions becomes a bm2.0-filled zone
- [ ] **CaptionPicker (caption style chips)** (KEEP) — `web/components/caption-picker.tsx` → stays: web/components/caption-picker.tsx
- [ ] **ConnectedAccounts (authenticated profiles panel)** (KEEP) — `web/components/connected-accounts.tsx` → stays: web/components/connected-accounts.tsx
- [ ] **Content Library (footage + auto-clipper dashboard)** (KEEP) — `web/app/content-library/page.tsx` → stays: web/app/content-library/page.tsx
- [ ] **Content Studio (post + image / multi-platform)** (KEEP) — `web/app/design-studio/page.tsx` → stays: web/app/design-studio/page.tsx (voice-QA score it displays gains bm2.0 lint/learned-guardrail/fact-check legs server-side)
- [ ] **Create hub (single front door)** (KEEP) — `web/app/create/page.tsx` → stays: web/app/create/page.tsx (its suggestions strip switches to the bm2.0-filled content_suggestions feed)
- [ ] **Engaging Reel maker (avatar + B-roll punctuation)** (KEEP) — `web/app/engaging-video/page.tsx` → stays: web/app/engaging-video/page.tsx
- [ ] **Help drawer ('How it works' tutorials)** (KEEP) — `web/components/help-drawer.tsx` → stays: web/components/help-drawer.tsx (TUTORIALS map in web/lib/tutorials.ts extended with entries for ported bm2.0 pages)
- [ ] **Hero Library (hero photos/videos + vision description)** (KEEP) — `web/app/hero/page.tsx` → stays: web/app/hero/page.tsx
- [ ] **HeyGen Video maker** (KEEP) — `web/app/heygen-video/page.tsx` → stays: web/app/heygen-video/page.tsx
- [ ] **HubTabs (page-cluster tab strips)** (KEEP) — `web/components/hub-tabs.tsx` → stays: web/components/hub-tabs.tsx (tab arrays extended to include ported bm2.0 dashboard/onboarding routes)
- [ ] **ImageStylePicker (B-roll still style chips)** (KEEP) — `web/components/image-style-picker.tsx` → stays: web/components/image-style-picker.tsx
- [ ] **JP Live brand health status** (KEEP) — `web/app/jp-live/page.tsx` → stays: web/app/jp-live/page.tsx (gains rows for the new bm2.0 eyes/heartbeat jobs)
- [ ] **Knowledge Base (docs, ask, white papers, briefs, commitments)** (KEEP) — `web/app/knowledge/page.tsx` → stays: web/app/knowledge/page.tsx (memory substrate, extraction and retrieval all KEEP)
- [ ] **Login** (KEEP) — `web/app/login/page.tsx` → stays: web/app/login/page.tsx (auth/tenancy/RLS survive as-is)
- [ ] **Long Form Cutter** (KEEP) — `web/app/long-form/page.tsx` → stays: web/app/long-form/page.tsx
- [ ] **Market Research (trends + topic research)** (KEEP) — `web/app/market-research/page.tsx` → stays: web/app/market-research/page.tsx (live Apify watchlist/viral-trend scraping is unique here; the ported bm2.0 Intelligence Radar lands as a sibling dashboard surface, not over this route)
- [ ] **MediaTabs (media library tab strip)** (KEEP) — `web/components/media-tabs.tsx` → stays: web/components/media-tabs.tsx
- [ ] **Morning Brief** (FILL) — `web/app/brief/page.tsx` → web/app/brief/page.tsx — dormant surface over the prescriptions/content_suggestions landing zones; goes live when the bm2.0 brain fills them (data in P3, restyle in P5)
- [ ] **OnboardingChecklist** (KEEP) — `web/components/onboarding-checklist.tsx` → stays: web/components/onboarding-checklist.tsx (gains a 'complete brand onboarding' step pointing at the ported bm2.0 onboarding flow that replaces /intake)
- [ ] **Output Library (finished videos + approved posts)** (KEEP) — `web/app/library/page.tsx` → stays: web/app/library/page.tsx
- [ ] **Post Images (AI hero image generator + library)** (KEEP) — `web/app/images/page.tsx` → stays: web/app/images/page.tsx
- [ ] **Profile (account + password)** (KEEP) — `web/app/profile/page.tsx` → stays: web/app/profile/page.tsx
- [ ] **Reference Library (clips / style refs / B-roll)** (KEEP) — `web/app/jp-clips/page.tsx` → stays: web/app/jp-clips/page.tsx
- [ ] **RenderTracker (live render progress)** (KEEP) — `web/components/render-tracker.tsx` → stays: web/components/render-tracker.tsx
- [ ] **Settings (API keys, memory uploads, voice rules, connections)** (KEEP) — `web/app/settings/page.tsx` → stays: web/app/settings/page.tsx (encrypted key store and connection health checks survive as-is)
- [ ] **Shared UI kit and small primitives** (MERGE) — `web/components/ui.tsx` → web/components/ui.tsx — component API (Card/Button/Badge/PageHeader/Toast/FilterChip/Skeleton/icons) survives so every page keeps compiling, while bm2.0's design language (globals.css tokens, tones, type scale) folds into the primitives; this is the single choke point where 'bm2.0 design wins everywhere' lands
- [ ] **Signup** (KEEP) — `web/app/signup/page.tsx` → stays: web/app/signup/page.tsx (tenancy claiming logic survives as-is)
- [ ] **Social Companion (watchlist alias)** (KEEP) — `web/app/social-companion/page.tsx` → stays: web/app/social-companion/page.tsx (thin redirect alias, zero-cost to keep for old bookmarks)
- [ ] **Social Listening (brand mentions)** (KEEP) — `web/app/social-listening/page.tsx` → stays: web/app/social-listening/page.tsx (live on-demand Xpoz mention search; bm2.0's scheduled appearances/press eyes run alongside as scanners, different modality)
- [ ] **Story Reel (mix) maker** (KEEP) — `web/app/story-mix/page.tsx` → stays: web/app/story-mix/page.tsx
- [ ] **Story Video maker (voice-driven slideshow)** (KEEP) — `web/app/story-video/page.tsx` → stays: web/app/story-video/page.tsx
- [ ] **Style Templates** (KEEP) — `web/app/style-templates/page.tsx` → stays: web/app/style-templates/page.tsx
- [ ] **Timeline Editor (Creatomate stitching)** (KEEP) — `web/app/editor/page.tsx` → stays: web/app/editor/page.tsx
- [ ] **TrendCard (viral post card + make-script action)** (KEEP) — `web/components/trends.tsx` → stays: web/components/trends.tsx (continues to serve /market-research; reusable for bm2.0 radar trend-lane items)
- [ ] **TrimBox (inline post-render trim)** (KEEP) — `web/components/trim-box.tsx` → stays: web/components/trim-box.tsx
- [ ] **Updates ('What's changing next' roadmap)** (KEEP) — `web/app/updates/page.tsx` → stays: web/app/updates/page.tsx (its feedback-interpretation loop is the surface the bm2.0 learned-guardrails leg merges behind)
- [ ] **Video Studio hub landing** (KEEP) — `web/app/video/page.tsx` → stays: web/app/video/page.tsx
- [ ] **Video Studio pipeline (composer / producer / clip)** (KEEP) — `web/app/pipeline/page.tsx` → stays: web/app/pipeline/page.tsx
- [ ] **VideoEditor (scene-based composer editor)** (KEEP) — `web/components/video-editor.tsx` → stays: web/components/video-editor.tsx
- [ ] **Voice Studio (voice corpus ingestion)** (KEEP) — `web/app/voice-studio/page.tsx` → stays: web/app/voice-studio/page.tsx (bm2.0 voice harvester PORTs alongside voice_ingest as an additional intake path surfaced here)
- [ ] **WatchlistEditor (creator cohort tracker)** (KEEP) — `web/components/watchlist-editor.tsx` → stays: web/components/watchlist-editor.tsx
- [ ] **Weekly Thesis developer** (KEEP) — `web/app/thesis/page.tsx` → stays: web/app/thesis/page.tsx (whitepaper/content-pack/podcast production chain KEEPs end-to-end)

### james-os · intelligence

- [ ] **Perception layer — video style fingerprinting** (KEEP) — `src/james_os/perception.py` → stays: src/james_os/perception.py (video production is KEEP-THEIRS; exercised during P5 restyle of production pages)

### james-os · platform

- [ ] **Speaker directory (reusable lower-third name tags)** (KEEP) — `src/james_os/speakers.py; src/james_os/main.py` → stays: src/james_os/speakers.py; src/james_os/main.py (production surface, exercised during P5 restyle)
