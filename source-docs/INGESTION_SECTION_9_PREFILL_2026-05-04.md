# LLM INGESTION SECTION 9 — PRE-FILLED FROM EXISTING DECISIONS
**Generated:** 2026-05-04 5:11 PM ET  
**Source authority:** James verbatim 5:11 PM ET 2026-05-04 — *"much of this has already been decided and is part of the protocols or rules we have framed out for you - arent you capable of sourcing those answers and directions - we talked through this extensively and i built stop gaps in along the way"*  
**Purpose:** Reduce James's intake-form burden by sourcing prior decisions before asking new questions. Separate doc from LLM_INGESTION_PLAN_2026-05-04.md — READ-ONLY on the main plan until James confirms this pre-fill.  
**Pre-fill mining sources:** past_session_contexts/sessions/ (all four date ranges), memories/ (all protocol + project files), MAX_OPERATING_PROTOCOL.md, JAMES_THE_FULL_VISION.md, foundation_docs/JAMES_PLATFORM_VISION_INTERVIEW.md, foundation_docs/JAMES_WHITEBOARD_ARCHIVE_RAW.md, ACADEMY_SOURCE_OF_TRUTH.md, VOICE_CORPUS_LANDSCAPE.md, daily_report_config.json, domain_audit_and_media.md, PREDICTION_GOLDMINE_STRATEGY.md, workspace artifact inventory.

---

## CROSS-CORPUS RULES (apply to multiple corpora)

### Speaker-Tagging Rule (locked 2026-05-04 5:11 PM ET)

**Source:** James verbatim, 2026-05-04 5:11 PM ET:
> *"recogize when a guest is speaking for context when i respond but only account for my answers when making a record of my voice"*

**Rule in operational language:**  
When Max ingests any multi-voice content (podcast episodes, press interviews, live streams, panel discussions, meeting transcripts), the ingestion pipeline MUST:
1. Speaker-tag every utterance — James's lines vs. all other speakers
2. Feed ONLY James-tagged lines into the voice corpus (the training signal for voice learning)
3. Retain ALL lines — including guest/interviewer/host lines — as context metadata, so Max can interpret what James was responding to when he made his statements
4. NEVER use guest lines, interviewer quotes, or co-host speech as voice corpus input — those are context frames, not James's voice

**Corpora where this rule applies:**
- **CORPUS 4 — Podcast Catalog** — guest voices are context, only James's lines feed voice corpus. Mike's voice ≠ guest voice ≠ James's voice. (Source: session 2026-04-28, Turn 182: *"Mike's voice ≠ your voice ≠ guest voice"*)
- **CORPUS 6 — Press Coverage** — interviewer quotes are context, only James's direct quotes are voice corpus input; reporter paraphrase NEVER counts as James's voice. (Source: P47 tier model, session 2026-04-27 Turn 119: *"Reporters paraphrase, edit, and sometimes flat-out misquote. Even when accurate, the framing isn't yours."*)
- **CORPUS 12 — Ongoing Chat Sessions** — Max's output is NOT James's voice; only James's typed utterances count as voice corpus input
- **Any future corpus with multi-voice content** — interviews, panels, IG Lives with guests, board meeting transcripts

**Pre-existing implementation:** `podcast_full_context.txt` (6.6MB, dated 2026-04-16) was already speaker-tagged — only James's lines were analyzed in the 4/16 voice corpus distillation. The rule was already operating before today; today James named it explicitly.  
(Source: VOICE_CORPUS_LANDSCAPE.md: *"podcast_full_context.txt — full podcast-speaker-tagged transcripts"*; session 2026-04-28 Turn 182 analysis)

---

### Podcast Ingestion Scope — YouTube Watching Authorized

**Source:** Peter (on behalf of JP system), session 2026-04-29 13:28 UTC:
> *"the podcast gets published to the youtube channel https://www.youtube.com/@ThePrendamanoProject and on spotify https://open.spotify.com/show/1kvqMm8TKj4CKCyYbovoQ3"*

**Prior state James referenced today:** Transcripts-only (the static `podcast_full_context.txt` file, uploaded 2026-04-16).

**Current authorized state (post-2026-04-29):**  
- Two YouTube channels are live and authorized for ingestion:
  - `@prereal` — 1,390 videos, 18.2K subscribers, shorts-heavy (resolved 2026-04-29 13:22 UTC)
  - `@ThePrendamanoProject` — 143 videos (resolved 2026-04-29 13:28 UTC)
- Two Spotify/RSS podcast feeds are live:
  - "The Real Problem with James Prendamano" — active feed, 9 episodes as of 2026-04-29
  - "PreReal Podcast" — archive feed, 189 episodes Jul 2020 → Mar 2024
- YouTube Data API v3 is enabled on Google Cloud project `ardent-bridge-431215-s3`
- Service account `jp-brand-manager-drive@ardent-bridge-431215-s3.iam.gserviceaccount.com` is authorized
- Infrastructure: `voice_corpus_ingest.py` handles both RSS and YouTube channel pulls with dedup logic

**Tranche status:** YouTube channel + RSS metadata ingestion is live (ongoing). Full audio transcription (Whisper) for the 174 episodes not in `podcast_full_context.txt` is **pending** (AI 49 — queued, not yet executed as of last session). Cost estimate: ~$47 one-time for archive backfill, $0.27/week ongoing.

**James confirmation from today (2026-05-04):** James referenced today that podcasts moved from "transcripts only" to "watch and ingest from YouTube" — confirming this is the current authorized state.  
(Source: James verbatim 2026-05-04 5:11 PM ET: *"the podcasts were transcripts only originally before we made you able to 'watch' and ingest them from you tube"*)

---

### Q5.1 Voice Corpus Retention (already locked)

**Source:** JAMES_PLATFORM_VISION_INTERVIEW.md, Q5.1, answered 2026-05-02 1:56 PM ET. Locked.

**Rule (verbatim from vision interview):**
- Voice corpus retention is **full** — nothing is forgotten
- Retention timeline varies by tenant use case
- **Check valve:** content approval queue with element-level feedback surfaces contradictions to human rather than auto-resolving
- Platform-wide principle: surface tension to human, never silently auto-resolve

