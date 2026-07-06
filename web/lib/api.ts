// Typed client for the JAMES OS FastAPI backend. Calls are same-origin
// (Next rewrites proxy to the backend) so no CORS, no base URL juggling.

export type Citation = { event_id: string; span: string; confidence: number };

export type SavedPost = {
  id: string;
  source_platform: string;
  post_id: string;
  author: string;
  text: string;
  likes: number;
  comments: number;
  url: string;
  niche: string;
  status: "saved" | "drafted";
  action_id: string | null;
  created_at: string;
};

/** One prior turn in a conversational Ask thread. */
export type AskTurn = { role: "user" | "assistant"; content: string };

/** The brand's identity — the Intake's output, read by every engine. */
export type BrandProfile = {
  tenant_id?: string;
  kind: "person" | "asset" | "institution" | "politician";
  identity: { name?: string; mission?: string; positioning?: string; audience?: string };
  goals: string[];
  pillars: string[];
  taboos: string[];
  platforms: string[];
  peers: string[];
  constraints: Record<string, unknown>;
  intake_done: boolean;
  updated_at?: string;
};

/** The Researcher agent's "is this your brand?" proposal. */
export type BrandResearchProposal = {
  proposal: {
    kind: BrandProfile["kind"];
    identity: { name: string; mission: string; positioning: string; audience: string };
    goals: string[];
    pillars: string[];
    peers: string[];
  };
  summary: string;
  sources: string[];
};

/** One deep-interview question (Interviewer asks, Researcher answers,
 *  the human confirms). */
export type BrandQuestion = {
  id: string;
  dimension: string;
  question: string;
  answer: string | null;
  source: "open" | "user" | "research";
  confidence: number;
  sources: string[];
  status: "open" | "answered" | "confirmed" | "dismissed";
};

export type InterviewStats = {
  open: number; answered: number; confirmed: number;
  dismissed: number; total: number;
};

/** A piece the brand manager proposes on its own (daily research etc.). */
export type ContentSuggestion = {
  id: string;
  source: string;
  title: string;
  topic: string;
  format: "post" | "reel";
  why: string;
  status: "suggested" | "accepted" | "dismissed";
  action_ref: string | null;
  created_at: string | null;
};

/** Result of developing a weekly thesis (intelligence + white paper). */
export type ThesisDevelopResult = {
  thesis_doc_id: string;
  thesis_filename: string;
  theme: string;
  claims: string[];
  intelligence: {
    topic: string | null;
    coverage?: unknown;
    briefs_saved: number;
    synthesis_file: string | null;
  };
  whitepaper: {
    title: string | null;
    sections: number;
    filename: string | null;
    document_id: string | null;
    low_grounding?: boolean;
  };
  // Present only on full-week runs:
  content_pack?: ContentPackResult;
  podcast?: {
    title?: string; audio_url?: string; duration_s?: number;
    skipped?: string; error?: string;
  };
};

export type ThesisDevelopJob = {
  status: "running" | "done" | "failed";
  stage?: "reading" | "researching" | "writing" | "content" | "podcast";
  doc_id?: string;
  full?: boolean;
  result?: ThesisDevelopResult;
  error?: string;
};

/** A content pack fanned out from a white paper (posts + reels). */
export type ContentPackResult = {
  source_filename?: string;
  posts_queued: number;
  reels_started: number;
  posts?: { title: string }[];
  reels?: { title: string; template?: string }[];
  errors?: string[];
  error?: string;
};

export type ContentPackJob = {
  status: "running" | "done" | "failed";
  stage?: "angles" | "posts" | "reels";
  doc_id?: string;
  result?: ContentPackResult;
  error?: string;
};

/** A generated podcast episode (narrated with the brand's cloned voice). */
export type PodcastEpisode = {
  id: string;
  status: string;               // pending | approved | rejected …
  created_at: string | null;
  title: string;
  description: string;
  audio_url: string;
  duration_s: number;
  source_filename: string;
};

export type PodcastJob = {
  status: "running" | "done" | "failed";
  stage?: "scripting" | "narrating" | "publishing";
  doc_id?: string;
  result?: {
    title: string; description: string; audio_url: string;
    duration_s: number; action_id: string; source_filename: string;
  };
  error?: string;
};

export type AskResponse = {
  response: string;
  citations: Citation[];
  refused: boolean;
  refusal_reason: string | null;
  confidence: number;
  retrieved_event_ids: string[];
  model: string;
  latency_ms: number;
};

/** The full picture of what steers generation — shown on /brand so the
 *  "what's the brain behind this?" question always has a real answer. */
export type BrandBrain = {
  counts: {
    manual_rules: number;
    learned_rules: number;
    voice_exemplars: number;
    knowledge_docs: number;
    knowledge_chunks: number;
  };
  learned: {
    id: string;
    reason: string;
    platform: string;
    topic: string;
    created_at: string | null;
  }[];
};

