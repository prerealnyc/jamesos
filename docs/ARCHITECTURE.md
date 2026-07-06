# Brand Manager OS — Technical Architecture Proposal

**Version:** 1.0 · **Date:** 2026-07-07 · Companion to [PRD.md](PRD.md) and
[ROADMAP.md](ROADMAP.md).

The architecture principle, answering the meeting's central question
("intelligence platform vs brand manager"): **there is one system.** The
intelligence layer was ported natively into Brand Manager and the corpus
migrated; "connecting the two platforms" is no longer a design problem.
Everything below extends one multi-tenant substrate.

---

## 1. Current architecture (as built)

```
┌────────────────────────── Railway ──────────────────────────┐
│  ┌───────────────┐        ┌─────────────────────────────┐   │
│  │ Next.js 14 UI │──rewrites──► FastAPI backend (Python) │   │
│  │ (james-os-    │  same   │  src/james_os/*             │   │
│  │  frontend)    │  origin │  session-cookie auth        │   │
│  └───────────────┘        └──────────┬──────────────────┘   │
└──────────────────────────────────────┼──────────────────────┘
                                       │ asyncpg (tenant-bound)
                    ┌──────────────────▼──────────────────┐
                    │  Supabase Postgres  (james-os)      │
                    │  • every table: tenant_id + FORCE   │
                    │    RLS via app.current_tenant       │
                    │  • events: vector(1024) HNSW +      │
                    │    GIN FTS  (the MEMORY substrate)  │
                    │  • document_metadata / silos /      │
                    │    entities / commitments /         │
                    │    clip_topics / actions / …        │
                    │  Storage: media (public), knowledge │
                    │  (private, signed URLs)             │
                    └─────────────────────────────────────┘

Providers (all behind thin adapters, keys in managed settings):
  LLM: Anthropic (default) / OpenAI · Embeddings: Voyage · Rerank: Cohere
  Research: Perplexity · Video: HeyGen, Runway, Higgsfield, Creatomate
  Voice: ElevenLabs (cloned voice) · Transcription: Whisper / AssemblyAI
  Social data: Xpoz, Meta APIs · Media processing: ffmpeg (in-container)
```

**Core mechanics worth preserving (they are the moat):**
- **Tenant binding:** `db.acquire(tenant_id)` resolves explicit arg →
  request contextvar → default, and sets `app.current_tenant` per
  transaction; detached jobs capture the tenant at spawn time. This is the
  entire multi-brand story at the data layer — already done.
- **Memory:** all knowledge (docs, briefs, voice exemplars, guardrails) are
  `events` rows with embeddings; retrieval is hybrid vector+FTS+rerank;
  Ask is cite-or-refuse with a second verification pass.
- **Learning loop:** approvals → voice exemplars; rejections/edits →
  frustration guardrails injected as `<avoid>` blocks + voice-QA gate.
- **Human gate:** every artifact is an `actions` row (status=pending) —
  the approval queue is the single choke point.
- **Job pattern:** long work = detached asyncio task + in-memory job dict +
  poll endpoint (gateway kills ~50s sync calls); tenant captured at start;
  CancelledError always flips jobs to failed.

## 2. Proposed additions (by roadmap milestone)

### 2.1 Brand identity layer (M1)

```
brand_profiles (NEW table)
  tenant_id PK/FK · kind (person|asset|institution|politician)
  identity jsonb   -- who/mission/positioning
  goals jsonb      -- ranked, measurable ("top search authority in X")
  pillars jsonb    -- topic mix targets (real estate 50%, mindset 20%, …)
  taboos jsonb · platforms jsonb · peers jsonb · constraints jsonb
  intake_version · updated_at
```

- **Intake** = a guided form writing `brand_profiles` + bulk-drop that
  routes files through the existing knowledge/asset ingest doors.
- **Injection points:** `build_content_system_prompt` (voice engine),
  autopilot ideation, topic-intelligence context, strategy engine. One
  profile feeds all engines — no per-engine duplication.
- **Provisioning:** admin endpoint creates tenant row + storage prefixes +
  operator invite (existing invite-code signup, scoped to the tenant).
  Per-brand login is already enforced by RLS; provisioning is UX, not
  security work.

### 2.2 Strategy engine (M2) — the "think" requirement

Three data products + one composer, all per-tenant:

```
platform_playbooks (NEW)          peer_snapshots (NEW)
  platform · brief_md               peer_handle · platform
  sources jsonb · version           cadence/format/topic stats jsonb
  refreshed_at                      captured_at

prescriptions (NEW)
  week_of · status (proposed|accepted|partial|expired)
  plan jsonb: [{platform, format, per_week, topics[], evidence[]}, …]
  growth_actions jsonb: [{action, why, evidence}]   -- podcast ladder, promote
  accepted_items jsonb
```

- **Playbook refresher** (scheduled): research job per platform →
  versioned brief; diff vs prior version → change notification. Cadence
  weekly; cheap re-check daily.
- **Peer bench** (scheduled): existing tracked-handle scraping aggregated
  into per-peer cadence/format stats.