**Operational implication for ingestion:** Every corpus ingested goes into the permanent voice corpus. Nothing is deleted or downweighted silently. If new content contradicts a previous voice pattern, the system surfaces it via the approval queue rather than auto-overwriting the older signal.

---

### Press Corpus Treatment — Tier 4: Reference Only, NOT Voice Corpus (locked framework)

**Source:** Session 2026-04-27 Turn 119 (Max's 4-tier proposal); James authorized the framework in Turn 182 (2026-04-28 20:09 UTC) by approving the broader voice corpus scope which incorporated the exclusion of press.

**Rule:** Press coverage is **Tier 4 — never auto-ingest to voice corpus.**  
Reason (Max's reasoning, not challenged by James): *"Reporters paraphrase, edit, and sometimes flat-out misquote. Even when accurate, the framing isn't yours."*

**What press corpus IS for:** Third-party credibility documentation, prediction receipts (publication timestamps), booking pitch evidence. It is a reference corpus, not a voice training corpus.  
**What press corpus is NOT:** James's voice. Reporter paraphrase, characterization, and framing never feeds voice training.

**Implementation:** Press corpus ingested separately from voice corpus, tagged `corpus_type: reference_only, voice_eligible: false`. Speaker-tagging rule still applies — James's direct quotes within press articles ARE extractable as voice signals if clearly demarcated as verbatim quotes.

🔵 **INFERRED:** The speaker-tagging rule James named today (2026-05-04 5:11 PM ET) extends to press interviews: interviewer questions are context, James's verbatim quoted answers are voice-eligible. This wasn't explicitly stated but follows directly from the cross-corpus rule. Needs James confirmation.

---

### 7 DEAD Whiteboard Items (already locked)

**Source:** JAMES_PLATFORM_VISION_INTERVIEW.md Q4 phase reconciliation; James verbatim 2026-05-02: *"all 7 dead."*  
**Lock date:** 2026-05-04 1:54 PM ET (locked in LLM_INGESTION_PLAN_2026-05-04.md Section 9, Corpus 10).

**The 7 DEAD items (archive_status: DEAD, query_filter: historical_only):**
1. Mortgage / Lending Company — IMG_0097.MOV
2. "Tinder of Real Estate" matching app — IMG_2239
3. SupportSI platform — IMG_3858
4. Crypto / Hybrid Payment Module — IMG_2213, IMG_2214
5. Investor / Equity Portal as standalone product
6. Unidentified company org chart — IMG_3353 (Dave Broom CEO, Efrain COO)
7. Environmental / Infrastructure projects — IMG_1124

**Rule:** These 7 items are indexed as `historical_archive_only` — "things we considered and rejected." Default RAG retrieval excludes them. They surface ONLY when queries explicitly ask for rejected ideas (e.g., "what platform ideas did James previously consider and reject?") and are always labeled as rejected. This is already implemented in the Tranche 1 ingestion build (exclusion_filter.py in `/home/user/workspace/ingestion/`).

---

### Public-Facing Content Auto-Ingest Rule (locked 2026-04-28)

**Source:** James verbatim 2026-04-28 4:09 PM ET (session Turn 182):
> *"please note you dont need my permission to add any of this information to voice corpus becasue its all public facing already"*

**Rule (per P47 amendment):** Public-facing JP content auto-ingests to voice corpus without per-instance James sign-off. Public availability IS the gate. Scope confirmed: Instagram, TikTok, X, Threads, Facebook, YouTube channels, Prendamano Academy, podcast feeds, Google Drive "JP Brand Manager" folder and subfolders.

**Excluded by explicit James direction (same session):** Gmail sent items — *"do not include gmail sent items as this is for the public facing voice."*

---

## CORPUS-BY-CORPUS PRE-FILL

---

### CORPUS 1 — May 1–3, 2026 Conversation Thread
*(Already pre-filled in LLM_INGESTION_PLAN — reproducing for completeness)*

- **Where it lives:** 🟢 `past_session_contexts/sessions/2026-04-27_2026-05-03/521e8d85/conversation.md` — already in workspace  
  *(Source: workspace directory; sessions_index.md)*
- **Access mechanism:** 🟢 Direct workspace access — no external credentials  
- **Existing organization:** 🟢 Well-organized per session protocol; session ID `521e8d85`  
- **Known gaps:** 🟢 None identified  
- **Sensitivity flags:** 🟢 Max filters operational details (system paths, API key references) at chunk level; James's thesis utterances are the target
- **Estimated total volume:** 🟢 ~150,000–200,000 words (file size ~1.2MB)  
  *(Source: glob output shows file size 1,189,851 bytes)*
- **Person who owns access:** 🟢 Max / workspace  

**STATUS: READY FOR TRANCHE 1 — NO JAMES INPUT NEEDED**

---

### CORPUS 2 — Academy Raw Transcripts

- **Where it lives:**  
  🟢 **Published modules (Corpus 2a — student site):** `https://prendamanoacademy.com` — 6–7 modules published as of 2026-04-24  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md; daily_report_config.json academy section)*  
  🟢 **Raw modules (Corpus 2b — Drive voice corpus source):** Google Drive — raw scripts + videos up through ~Module 20, within the "JP Brand Manager" Shared Drive  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md: "Drive: raw scripts + videos (original uploads)"; James verbatim 2026-04-24 11:43 AM ET: "the academy videos are cleaned up 100% - the others are raw- the acadmeny has only released up to module 6 or 7 i think i gave you scripts and videos on raw modules up into the 20s again to help with voice corpus")*  
  🟢 **Static workspace artifact:** `academy_training_text.txt` (727,241 bytes, dated 2026-04-16) — one-time drop of early modules, already in workspace  
  *(Source: VOICE_CORPUS_LANDSCAPE.md; workspace file listing)*

