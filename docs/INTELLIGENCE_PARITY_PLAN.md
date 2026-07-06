# Intelligence → Brand Manager: Full Feature-Parity Porting Plan

**Goal:** every feature of the **PreReal Intelligence** platform exists inside
**Brand Manager (JAMES OS)**, rebuilt in BM's design — FastAPI/Python backend,
Next.js frontend, and BM's **multi-tenant `events` + RLS substrate** — so any
brand can upload its own company docs and get the full intelligence stack
(Ask, white papers, synthesis, governance). **No feature removed.**

**Constraints honored**
- **BM-native design.** No Next.js/TS bolt-on; everything is a BM Python module +
  BM UI, on BM's `tenant_id`/RLS substrate → multi-tenant + sellable from day one.
- **Nothing lost in BM.** All ports are additive (new modules + additive
  `extract_text` formats). No existing BM pipeline (content/clipper/video/
  autopilot/ask) is modified destructively.
- **PreReal stays standalone.** Its own repo/Supabase/deploy, unchanged — still
  James's real-estate tool and still separately sellable.
- **Reuse, don't duplicate.** Where BM already has a stronger equivalent (Ask,
  retrieval, research), we reuse it and only fill gaps.

---

## Data layer — where everything lives (verified against the code)

**One database: Brand Manager's own Supabase Postgres** (the `james-os` DB, same
substrate BM runs on today). Nothing points at PreReal's Supabase; documents are
imported and **re-embedded by BM's embedder at ingest** (we import documents, not
vectors — one embedder embeds both query and corpus, so provider/dimension drift
is a non-issue; BM = Voyage `voyage-3-large`, 1024-dim, model recorded on each row).

| Data | Where | Status | Isolation |
|---|---|---|---|
| Document chunks + embeddings (vector 1024) + FTS | **`events`** table (`event_type='document'`) — HNSW cosine + GIN FTS indexes already exist | **EXISTS — reuse** | tenant_id + FORCE RLS (already on) |
| Per-document metadata (BU, doc type, status, **sensitivity**, version, indexing status) | **`document_metadata`** | NEW (mig 045) | tenant_id + FORCE RLS |
| Entity registry (`[BU]-[TypeCode]-[NNNN]`) | **`entities`** | NEW (046) | tenant_id + FORCE RLS |
| Silos (knowledge-base buckets / corpus scoping) | **`silos`** | NEW (047) | tenant_id + FORCE RLS |
| Commitments (mined action items) | **`commitments`** | NEW (048) | tenant_id + FORCE RLS |
| Raw files (PDF/DOCX/audio bytes) | Supabase Storage `media` bucket, path `{tenant_id}/{uuid}.{ext}` | **EXISTS — reuse** | per-tenant path prefix |

Isolation mechanism (already live in BM): auth middleware → `db.acquire()` runs
`set_config('app.current_tenant', <uuid>, true)` per transaction → RLS policies
filter `tenant_id = current_setting('app.current_tenant')`, FORCE RLS so even the
table owner can't cross tenants. New tables copy the exact same pattern.

## Core engine — reuse vs must-port (the machinery, not just features)

**(a) Generic RAG core — BM already has it multi-tenant → REUSE wholesale:**
chunking (`documents.chunk_text`), embeddings (`embedder.py`), rerank
(`rerank.py`, Cohere v3.5), indexing (`ingestion.ingest_many` → events), hybrid
retrieval (`retrieval.py` vector+FTS), grounded answering (`ask.py` + `llm.py`).
Reusing this drops **zero** intelligence capability — PreReal's differentiation
was never this layer.

**(b) Intelligence-specific core — MUST PORT (TS → BM Python), ~11 modules:**

| PreReal source | → BM module | What it does |
|---|---|---|
| `research-planner.ts` | `research_planner.py` | LLM plans 3-6 tailored search directives from topic + corpus |
| `perplexity.ts` | `web_research.py` | sonar-pro client, cited briefings, rate-limited |
| `classify.ts` | `classify.py` | LLM → BU/asset/doc-type/sensitivity/entity + confidence |
| `resolve-entity.ts` + `entity-id.ts` | `entities.py` | findOrCreateEntity + stable ID minting |
| `constants.ts` | `vocab.py` | controlled vocabularies + sensitivity policy map |
| Ask sensitivity gating | `sensitivity.py` | internal-vs-public output policy injected at answer time |
| `whitepaper/route.ts` | `whitepaper.py` | corpus assembly → structure-learning → cited sections |
| `intelligence.ts` | `intelligence.py` | multi-angle sweep → synthesis → decision brief |
| `research-store.ts` | `research_store.py` | research briefs saved back as queryable corpus |
| `extract-commitments.ts` | `commitments.py` | mine action items post-index |
| `ingest.ts` orchestration | fold into `documents.py` | canonical filename, versioning, classify-then-index |

---

## Feature-parity matrix — all 21 PreReal features → their BM home

Legend: **REUSE** = BM already has it (map onto it) · **PORT** = build natively in
BM · **EXTEND** = reuse BM's + add a gap.

