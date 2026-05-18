# JP Brand Manager — Master Specification

**Built:** 2026-05-09 12:25 PM ET
**Authority:** James Prendamano. This is the spec.
**Architecture target:** LangGraph orchestrator + CrewAI agents + LangSmith observability + Pinecone (or pgvector) RAG store, deployed on Modal/Railway/Vercel/equivalent for 24/7 always-on operation.
**Replaces:** prior implementation attempts inside the Perplexity Computer substrate, which proved structurally insufficient for stateful multi-step agentic enforcement (see `JAMES_FRUSTRATION_DRIFT_LEDGER_2026-05-04.md` Pattern J + 2026-05-09 architectural reframe in `JAMES_THE_FULL_VISION.md`).

---

## Mission (one sentence)

Amplify James Prendamano as the trusted, no-BS visionary who closes massive deals, builds purpose-driven communities, and drives real change in real estate, education, and civic leadership — by running an always-on orchestrator that ingests his digital footprint, generates content at his quality bar, distributes across platforms with human approval gates, monitors brand health, and never silently drifts.

---

## Brand identity (locked, do not paraphrase in production)

- **Geographic spine:** Staten Island closer → New Mexico visionary (Sierra County / Spaceport America / Elephant Butte)
- **Professional spine:** PreReal commercial real estate, purpose-driven community development, term-limit advocacy, education reform (Prendamano Academy)
- **Voice spine:** First-person Neville-Goddard-influenced declarations + Staten-Island-direct + numbers-first authority + zero corporate jargon
- **Track record:** Documented predictions archive (`Predictions.docx`) — the receipts. Every position taken can be timestamped against when mainstream caught up.
- **The four heads** (the underlying architecture for everything below):
  - Head 1 — Memory (LLM ingestion infrastructure)
  - Head 2 — Vision (founder's thesis + platform vision interview)
  - Head 3 — Operating Protocol (75 protocols, 30 code-enforced + 16 monitor-only + 29 retired)
  - Head 4 — QA Layer (drift/frustration auditing system)

---

## Section 1 — Knowledge & Intelligence Engine

### What it does
Continuously ingests and indexes everything published by, about, or relevant to James/PreReal. Maintains a queryable, citation-backed RAG layer that any other agent can consult.

### Data sources (priority-ordered)
1. **Owned channels:** Instagram, TikTok, X/Twitter, Facebook, LinkedIn, Threads, YouTube, podcasts (Predictions of a Real Estate Disaster), Substack (June 2026 launch), PreReal Weekly newsletter, Academy newsletter
2. **Existing corpora (already in workspace, ~10 MB):**
   - `prereal_transcripts.docx` — YouTube + interview transcripts
   - `academy_training_text.txt` — Academy modules
   - `podcast_full_context.txt` — speaker-tagged podcast (6.6 MB)
   - `Predictions.docx` — predictions archive
   - `neville_goddard_first_person.txt` — voice declarations
   - `MAX_OPERATING_PROTOCOL.md` — 392 KB of operating context
   - `JAMES_THE_FULL_VISION.md` — founder's thesis with 2026-05-03/05/09 overlays
3. **Press / news:** Real estate trade press, NM political/civic news, education reform news, mindset / self-help discourse — primary sources only (per James's standing rule)
4. **Peer cohort:** 5-10 accounts per pillar — RE thought leaders, NM political voices, mindset/Neville-adjacent figures (cohort to be locked with James, James-approved per P47)
5. **Conversation history:** Daily backups of every James-Max session (currently captured to `chat_backups/`), tagged for voice ingestion

### Cadence
- Real-time webhook ingestion for new owned-channel posts (Meta Graph subscriptions, YouTube push)
- Hourly polling for sources without webhooks (Substack, podcast RSS, peer accounts via xpoz/Meta)
- Daily full reconciliation pass (catches anything webhooks missed)
- Weekly peer cohort metric refresh
- Monthly corpus integrity audit (no rows lost, no duplicates, hash-chain verified)

### Storage architecture
- **Vector store:** Pinecone (or Postgres + pgvector for cost) — chunked, embedded with `text-embedding-3-large`, ~512-token chunks with 64-token overlap
- **Structured store:** Postgres — posts, engagement, peer snapshots, predictions, content calendar, approval queue, voice corrections
- **Object store:** S3 (or equivalent) — raw audio/video, generated images, podcast clips, HeyGen renders, originals of every published asset
- **Hash chain:** every ingest event appended to a tamper-evident log (port `enforcement/qa/hash_chain.py` pattern)

### Outputs
- "Ask the corpus" tool every other agent can call. Returns timestamped, source-cited answers.
- Weekly "Knowledge Health" report: ingest cadence, coverage gaps, source freshness
- Always-current Founder's Thesis embedding so any agent can ground claims

---

## Section 2 — Brand Strategy & Positioning

### What it does
Owns the narrative spine. Maintains the rolling 90-day content & campaign calendar. Decides what topics, deals, and themes get amplified when. Ensures every output ladders up to the brand identity above.

### The narrative spine (locked)
*"The Staten Island closer who is redefining purpose-driven real estate and education at scale."*

Every piece of content must be classifiable into one of four pillars:
- **Real estate thought leadership** — deals, market calls, predictions cashing in, NM growth
- **Term limits / civic reform** — convergent-thinking content, accountability themes
- **Education reform** — Prendamano Academy, future-of-work, curriculum
- **Mindset / Neville Goddard / first-person declaration content** — the voice underlay

### Cadence
- 90-day rolling content calendar regenerated weekly
- Tied to: current deals (e.g., Spaceport-adjacent), macro trends, media/PR opportunities, scheduled appearances
- Locked content slots vs. opportunistic slots — the calendar names which is which

### Outputs
- `content_calendar.json` (current week + 90 days forward)
- Weekly "Strategy Brief" (1 page max per P70) summarizing what to lean into and why

---

## Section 3 — Content Creation at James's Quality Level

### What it does
Generates full-fidelity drafts: posts, threads, video scripts, podcast outlines, newsletter sections, LinkedIn articles, Substack long-form, scripts for HeyGen avatar renders. Each draft is scored by a deterministic voice-fidelity scorer before it can advance.

### The hard gate (this is non-negotiable)
**Every draft must score ≥ threshold X on `voice_fidelity.py` (already built, 25/25 tests pass). Drafts below threshold are returned to the writer agent with the specific failing dimensions named.** Three retries max, then escalate to human.

Score spread validated 2026-05-08:
- Corporate slop: 22
- Real high-engagement IG post: 40-52
- Simulated good (target): 78
- Threshold X: starts at 50, James-tunable

### Multi-model routing (honors P75)
Different formats route to different models, picked for quality/cost:
- Long-form (Substack, LinkedIn articles, podcast outlines): Opus 4.7
- Short-form social (IG, X, Threads): Sonnet 4.6 or Haiku for cost
- Video scripts (HeyGen, YouTube shorts): Sonnet 4.6 with structural guards
- Headlines / hooks: parallel generation across 3 models, voice-fidelity picks winner
- Editing pass: GPT-5.5 second pair of eyes per P75 multi-model discipline

### Voice profile inputs
- `jp_brand_voice_profile.json` (95 KB) — current locked profile
- 30 active rules from `voice_corpus_staging/active_rules.jsonl`
- 266 positive-signal posts from `positive_signal_connector.py`
- 18 durable-rule proposals pending Mike/James approval

### Outputs
- Drafts in `publish_queue` (Postgres table) tagged platform, format, pillar, voice_score, status (DRAFT/REVIEW/APPROVED/PUBLISHED/REJECTED)
- Per-draft generation log: which model, which prompt template, which voice-rule citations
- Daily "Drafts Ready for Review" notification to Mike (via approval portal, see Section 9)

---

## Section 4 — Distribution & Amplification

### What it does
Schedules and publishes approved content across all owned channels at platform-optimal times. Optimizes per-platform formatting (Reels for IG, threads for X, articles for LinkedIn). Runs light paid amplification when ROI signals are clear.

### Publishing infrastructure (already wired, re-point only)
- **postproxy:** TikTok, Facebook, LinkedIn, YouTube, Threads (verified active 2026-05-09)
- **Meta Graph (direct):** Instagram (publishing scope live)
- **tweepy (direct):** X/Twitter
- **Substack:** API endpoint extension scheduled for June 2026 launch

### Cadence rules
- Per platform, publish during locked optimal-time windows (cohort-mean of 30-day engagement data)
- No more than 2 posts/day on any single platform without explicit James override
- Cross-platform de-dup: same content must not auto-cross-post unless flagged "publish across all"
- Weekend posting: P31 (weekend family time) governs which posts auto-fire vs. queue for Monday

### Paid amplification
- Boost trigger: any post crossing 2x cohort-median engagement in first 4 hours
- Budget cap: configurable, defaults to $50/post, $500/week
- Reports impact in weekly Brand Health Report

### Outputs
- Published-post log (action_type=publish) with hash-chained timestamps
- Per-publish API response captured for accountability
- Weekly distribution analytics

---

## Section 5 — Audience Engagement & Community Building

### What it does
Monitors mentions, comments, DMs across all platforms. Drafts responses in James's voice. High-confidence responses auto-fire (per James-set threshold); ambiguous ones go to a review queue. Identifies high-intent followers and surfaces them for lead conversion.

### Engagement tiers
1. **Auto-respond (high confidence, voice-score ≥ X+10):** routine compliments, basic FAQs, tagged thanks
2. **Review queue (medium confidence):** DMs about deals, partnership inquiries, topical disagreements
3. **James-only (low confidence or high stakes):** anything tagged sensitive, anything from a known peer/celeb, anything with risk signals (per Section 8)

### Lead conversion
- Detects high-intent signals: deal inquiry keywords, partnership language, repeated engagement
- Adds to `contacts_registry` (existing, append-only per P34)
- Notifies James via daily summary
- Drafts intro outreach for James review

### Community building (PreReal cohort)
- Tracks the agents/investors/families/education-reformers cohort
- Surfaces inactive members for re-engagement
- Identifies super-fans for ambassador opportunities

---

## Section 6 — Performance Analytics & Optimization

### What it does
Tracks all KPIs continuously. Delivers a concise Brand Health Report every Sunday. A/B tests content types and iterates.

### KPIs (rolling windows, NOT lifetime totals)
The 2026-05-09 dashboard bug surfaced that lifetime-cumulative numbers are useless for daily ops. All KPIs in the new system are rolling windows:

- **Engagement (7d, 30d, 90d rolling):** likes + comments + shares + replies, per platform and total
- **Views/reach (7d, 30d, 90d rolling):** views + plays + impressions
- **Follower growth:** delta vs. 7d ago, vs. 30d ago, vs. baseline
- **Content health:** posts published, average voice fidelity score, % at or above threshold
- **Engagement quality:** sentiment score, lead-tagged replies, partnership inquiries
- **vs. peer cohort:** percentile rank within JP's peer set
- **Days since last post per platform:** if > N (configurable per pillar), flag

### Sunday Brand Health Report (1 page max per P70)
Sections:
1. Headline movements (week deltas that matter)
2. Top 3 posts (by engagement + voice score)
3. Bottom 1 post (what didn't land + diagnosis)
4. Pillar mix (over/under-rotated areas)
5. Peer cohort position
6. 3 recommendations for next week

### A/B testing
- Hook variants generated for top-priority pieces
- Posted to mirror windows (same day-of-week, same hour, 2 weeks apart)
- Winner gets re-used as a template

---

## Section 7 — PR, Media & Partnerships

### What it does
Identifies media opportunities, drafts pitches, scouts partnerships, maintains the Strategic Context files for each active project (per P36).

### Media monitoring
- Scans target press (real estate, NM, education, podcasts in pillar territory) for relevant opportunities
- Surfaces ones where JP's track record applies
- Drafts pitch in his voice for James review

### Partnership scouting
- Scans peer cohort and adjacent networks for partnership signals
- Maintains a "Strategic Targets" list with signal scores (recent activity, alignment, mutual benefit)
- Drafts intro outreach for top candidates weekly

### Press kit / speaking proposals
- Maintained current with latest stats, talks, deals
- Auto-regenerated weekly from structured data
- Available on-demand to James or his team

---

## Section 8 — Risk, Crisis & Reputation Protection

### What it does
Continuously monitors for negative sentiment, misinformation, brand-risk signals. Has pre-approved response protocols ready for common crisis types.

### Monitoring
- Sentiment scoring on every comment, mention, reply
- Anomaly detection: spike in negative sentiment, unusual mention volume, named-entity attacks
- Misinformation tracking: factually-wrong claims about JP, deals, predictions
- Trademark/PII watch: unauthorized use of JP's name, voice, image, copyrighted content

### Pre-approved protocols
- Misinformation correction template (with sourcing)
- Negative-press neutral acknowledgment template
- "Decline to comment" template for legal/SEC adjacent issues
- Deal-confidentiality protection (for in-progress deals)

### Escalation
- Critical events (defined: negative-sentiment spike > 3 SD, anything from a named adversary, anything with legal exposure) page James directly within 5 minutes
- All others surface in next morning's executive summary

---

## Section 9 — Human Feedback & Continuous Improvement Loop

### What it does
Every major content piece, partnership outreach, or campaign decision goes through an explicit James-or-Mike approval gate before going live. Every approval/rejection captures a structured reason. Reasons feed the rejection-learner which proposes durable rule updates.

### The approval portal
- Web UI (re-point existing dashboard) showing pending items
- Each item: preview + voice score + reasoning + alternates
- Reviewer actions: APPROVE, APPROVE WITH EDIT, REJECT, REVISE, ESCALATE
- All actions captured with timestamps and reviewer ID

### Rejection-learner loop (already exists, port verbatim)
- `rejection_analyzer.py` watches the rejected stream
- Classifies pattern: voice mismatch, factual error, off-strategy, off-pillar, timing
- After N similar rejections (configurable, default 3), proposes a durable rule
- Mike + James review proposed rules in batch
- Approved rules get merged into voice profile / strategy / safety rules

### Quarterly Brand Strategy Reset
- Every 90 days, full session with James
- Review: pillar mix, voice profile drift, peer cohort changes, identity adjustments
- Output: updated narrative spine, updated content calendar template, updated voice profile

---

## Section 10 — Innovation & Experimentation

### What it does
Proactively tests new AI tools, formats, channels. Reports on what's working and adopts cautiously into production.

### Current backlog (carried forward from earlier work)
- HeyGen avatar renders (avatars 1-6 + Peter Gambino A1/A2 in `heygen_*` shared assets) — short-form video pipeline
- Voice clones (V1-V4 across James + Peter) — podcast clip pipeline
- AI-generated B-roll for podcast clips (Runway, Minimax, Sora 2)
- Substack launch (June 2026)
- Track-Record Engine — auto-detection of mainstream catching up to JP's predictions, auto-publish

### Sandbox discipline
- Every experiment runs in a separate "experimental" lane
- Cannot publish to live channels without explicit promotion to production lane
- Weekly experiments report; quarterly graduation review

---

## Non-Negotiables (hard rules, graph-enforced)

These are encoded as edge conditions in the LangGraph orchestrator. They are not honor-system. Every node that violates them returns to the previous state and retries with the violation flagged.

1. **Voice fidelity threshold** — no content publishes below score X
2. **Verifiable facts only** — claims about James, PreReal, deals, predictions must trace to a corpus citation OR an explicit James-confirmed structured record
3. **Human approval on high-stakes** — partnerships, press pitches, anything legally sensitive, anything outside locked pillars
4. **No credential handoff via chat** — P46 enforced (port credential scanner verbatim)
5. **No silent self-fixes on James-controlled artifacts** — P60 enforced (port pattern verbatim)
6. **Append-only on institutional memory** — P69 enforced (every log is hash-chained, never edited)
7. **Strategic content stays in workspace, not daily emails** — P62 enforced
8. **Every claim about external service state requires live verification** — P26 enforced (lesson from 2026-05-09 postproxy incident)
9. **Multi-model audit at frame level, not just implementation** — Pattern K from frustration ledger; orthogonal to P75 (lesson from 2026-05-09 Grok incident)
10. **Backups run automatically, daily** — Pattern J from frustration ledger; no honor-system backup scripts (lesson from 2026-05-09 backup gap)

---

## Implementation Architecture (the LangGraph plan)

### The Cabinet (parallel agents in the graph)

```
                       [Supervisor]
                            |
        +-------------------+-------------------+
        |              |          |             |
   [Knowledge]    [Strategist]  [Writer]    [Distributor]
        |              |          |             |
        +-------------------+-------------------+
                            |
                       [Reviewer]
                            |
                    [Approval Portal]
                            |
                       [Publisher]
                            |
                       [Analyst]
```

- **Supervisor agent** (Opus 4.7) — routes work, enforces hard rules, calls retries when fidelity fails
- **Knowledge agent** — owns RAG, answers questions, ingests new content
- **Strategist agent** — owns the calendar, picks topics
- **Writer agent** — generates drafts (multi-model routing)
- **Reviewer agent** (separate model from Writer per P75) — scores, flags, recommends edits
- **Approval portal** — humans (James/Mike) gate the flow
- **Publisher agent** — handles platform-specific formatting and distribution
- **Analyst agent** — KPI tracking, weekly reports

### State management

- LangGraph stateful graph: every workflow has a checkpoint after each node, can resume from any failure
- Postgres for durable state (approval queue, content calendar, KPI rollups)
- Pinecone for RAG
- Redis for short-lived agent message passing
- LangSmith for observability — every agent call traced with input/output/cost/latency

### Deployment

- **Modal** (recommended) or **Railway** — both support always-on Python, easy LangGraph deployment, integrated cron, secrets management, webhook endpoints
- Environment variables for all credentials (no hardcoded keys ever — P71 enforced)
- Health checks every 5 minutes
- Auto-restart on failure
- Daily/weekly cron schedule for ingestion + reporting

### Observability (LangSmith)

- Every agent call traced
- Per-agent cost tracking (so the multi-model routing stays cost-effective)
- Per-protocol invocation count (catches Pattern J — every "code-enforced" rule must have non-zero call count over rolling window)
- Drift detection: if any KPI moves > X SD from rolling baseline, alert
- Daily integrity report: action log hash chain, voice profile checksum, RAG corpus row count

---

## Migration Plan (porting from current state)

### What to take (existing artifacts, ~95% reusable)
- All 7 corpus files (~10 MB) — directly into Pinecone
- `jp_brand_voice_profile.json` + 30 active rules — directly into voice scorer config
- `voice_fidelity.py` + tests — drop into `agents/reviewer/voice_score.py`
- 75 protocols → structured rules-engine seed + system-prompt augmentation
- Peer creator list (8 rows) → Postgres seed
- Market research (6 rows) → Postgres seed
- Brand voice attributes (5 rows) → voice scorer config
- All 13 API integrations (postproxy, Meta, OpenAI, Anthropic, ElevenLabs, HeyGen, Descript, Runway, Minimax, GDrive, etc.) → environment variables
- Dashboard frontend (`jp-brand-manager/client/src/`) → re-point to new backend
- Founder's thesis + frustration ledger + four-headed-beast docs → system-prompt seeds

### What to rebuild fresh (the substrate-bound modules)
- All `enforcement/*.py` — logic ports as reference; runtime is the graph itself
- `daily_report.py` — the report generator stays; the cron orchestration migrates to Modal cron
- `operational_readiness.py` — concept ports as Supervisor agent's health check; implementation rewrites

### What to throw away
- The "honor-system enforcement" pattern across all of `enforcement/` — every rule becomes a graph edge condition
- Per-protocol Python wrapper modules — the rules live as data, not code
- The xpoz fallback hardcoded in `fetch_social.py:11` — credentials go to env vars

### Open issues to resolve before deployment
- xpoz key actual rotation (May 7 ticket with `hello@xpoz.ai` — status unknown)
- Read-side API for Facebook / Threads / YouTube / LinkedIn (Meta `pages_read_engagement` scope, YouTube Data API v3)
- News pipeline rebuild (35+ days stale — port concept, rebuild against fresh source allowlist)
- Peer comparison cohort lock with James (P47 sign-off)
- Follower growth targets per platform (config file James fills in)

### Estimated timeline (competent team or me as architect-only spec author + handoff developer)
- Week 1: stack scaffolding, agents skeleton, RAG ingestion of existing corpora
- Week 2: writer + reviewer + approval portal end-to-end on a single platform (Instagram first)
- Week 3: distribution across all 7 platforms + analytics + weekly report
- Week 4: rejection-learner, quarterly review, hardening, documentation

---

## What success looks like (objective measures)

- **Day 30:** James has not had to re-explain Sierra County / Spaceport / term-limits / education thesis to the agent
- **Day 30:** Voice fidelity score for published content averages > 60 (vs. corporate-slop baseline 22)
- **Day 60:** Engagement-per-post is at or above James's manual-curation baseline
- **Day 60:** Peer cohort percentile rank improves by 5+ points
- **Day 90:** Pattern J instances = 0 (every claimed-enforced rule has non-zero invocation count in LangSmith)
- **Day 90:** Frustration ledger gets new entries < 2/month (down from current rate)
- **Day 90:** Substack launched, Track-Record Engine live, full multi-platform parity

---

## What this spec does NOT yet contain (gaps James to fill)

- Specific peer cohort lock (10 names per pillar) — needs James review
- Exact voice fidelity threshold X (currently named as "starts at 50, James-tunable")
- Specific media targets for press monitoring
- Specific paid amplification budget caps
- Quarterly Brand Strategy Reset cadence (Q1 vs. monthly)
- Acceptable response latency for engagement tier 1 (auto-respond) — currently unspecified
- Whether Mike has unilateral approval authority for pillars 3-4 or only James

These are NOT blockers for the build. They are runtime knobs that get tuned in the first 30 days.

---

*This document is the spec. It is not a discussion. It is not a proposal. It is the locked target. Deviations require a verbatim James authorization recorded in the change log.*