- **Access mechanism:**  
  🟢 **Max student account:** Created on `prendamanoacademy.com` using `max@prereal.com`. Credentials file: `/home/user/workspace/secrets/academy_credentials.rtf` (484 bytes, added 2026-04-27 14:46). Status as of last session: account email verification pending confirmation from Peter.  
  *(Source: daily_report_config.json; secrets/ file listing; session Turn 215–220)*  
  🟢 **Shared Drive:** Service account `jp-brand-manager-drive@ardent-bridge-431215-s3.iam.gserviceaccount.com` has Content Manager access to "JP Brand Manager" Shared Drive (Shared Drive ID: `0AE5JjXEpb8rsUk9PVA`). New modules exported by Mike route to Drive → auto-ingested.  
  *(Source: daily_report_config.json google_drive section)*  
  🟡 **NEEDS JAMES INPUT:** Exact Drive subfolder name/path where Academy raw module exports live (is there a dedicated "Academy Module Exports" subfolder, or are they in the root of Brainstorm Archive / Digital Docs?)  

- **Existing organization:**  
  🟢 Two-tier split established and locked:  
  — `prendamanoacademy.com` = source of truth for student experience  
  — Drive raw content = source of truth for voice corpus + content repurposing  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md, established 2026-04-24)*  
  🔵 **INFERRED:** Modules are being added daily (per James 2026-04-24: "modules are added daily") but the organization format of the Drive folder for exports is not explicitly documented. Likely matches whatever Peter/Mike use.  

- **Known gaps:**  
  🟢 **Published vs. raw mismatch:** Published site has 6–7 modules; raw Drive content goes to ~Module 20. The gap is deliberate — raw modules are pre-release content  
  🟢 **No recurring ingest pipeline yet for new modules:** `academy_training_text.txt` was a one-time drop (2026-04-16). The Daily + Weekly ingest cron (`voice_corpus_ingest.py`) targets Academy on Mondays but requires the Drive export path to be correct  
  🟡 **NEEDS JAMES INPUT:** Physical/somatic pillar — is this in the Drive raw modules (Module 20+), or is it still being developed?  

- **Sensitivity flags:**  
  🟢 Published academy content = Tier 1 (public-facing, auto-ingest authorized per James 2026-04-28)  
  🟢 Raw Drive content = voice corpus source (James authorized: "i gave you scripts and videos on raw modules up into the 20s again to help with voice corpus")  
  🟢 Student-identifying data: any student voices, student assessments, or identifiable student information = **excluded**. Not James's voice; tagged `[OTHER_SPEAKER]` per P71 cybersecurity gate  
  🔵 **INFERRED:** Academy lessons Mike may have edited (formatting, post-production) = Tier 3 (batch review before voice corpus integration). This was listed as an open question in the 4/28 tier model but not explicitly resolved. Needs James's read: are the raw Drive videos unedited, or did Mike shape the content?

- **Estimated total volume:**  
  🟢 Workspace artifact `academy_training_text.txt` = 727,241 bytes / ~550K words (rough estimate). Covers early modules only  
  🟡 **NEEDS JAMES INPUT (or Mike):** Total module count and approximate length per module for the full Drive raw archive

- **Person who owns access:**  
  🟢 Mike McGinn created the Max student account; Peter Gambino stores credentials  
  🟢 Credential path: `/home/user/workspace/secrets/academy_credentials.rtf`  
  *(Source: P20 — technical credentials route to Peter; session Turn 215)*

---

### CORPUS 3 — Academy Website (Clean Presentation)

- **Where it lives:**  
  🟢 `https://prendamanoacademy.com` — live Lovable-built web application  
  🟢 Personal hub (NOT the academy): `https://prendamanoproject.com` — repositioned 2026-04-24 to James personal brand hub  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md; Mike confirmation 2026-04-24; daily_report_config.json)*  

- **Access mechanism:**  
  🟢 Public URL — no login required to read published modules 1–7  
  🟢 Max student account (`max@prereal.com`) provides authenticated access to any gated tools or premium content  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md; session Turn 216)*

- **Existing organization:**  
  🟢 Well-organized — purpose-built Lovable web app; course catalog structure with modules by category  
  🟢 Weekly cadence: Max checks for new modules every Monday, reports in Tuesday daily brief  
  *(Source: ACADEMY_SOURCE_OF_TRUTH.md: "weekly Monday check-in and Tuesday report")*

- **Known gaps:**  
  🟢 Site is "work in progress" as of 2026-04-24 — new modules added daily  
  🟢 Site is the cleaned/polished version, not the raw voice corpus version — important to ingest separately from the raw Drive content  
  🔵 **INFERRED:** "Real Estate Training (Coming Early 2026)" referenced in domain_audit_and_media.md as not yet published as of April 2026. Status unknown.

- **Sensitivity flags:**  
  🟢 Public-facing content auto-ingest authorized (P47 amendment, James 2026-04-28)  
  🟢 Any student-identifiable data, assessment responses, private member content = excluded  
  🟢 Corpus 3 (clean site) and Corpus 2 (raw Drive) serve different purposes — must be ingested separately and tagged accordingly

- **Estimated total volume:**  
  🟢 Plan estimate: ~10,000–50,000 words (reasonable, consistent with 6–7 published modules)

- **Person who owns access:**  
  🟢 Mike McGinn (operations, academy); Max student account for authenticated reads  
  *(Source: P20 — Mike owns academy operations)*

---

### CORPUS 4 — All Published Podcasts

- **Where it lives:**  
  🟢 **YouTube Channel 1:** `@ThePrendamanoProject` — `https://www.youtube.com/@ThePrendamanoProject` — 143 videos; primary podcast YouTube home  
  🟢 **YouTube Channel 2:** `@prereal` — `https://www.youtube.com/@prereal` — 1,390 videos, shorts-heavy; secondary channel  
  🟢 **Spotify (active show):** "The Real Problem with James Prendamano" — `https://open.spotify.com/show/1kvqMm8TKj4CKCyYbovoQ3` — 9 episodes as of 2026-04-29  
  🟢 **Spotify (archive):** "PreReal Podcast" — 189 episodes Jul 2020 → Mar 2024; same Spotify RSS infrastructure  
  *(Source: session 2026-04-29, Turn 211: "@prereal resolved (1,390 videos, 18.2K subscribers)"; Turn 214: "the podcast gets published to the youtube channel https://www.youtube.com/@ThePrendamanoProject and on spotify [URL]")*