### A. Document intake (the common upload door)
| # | PreReal feature | BM landing | How | Effort |
|---|---|---|---|---|
| 1 | Single-file upload + collision-safe versioning | PORT | `documents.py` upload path (mirror media-upload pattern) → tenant Supabase storage | S |
| 2 | Batch ZIP ingest (≤60 files, 2 workers) | PORT | extend upload to unpack ZIP + queue | S |
| 3 | Multi-format extraction: PDF, DOCX, **PPTX, XLSX, HTML, audio(Whisper), image(OCR)** | EXTEND | extend `documents.extract_text` (has pdf/docx) → +pptx(`python-pptx`), +xlsx(`openpyxl`), +html, +audio(`transcription.py`), +OCR(`perception.py`) | M |
| 4 | Naming Convention v1.0 (8-slot filename) | PORT | `naming.py` port of `filename.ts` + controlled vocab from constants | S |
| 5 | AI auto-classification + confidence-gated review flag | PORT | `classify.py` — LLM → controlled vocab, low-confidence → review | M |
| 6 | Storage path `{BU}/{EntityID}/{filename}` | EXTEND | tenant-scoped path structure in `storage_supabase.py` | S |

### B. Domain model / governance
| # | PreReal feature | BM landing | How | Effort |
|---|---|---|---|---|
| 7 | Entity registry — stable EntityIDs, hierarchy, auto-number | PORT | `entities` table (tenant-scoped) + `entities.py` + endpoints | M |
| 8 | Silos — project/topic groupings + stats + scoping | PORT | `silos` table + `silos.py`; scope retrieval/Ask by silo | M |
| 9 | Sensitivity model — Public/Shareable/Restricted/NDA-Protected | PORT | `sensitivity` on documents + enforce in Ask + generation | M |
| 10 | Commitments — action items (owner/due/status) + AI extraction | PORT | `commitments` table + `commitments.py` + LLM extract from docs/meetings | M |
| 11 | Guidelines/Rules engine — corrections → reusable rules, silo-scoped | REUSE+EXTEND | BM `plug_ins` (framework/guideline/frustration) + `feedback_changes.py`; add silo scope | S |

### C. Retrieval & generation
| # | PreReal feature | BM landing | How | Effort |
|---|---|---|---|---|
| 12 | Vector index + rerank semantic search | REUSE | `retrieval.py` (hybrid vector+FTS+Cohere rerank); index docs into it | — |
| 13 | Ask/RAG — grounded, cited, **sensitivity-aware**, research gap-fill | REUSE+EXTEND | `ask.py` (stronger: 2-pass verify) + add sensitivity filter + `research.py` gap-fill | S |
| 14 | **White paper generation** — structure-learning + multi-section synthesis | PORT | `whitepaper.py` on the tenant corpus → `{title,abstract,sections,takeaways,citations}` | M |
| 15 | Topic Intelligence + synthesis — coverage detection + multi-angle planner | PORT | `intelligence.py` — research planner + gap detection → decision brief | M |
| 16 | Cross-silo portfolio synthesis — patterns/synergies/tensions | PORT | `synthesize.py` across silos | M |
| 17 | Web research (Perplexity, persisted) | REUSE | `research.py` (provider-abstracted, ingests to memory) | — |
| 18 | Documents API — entity-scoped programmatic retrieval | PORT | `/documents` query endpoints (by entity/silo/type) | S |
| 19 | Full-text metadata search + rich filters | REUSE+EXTEND | BM FTS + metadata filters (entity/silo/type/sensitivity) | S |
| 20 | File download via signed URLs | REUSE | storage signed-URL download | — |
| 21 | Per-client rate limiting on expensive endpoints | REUSE | BM middleware | — |

### D. UI — in BM's design (Next.js shell, BM components)
- **Knowledge Base** hub — the common upload door: drop docs, see the corpus, per-doc status. (new nav item)
- **Ask** — reuse BM's Ask page, scoped to the brand's docs.
- **White Papers** — generate, view, "→ turn into content."
- **Silos / Entities / Sensitivity** — governance management.
- **Commitments** board · **Search** page.

---

## Phased sequence (value early; foundation before white papers)

- **P1 — Document-intake foundation** *(the corpus white papers need)*: features
  1,2,3,6 + index into memory (12) + Ask-over-docs (13) + download (20) + the
  **Knowledge Base upload UI**. → a brand uploads docs and asks them. *(~1 wk)*
- **P2 — White papers** (14) + thesis→paper→**content** wiring (BM content/clipper
  already exist). → the CEO's ask, end-to-end. *(~3–5 d)*
- **P3 — Governance & structure**: entities (7), silos (8), sensitivity (9),
  naming (4), classify (5), metadata search (18,19). *(~1–2 wk)*
- **P4 — Deep intelligence**: topic-intelligence (15), cross-silo synthesis (16),
  commitments (10), guideline silo-scoping (11). *(~1–2 wk)*
- **Cross-cutting** (every phase): multi-tenant `tenant_id`/RLS, rate limiting (21)
  reuse, feature-flag the module for tier-gating.

---

## Parity checklist — nothing removed
Upload✔(1,2) · Extraction-all-formats✔(3) · Naming✔(4) · Auto-classify✔(5) ·
Storage-paths✔(6) · Entities✔(7) · Silos✔(8) · Sensitivity✔(9) · Commitments✔(10)
· Guidelines-engine✔(11) · Vector+rerank✔(12) · Ask/RAG✔(13) · **White papers✔(14)**
· Topic-intelligence✔(15) · Cross-silo synthesis✔(16) · Web research✔(17) ·
Documents-API✔(18) · Metadata-search✔(19) · Signed-download✔(20) · Rate-limit✔(21).

**End state:** 100% of PreReal Intelligence's capability lives in BM, multi-tenant,
in BM's design — plus everything BM already had.