- **Prescription composer** (weekly + on-demand): LLM pass over
  brand_profile + playbooks + peer_snapshots + analytics summaries +
  content inventory → `prescriptions` row. HARD RULE: every line carries
  `evidence[]` (playbook cite / peer stat / analytics datum).
- **Executor:** "accept" maps plan lines onto the existing autopilot batch
  API (volumes per format) — no new production machinery.

### 2.3 Press monitor (M3)

- Scheduled job (cron worker, below): scrape configured press pages
  (Firecrawl adapter) + mention search (Perplexity/news search) →
  candidate items → LLM triage (content-worthy? credibility-signal?
  which holding?) → (a) suggestion into approval queue, (b) row in
  `press_ledger` (NEW: url, outlet, date, subject_entity, signal_tier,
  content_worthy, reason) which feeds the trust-signal ladder and the
  Wikipedia notability dossier (M5).

### 2.4 Lessons pipeline (M3)

New render mode `lesson` reusing existing parts: transcript segmentation
(LLM: lesson boundaries + learning objectives) → per-segment narration
(existing TTS with cloned voice — or original audio when clean) → visual
plan (calculation graphics via the designed-image compositor; diagrams;
b-roll stills) → Creatomate assembly with intro/outro placeholder slots →
approval queue. Fits the existing `video_productions` mode enum + job
pattern.

### 2.5 Scheduler (M1–M3 prerequisite)

Today's jobs are in-process asyncio (fine for request-triggered work).
Recurring jobs (daily asset research, playbook refresh, press scan,
analytics pulls) need a **scheduler loop**: a single lightweight
asyncio-cron in the backend process reading a `scheduled_jobs` table
(tenant_id, kind, cadence, last_run, enabled). Rationale: one process on
Railway today — avoid premature Celery/RabbitMQ; the table + loop gives
durability across restarts and per-tenant fan-out. If job volume grows
(>50 tenants), promote to a separate Railway worker service consuming the
same table — no schema change.

### 2.6 Analytics maturation (M4)

- Scheduled per-tenant pulls → `post_metrics` (NEW: action_id/production_id,
  platform, captured_at, views/likes/comments/shares/saves).
- Attribution join: `actions.payload` already records format/topic/
  template/hook — a nightly rollup materializes `content_attribute_stats`
  per tenant.
- Strategy feedback: prescription composer consumes the rollup ("carousels
  outperform 3:1 for this brand").

### 2.7 Credit metering & packaging (M4–M5)

- `usage_events` (NEW): tenant_id, provider, operation, units, est_cost,
  ref (production/doc/job id), created_at. Written by the provider
  adapters (single choke point per adapter).
- Per-tenant caps in settings; enforcement = soft warning → hard queue-and-
  notify (never silent failure). Usage dashboard per tenant.
- Pricing later maps tiers → included `usage_events` budgets; billing
  provider (Stripe) integration is M5 and isolated behind one module.

## 3. Multi-brand isolation review (the packaging risk)

Already enforced: RLS on every table; per-tenant storage prefixes; learning
loop keyed per tenant; detached jobs tenant-bound at spawn.

To verify before external tenants (checklist, mostly audit not build):
1. Every NEW table above ships with the same tenant_id + FORCE RLS pattern.
2. In-memory job dicts key by job id (fine) but list endpoints must filter
   by the requester's tenant (audit existing + new).
3. Provider keys: today global (platform-pays model). If tenants bring their
   own keys later, move to per-tenant `credentials` rows — the managed-
   settings pattern already supports scoping.
4. Media URLs: public bucket assets are unguessable-UUID paths; knowledge
   is private+signed. Acceptable now; revisit public-bucket policy before
   politician-tier clients.

## 4. Build vs buy

| Capability | Position |
|---|---|
| Voice/LLM/video/TTS providers | Buy (already adapters); keep swappable |
| Scheduler | Build tiny (table + loop); no Celery until >50 tenants |
| Press scraping | Buy (Firecrawl adapter exists in ecosystem) |
| Billing | Buy (Stripe) at M5; metering built in-house (must be provider-agnostic) |
| Analytics ingestion | Build (platform APIs + existing scrapers); no 3rd-party analytics suite — attribution to our content attributes IS the product |

## 5. Non-goals / explicitly rejected

- **Separate "intelligence platform" service + API bridge** — rejected;
  parity port is done, one substrate.
- **Per-brand model fine-tuning** — the memory substrate (exemplars +
  guardrails + retrieval) already personalizes without training costs or
  cross-tenant risk.
- **Microservices split now** — one backend service + (eventually) one
  worker service; revisit at real multi-tenant load.
- **Auto-publishing** — the approval queue stays mandatory.

## 6. Risks & mitigations

1. **Single-process scheduling** — restart drops in-flight jobs → all jobs
   idempotent + resumable from their tables (pattern already used by
   migrations/ingest).
2. **Provider dependence** (HeyGen/ElevenLabs pricing or ToS shifts) —
   adapters keep swap costs low; metering (M4) quantifies exposure early.
3. **Prescription trust** — hard evidence requirement + human gate; the
   operator can always see *why*.
4. **Cost blowups from daily research jobs** — per-tenant caps precede any
   externally-billed tenant (M4.4 before 5.6).