- **Access mechanism:**  
  🟢 **YouTube:** YouTube Data API v3 enabled on Google Cloud project `ardent-bridge-431215-s3`; service account `jp-brand-manager-drive@ardent-bridge-431215-s3.iam.gserviceaccount.com` authorized. Already live in `voice_corpus_ingest.py`  
  🟢 **Podcast RSS:** Both feeds wired via standard RSS/stdlib in `voice_corpus_ingest.py`. No new credentials needed.  
  🟢 **Podcast Inbox (Drive):** Folder ID `1HmwcX4l9Zg_yBpgkfZohRJo1jplfZw3i` / URL `https://drive.google.com/drive/folders/1NcI5Nta11XKut5Kz0ZmRvHjk29os2yf2` — pipeline watcher folder for new MP4/M4V drops  
  *(Source: daily_report_config.json; session Turn 211, 214)*

- **Existing organization:**  
  🟢 Two-tier corpus:  
  — Tier 1: `podcast_full_context.txt` (6.6MB, 2026-04-16) — 26 of 189 PreReal Podcast episodes fully transcribed; already in workspace voice corpus  
  — Tier 2: RSS metadata (title + description) for all 198 episodes staged in `voice_corpus_staging/podcast/`; 174 episodes pending Whisper transcription (AI 49 — queued)  
  🟢 Dedup logic implemented: `voice_corpus_ingest.py` checks against transcript map before staging new episodes  
  *(Source: session 2026-04-29, Turn 221: "26 of 189 PreReal episodes are transcribed in podcast_full_context.txt"; Turn 222)*

- **Known gaps:**  
  🟢 **174 episodes pending Whisper transcription (AI 49):** ~$47 one-time cost. These are episodes from both feeds not covered by the original `podcast_full_context.txt` upload  
  🟢 **Pre-existing transcript file is selectively complete:** Only 26 of 189 archive episodes are fully transcribed in `podcast_full_context.txt` — not all 189 as originally estimated  
  🟡 **NEEDS JAMES INPUT:** Are there any podcast episodes that were pulled, deleted, or only exist as private/unlisted YouTube videos not covered by the RSS feeds?

- **Sensitivity flags:**  
  🟢 **Speaker-tagging rule applies** (new, locked 2026-05-04): guest voices = context; only James's lines = voice corpus input  
  🟢 **Mike's voice (Real Problem Podcast co-host):** Tagged as non-James speaker, excluded from voice corpus  
  🟢 Public podcast content = Tier 1/2 auto-ingest authorized (P47 amendment 2026-04-28)  
  🟡 **NEEDS JAMES INPUT:** Are there any episodes featuring NDA-covered content, confidential guest conversations, or material James would not want indexed in the corpus?

- **Estimated total volume:**  
  🟢 198 total episodes (189 archive + 9 active as of 2026-04-29); average ~45 minutes per episode  
  🟢 At 100% Whisper transcription: ~150+ hours of audio → ~1.5–2M words  
  🟢 Current state: 26 episodes transcribed in `podcast_full_context.txt`; remaining 174 episodes = titles + descriptions only

- **Person who owns access:**  
  🟢 Peter Gambino — YouTube API, service account credentials, Podcast Inbox Drive folder  
  🟢 Max has direct pipeline access via `voice_corpus_ingest.py`

---

### CORPUS 5 — Predictions.docx + Dashboard Archive

- **Where it lives:**  
  🟢 **Workspace (static drop):** `/home/user/workspace/Predictions.docx` (1,724,781 bytes, 2026-04-16) — also exists as `/home/user/workspace/jp_predictions_archive.docx` (identical file, same size)  
  🟢 **Drive — Prediction Archive subfolder:** `🔮 Prediction Archive` — Folder ID `1ld3zhsT7sKDgOF-w92Bl_fhdXmtuH367`, within "Brainstorm Archive — Raw" Shared Drive  
  🟢 **Drive — Brainstorm Archive root:** `https://drive.google.com/drive/folders/1TNcGGo4X1MPn0R84S8ZAFY8hHoFDuZvx`  
  *(Source: daily_report_config.json; PREDICTION_GOLDMINE_STRATEGY.md: "Source file: jp_predictions_archive.docx (1.7 MB) in /home/user/workspace/")*

- **Access mechanism:**  
  🟢 `/home/user/workspace/Predictions.docx` — direct workspace access, no credentials needed  
  🟢 Drive Prediction Archive folder: service account has Content Manager access to "Brainstorm Archive — Raw" Shared Drive  
  🟢 Peter Gambino cataloged 55 video prediction clips into the docx (as of 2026-04-16)  
  *(Source: PREDICTION_GOLDMINE_STRATEGY.md: "55 prediction clips across 6 categories — cataloged by Peter Apr 16")*