export type PlugIn = {
  id: string;
  slot: string;
  name: string;
  content: Record<string, unknown>;
  version: number;
  active: boolean;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const u = (path: string) => `${API_BASE}${path}`;

/** Resolve a served media path (e.g. /media-files/...) to a full URL the
 *  browser can load, honoring the configured API origin. External URLs
 *  (http...) are returned unchanged. */
export const mediaUrl = (uri: string) =>
  uri.startsWith("http") ? uri : `${API_BASE}${uri}`;

// Every request includes credentials so the session cookie travels
// cross-origin (3000 → 8001 in dev). The backend's CORS config has
// allow_credentials=true to match.
const FETCH_OPTS: RequestInit = { credentials: "include" };

// On a 401 from any helper, redirect the browser to /login while
// preserving the current path as a `next` query param so the user
// lands back where they were after signing in. Skips when already
// on an auth page so we don't bounce-loop, and skips the /auth/me
// probe (callers handle it themselves to decide whether to render).
function _handle401(path: string): void {
  if (typeof window === "undefined") return;
  if (path === "/auth/me") return;
  const here = window.location.pathname;
  if (here === "/login" || here === "/signup") return;
  const next = encodeURIComponent(here + window.location.search);
  window.location.replace(`/login?next=${next}`);
}

async function jdel<T>(path: string): Promise<T> {
  const r = await fetch(u(path), { method: "DELETE", ...FETCH_OPTS });
  if (r.status === 401) { _handle401(path); throw new Error("Not authenticated"); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function jpatch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(u(path), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...FETCH_OPTS,
  });
  if (r.status === 401) { _handle401(path); throw new Error("Not authenticated"); }
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function jput<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(u(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...FETCH_OPTS,
  });
  if (r.status === 401) { _handle401(path); throw new Error("Not authenticated"); }
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(u(path), { cache: "no-store", ...FETCH_OPTS });
  if (r.status === 401) { _handle401(path); throw new Error("Not authenticated"); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(u(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...FETCH_OPTS,
  });
  if (r.status === 401) { _handle401(path); throw new Error("Not authenticated"); }
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

/** Shared helper for multipart fetches that should usually return JSON.
 *  When the server returns non-JSON (Next dev proxy hiccup, gateway
 *  HTML error page, etc.) we surface the HTTP code + a short body
 *  snippet instead of letting "Unexpected token 'I'…" leak through. */
async function _safeJsonOrThrow<T>(r: Response): Promise<T> {
  const text = await r.text();
  let parsed: { detail?: string } | T | null = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (!r.ok) {
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      throw new Error((parsed as { detail: string }).detail || `HTTP ${r.status}`);
    }
    // Non-JSON error body — show the HTTP code + a snippet so the
    // user has SOMETHING actionable instead of a JSON-parse error.
    const snippet = text.slice(0, 120).replace(/\s+/g, " ").trim();
    throw new Error(
      snippet
        ? `HTTP ${r.status} — ${snippet}${text.length > 120 ? "…" : ""}`
        : `HTTP ${r.status}`,
    );
  }
  if (!parsed) {
    throw new Error("Server returned empty or invalid response");
  }
  return parsed as T;
}

export type QueueItem = {
  id: string;
  status: string;
  platform: string;
  pillar: string;
  format: string;          // 'video', 'reel_script', 'instagram_post', etc.
  content: string;
  caption: string;
  voiceScore: number | null;
  flagged?: boolean;       // failed voice-QA — needs override to approve
  qaDrift?: string[];      // voice-QA drift notes

  imageUrl?: string | null;   // post-image attachments
  mediaUrl?: string | null;   // rendered video URL (Creatomate / Backblaze)
  proposedBy: string;
  createdAt: string | null;
  scheduledFor?: string | null;  // ISO datetime once scheduled
  reason: string | null;
};

export type QueueStats = {
  pending: number;
  approved: number;
  rejected: number;
  executed: number;
  total: number;
};

export type CredentialField = {
  name: string;
  label: string;
  group: string;
  secret: boolean;
  placeholder: string;
  configured: boolean;
  masked: string;
  source: "ui" | "env" | "none";
};

export type CredentialStatus = {
  fields: CredentialField[];
  research_provider: string;
  note: string;
};

export type IntegrationCheck = {
  checked_at: string;
  results: Record<string, { status: string; detail: string }>;
};

export type ContentBrief = {
  platform: string;
  format: string;
  pillar: string;
  topic: string;
  research_subject: string;
  extra_instructions: string;
};

export type QAVerdict = {
  voice_score: number;
  passed: boolean;
  drift: string[];
};

export type ContentDraft = {
  status: "generated" | "flagged" | "not_generated";
  draft: string;
  platform: string;
  format: string;
  pillar: string;
  angle: string;
  voice_score: number;
  qa: QAVerdict | null;
  grounded_event_ids: string[];
  memory_used: Record<string, number>;
  action_id: string | null;
  model: string;
  latency_ms: number;
  note: string | null;
};

export type ResearchSource = { url: string; title: string };

export type ResearchResponse = {
  subject: string;
  provider: string;
  summary: string;
  findings: string[];
  sources: ResearchSource[];
  stored_event_ids: string[];
  ingested_into_memory: boolean;
  note: string | null;
};

export type VideoJob = {
  id: string;
  provider: string;
  prompt: string;
  prompt_image: string | null;
  status: "queued" | "submitted" | "processing" | "succeeded" | "failed";
  provider_job_id: string | null;
  result_url: string | null;
  error: string | null;
  source_action_id: string | null;
  queued_action_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type Trend = {
  event_id?: string;
  platform: string;
  handle: string;
  url: string;
  caption: string;
  has_transcript: boolean;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  posted_at: string;
  thumbnail: string;
  outlier_score: number;
  velocity: number;
};

export type TrendResult = {
  provider?: string;
  topic?: string;
  found?: number;
  stored_event_ids?: string[];
  trends: Trend[];
  note: string | null;
};

export type Creator = {
  platform: string;
  handle: string;
  // Optional context — populated when the watchlist was seeded from a
  // curated source (e.g. Speaking Targets). Older entries lack these.
  name?: string;
  interests?: string[];
};

export type Guardrail = {
  id: string;
  reason: string;
  platform: string;
  topic: string;
  created_at: string | null;
};

export type MediaRole =
  | "style_reference"
  | "james_clip"
  | "broll"
  | "post_image"
  | "hero_photo"
  | "hero_video"
  | "music"
  | "sfx"
  | "brand_logo";

/** Aesthetic preset for /images/generate. Each maps to a distinct
 *  prompt prefix on the backend (see imagegen.POST_STYLES). Same topic
 *  + different style = different render. */
export type TopicIdea = {
  id?: string;
  title: string;
  topic: string;
  pillar: string;
  trend_basis: string;
  status?: "pending" | "accepted";
};

export type PostImageStyle = "editorial" | "photoreal" | "minimal" | "bw_photo";

export type StyleFingerprint = {
  hook?: string;
  structure?: string | string[];
  pacing?: string;
  captions?: string;
  visual_style?: string;
  replication_tips?: string | string[];
  error?: string;
};

export type MediaAsset = {
  id: string;
  role: MediaRole;
  title: string;
  platform: string;
  source_type: "upload" | "url" | "generated";
  uri: string;
  mime: string;
  duration: number;
  tags: string[];
  notes: string;
  transcript: string;
  analyzed: boolean;
  analysis_status: "none" | "pending" | "done" | "failed" | "unsupported";
  analysis: { fingerprint?: StyleFingerprint; note?: string };
  mute_audio?: boolean;

  created_at: string;
  updated_at: string;
};

// ── Style Templates (Design Inspector → library of trending styles) ──
// A reference video, reverse-engineered into a reusable production
// template: a beat-by-beat map of where every element sits and what the
// sound is doing, in the assembly engine's own vocabulary.
export type TemplateSegment = {
  start?: number;
  end?: number;
  role?: string;
  visual?: string;
  speaker?: { present?: boolean; position?: string; framing?: string };
  on_screen_text?: { present?: boolean; example?: string; position?: string; style?: string };
  logo?: { present?: boolean; position?: string };
  transition_out?: string;
};

export type TemplateLayout = {
  type?: string;                 // full_frame | split_horizontal | split_vertical | pip | grid | other
  persistent?: boolean;
  description?: string;
  regions?: { position?: string; contains?: string }[];
};

export type TemplateSpec = {
  style_name?: string;
  summary?: string;
  distinctive_features?: string[];  // the point: what's NEW/copyable about this style
  layout?: TemplateLayout;          // composition (splits, PIP) captured in open form
  format_type?: string;
  aspect_ratio?: string;
  hook?: string;
  pacing?: { energy?: string; avg_cut_seconds?: number; notes?: string };
  segments?: TemplateSegment[];
  logo?: { present?: boolean; position?: string; persistence?: string };
  captions?: { present?: boolean; position?: string; look?: string; animation?: string; preset_guess?: string };
  audio?: {
    music?: { present?: boolean; type?: string; mood?: string };
    voiceover?: boolean;
    sfx?: string;
    sound_signature?: string;
  };
  color_palette?: string;
  vibe?: string;
  production_mode?: string;
  replication_recipe?: string[];
};

export type StyleTemplate = {
  id: string;
  reference_media_id: string | null;
  name: string;
  slug: string;
  summary: string;
  format_type: string;
  production_mode: string;
  duration: number;
  template: TemplateSpec;
  transcript: string;
  status: "pending" | "ready" | "failed";
  tags: string[];
  trending_score: number;
  created_at: string;
  updated_at: string;
};

export type Scene = {
  index: number;
  label?: string;
  kind: "talking_head" | "broll";
  source: "avatar" | "james_clip" | null;
  voiceover: string;
  on_screen_text: string;
  visual_prompt: string;
  duration: number;
  url?: string;
  provider_url?: string;          // transient URL from HeyGen/Runway before we re-host
  persisted?: boolean;            // true once the clip lives on our own storage
  clip_status?: "ok" | "stub";
  note?: string;
  // production layer (optional; older plans may lack these)
  audio_music?: "upbeat" | "calm" | "dramatic" | "tension" | "none" | string;
  audio_sfx?: string;
  branding_logo?: boolean;
  branding_position?: "bottom-right" | "bottom-center" | "top-right" | "none" | string;
  transition_in?: "cut" | "fade" | "slide" | string;
};

export type ScenePlan = {
  title: string;
  scenes: Scene[];
  structure?: string[];
  james_clips_available?: number;
  error?: string;
};

export type ComposeResult = {
  idea?: { title: string; topic: string; trend_basis?: string };
  script: string;
  voice_score?: number;
  voice_status?: string;
  title: string;
  scenes: Scene[];
  intel?: { provider?: string; summary?: string; sources?: string[] } | null;
  platform?: string;
  aspect?: string;
  error?: string;
};

export type Production = {
  id: string;
  status: "queued" | "planning" | "rendering_clips" | "assembling" | "succeeded" | "failed" | "canceled";
  mode?: "mixed" | "avatar_only" | "timeline" | "story_audio" | "avatar_story_mix" | "engaging_avatar" | "long_form_reel" | "hero_clone";
  title: string;
  platform: string;
  aspect: string;
  script: string;
  plan: ScenePlan | Record<string, never>;
  scenes: Scene[];
  caption_style?: string;
  image_style?: string;
  final_url: string | null;
  // Manager review state — null until someone Approves / Rejects on
  // the Output Library. Drives the chip + the post-review summary.
  review_status?: "approved" | "rejected" | "approved_with_notes" | null;
  review_reason?: string | null;
  reviewed_at?: string | null;
  error: string | null;
  avatar_provider: string;
  broll_provider: string;
  assembly_provider: string;
  queued_action_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  // Server-computed render progress for the tracker (videos only).
  progress?: {
    stage: string;
    stage_index: number;
    total_stages: number;
    label: string;       // "what it's doing" right now
    pct: number;         // 0-100
    elapsed_s: number;
    eta_s: number;       // approximate seconds remaining
  };
};

/** One assemblable clip surfaced by /video/clips/library — used as a source
 *  drop in the timeline editor. `assemblable` is false for clips Creatomate
 *  can't fetch (e.g. local /media-files/* paths from pre-Storage uploads). */
export type ClipLibraryItem = {
  kind: "production_final" | "production_scene" | "reference";
  label: string;
  url: string;
  duration: number | null;
  aspect: string | null;
  source_id: string;
  source_meta: Record<string, unknown>;
  assemblable: boolean;
};

/** A long-form source uploaded for the Long Form Cutter. After
 *  ingest_source runs it carries a transcript, a duration, and a
 *  list of candidates the LLM picked as standalone reel windows. */
export type LongSource = {
  id: string;
  title: string;
  source_url: string;
  audio_url: string;
  duration_s: number;
  status: "uploading" | "transcribing" | "analyzing" | "ready" | "failed";
  error: string | null;
  created_at: string;
  updated_at: string;
};

/** One filed company document in the Knowledge Base ledger. */
export type KnowledgeDoc = {
  id: string;
  filename: string;
  original_name: string;
  category: string;
  version: number;
  status: string;
  sensitivity: string;
  flagged_for_review: boolean;
  review_reason: string | null;
  mime_type: string | null;
  size_bytes: number;
  notes: string | null;
  chunks: number;
  business_unit: string | null;
  asset_class: string | null;
  doc_type: string | null;
  descriptor: string | null;
  entity_id: string | null;
  silo_id: string | null;
  indexing_status: "indexed" | "skipped" | "failed" | "pending";
  indexing_error: string | null;
  indexed_at: string | null;
  created_at: string;
};

/** Per-file outcome of a knowledge ingest (single file or ZIP batch). */
export type KnowledgeIngestResult = {
  ok: boolean;
  expanded: boolean;
  total: number;
  filed: number;
  skipped: number;
  failed: number;
  results: {
    originalName: string; ok: boolean; skipped?: boolean;
    reason?: string | null; filename?: string; fileId?: string; chunks?: number;
  }[];
};

/** A generated, corpus-grounded white paper (Knowledge Base intelligence). */
export type Whitepaper = {
  ok: boolean;
  topic: string;
  low_grounding: boolean;
  title: string;
  subtitle: string;
  abstract: string;
  sections: { heading: string; body: string }[];
  key_takeaways: string[];
  structure_summary: string;
  exemplars: { url: string; title: string | null }[];
  sources: { n: number; filename: string }[];
  markdown: string;
  file: { id: string | null; filename: string | null; chunks: number } | null;
};

/** A silo (project/topic corpus grouping). */
export type Silo = { id: string; name: string; description: string | null; files?: number };

/** A registered entity (stable EntityID, e.g. TBM-H-001). */
export type KbEntity = {
  id: string; business_unit: string; entity_type_code: string;
  display_name: string; notes: string | null; created_at: string;
};

/** An action item mined from a document/transcript. */
export type Commitment = {
  id: string; text: string; owner: string; author: string;
  entity_id: string | null; source_file_id: string | null;
  due: string | null; status: "open" | "in_progress" | "blocked" | "done" | "missed";
  notes: string | null; created_at: string;
};

/** Topic-intelligence result (multi-angle sweep + synthesis). */
export type IntelBrief = {
  ok: boolean;
  topic: string;
  coverage: { had_enough: boolean; existing_docs: number; top_similarity: number; researched: boolean };
  planned: boolean;
  gathered: { angle: string; label: string; rationale: string; citations: number; saved: boolean; error?: string | null }[];
  synthesis: { answer: string; file: { id: string | null; filename: string | null } | null };
  sources: { n: number; filename: string; origin: string }[];
};

/** Cross-silo portfolio synthesis. */
export type PortfolioSynthesis = {
  ok: boolean;
  executive_summary: string;
  points: { type: string; title: string; insight: string; silos: string[]; evidence: number[] }[];
  silos: { id: string; name: string }[];
  sources: { n: number; filename: string; silo: string }[];
};

/** One clippable topic-reel found inside a source, with its clip lifecycle. */
export type LibCandidate = {
  id: string;
  hook_quote: string;
  summary: string;
  score: number;
  start_s: number;
  end_s: number;
  state: "clippable" | "clipping" | "clipped";
  production_id: string | null;
  final_url: string | null;
  review_status: string | null;
};

export type LibSource = {
  id: string;
  title: string;
  status: string;
  duration_s: number;
  created_at: string | null;
  candidates: LibCandidate[];
};

/** One segment inside a topic suggestion (a candidate window, possibly
 *  from a different footage than its siblings). */
export type ClipTopicSegment = {
  candidate_id: string;
  source_id: string;
  start_s: number;
  end_s: number;
  quote?: string;
  source_title?: string;
};

/** A topic the clipper suggests it can build — click Build and it cuts
 *  every segment, stitches them, and runs the full engaging treatment. */
export type ClipTopic = {
  id: string;
  title: string;
  hook: string;
  why: string;
  score: number;
  segments: ClipTopicSegment[];
  state: "suggested" | "building" | "built";
  production_id: string | null;
  final_url: string | null;
};

export type ContentLibrary = {
  summary: { sources: number; clippable: number; clipping: number; clipped: number };
  sources: LibSource[];
  topics: ClipTopic[];
  topics_mining: boolean;
};

/** A saved speaker for on-screen name-tags. */
export type Speaker = {
  id: string;
  handle: string;
  subtitle: string;
  face_ref: string;
  created_at: string;
};

/** A distinct on-camera speaker detected in a source (the "who is this?" step). */
export type DetectedSpeaker = {
  face_x: number;
  label: string;
  preview_url: string;
};

/** One assigned identity saved on a source → drives a name-tag at render. */
export type SpeakerTag = {
  face_x: number;
  handle: string;
  subtitle: string;
};

/** One LLM-picked 30-45s window inside a LongSource. Each candidate
 *  can be rendered into a video_production with mode=long_form_reel. */
export type ReelCandidate = {
  id: string;
  source_id: string;
  start_s: number;
  end_s: number;
  hook_quote: string;
  summary: string;
  score: number;
  production_id: string | null;
  production_status?: string | null;   // queued|processing|succeeded|failed
  dismissed: boolean;
  created_at: string;
};

export type AutopilotConfig = {
  enabled: boolean;
  daily_count: number;
  platforms: string[];
  format: string;
  hour: number;
  topic_hint: string;
  use_style_templates?: boolean;   // video reels cycle distinct library styles
  broll_engine?: string;           // '' | 'runway' | 'higgsfield' for B-roll
  caption_mode?: string;           // 'rotate' | 'smart' | 'template'
  default_caption_style?: string;  // reel caption look, e.g. 'bold_pop' (white)
  last_run_date: string;
};

export type AutopilotRun = {
  id: string;
  status: "running" | "succeeded" | "failed";
  trigger: "manual" | "scheduled";
  stage: string;
  requested: number;
  generated: number;
  queued: number;
  ideas: { title: string; topic: string; pillar?: string; trend_basis?: string }[];
  results: { title: string; platform: string; voice_score: number; status: string; action_id: string | null }[];
  research: {
    provider?: string;
    subject?: string;
    summary?: string;
    findings?: string[];
    sources?: string[];
    trends?: string[];
    // Speaking-targets cohort matched on interests for this run's topic.
    // Empty when no creator's interests overlap, or when the watchlist
    // hasn't been scraped yet (creators present, trends empty).
    cohort_creators?: { name: string; platform: string; handle: string; interests?: string[] }[];
    cohort_trends?: { platform: string; handle: string; caption: string; outlier_score: number; views: number; url: string }[];
  };
  error: string | null;
  created_at: string;
  completed_at: string | null;
};

export type ChangeItem = {
  id: string;
  area: string;
  diagnosis: string;
  plain_english: string;
  kind: "live_config" | "code_change";
  config_key: string | null;
  config_value: unknown;
  confidence: number;
  status: "applied" | "queued" | "proposed" | "done" | "dismissed";
  created_at: string;
  applied_at: string | null;
  production_id?: string | null;   // traced to a rejected video
  source_event_id?: string | null; // traced to a rejected post
  pr_url?: string | null;          // PR opened by the autonomous agent
};

export const api = {
  health: () => jget<{ status: string }>("/health"),
  getAutopilotConfig: () => jget<AutopilotConfig>("/autopilot/config"),
  setAutopilotConfig: (cfg: Partial<AutopilotConfig>) =>
    jpost<AutopilotConfig>("/autopilot/config", cfg),
  runAutopilot: () => jpost<{ started: boolean; note: string }>("/autopilot/run", {}),
  listAutopilotRuns: () => jget<AutopilotRun[]>("/autopilot/runs"),
  planVideo: (script: string, platform = "instagram", aspect = "9:16") =>
    jpost<ScenePlan>("/video/plan", { script, platform, aspect }),
  composeVideo: (topic_hint: string, platform = "instagram", aspect = "9:16") =>
    jpost<ComposeResult>("/video/compose", { topic_hint, platform, aspect }),
  // Trend-driven batch: generate N ready topic+script options (no topic input).
  startScriptBatch: (body: { n?: number; platform?: string; aspect?: string }) =>
    jpost<{ batch_id: string; status: string }>("/video/scripts/batch", body),
  getScriptBatch: (id: string) =>
    jget<{
      batch_id: string; status: "running" | "done" | "failed"; count?: number;
      niche?: string; error?: string | null;
      scripts: { title: string; topic: string; trend_basis: string; script: string; voice_score?: number; voice_status?: string }[];
    }>(`/video/scripts/batch/${id}`),
  renderScene: (scene: Scene, aspect = "9:16") =>
    jpost<Scene>("/video/render-scene", { scene, aspect }),
  produceVideo: (opts: {
    script?: string; platform?: string; aspect?: string; title?: string;
    scenes?: Scene[];
    mode?: "mixed" | "avatar_only" | "timeline" | "story_audio" | "avatar_story_mix" | "engaging_avatar" | "split_horizontal";
    caption_style?: string;             // blank → AI picks
    image_style?: string;               // blank → cinematic for story modes
  }) => jpost<Production>("/video/produce", {
    platform: "instagram", aspect: "9:16", mode: "mixed", ...opts,
  }),
  // ── Auth ────────────────────────────────────────────────────────
  // Session cookie is httpOnly; client only sees a 200 / 401 from
  // the server. Use whoami() on app load to discover the current
  // user; null = not signed in.
  whoami: async () => {
    const r = await fetch(u("/auth/me"), {
      cache: "no-store", credentials: "include",
    });
    if (r.status === 401) return null;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<{
      id: string; tenant_id: string; email: string;
      display_name: string; role: string;
    }>;
  },
  signup: (body: { email: string; password: string; display_name?: string }) =>
    jpost<{
      id: string; tenant_id: string; email: string;
      display_name: string; role: string;
    }>("/auth/signup", body),
  login: (body: { email: string; password: string }) =>
    jpost<{
      id: string; tenant_id: string; email: string;
      display_name: string; role: string;
    }>("/auth/login", body),
  logout: () => jpost<{ ok: boolean }>("/auth/logout", {}),
  changePassword: (body: { current_password: string; new_password: string }) =>
    jpost<{ ok: boolean }>("/auth/password", body),

  // ── Agent (Ask the memory → "Do" mode) ──────────────────────────
  // Read-only and write-capable tool calls over the system's own
  // feature set. Runs are persisted to agent_runs so the UI can
  // poll for progress and show history.
  agentRun: (prompt: string) =>
    jpost<{ id: string; status: string; prompt: string; created_at: string }>(
      "/agent/run", { prompt },
    ),
  listAgentRuns: (limit = 30) =>
    jget<{
      runs: {
        id: string; prompt: string; status: string; summary: string;
        answer: string; tool_call_count: number;
        created_at: string | null; completed_at: string | null;
      }[];
    }>(`/agent/runs?limit=${limit}`),
  getAgentRun: (id: string) =>
    jget<{
      id: string; prompt: string; status: string; summary: string;
      answer: string; error: string | null;
      tool_calls: {
        name: string; args: Record<string, unknown>;
        result: unknown; ok: boolean; error: string;
        started_at: string; duration_ms: number;
      }[];
      citations: { event_id: string; span: string; confidence: number }[];
      created_at: string | null; updated_at: string | null;
      completed_at: string | null;
    }>(`/agent/runs/${id}`),
  listAgentTools: () =>
    jget<{ tools: { name: string; description: string; writes: boolean }[] }>(
      "/agent/tools",
    ),

  // ── Autopilot bulk generation ───────────────────────────────────
  // One click → N pieces into the queue. mix: "video" = all reels,
  // "text" = all text+image posts, "mixed" = half and half.
  bulkGenerate: (count: number, mix: "mixed" | "video" | "text" = "mixed") =>
    jpost<{ started: boolean; requested: number; mix?: string; note?: string }>(
      "/autopilot/bulk", { count, mix },
    ),

  // ── Xpoz social listening (X / Instagram / TikTok / Reddit) ─────
  xpozAccount: () =>
    jget<{
      configured: boolean;
      plan_name?: string | null; credits?: number | null;
      tracked_items?: number | null; reset_frequency?: string | null;
      credits_remaining?: number | null; extra_credits_remaining?: number | null;
      billing_period?: string | null; next_renewal?: string | null;
      error?: string;
    }>("/research/social/account"),
  xpozSearch: (query: string, platforms: string[] | null, limit = 10, minLikes = 10000) =>
    jpost<{
      query?: string;
      results?: {
        platform: string; id: string; text: string; author: string | null;
        likes: number | null; comments: number | null; shares?: number | null;
        views?: number | null; subreddit?: string | null;
        created: string; url: string | null;
      }[];
      count?: number;
      errors?: Record<string, string>;
      error?: string;
      min_likes?: number;
      filtered_out?: number;
    }>("/research/social/search", { query, platforms, limit, min_likes: minLikes }),
  xpozTrending: (niche: string, platforms: string[] | null, limit = 8, days = 7) =>
    jpost<{
      results?: {
        platform: string; id: string; text: string; author: string | null;
        likes: number | null; comments: number | null; shares?: number | null;
        views?: number | null; subreddit?: string | null;
        created: string; url: string | null;
      }[];
      count?: number; errors?: Record<string, string>; error?: string;
    }>("/research/social/trending", { niche, platforms, limit, days }),
  xpozDraftFromPost: (body: {
    text: string; platform?: string; author?: string | null;
    source_platform?: string | null; format?: string;
  }) =>
    jpost<{ status: string; action_id: string | null; voice_score: number; note: string | null }>(
      "/research/social/draft-from-post", body,
    ),

  // ── Saved-posts shelf: save from search → curate → generate ──────
  xpozSave: (post: Record<string, unknown>, niche: string) =>
    jpost<SavedPost>("/research/social/saved", { post, niche }),
  xpozListSaved: () =>
    jget<{ saved: SavedPost[] }>("/research/social/saved"),
  xpozDeleteSaved: (id: string) =>
    jdel<{ ok: boolean }>(`/research/social/saved/${id}`),
  xpozDraftSaved: (id: string, platform = "instagram", format = "reel_script") =>
    jpost<{ status: string; action_id: string | null; voice_score: number; note: string | null }>(
      `/research/social/saved/${id}/draft`, { platform, format },
    ),

  // ── Live analytics (aggregated across connected accounts) ───────
  analyticsLiveSummary: () =>
    jget<{
      total_followers: number; followers_partial?: boolean;
      total_posts: number; account_count: number; platform_count: number;
      per_platform: { platform: string; accounts: number; posts: number; followers: number; followers_known: boolean }[];
      notes?: string[];
    }>("/analytics/live/summary"),
  analyticsLiveBreakdown: () =>
    jget<{
      rows: {
        provider: string; platform: string; id: string; handle: string;
        name: string; status: string; posts: number;
        followers: number | null; recent_engagement: number | null;
      }[];
      notes?: string[];
    }>("/analytics/live/breakdown"),

  // ── Social Research roster (weekly Apify influencer scrape) ─────
  researchRoster: () =>
    jget<{
      creators: {
        platform: string; handle: string; name?: string;
        interests?: string[]; post_count: number; last_post_at: string | null;
      }[];
    }>("/research/roster"),
  refreshResearchRoster: (limit = 15) =>
    jpost<{ started: boolean }>(`/research/roster/refresh?limit=${limit}`, {}),
  researchRosterStatus: () =>
    jget<{ last_refresh: string | null; due: boolean }>("/research/roster/status"),

  // ── Voice Studio: drop Drive folder/links → transcribe → voice_corpus ──
  ingestVoiceDrive: (folderUrl: string, links: string[], category = "voice_corpus") =>
    jpost<{
      started: boolean; job_id?: string; total: number;
      files?: string[]; list_errors?: { source?: string; error: string }[];
      errors?: { source?: string; error: string }[]; note?: string;
    }>("/voice/ingest-drive", { folder_url: folderUrl, links, category }),
  voiceJobs: () =>
    jget<{
      jobs: {
        id: string; source: string; category: string; status: string; stage: string;
        total: number; processed: number; chunks: number;
        files: { name: string; chunks: number; type: string }[];
        errors: { source?: string; error: string }[];
        created_at: string; completed_at: string | null;
      }[];
    }>("/voice/jobs"),
  voiceCorpus: () =>
    jget<{
      total_chunks: number;
      sources: { filename: string; chunks: number; chars: number }[];
    }>("/voice/corpus"),

  // ── Connected accounts (Meta + PostProxy unified) ──────────────
  listConnections: () =>
    jget<{
      profiles: {
        provider: "meta" | "postproxy";
        platform: string;
        id: string;
        handle: string;
        name: string;
        status: string;
        post_count: number;
        avatar_url: string;
        expires_at: string | null;
        raw: Record<string, unknown>;
      }[];
      providers: Record<string, { configured: boolean; ok?: boolean; error?: string; profile_count?: number; scopes?: string[]; type?: string }>;
      by_platform: Record<string, number>;
      total: number;
    }>("/integrations/connections"),
  listProfilePosts: (provider: string, profileId: string, platform = "", limit = 20) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (platform) q.set("platform", platform);
    return jget<{
      posts: {
        id: string; caption: string; title: string; permalink: string;
        thumbnail: string; platform: string; status: string;
        posted_at: string;
        views: number | null; likes: number | null; comments: number | null;
      }[];
      error?: string;
    }>(`/integrations/profile/${provider}/${encodeURIComponent(profileId)}/posts?${q}`);
  },

  // ── Analytics ───────────────────────────────────────────────────
  // Reads aggregate stats over the scraped social-media posts in the
  // events table. NO scraping happens here — that's /trends/refresh.
  listAnalyticsHandles: () =>
    jget<{ handles: { platform: string; handle: string; name?: string; posts: number; last_post_at: string | null }[] }>(
      "/analytics/handles",
    ),
  // Brand accounts — the handles the BRAND owns. Analytics filters to
  // these only; peer/competitor data lives on /trends/watchlist.
  listBrandAccounts: () =>
    jget<{ accounts: { platform: string; handle: string; name?: string }[] }>(
      "/analytics/accounts",
    ),
  setBrandAccounts: (accounts: { platform: string; handle: string; name?: string }[]) =>
    jpost<{ accounts: { platform: string; handle: string; name?: string }[] }>(
      "/analytics/accounts", { accounts },
    ),
  refreshAnalytics: (limit = 30) =>
    jpost<{ scraped: number; stored: number; provider?: string; note?: string }>(
      `/analytics/refresh?limit=${limit}`, {},
    ),
  analyticsSummary: (opts: { handle?: string; platform?: string; days?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.handle) q.set("handle", opts.handle);
    if (opts.platform) q.set("platform", opts.platform);
    if (opts.days != null) q.set("days", String(opts.days));
    return jget<{
      handle: string; platform: string; days: number;
      post_count: number; views: number; likes: number; comments: number;
      shares: number; engagement: number; engagement_rate: number;
      median_outlier: number; avg_outlier: number;
      by_platform: Record<string, number>;
      best_post: {
        url: string; caption: string; views: number; outlier_score: number;
        platform: string; thumbnail: string; posted_at: string;
      } | null;
    }>(`/analytics/summary?${q}`);
  },
  analyticsPosts: (opts: { handle?: string; platform?: string; days?: number; sort?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.handle) q.set("handle", opts.handle);
    if (opts.platform) q.set("platform", opts.platform);
    if (opts.days != null) q.set("days", String(opts.days));
    if (opts.sort) q.set("sort", opts.sort);
    if (opts.limit != null) q.set("limit", String(opts.limit));
    return jget<{
      posts: {
        platform: string; handle: string; url: string; caption: string;
        thumbnail: string; views: number; likes: number; comments: number;
        shares: number; engagement_rate: number; outlier_score: number;
        velocity: number; posted_at: string;
      }[];
    }>(`/analytics/posts?${q}`);
  },
  analyticsTimeline: (opts: { handle?: string; platform?: string; days?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.handle) q.set("handle", opts.handle);
    if (opts.platform) q.set("platform", opts.platform);
    if (opts.days != null) q.set("days", String(opts.days));
    return jget<{
      timeline: { date: string; views: number; posts: number; engagement: number }[];
    }>(`/analytics/timeline?${q}`);
  },
  analyticsCohort: (opts: { platform?: string; days?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.platform) q.set("platform", opts.platform);
    if (opts.days != null) q.set("days", String(opts.days));
    return jget<{
      rows: { platform: string; handle: string; posts: number; views: number; engagement: number; median_outlier: number }[];
    }>(`/analytics/cohort?${q}`);
  },
  listProductions: () => jget<Production[]>("/video/productions"),
  // Per-production review — feeds the video-feedback learning loop.
  // Empty `note` on approve = pure approval (no memory event). Reject
  // ALWAYS captures a reason; the backend rejects empty strings at
  // the learning layer so we never pollute memory with "rejected"
  // with no reason.
  approveProduction: (id: string, note = "") =>
    jpost<{ ok: boolean; id: string; status: string; learned_id: string | null }>(
      `/video/productions/${id}/approve`, { note },
    ),
  rejectProduction: (id: string, reason: string) =>
    jpost<{ ok: boolean; id: string; status: string; learned_id: string | null }>(
      `/video/productions/${id}/reject`, { reason },
    ),
  cancelProduction: (id: string) =>
    jpost<{ ok: boolean; id: string; status: string | null; reason?: string }>(
      `/video/productions/${id}/cancel`, {},
    ),
  trimProduction: (id: string, body: { start_s: number; end_s: number }) =>
    jpost<{ ok: boolean; url: string; duration: number }>(
      `/video/productions/${id}/trim`, body,
    ),
  deleteProduction: (id: string) =>
    jdel<{ ok: boolean; id: string }>(`/video/productions/${id}`),
  listVideoFeedback: (limit = 30, tag = "") => {
    const q = new URLSearchParams();
    if (limit) q.set("limit", String(limit));
    if (tag) q.set("tag", tag);
    return jget<{
      feedback: {
        id: string; reason: string; status: string;
        mode: string; caption_style: string; platform: string;
        tags: string[]; production_id: string;
        created_at: string | null;
      }[];
    }>(`/video/feedback?${q}`);
  },
  getProduction: (id: string) => jget<Production>(`/video/productions/${id}`),
  listClipLibrary: () => jget<{ items: ClipLibraryItem[] }>("/video/clips/library"),
  listCaptionStyles: () =>
    jget<{ presets: { name: string; label: string; description: string }[] }>(
      "/video/caption-styles",
    ),
  listImageStyles: () =>
    jget<{ presets: { name: string; label: string; description: string }[] }>(
      "/video/image-styles",
    ),
  getHeroContext: () =>
    jget<{ description: string; photo_count: number; photo_urls: string[]; video_urls: string[] }>(
      "/hero/context",
    ),
  refreshHeroContext: () =>
    jpost<{ description: string; photo_count: number; photo_urls: string[]; video_urls: string[] }>(
      "/hero/context/refresh", {},
    ),
  // Clone the hero into a lip-synced talking video (HeyGen Talking Photo).
  // Provide a topic (script written in brand voice) OR a finished script.
  heroTalkingVideo: (body: { script?: string; topic?: string; platform?: string; aspect?: string; title?: string }) =>
    jpost<Production>("/hero/talking-video", body),
  // ── Long Form Cutter ────────────────────────────────────────────
  listLongSources: () =>
    jget<{ sources: LongSource[] }>("/long-form/sources"),
  getLongSource: (id: string) =>
    jget<LongSource & { candidates: ReelCandidate[] }>(`/long-form/${id}`),
  async uploadLongSource(file: File, title = "") {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    const r = await fetch(u("/long-form/upload"), { method: "POST", body: fd, credentials: "include" });
    return _safeJsonOrThrow<LongSource>(r);
  },
  async importLongSourceFromDrive(driveUrl: string, title = "") {
    const fd = new FormData();
    fd.append("drive_url", driveUrl);
    fd.append("title", title);
    const r = await fetch(u("/long-form/drive-import"), { method: "POST", body: fd, credentials: "include" });
    return _safeJsonOrThrow<LongSource>(r);
  },
  browseDriveFolder: (folderId = "") =>
    jget<{
      folder_id: string;
      default_folder_id: string;
      videos: { id: string; name: string; mimeType?: string; size?: string; modifiedTime?: string }[];
    }>(`/long-form/drive-browse${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ""}`),
  async importLongSourceFromDriveId(fileId: string, title = "") {
    const fd = new FormData();
    fd.append("file_id", fileId);
    fd.append("title", title);
    const r = await fetch(u("/long-form/drive-import-id"), { method: "POST", body: fd, credentials: "include" });
    return _safeJsonOrThrow<LongSource>(r);
  },
  reanalyzeLongSource: (id: string) =>
    jpost<{ source_id: string; new_candidates: number }>(
      `/long-form/${id}/reanalyze`, {},
    ),

  // ── Knowledge Base (company documents → askable memory) ──
  async knowledgeIngest(file: File, opts: { category?: string; notes?: string } = {}) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("category", opts.category || "company_doc");
    fd.append("notes", opts.notes || "");
    const r = await fetch(u("/knowledge/ingest"), { method: "POST", body: fd, credentials: "include" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as KnowledgeIngestResult;
  },
  listKnowledgeDocuments: () =>
    jget<{ documents: KnowledgeDoc[] }>("/knowledge/documents"),
  deleteKnowledgeDocument: (id: string) =>
    jdel<{ ok: boolean; id: string }>(`/knowledge/documents/${id}`),
  knowledgeDownloadUrl: (id: string) =>
    jget<{ url: string }>(`/knowledge/documents/${id}/download`),

  // ── Agentic intake (Researcher + Interviewer agents) ──
  researchBrand: (name: string, hints = "") =>
    jpost<BrandResearchProposal>("/intake/research", { name, hints }),
  listIntakeQuestions: (status = "") =>
    jget<{ questions: BrandQuestion[]; stats: InterviewStats }>(
      `/intake/questions?status=${status}`,
    ),
  confirmIntakeQuestion: (id: string, answer?: string) =>
    jpost<{ id: string; status: string }>(
      `/intake/questions/${id}/confirm`, answer ? { answer } : {},
    ),
  dismissIntakeQuestion: (id: string) =>
    jpost<{ ok: boolean }>(`/intake/questions/${id}/dismiss`, {}),
  runInterview: () =>
    jpost<{ started: boolean }>("/intake/interview/run", {}),

  // ── Brand identity (Intake) + proactive suggestions ──
  getBrandProfile: () => jget<BrandProfile>("/brand-profile"),
  saveBrandProfile: (body: Partial<BrandProfile>) =>
    jput<BrandProfile>("/brand-profile", body),
  listSuggestions: (status = "suggested") =>
    jget<{ suggestions: ContentSuggestion[] }>(`/suggestions/list?status=${status}`),
  acceptSuggestion: (id: string) =>
    jpost<{ id: string; status: string; ref?: string }>(`/suggestions/${id}/accept`, {}),
  dismissSuggestion: (id: string) =>
    jpost<{ ok: boolean }>(`/suggestions/${id}/dismiss`, {}),
  refreshSuggestions: () =>
    jpost<{ started: boolean }>("/suggestions/refresh", {}),

  // Weekly thesis → intelligence → white paper (background job + poll).
  // full=true is the ONE BUTTON: continues into content pack + podcast.
  developThesis: (docId: string, full = false) =>
    jpost<{ job_id: string; status: string }>(
      `/knowledge/thesis/${docId}/develop${full ? "?full=1" : ""}`, {},
    ),
  // White paper → content pack (N posts + M reels via autopilot machinery).
  startContentPack: (docId: string, posts = 3, reels = 2) =>
    jpost<{ job_id: string; status: string }>(
      "/knowledge/content-pack", { doc_id: docId, posts, reels },
    ),
  contentPackStatus: (jobId: string) =>
    jget<ContentPackJob>(`/knowledge/content-pack/${jobId}`),
  thesisDevelopStatus: (jobId: string) =>
    jget<ThesisDevelopJob>(`/knowledge/thesis/develop/${jobId}`),

  // Podcast: thesis / white paper → narrated episode (background job).
  startPodcast: (docId: string) =>
    jpost<{ job_id: string; status: string }>("/knowledge/podcast", { doc_id: docId }),
  podcastStatus: (jobId: string) =>
    jget<PodcastJob>(`/knowledge/podcast/${jobId}`),
  listPodcasts: () =>
    jget<{ episodes: PodcastEpisode[] }>("/knowledge/podcasts"),

  // Generation takes 1-2 min → background job + poll.
  startWhitepaper: (body: { topic: string; audience?: string; goal?: string }) =>
    jpost<{ job_id: string; status: string }>("/knowledge/whitepaper", body),
  whitepaperStatus: (jobId: string) =>
    jget<{ status: "running" | "done" | "failed"; result?: Whitepaper; error?: string }>(
      `/knowledge/whitepaper/${jobId}`,
    ),
  // Topic intelligence (multi-angle research sweep + synthesis) — background job.
  startIntelligence: (body: { topic: string; auto_plan?: boolean; force?: boolean }) =>
    jpost<{ job_id: string; status: string }>("/knowledge/intelligence", body),
  intelligenceStatus: (jobId: string) =>
    jget<{ status: "running" | "done" | "failed"; result?: IntelBrief; error?: string }>(
      `/knowledge/intelligence/${jobId}`,
    ),
  synthesizePortfolio: (body: { theme?: string; silo_ids?: string[] }) =>
    jpost<PortfolioSynthesis>("/knowledge/synthesize", body),
  listSilos: (stats = false) =>
    jget<{ silos: Silo[] }>(`/knowledge/silos${stats ? "?stats=1" : ""}`),
  createSilo: (body: { name: string; id?: string; description?: string }) =>
    jpost<{ ok: boolean; silo: Silo }>("/knowledge/silos", body),
  listEntities: () => jget<{ entities: KbEntity[] }>("/knowledge/entities"),
  listCommitments: (status = "") =>
    jget<{ commitments: Commitment[] }>(
      `/knowledge/commitments${status ? `?status=${status}` : ""}`,
    ),
  updateCommitment: (id: string, status: string) =>
    jpatch<{ ok: boolean }>(`/knowledge/commitments/${id}`, { status }),

  // ── Content Library (unified clippable-reels dashboard) ──
  // /content-library is the Next PAGE; the data endpoint is a sub-path.
  contentLibrary: () => jget<ContentLibrary>("/content-library/data"),
  autoClipSource: (id: string, topN = 0) =>
    jpost<{ source_id: string; clips_started: number }>(
      `/long-form/${id}/auto-clip${topN ? `?top_n=${topN}` : ""}`, {},
    ),
  refreshClipTopics: () =>
    jpost<{ started: boolean }>("/content-library/topics/refresh", {}),
  buildClipTopic: (id: string) =>
    jpost<{ production_id: string; state: string }>(
      `/content-library/topics/${id}/build`, {},
    ),
  dismissClipTopic: (id: string) =>
    jpost<{ ok: boolean }>(`/content-library/topics/${id}/dismiss`, {}),

  // ── Speaker directory + on-screen name-tags ──
  listSpeakers: () => jget<Speaker[]>("/speakers"),
  createSpeaker: (body: { handle: string; subtitle?: string }) =>
    jpost<Speaker>("/speakers", body),
  deleteSpeaker: (id: string) =>
    jdel<{ ok: boolean; id: string }>(`/speakers/${id}`),
  detectSpeakers: (sourceId: string) =>
    jpost<{ source_id: string; speakers: DetectedSpeaker[] }>(
      `/long-form/${sourceId}/detect-speakers`, {},
    ),
  getSpeakerTags: (sourceId: string) =>
    jget<{ source_id: string; speaker_tags: SpeakerTag[] }>(
      `/long-form/${sourceId}/speaker-tags`,
    ),
  setSpeakerTags: (sourceId: string, tags: SpeakerTag[]) =>
    jput<{ source_id: string; speaker_tags: SpeakerTag[] }>(
      `/long-form/${sourceId}/speaker-tags`, { tags },
    ),
  async renderLongCandidate(candidateId: string, opts: {
    platform?: string; aspect?: string;
    image_style?: string; caption_style?: string;
    video_engine?: string; broll_pacing?: string; broll_style?: string;
  } = {}) {
    const fd = new FormData();
    fd.append("platform", opts.platform || "instagram");
    fd.append("aspect", opts.aspect || "9:16");
    fd.append("image_style", opts.image_style || "");
    fd.append("caption_style", opts.caption_style || "");
    fd.append("video_engine", opts.video_engine || "");
    fd.append("broll_pacing", opts.broll_pacing || "");
    fd.append("broll_style", opts.broll_style || "");
    const r = await fetch(
      u(`/long-form/candidates/${candidateId}/render`),
      { method: "POST", body: fd },
    );
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as Production;
  },
  // Render the WHOLE source as one reel — for short talking clips
  // where the candidate picker isn't relevant. caption_style picks
  // one of the five presets ('', 'tiktok_yellow', 'clean_white',
  // 'bold_pop', 'subtle_minimal', 'branded_red'); blank → AI picks.
  async renderLongSourceWhole(sourceId: string, opts: {
    platform?: string; aspect?: string;
    image_style?: string; caption_style?: string;
    video_engine?: string; broll_pacing?: string; broll_style?: string;
  } = {}) {
    const fd = new FormData();
    fd.append("platform", opts.platform || "instagram");
    fd.append("aspect", opts.aspect || "9:16");
    fd.append("image_style", opts.image_style || "");
    fd.append("caption_style", opts.caption_style || "");
    fd.append("video_engine", opts.video_engine || "");
    fd.append("broll_pacing", opts.broll_pacing || "");
    fd.append("broll_style", opts.broll_style || "");
    const r = await fetch(
      u(`/long-form/${sourceId}/render-whole`),
      { method: "POST", body: fd },
    );
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as Production;
  },
  dismissLongCandidate: (candidateId: string) =>
    jpost<{ ok: boolean }>(
      `/long-form/candidates/${candidateId}/dismiss`, {},
    ),
  generatePostImage: (body: {
    topic: string;
    platform?: string;
    brief?: string;
    aspect?: string;
    style?: PostImageStyle;
    title?: string;
    tags?: string[];
  }) => jpost<MediaAsset & { generation?: Record<string, unknown> }>(
    "/images/generate",
    { platform: "linkedin", style: "editorial", ...body },
  ),
  listMedia: (role = "") =>
    jget<{ media: MediaAsset[]; roles: MediaRole[] }>(
      `/media${role ? `?role=${role}` : ""}`
    ),
  async uploadMedia(
    file: File,
    role: MediaRole,
    opts: { title?: string; platform?: string; notes?: string; tags?: string } = {}
  ) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("role", role);
    fd.append("title", opts.title || "");
    fd.append("platform", opts.platform || "");
    fd.append("notes", opts.notes || "");
    fd.append("tags", opts.tags || "");
    const r = await fetch(u("/media/upload"), { method: "POST", body: fd, credentials: "include" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as MediaAsset;
  },
  linkMedia: (body: {
    url: string;
    role: MediaRole;
    title?: string;
    platform?: string;
    notes?: string;
    tags?: string[];
  }) => jpost<MediaAsset>("/media/link", body),
  updateMedia: (
    id: string,
    fields: { title?: string; notes?: string; platform?: string; tags?: string[]; mute_audio?: boolean; source_type?: "upload" | "url" | "generated" }
  ) => jpatch<MediaAsset>(`/media/${id}`, fields),
  deleteMedia: (id: string) => jdel<{ ok: boolean }>(`/media/${id}`),
  analyzeMedia: (id: string) => jpost<MediaAsset>(`/media/${id}/analyze`, {}),
  // ── Style Templates: the "library of trending video styles" ─────
  // Style references are inspected automatically on upload; these let
  // the library list them, (re-)inspect on demand, rename, and remove.
  listTemplates: () => jget<{ templates: StyleTemplate[] }>("/templates"),
  // Render-composition build queue — which reference layouts we can render
  // today (live) vs are queued to build (e.g. split-screen).
  listCompositions: () =>
    jget<{
      compositions: {
        layout_type: string; count: number; example: string;
        description: string; supported: boolean; status: string;
      }[];
    }>("/compositions"),
  // Higgsfield Soul IDs (trained characters / custom-references) on the
  // connected account, + generate one consistent character image from a Soul ID.
  listHiggsfieldSouls: () =>
    jget<{
      configured: boolean; count: number; error: string | null;
      souls: { id: string; name: string; status: string; thumbnail: string; created_at: string }[];
    }>("/higgsfield/souls"),
  generateSoulImage: (body: { custom_reference_id: string; prompt: string; aspect?: string; strength?: number }) =>
    jpost<{ status: string; image_url?: string; request_id: string; note?: string }>("/higgsfield/soul-image", body),
  // Train a NEW Soul ID from the uploaded hero photo library (role=hero_photo).
  trainHiggsfieldSoul: (body: { name: string }) =>
    jpost<{ ok: boolean; reference_id?: string; status?: string; trained_on?: number; note?: string; error?: string }>("/higgsfield/train-soul", body),
  // Brand kit — nameplate / watermark / end-card identity on every render.
  getBrandKit: () =>
    jget<{ display_name: string; tagline: string; handle: string; logo_url: string }>("/brand-kit"),
  putBrandKit: (body: { display_name?: string; tagline?: string; handle?: string; logo_url?: string }) =>
    jput<{ display_name: string; tagline: string; handle: string; logo_url: string }>("/brand-kit", body),
  getTemplate: (id: string) => jget<StyleTemplate>(`/templates/${id}`),
  inspectTemplate: (mediaId: string) =>
    jpost<{ started: boolean; media_id: string; note: string }>(
      `/templates/inspect/${mediaId}`, {},
    ),
  renameTemplate: (id: string, body: { name?: string; tags?: string[]; trending_score?: number }) =>
    jpatch<StyleTemplate>(`/templates/${id}`, body),
  deleteTemplate: (id: string) => jdel<{ deleted: boolean }>(`/templates/${id}`),
  // Feedback → "What's changing next" board.
  listChanges: () =>
    jget<{ applied_live: ChangeItem[]; queued: ChangeItem[]; proposed: ChangeItem[]; done: ChangeItem[] }>("/changes"),
  refreshChanges: () =>
    jpost<{ processed: number; recorded: number; applied_live: ChangeItem[]; queued: ChangeItem[]; proposed: ChangeItem[]; done: ChangeItem[] }>(
      "/changes/refresh", {},
    ),
  markChangeDone: (id: string) => jpost<{ ok: boolean }>(`/changes/${id}/done`, {}),
  dismissChange: (id: string) => jpost<{ ok: boolean }>(`/changes/${id}/dismiss`, {}),
  // Phase 2 — produce a new brand video in this template's style. Provide a
  // `topic` (script written in brand voice) OR a finished `script`.
  replicateTemplate: (
    id: string,
    body: { script?: string; topic?: string; platform?: string; aspect?: string; title?: string; video_engine?: string },
  ) =>
    jpost<{
      production: Production;
      applied: Record<string, string>;
      approximations: string[];
      script_source: "pasted" | "generated";
    }>(`/templates/${id}/replicate`, body),
  // Produce a short B-roll-ONLY reel in this template's style, animated by
  // `engine` (higgsfield by default) without changing the global default.
  brollReel: (
    id: string,
    body: { script?: string; topic?: string; platform?: string; seconds?: number; engine?: string },
  ) =>
    jpost<{
      production: Production;
      applied: Record<string, string | number>;
      approximations: string[];
    }>(`/templates/${id}/broll-reel`, body),
  discoverTrends: (topic: string, platforms: string[], limit = 20) =>
    jpost<TrendResult>("/trends/discover", { topic, platforms, limit }),
  listTrends: (platform = "") =>
    jget<TrendResult>(`/trends${platform ? `?platform=${platform}` : ""}`),
  getWatchlist: () => jget<{ creators: Creator[] }>("/trends/watchlist"),
  setWatchlist: (creators: Creator[]) =>
    jpost<{ creators: Creator[] }>("/trends/watchlist", { creators }),
  refreshWatchlist: (limit = 15) =>
    jpost<TrendResult>("/trends/refresh", { limit }),
  generateScript: (event_id: string, platform = "", extra_instructions = "") =>
    jpost<ContentDraft>("/generate-script", { event_id, platform, extra_instructions }),
  generate: (brief: Partial<ContentBrief> & { topic: string }) =>
    jpost<ContentDraft>("/generate", brief),
  // Attach a hero-referenced AI image to an already-queued post action — 2nd
  // step of the browser composer (1st is `generate`). Split from /post/compose
  // so each request stays under the gateway's synchronous timeout.
  attachPostImage: (body: {
    action_id: string;
    platform?: string;
    topic?: string;
    draft_text?: string;
  }) =>
    jpost<{ image_url: string | null; image_error: string | null }>(
      "/post/attach-image",
      { platform: "instagram", ...body }
    ),
  // Attach a REAL hero photo (chosen by the user) to a queued post — no
  // generation, guaranteed to look like James.
  setPostImage: (body: { action_id: string; image_url: string }) =>
    jpost<{ ok: boolean; image_url: string }>("/post/set-image", body),
  // Generate a NEW James image from the trained Higgsfield Soul ID and attach
  // it. Backgrounded (Soul render is 30-90s): start → poll.
  startSoulImage: (body: {
    action_id: string;
    topic?: string;
    draft_text?: string;
    aspect?: string;
  }) => jpost<{ job_id: string; status: string }>("/post/soul-image", body),
  getSoulImage: (id: string) =>
    jget<{
      job_id: string;
      status: "running" | "done" | "failed";
      image_url: string | null;
      error: string | null;
    }>(`/post/soul-image/${id}`),
  // Multi-format DESIGNED image — art director picks quote card / meme,
  // generates a text-free background, Pillow overlays crisp text + branding.
  startDesignedImage: (body: {
    action_id: string;
    topic?: string;
    draft_text?: string;
    aspect?: string;
  }) => jpost<{ job_id: string; status: string }>("/post/designed-image", body),
  getDesignedImage: (id: string) =>
    jget<{
      job_id: string;
      status: "running" | "done" | "failed";
      image_url: string | null;
      error: string | null;
    }>(`/post/designed-image/${id}`),
  // Create posts (text + image) for one or more topics in the background.
  startCreateBatch: (body: {
    topics: { topic: string; pillar?: string }[];
    platform?: string;
    image_mode?: "photo" | "soul" | "designed" | "none";
    image_url?: string;
  }) =>
    jpost<{ job_id: string; status: string; total: number }>(
      "/post/create-batch",
      { platform: "instagram", image_mode: "photo", ...body }
    ),
  getCreateBatch: (id: string) =>
    jget<{
      job_id: string;
      status: "running" | "done" | "failed";
      total: number;
      done: number;
      results: { topic: string; action_id: string | null; image_url: string | null; format?: string | null; error: string | null }[];
      error?: string | null;
    }>(`/post/create-batch/${id}`),
  // Backfill images for queued posts that don't have one yet (designed cards,
  // hero-photo fallback). Re-runnable; skips posts that already have an image.
  startBackfillImages: (body?: { mode?: "designed" | "photo"; limit?: number }) =>
    jpost<{ job_id: string; status: string; total: number }>(
      "/post/backfill-images",
      { mode: "designed", limit: 60, ...(body || {}) }
    ),
  getBackfillImages: (id: string) =>
    jget<{
      job_id: string;
      status: "running" | "done" | "failed";
      total: number;
      done: number;
      generated: number;
      failed: number;
      skipped?: number;
      error?: string | null;
    }>(`/post/backfill-images/${id}`),
  // Suggested post topics, steered from live data (same engine as video
  // 'Generate 10 scripts'): tracked creators + trends + James's real topics,
  // pillar-quota'd. Backgrounded (intel is 30-60s, would time out a sync call):
  // start → poll. Topics only, no scripts.
  startPostIdeas: (n = 10) =>
    jpost<{ batch_id: string; status: string }>(`/post/ideas?n=${n}`, {}),
  getPostIdeas: (id: string) =>
    jget<{
      batch_id: string;
      status: "running" | "done" | "failed";
      ideas: TopicIdea[];
      count?: number;
      niche?: string;
      error?: string | null;
    }>(`/post/ideas/${id}`),
  // Persisted suggestions — shown on load so we don't re-ideate every visit.
  getSavedIdeas: () => jget<{ ideas: TopicIdea[] }>("/post/ideas/saved"),
  setIdeaStatus: (id: string, status: "accepted" | "rejected" | "pending") =>
    jpost<{ ideas: TopicIdea[] }>("/post/ideas/status", { id, status }),
  generateMulti: (body: {
    topic: string;
    pillar?: string;
    platforms: string[];
    carousel: boolean;
    carousel_slides?: number;
    research_subject?: string;
    extra_instructions?: string;
  }) => jpost<{ topic: string; drafts: ContentDraft[]; queued: number }>(
    "/generate-multi",
    body
  ),
  generateVideo: (prompt: string, prompt_image = "") =>
    jpost<VideoJob>("/video/generate", { prompt, prompt_image }),
  listVideoJobs: () => jget<VideoJob[]>("/video/jobs"),
  getVideoJob: (id: string) => jget<VideoJob>(`/video/jobs/${id}`),
  getCredentials: () => jget<CredentialStatus>("/api/credentials"),
  setCredentials: (updates: Record<string, string>) =>
    jpost<{ ok: boolean } & CredentialStatus>("/api/credentials", { updates }),
  checkIntegrations: () => jget<IntegrationCheck>("/api/integrations/check"),
  queue: () => jget<QueueItem[]>("/api/queue"),
  queueStats: () => jget<QueueStats>("/api/queue/stats"),
  approve: (id: string, reason = "approved via dashboard", override = false) =>
    jpost(`/api/queue/${id}/approve`, { reason, override }),
  reject: (id: string, reason = "rejected via dashboard") =>
    jpost<{ ok: boolean; learned: boolean; guardrail_id: string | null }>(
      `/api/queue/${id}/reject`,
      { reason }
    ),
  deleteQueueItem: (id: string) =>
    jdel<{ ok: boolean; deleted: boolean }>(`/api/queue/${id}`),
  editQueueItem: (id: string, content: string) =>
    jpatch<{ ok: boolean; content: string }>(`/api/queue/${id}`, { content }),
  scheduleQueueItem: (id: string, scheduledFor: string) =>
    jpost<{ ok: boolean; scheduled_for: string | null }>(`/api/queue/${id}/schedule`, { scheduled_for: scheduledFor }),
  guardrails: () =>
    jget<{ guardrails: Guardrail[] }>("/api/guardrails"),
  integrations: () =>
    jget<{ configured: Record<string, boolean>; active: string[] }>("/api/integrations"),
  lastScan: () => jget<{ lastScanAt: string | null }>("/api/system/last-scan"),
  connections: () =>
    jget<{ platform: string; handle: string; enabled: boolean; status: string }[]>(
      "/api/connections"
    ),
  upsertConnection: (platform: string, handle: string, enabled: boolean) =>
    jpost<{ ok: boolean; platform: string; status: string }>("/api/connections", {
      platform,
      handle,
      enabled,
    }),
  getProfile: () =>
    jget<{ name: string; email: string; brand: string }>("/api/profile"),
  setProfile: (p: { name: string; email: string; brand: string }) =>
    jpost<{ ok: boolean }>("/api/profile", p),
  ask: (question: string, history?: AskTurn[]) =>
    jpost<AskResponse>("/ask", { question, ...(history?.length ? { history } : {}) }),
  research: (subject: string, focus = "") =>
    jpost<ResearchResponse>("/research", { subject, focus }),
  listPlugIns: () => jget<PlugIn[]>("/plug-ins"),
  brandBrain: () => jget<BrandBrain>("/plug-ins/brain"),
  addPlugIn: (slot: string, name: string, rule: string) =>
    jpost<PlugIn>("/plug-ins", { slot, name, content: { rule }, applies_to: [] }),
  async uploadDocument(file: File, category = "reference") {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("category", category);
    const r = await fetch("/ingest/document", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as {
      filename: string;
      category: string;
      chunks_created: number;
      superseded_chunks: number;
    };
  },
};
