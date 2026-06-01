// Typed client for the JAMES OS FastAPI backend. Calls are same-origin
// (Next rewrites proxy to the backend) so no CORS, no base URL juggling.

export type Citation = { event_id: string; span: string; confidence: number };

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

async function jdel<T>(path: string): Promise<T> {
  const r = await fetch(u(path), { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function jpatch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(u(path), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(u(path), { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(u(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  format: string;
  content: string;
  caption: string;
  voiceScore: number | null;
  proposedBy: string;
  createdAt: string | null;
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
  | "hero_video";

/** Aesthetic preset for /images/generate. Each maps to a distinct
 *  prompt prefix on the backend (see imagegen.POST_STYLES). Same topic
 *  + different style = different render. */
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
  source_type: "upload" | "url";
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
  status: "queued" | "planning" | "rendering_clips" | "assembling" | "succeeded" | "failed";
  mode?: "mixed" | "avatar_only" | "timeline" | "story_audio" | "avatar_story_mix" | "engaging_avatar" | "long_form_reel";
  title: string;
  platform: string;
  aspect: string;
  script: string;
  plan: ScenePlan | Record<string, never>;
  scenes: Scene[];
  caption_style?: string;
  image_style?: string;
  final_url: string | null;
  error: string | null;
  avatar_provider: string;
  broll_provider: string;
  assembly_provider: string;
  queued_action_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
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
  renderScene: (scene: Scene, aspect = "9:16") =>
    jpost<Scene>("/video/render-scene", { scene, aspect }),
  produceVideo: (opts: {
    script?: string; platform?: string; aspect?: string; title?: string;
    scenes?: Scene[];
    mode?: "mixed" | "avatar_only" | "timeline" | "story_audio" | "avatar_story_mix" | "engaging_avatar";
    caption_style?: string;             // blank → AI picks
    image_style?: string;               // blank → cinematic for story modes
  }) => jpost<Production>("/video/produce", {
    platform: "instagram", aspect: "9:16", mode: "mixed", ...opts,
  }),
  listProductions: () => jget<Production[]>("/video/productions"),
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
  // ── Long Form Cutter ────────────────────────────────────────────
  listLongSources: () =>
    jget<{ sources: LongSource[] }>("/long-form/sources"),
  getLongSource: (id: string) =>
    jget<LongSource & { candidates: ReelCandidate[] }>(`/long-form/${id}`),
  async uploadLongSource(file: File, title = "") {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    const r = await fetch(u("/long-form/upload"), { method: "POST", body: fd });
    return _safeJsonOrThrow<LongSource>(r);
  },
  async importLongSourceFromDrive(driveUrl: string, title = "") {
    const fd = new FormData();
    fd.append("drive_url", driveUrl);
    fd.append("title", title);
    const r = await fetch(u("/long-form/drive-import"), { method: "POST", body: fd });
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
    const r = await fetch(u("/long-form/drive-import-id"), { method: "POST", body: fd });
    return _safeJsonOrThrow<LongSource>(r);
  },
  reanalyzeLongSource: (id: string) =>
    jpost<{ source_id: string; new_candidates: number }>(
      `/long-form/${id}/reanalyze`, {},
    ),
  async renderLongCandidate(candidateId: string, opts: {
    platform?: string; aspect?: string;
    image_style?: string; caption_style?: string;
  } = {}) {
    const fd = new FormData();
    fd.append("platform", opts.platform || "instagram");
    fd.append("aspect", opts.aspect || "9:16");
    fd.append("image_style", opts.image_style || "");
    fd.append("caption_style", opts.caption_style || "");
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
  } = {}) {
    const fd = new FormData();
    fd.append("platform", opts.platform || "instagram");
    fd.append("aspect", opts.aspect || "9:16");
    fd.append("image_style", opts.image_style || "");
    fd.append("caption_style", opts.caption_style || "");
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
    const r = await fetch(u("/media/upload"), { method: "POST", body: fd });
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
    fields: { title?: string; notes?: string; platform?: string; tags?: string[]; mute_audio?: boolean }
  ) => jpatch<MediaAsset>(`/media/${id}`, fields),
  deleteMedia: (id: string) => jdel<{ ok: boolean }>(`/media/${id}`),
  analyzeMedia: (id: string) => jpost<MediaAsset>(`/media/${id}/analyze`, {}),
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
  approve: (id: string, reason = "approved via dashboard") =>
    jpost(`/api/queue/${id}/approve`, { reason }),
  reject: (id: string, reason = "rejected via dashboard") =>
    jpost<{ ok: boolean; learned: boolean; guardrail_id: string | null }>(
      `/api/queue/${id}/reject`,
      { reason }
    ),
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
  ask: (question: string) => jpost<AskResponse>("/ask", { question }),
  research: (subject: string, focus = "") =>
    jpost<ResearchResponse>("/research", { subject, focus }),
  listPlugIns: () => jget<PlugIn[]>("/plug-ins"),
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