- **Existing organization:**  
  🟢 Video clips: 55 clips across 6 categories (Peter's catalog, Apr 16)  
  🟢 Podcast-embedded predictions: multiple episodes with deliberate time-stamps (James explicitly used podcast publication dates as cryptographic timestamps)  
  🟢 Whiteboard/butcher paper predictions: 2021 stimulus/inflation/bank-defaults whiteboard confirmed + additional whiteboard prediction artifacts  
  🟢 Content series planned: "I Called It" — 1 prediction/week, multi-year runway  
  *(Source: PREDICTION_GOLDMINE_STRATEGY.md; James verbatim 2026-04-24: "i used my podcast to weave many of them in so they were time stamped")*  
  🟡 **NEEDS JAMES INPUT:** Is there an additional dashboard (Google Sheets / Airtable / other) for tracking predictions beyond the docx? The plan calls for a "dashboard archive" — does this exist as a structured file, or is Predictions.docx the complete archive?

- **Known gaps:**  
  🟢 Private archive — most predictions not yet publicly released: *"nobody has seen most of them but me you and peter"*  
  🟡 **NEEDS JAMES INPUT:** Any predictions that were verbal-only and never documented in the docx or on a whiteboard?  
  🟡 **NEEDS JAMES INPUT:** Confirm the 6 prediction categories Peter used — are they thematic (real estate, macro-economic, AI, civic, etc.)?

- **Sensitivity flags:**  
  🟢 Private strategic asset — three-track deployment strategy (public "I Called It" / VC pitch weapons / Netflix/acquirer weapons). NOT for public corpus until James authorizes release per content calendar  
  🟡 **NEEDS JAMES INPUT:** Any predictions touching specific named individuals or deals under NDA that should be flagged `sensitivity: high` in the corpus?

- **Estimated total volume:**  
  🟢 `Predictions.docx` = 1,724,781 bytes — likely 100,000–300,000 words depending on density  
  🟢 Additional Drive archive content TBD

- **Person who owns access:**  
  🟢 James (strategic owner) + Peter Gambino (technical catalog) + Max (corpus access via workspace + Drive)

---

### CORPUS 6 — Press Coverage Archive

- **Where it lives:**  
  🟢 **Domain audit compilation (workspace):** `/home/user/workspace/domain_audit_and_media.md` (30,957 bytes, 2026-04-19) — independently compiled press table of 20+ articles/mentions  
  🟢 **prendamanoproject.com/news page** — James's curated press archive (live URL; partially audited Apr 2026)  
  🟡 **NEEDS JAMES INPUT:** Does a private Drive folder with saved press clips exist? Or is the news page the primary archive?

- **Access mechanism:**  
  🟢 **Public URLs:** Realtor.com, Bizjournals, City & State, Commercial Observer, Sierra County Citizen, NM Governor records — all publicly accessible, no credentials  
  🟢 **Site press page:** `https://www.prendamanoproject.com/news` — public  
  🟡 **NEEDS JAMES INPUT:** Is there a press clipping service (Meltwater, Cision, etc.) providing an export? Or does Shahd (PR) maintain a separate archive?  
  *(Source: VOICE_CORPUS_LANDSCAPE.md; domain_audit_and_media.md)*

- **Existing organization:**  
  🟢 **Identified / independently verified:**
  - Realtor.com — "NYC Real Estate Mogul Revitalizing Small Towns in New Mexico Desert" — Dec 2025
  - Albuquerque Business First — Spaceport America board appointment — Feb 2026
  - City & State New York — Staten Island Power 100 (2016–2025); Trailblazers 2025
  - Commercial Observer — NYC Top 10 Boutique Firms
  - Enterprise World Magazine — "Most Influential Leader in Real Estate to Watch 2025"
  - NM Governor's Office — Senate Executive Message re: Spaceport Board — Feb 2026
  - KOAT-TV (ABC affiliate) — Spaceport board appointment — Feb 2026
  - The Real Deal — 2006, 2018, 2022 mentions
  - Sierra County Citizen — multiple articles (2025–2026)
  - Ignite Golf Network / KTSM / Yahoo Sports — Turtleback Mountain golf coverage
  *(Source: domain_audit_and_media.md; JAMES_RESUME_AUDIT.md)*  
  🟡 **NEEDS JAMES INPUT:** Any coverage James knows about that isn't listed above (e.g., specific NY1 segments, Staten Island Advance pieces, trade publications)

- **Known gaps:**  
  🟢 **Confirmed national gap:** No NYT, WSJ, Politico, or national magazine profile as of April 2026  
  🟢 **NM/Regional gap:** Sierra County Citizen coverage includes skeptical/investigative pieces — these exist but require sensitivity flagging  
  🟢 Per LLM_INGESTION_PLAN note: "Crain's archive direct search" and "2018 Real Deal article URL" are open research items from MAX_ACTIVE_BOOKMARKS.md (not yet executed)

- **Sensitivity flags:**  
  🟢 **Press is Tier 4 — NEVER voice corpus input** (locked per P47 / session 2026-04-27 Turn 119). Reporter paraphrase ≠ James's voice  
  🟢 **Speaker-tagging exception:** James's VERBATIM QUOTED statements within press articles ARE voice-corpus-eligible if clearly demarcated as direct quotes (not paraphrase). Tagging required  
  🟢 **Sierra County Citizen skeptical coverage:** Articles that mischaracterized James's thesis or took adversarial framing — these should be tagged `characterization_accuracy: contested` before indexing  
  🟡 **NEEDS JAMES INPUT:** Any specific articles James would categorically exclude from even the reference corpus (pieces he considers inaccurate enough to pollute the record)?  
  🔵 **INFERRED:** P37 (Internal Insight Silos) applies — any political relationship context in press articles should be flagged workspace-only; P62 (silo content) applies to anything James shared with a journalist that shouldn't surface in outward-facing queries

- **Estimated total volume:**  
  🟢 ~20–200 articles (plan estimate); domain audit identified ~15–20 verifiable pieces; total likely 30–50 distinct press mentions  

- **Person who owns access:**  
  🟢 Shahd (PR/communications) — likely owns any press clipping service or archive  
  🟢 Max compiled the public-URL archive independently  
  *(Source: domain_audit_and_media.md; contacts_registry.md lists shahd@prerealinvestments.com)*

---

### CORPUS 7 — Social Channels (IG, TikTok, X — Historical)

- **Where it lives (per platform):**

  **Instagram:**  
  🟢 Handle: `@j_prendamano`  
  🟢 User ID: `17841402094659742`  
  *(Source: daily_report_config.json: `social_accounts.instagram.handle: j_prendamano`; `api_credentials.instagram.user_id: 17841402094659742`)*

  **TikTok:**  
  🟢 Handle: `@j_prendamano` (Postproxy profile: "James Prendamano", profile_id: `g2rUdk`, group `qobFw8`)  
  *(Source: daily_report_config.json)*

  **X (Twitter):**  
  🟢 Handle: `@j_prendamano`  
  *(Source: daily_report_config.json: `social_accounts.twitter.handle: j_prendamano`; domain_audit_and_media.md: "X/Twitter @jprendamano" — note possible alias variation @jprendamano vs @j_prendamano; confirmed `j_prendamano` in config)*

  **Facebook:**  
  🟢 Page ID: `162256677128352`; profile name "James Prendamano" (Postproxy profile ID: `yZDUoY`)  
  *(Source: daily_report_config.json)*

  **Threads:**  
  🟢 Postproxy profile active (profile_id: `threads_active`)  
  *(Source: daily_report_config.json)*  
  ⚠️ Threads skipped in April 2026 due to Threads API requiring separate OAuth (not system-user token); deferred per James: *"skip threads for now"* (2026-04-29 13:26 UTC). Revisit needed.

  **LinkedIn:**  
  🟢 Profile: "James Prendamano" (Postproxy: active); URL `https://www.linkedin.com/in/jamesprendamano`  
  *(Source: daily_report_config.json; domain_audit_and_media.md)*

  **YouTube:**  
  🟢 Two channels confirmed (see Corpus 4):
  - `@prereal` — 1,390 videos
  - `@ThePrendamanoProject` — 143 videos  
  *(Source: session 2026-04-29)*

- **Access mechanism:**  
  🟢 **Instagram, TikTok, X:** xpoz API (already wired for daily report + voice corpus ingest)  
  🟢 **Facebook:** Meta Graph API (System User token; Meta app ID `1993321367941506`, Business: PreReal Prendamano Real Estate)  
  🟢 **YouTube:** YouTube Data API v3 (Google Cloud project `ardent-bridge-431215-s3`, service account)  
  🟢 **Threads:** Deferred — needs separate Threads OAuth consent flow (~15 min Peter work + 1 James click)  
  🟢 All 7 live social sources are wired into `voice_corpus_ingest.py` (daily run) and `voice_corpus_distill.py` (weekly Sunday rebuild)  
  *(Source: session 2026-04-29 Turn 222; daily_report_config.json)*

- **Existing organization:**  
  🟢 Platform-standard (chronological, by post)  
  🟢 Historical depth: xpoz fetches last 5 posts per daily report run; historical archive not yet bulk-pulled  
  🟡 **NEEDS JAMES INPUT:** Are there specific time periods (pre-voice-evolution) James wants date-gated for social historical import? The plan flagged this as an open question: "Old social posts (pre-current-voice-evolution — date-gate?)"

- **Known gaps:**  
  🟢 Threads not yet wired (deferred April 2026)  
  🟢 Historical social archive beyond most-recent-5 not yet bulk-pulled — only forward-going daily ingest is live  
  🟢 Video posts on social (TikTok, Reels, YouTube Shorts) = captions and titles captured; actual audio transcription of short-form video NOT yet automated  
  🟡 **NEEDS JAMES INPUT:** Any specific deleted posts James knows about that should be recovered (e.g., from account export)?

- **Sensitivity flags:**  
  🟢 Auto-generated social posts (e.g., "5 years on Instagram!" memory posts, tagged/shared-by-platform posts) = **excluded**. Only James-authored captions count  
  🟢 Reposts and quote graphics = **excluded** from voice corpus  
  🟢 Rule from P47 / session 2026-04-28 Turn 182: *"Captions you actually wrote count. Captions Instagram wrote for you don't."*  
  🟡 **NEEDS JAMES INPUT:** Are there any specific posts or periods NOT to ingest (e.g., personal posts unrelated to professional content)?

- **Estimated total volume:**  
  🟢 James's account at roughly 4+ years of active posting across platforms  
  🟡 **NEEDS JAMES INPUT (or Peter):** Approximate total post count per platform for historical bulk-pull scoping

- **Person who owns access:**  
  🟢 Shahd manages day-to-day social; Peter owns API credentials  
  🟢 P20: technical access routes to Peter; P8: strategic direction routes to James only  
  *(Source: contacts_registry.md; MAX_OPERATING_PROTOCOL.md P20)*

---

### CORPUS 8 — PreReal Local RE Weekly Newsletter Archive

- **Where it lives:**  
  🟡 **NEEDS JAMES INPUT:** Newsletter platform unknown. No reference to Mailchimp, ConvertKit, Klaviyo, Constant Contact, or other email marketing platform found in any session context or workspace file.  
  🔵 **INFERRED from context:** James referenced his "weekly real estate thesis" repeatedly (e.g., JAMES_THE_FULL_VISION.md overlay: *"almost my entire social feed and weekly real estate thesis is contrarian to mainstream reporting"*). This newsletter exists and is published regularly. It has never been named in a platform context.  
  🔵 **INFERRED:** The weekly thesis James uploaded on 2026-04-29 (session Turn 245) was directed to Drive Brainstorm Archive as a Drop — suggesting current issues may already be routing to Drive. Historical archive is a separate question.  
  *(Source: JAMES_THE_FULL_VISION.md; session Turn 245)*

- **Access mechanism:**  
  🟡 **NEEDS JAMES INPUT:** Export method unknown. Candidates: email marketing platform export / RSS feed / manual Drive uploads / James pastes into session.

- **Existing organization:**  
  🔵 **INFERRED:** Published weekly; James describes it as consistently contrarian to mainstream real estate reporting. "Almost always seeing things a few years in advance." Issue-by-issue format likely.

- **Known gaps:**  
  🟡 **NEEDS JAMES INPUT:** When did the newsletter start? How far back does the archive go?

- **Sensitivity flags:**  
  🟢 Weekly newsletter is public-facing → auto-ingest authorized per P47 (James 2026-04-28: public-facing content needs no per-instance approval)  
  🟡 **NEEDS JAMES INPUT:** Any issues containing subscriber-exclusive deals, third-party advertiser material, or guest-authored content that should be excluded?

- **Estimated total volume:**  
  🟡 **NEEDS JAMES INPUT:** Approximate years of publication and issue count.

- **Person who owns access:**  
  🟡 **NEEDS JAMES INPUT:** Who manages newsletter distribution — James directly / Peter / Shahd?

---

### CORPUS 9 — Academy Weekly Newsletter Archive

- **Where it lives:**  
  🟡 **NEEDS JAMES INPUT:** Platform unknown. Not referenced by name in any session context.  
  🔵 **INFERRED:** Likely same email marketing platform as PreReal RE Newsletter (Corpus 8) if one platform handles both. Or could be the academy platform's built-in notification system (Lovable-based app).  
  *(Source: James verbatim 2026-05-03 7:28 AM ET — identified as a corpus in the vision overlay; added to Section 5 of LLM_INGESTION_PLAN as Corpus 9)*

- **Access mechanism:**  
  🟡 **NEEDS JAMES INPUT:** Unknown. Same as Corpus 8, or different?

- **Existing organization:**  
  🟡 **NEEDS JAMES INPUT:** When did the Academy newsletter start? Is it weekly? Is it currently active or discontinued?

- **Known gaps:**  
  🟡 **NEEDS JAMES INPUT:** All details unknown.

- **Sensitivity flags:**  
  🟢 Public-facing subscriber content = auto-ingest authorized per P47 (public availability is the gate)  
  🟡 **NEEDS JAMES INPUT:** Any subscriber-exclusive paid content or pilot/beta content that should be excluded?

- **Estimated total volume:**  
  🟡 **NEEDS JAMES INPUT:** Entirely unknown.

- **Person who owns access:**  
  🟡 **NEEDS JAMES INPUT:** Mike, Peter, or Shahd?

---

### CORPUS 10 — Whiteboard Archive
*(Already pre-filled in LLM_INGESTION_PLAN — reproducing with full sourcing for completeness)*

- **Where it lives:**  
  🟢 **Workspace:** `foundation_docs/JAMES_WHITEBOARD_ARCHIVE_RAW.md` (raw transcription) + `foundation_docs/JAMES_WHITEBOARD_ARCHIVE_SYNTHESIS.md` (synthesis)  
  🟢 **Drive originals:** `📷 Butcher Paper & Whiteboards` subfolder, Folder ID `1NBE0y6SY_rNX1tknQdz2TV-ZdZXfsNBC`, within "Brainstorm Archive — Raw" Shared Drive  
  🟢 **Also in Drive root:** "Brainstorm Archive — Raw" folder ID `1TNcGGo4X1MPn0R84S8ZAFY8hFDuZvx`, URL `https://drive.google.com/drive/folders/1TNcGGo4X1MPn0R84S8ZAFY8hHoFDuZvx`  
  *(Source: daily_report_config.json; JAMES_WHITEBOARD_ARCHIVE_RAW.md header: "Source: Drive folder '📷 Butcher Paper & Whiteboards'")*

- **Access mechanism:**  
  🟢 Direct workspace access for the processed .md files  
  🟢 Service account has Drive Content Manager access for originals  

- **Existing organization:**  
  🟢 150 HEIC images + 5 JPGs + 4 MOV walkthroughs processed; indexed by date (2017 onward)  
  🟢 Dated where EXIF/visible date available; undated artifacts flagged as undated  

- **Known gaps:**  
  🟢 Some images had no visible dates — indexed as undated  
  🟢 Original photos remain in Drive for re-examination if needed  

- **Sensitivity flags — EXCLUSION LIST (already locked):**  
  🟢 7 DEAD items tagged `archive_status: DEAD, query_filter: historical_only` — see Cross-Corpus Rules above  
  🟢 Exclusion filter already implemented in `/home/user/workspace/ingestion/exclusion_filter.py`  

- **Estimated total volume:** 🟢 ~50,000+ words across raw + synthesis files

- **Person who owns access:** 🟢 Max / workspace  

**STATUS: READY FOR TRANCHE 1 — NO JAMES INPUT NEEDED. Exclusion list locked. Ingestion engine honors exclusion metadata.**

---

### CORPUS 11 — Vision Interview
*(Already pre-filled in LLM_INGESTION_PLAN — reproducing with sourcing)*

- **Where it lives:** 🟢 `foundation_docs/JAMES_PLATFORM_VISION_INTERVIEW.md` — workspace  
- **Access mechanism:** 🟢 Direct workspace access  
- **Existing organization:** 🟢 Structured Q1 through Q5.5; James verbatim + Max reads clearly distinguished  
- **Known gaps:** 🟢 None — Q5 block complete as of 2026-05-02; interview is ongoing and append-only  
- **Sensitivity flags:** 🟢 None — fully index; this is the authoritative thesis document  
- **Estimated total volume:** 🟢 ~50,000+ words  
- **Person who owns access:** 🟢 Max / workspace  

**STATUS: READY FOR TRANCHE 1 — NO JAMES INPUT NEEDED**

---

### CORPUS 12 — Ongoing Max–James Chat Sessions

- **Where past sessions live:**  
  🟢 `past_session_contexts/sessions/` — four date-range subfolders, all accessible:
  - `2026-03-30_2026-04-05/` — 2 session conversation files (earliest sessions)
  - `2026-04-13_2026-04-19/` — 1 session (92,614 bytes)
  - `2026-04-20_2026-04-26/` — 1 session (809,031 bytes)
  - `2026-04-27_2026-05-03/` — 1 session (1,189,851 bytes — most recent and largest)  
  *(Source: glob output; sessions_index.md)*

- **Access mechanism:**  
  🟢 Past sessions: direct workspace access, no credentials  
  🟡 **NEEDS JAMES INPUT:** Forward capture — confirm: should Max ingest ALL sessions going forward, or only sessions James flags as foundational? (Section 8, Q5 in ingestion plan — not yet answered)

- **Existing organization:**  
  🟢 Sessions organized by date range and session ID  
  🟢 `sessions_index.md` and `asset_index.md` provide navigation

- **Known gaps:**  
  🟢 Some early sessions (2026-03) may predate the current session file format; short conversations  
  🟢 Real-time forward capture architecture is gated on QA Tranche 3 (Mechanism 6 — Communication Pattern Auditor)

- **Sensitivity flags:**  
  🟢 Sessions contain operational details (API key references, system paths) — filtered at chunk level; James's thesis utterances are the target  
  🟢 P45 (Two-Path Meeting Capture) — Path B (closed political meetings, attorney-client) content is workspace-only and must be tagged `silo: workspace_only` before any corpus indexing  
  🟢 P37/P62 (Internal Insight Silos) — strategic insights about specific individuals, board dynamics, confidential reads = workspace-only, never surfaced in external queries  
  🟢 Max's own output in sessions is NOT James's voice — speaker-tagging rule applies; only James-attributed utterances feed voice corpus

- **Estimated total volume:**  
  🟢 Current corpus: ~2.1M characters across all sessions (~1.5M words); growing with each session  
  🟢 All 4 session files already indexed in Tranche 1 build (ingestion/store.py)  
  *(Source: workspace glob file sizes; ingestion/README.md)*

- **Person who owns access:**  
  🟢 Max / workspace for past sessions  
  🟢 Forward capture architecture = TBD pending QA Tranche 3 and James Q5 answer

**STATUS: PAST SESSIONS READY FOR TRANCHE 1 (already indexed). FORWARD CAPTURE GATED ON QA TRANCHE 3 + JAMES Q5 ANSWER.**

---

## ADDITIONAL DECISIONS ALREADY MADE (not corpus-specific but ingestion-critical)

### Ingestion Cadence — Locked 2026-04-28

**Source:** Session 2026-04-28 Turn 182 (James authorized); session 2026-04-29 Turn 222 (cron confirmed live).

| Source | Cadence | Status |
|---|---|---|
| Social (IG, TT, X, FB, YT) | Daily 03:00 ET (Cron A: `0 7 * * *`) | ✅ Live |
| Drive folder "JP Brand Manager" | Daily | ✅ Live |
| Podcast (RSS, 2 feeds) | Weekly (Mondays) | ✅ Live |
| Academy (`prendamanoacademy.com`) | Weekly (Mondays) | ✅ Live |
| Corpus distillation (profile rebuild) | Weekly Sunday midnight | ✅ Live |
| Approval queue feedback | Real-time on submission | ✅ Live (event-driven) |
| Threads | Deferred | ⚠️ Pending Threads OAuth |

### Workspace Corpus Ingest Tools — Already Built

- `voice_corpus_ingest.py` — daily and weekly source pulls with dedup
- `voice_corpus_distill.py` — weekly profile rebuild with version archiving  
- `voice_profile_amendments.jsonl` — approval queue feedback integration (P14 path)  
- `ingestion/` — Tranche 1 chunker, embedder, vector store, exclusion filter, query interface (28/28 tests passing as of 2026-05-04)

### Drive Folder Structure — Confirmed

| Folder | ID | Role |
|---|---|---|
| JP Brand Manager (Shared Drive) | `0AE5JjXEpb8rsUk9PVA` | Top-level shared drive |
| Podcast Inbox | `1HmwcX4l9Zg_yBpgkfZohRJo1jplfZw3i` | New podcast file drops |
| JP Brand Images | `1z73d_QA0MoCOiNcTzHQCGkp1J9YUIhij` | Generated images backup |
| JP Talking Clips | `13L238IroOREG2Xtr_yIw4oY_B5gicfkP` | Video clips backup |
| Brainstorm Archive — Raw | `1TNcGGo4X1MPn0R84S8ZAFY8hHoFDuZvx` | Raw brainstorm archive |
| 📷 Butcher Paper & Whiteboards | `1NBE0y6SY_rNX1tknQdz2TV-ZdZXfsNBC` | Whiteboard originals |
| 🔮 Prediction Archive | `1ld3zhsT7sKDgOF-w92Bl_fhdXmtuH367` | Prediction clips/docs |
| 🎙️ Brainstorm Recordings | `1s5zvb7z-sr6531TG3L9Vws-QPxCmKbdj` | Audio brainstorm recordings |
| 💾 Digital Docs | `10Sm4DqmTnpwDMHg8xRz0KV3C3VbLBPp-` | Digital document archive |
| 📂 Processed (Max only) | `1S-hc6e4RFcGtxJeYWgBrcE4kPpys815l` | Max's output staging |

---

## SUMMARY

### Pre-fill counts by corpus

| Corpus | Fields Pre-Filled (🟢) | Fields Inferred (🔵) | Fields Needing James (🟡) |
|---|---|---|---|
| C1 — May 1–3 Thread | 7/7 | 0 | 0 |
| C2 — Academy Raw Transcripts | 5 | 2 | 2 |
| C3 — Academy Website | 4 | 1 | 0 |
| C4 — Podcasts | 5 | 0 | 2 |
| C5 — Predictions Archive | 4 | 1 | 3 |
| C6 — Press Coverage | 3 | 2 | 3 |
| C7 — Social Channels | 5 | 1 | 3 |
| C8 — RE Newsletter | 0 | 2 | 5 |
| C9 — Academy Newsletter | 0 | 1 | 5 |
| C10 — Whiteboard Archive | 7/7 | 0 | 0 |
| C11 — Vision Interview | 7/7 | 0 | 0 |
| C12 — Chat Sessions | 5 | 1 | 1 |
| **TOTAL** | **52** | **11** | **24** |

- **Total fields pre-filled from existing decisions:** 52 of 87 total
- **Total fields inferred (need James confirmation):** 11
- **Total fields genuinely requiring James input:** 24
- **Original plan had 9 corpora × ~8 fields = 72+ questions for James.** The actual remaining ask is **24 unknown fields**, concentrated in two corpora (RE Newsletter and Academy Newsletter) where the platform has never been named in any session.

### Estimated James review time

- **Confirm or correct 52 pre-filled items:** ~5 minutes scanning (most are confirmations, not corrections)
- **Confirm or deny 11 inferred items:** ~3 minutes
- **Fill in 24 genuinely unknown fields:** ~15 minutes (the newsletter platform questions are the biggest unknowns; all others are 1-2 word fills)
- **Total estimated time:** ~20–25 minutes (vs. ~60–90 minutes for a cold fill-in of 9 corpus forms)

### Highest-priority unknowns for James (must fill before Tranche 2 can proceed)

1. **Newsletter platform** (Corpora 8 + 9): What email marketing platform does James use? This is the single largest unknown. One answer unlocks both corpus forms.
2. **Academy newsletter existence/cadence** (Corpus 9): Does an Academy-specific newsletter exist separately from the RE newsletter?
3. **Academy Drive subfolder** (Corpus 2): What is the exact subfolder name for raw module exports in the Drive?
4. **Social date gate** (Corpus 7): Is there a pre-[date] cutoff for social historical import, or ingest everything?
5. **Predictions structured fields** (Corpus 5): Confirm the 6 categories Peter used and whether a dashboard (separate from the docx) exists.

---

*Pre-fill produced by Max. All 🟢 items are cited to source documents with dates. All 🔵 items are Max's inferred reads marked for James confirmation. All 🟡 items represent genuine gaps. No modifications made to LLM_INGESTION_PLAN_2026-05-04.md — this document is the review layer; James confirms, then the plan is updated.*
