/**
 * Typed client for the merged backend's /manager surface (the bm2.0 brand-
 * manager port living in src/james_os/manager/*_api.py).
 *
 * Structure ports bm2.0 frontend/lib/api.ts (ApiError, json helper, the
 * 202 + poll + 409 background-job convention) onto this app's idioms:
 * same-origin fetches with `credentials: "include"` (cookie auth), the
 * NEXT_PUBLIC_API_BASE override, and the 401 -> /login redirect from
 * web/lib/api.ts. Types mirror what the routers actually return — do not
 * invent fields here.
 *
 * Actual endpoint contract (see the router files for authority):
 *   sources_api.py
 *     GET  /system/sources                       -> SystemSourcesResponse (D12 inventory)
 *     GET  /manager/sources                      -> TenantSourcesResponse (per-stream contribution)
 *   manager_api.py
 *     GET  /manager/actions[?status=a,b]         -> {actions: ActionItem[]}
 *     POST /manager/actions/{id}/status          {status, snooze_days?} -> {action}
 *     POST /manager/actions/{id}/note            {note} -> {action}
 *     DELETE /manager/actions/{id}               -> {deleted}
 *     POST /manager/actions/{id}/research-contact -> ContactResearchResult
 *     POST /manager/daily-cycle                  -> DailyCycleReport (step -> outcome)
 *     GET  /manager/daily-digest                 -> DailyDigest
 *     POST /manager/scan/{eye}                   -> per-eye report (5 eyes)
 *     POST /manager/algorithm/refresh            -> AlgorithmReport
 *     POST /manager/audit                        -> AuditReport
 *     POST /manager/peers/discover               -> 202 JobAccepted (409 while running)
 *     GET  /manager/peers/discover/status        -> JobRunStatus
 *     GET  /manager/peers/candidates             -> {candidates: WatchlistEntry[]}
 *     POST /manager/peers/approve|reject         {handle} -> {peer: WatchlistEntry}
 *     POST /manager/peers/snapshot               -> snapshot report
 *     POST /manager/collab/generate              -> CollabReport
 *     GET  /manager/collab/latest                -> CollabLatest ({generated:false,...} shell)
 *   planning_api.py
 *     POST /manager/plan/weekly                  -> WeeklyPlanDraft
 *     GET  /manager/plan/latest                  -> {plan: LatestPlan | null}
 *     POST /manager/plan/{id}/activate           {item_indices?} -> ActivateResult
 *                                                (404 unknown, 409 superseded, 422 bad selection)
 *     GET  /manager/brief                        -> MorningBrief (deterministic, cheap)
 *   execution_api.py
 *     POST /manager/opportunities/{aid}/execute  {format?} -> ExecuteResult (409 bad edge)
 *     POST /manager/work-orders/{id}/approve     {reason?} -> ApproveResult (publishes too)
 *     POST /manager/work-orders/{id}/reject      {reason} -> RejectResult
 *     POST /manager/work-orders/{id}/publish     -> PublishResult
 *     GET  /manager/work                         -> {work: WorkRow[]}
 *   intake_api.py
 *     GET  /manager/intake/interview/next?budget=N        -> InterviewNext
 *     POST /manager/intake/questions/{qid}/suggest        -> AnswerSuggestion
 *     POST /manager/intake/questions/{qid}/answer {answer} -> AnswerResult
 *     POST /manager/intake/questions/auto-answer          -> 202 AutoAnswerAccepted (409)
 *     GET  /manager/intake/questions/auto-answer/status   -> AutoAnswerStatus
 *     GET|POST /manager/intake/research-seed              -> {research_seed}
 *   research_api.py
 *     POST /manager/research/discover            {name,...} -> DiscoverResponse (sync)
 *     POST /manager/research/confirmed           {confirmed_urls} -> 202 (409 while running)
 *     GET  /manager/research/status              -> ResearchStatus
 *   voice_api.py
 *     POST /manager/voice/harvest                -> 202 JobAccepted (409 while running)
 *     POST /manager/voice/add-source             {url} -> 202 JobAccepted & {url}
 *     GET  /manager/voice/status                 -> JobRunStatus
 *     GET  /manager/voice/profile                -> VoiceProfileResponse
 *   accounts_api.py
 *     GET  /manager/accounts                     -> AccountsResponse
 *     POST /manager/accounts                     {platform, handle} -> ConnectedAccount (201; 409 dup)
 *     GET  /manager/accounts/groups              -> AccountGroupsResponse
 *     POST /manager/accounts/bind                {group_id} -> SyncResponse
 *     POST /manager/accounts/sync                -> SyncResponse
 *     GET  /manager/accounts/connect-url?platform= -> ConnectUrlResponse
 *     GET  /manager/next-steps                   -> NextStep[]
 *     GET  /manager/brand-profile                -> BrandProfileProjection
 */

// ── base URL + fetch plumbing (matches web/lib/api.ts exactly) ─────────────

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const u = (path: string) => `${API_BASE}${path}`;

// Every request includes credentials so the session cookie travels
// cross-origin (3000 -> 8001 in dev). The backend's CORS config has
// allow_credentials=true to match.
const FETCH_OPTS: RequestInit = { credentials: "include" };

/** HTTP-status-aware error: lets callers branch on 409 (job already running /
 * stale plan), 404 (not found / manager_v2 off), 502 (agent died — the failed
 * run is on record in job_runs). */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

/** 409 = a background job is already in flight (or a state-machine edge was
 * refused) — poll the matching status route instead of treating it as fatal. */
export function isConflict(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

// On a 401, bounce to /login preserving the current path (web/lib/api.ts
// idiom); skip on auth pages so we don't loop.
function handle401(): void {
  if (typeof window === "undefined") return;
  const here = window.location.pathname;
  if (here === "/login" || here === "/signup") return;
  const next = encodeURIComponent(here + window.location.search);
  window.location.replace(`/login?next=${next}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(u(path), {
      cache: "no-store",
      ...FETCH_OPTS,
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API${API_BASE ? ` at ${API_BASE}` : ""}. Is the backend running?`);
  }
  if (res.status === 401) {
    handle401();
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

// ── background jobs: the 202 + poll + 409 convention ───────────────────────

/** 202 body from the background-job kickers (peers/discover, voice/harvest,
 * research/confirmed, intake auto-answer). `poll` is the status route. */
export interface JobAccepted {
  status?: string; // "started"
  state?: string; // "running" (research/auto-answer variants)
  poll: string;
  url?: string; // voice add-source echoes the URL back
}

/** Latest job_runs row for an agent — the poll target for peers/discover and
 * voice/harvest. `{status:"never_run"}` before any run. */
export interface JobRunStatus {
  status: "never_run" | "running" | "succeeded" | "failed";
  output?: Record<string, unknown> | null;
  error?: string | null;
  started_at?: string;
  finished_at?: string | null;
}

export interface PollOptions {
  /** ms between polls (default 2500) */
  intervalMs?: number;
  /** give up after this long (default 5 min); rejects with ApiError(0) */
  timeoutMs?: number;
}

/** Generic poller for the 202 convention: call `fetchStatus` until `isDone`
 * says the run reached a terminal state, then resolve with that status. */
export async function pollUntil<T>(
  fetchStatus: () => Promise<T>,
  isDone: (s: T) => boolean,
  opts: PollOptions = {},
): Promise<T> {
  const interval = opts.intervalMs ?? 2500;
  const deadline = Date.now() + (opts.timeoutMs ?? 5 * 60_000);
  for (;;) {
    const status = await fetchStatus();
    if (isDone(status)) return status;
    if (Date.now() >= deadline) throw new ApiError(0, "Timed out waiting for the background run to finish.");
    await new Promise((r) => setTimeout(r, interval));
  }
}

const jobDone = (s: JobRunStatus) => s.status === "succeeded" || s.status === "failed";

// ── value sets (bm2.0 contracts, carried onto the merged substrate) ─────────

export type EntityType = "person" | "company" | "physical_asset" | "institution";

/** Provenance of a profile fact (D2). */
export type Source =
  | "user_stated"
  | "negotiated"
  | "audited"
  | "researched"
  | "inferred"
  | "derived"
  | "queue_signal";

export type FieldStatus = "unconfirmed" | "confirmed" | "contradicted" | "stale" | "superseded";

/** The D5 work-order lifecycle (manager/contracts.py WorkOrderStatus). */
export type WorkOrderStatus =
  | "queued"
  | "generating"
  | "review"
  | "pending_approval"
  | "approved"
  | "scheduled"
  | "published"
  | "measured"
  | "rejected"
  | "superseded"
  | "cancelled";

export type PeerKind = "leader" | "aspirational" | "collaborator" | "competitor";

export type OutreachConfidence = "low" | "medium" | "high";

/** Citations arrive as dicts or bare URL strings depending on the writer. */
export interface Citation {
  url: string;
  ref: string;
  note: string;
}
export type CitationLike = Citation | string | Partial<Citation>;

// ── sources (D12) ───────────────────────────────────────────────────────────

export interface SystemSource {
  stream: string;
  provider_impl: string;
  status: "live" | "mock" | "idle";
  note: string;
  chain?: string[]; // llm stream only: availability chain
}

export interface SystemSourcesResponse {
  env: string; // mock | live
  sources: SystemSource[];
}

export interface TenantSourcesResponse {
  last_run: string | null; // latest succeeded researcher fan-out
  contributions: Record<string, number>; // stream -> fields contributed
  profile_sections: Record<string, number>; // section -> current field count
}

// ── actions (follow-ups) ────────────────────────────────────────────────────

export type ActionStatus = "suggested" | "active" | "done" | "dismissed" | "snoozed";

export interface ActionUpdate {
  ts: string;
  note: string;
  actor: string; // "user" | "hands" | "contact_research" | ...
}

export interface ContactPath {
  type: string; // press_form | booking_page | business_email | official_dm | contact_page
  value: string;
  confidence: OutreachConfidence;
  source: string;
}

export interface ActionMeta {
  peer_handle?: string;
  peer_kind?: PeerKind;
  outreach_path?: string;
  outreach_confidence?: OutreachConfidence;
  source?: string;
  contact_paths?: ContactPath[];
  [key: string]: unknown;
}

/** manager_api._item_out — note `related_peer` (a handle), not an id. */
export interface ActionItem {
  id: string;
  kind: string; // collaboration | visibility | content | research | press | general
  title: string;
  detail: string;
  status: ActionStatus;
  related_peer: string | null;
  dedupe_key: string | null;
  meta: ActionMeta;
  updates: ActionUpdate[];
  snooze_until: string | null;
  last_activity_at: string | null;
}

export interface ActionsResponse {
  actions: ActionItem[];
}

export interface ContactResearchResult {
  action_id?: string;
  name?: string;
  paths?: ContactPath[];
  note?: string;
  [key: string]: unknown;
}

// ── daily cycle + digest ────────────────────────────────────────────────────

/** heartbeat.run_daily_cycle: a step -> outcome report (values are counts,
 * sub-reports, or the string "failed"). */
export type DailyCycleReport = Record<string, unknown>;

export interface DailyDigestItem {
  action_id?: string;
  title: string;
  why: string;
  kind: string;
}

export interface DailyDigest {
  date: string | null; // null before the first cycle
  summary: string;
  items: DailyDigestItem[];
}

// ── the five eyes (scan/{eye}) ──────────────────────────────────────────────

export type EyeName = "content-radar" | "trends" | "press" | "questions" | "appearances";

export interface ContentOpportunity {
  title: string;
  problem: string;
  content_type: string;
  angle: string;
  why: string;
  recurring_count?: number;
  reddit_links?: string[];
  [key: string]: unknown;
}

export interface ContentRadarReport {
  opportunities: ContentOpportunity[];
  competitor_themes: string[];
  reddit_threads_scanned: number;
  note: string;
}

export interface TrendItem {
  title: string;
  trend: string;
  angle: string;
  content_type: string;
  why: string;
  links?: string[];
  [key: string]: unknown;
}

export interface TrendsReport {
  trends: TrendItem[];
  news_scanned: number;
  note: string;
}

export interface PressItem {
  title: string;
  signal: string; // award | feature | appointment | coverage | milestone
  post: string; // ready-to-use amplification copy
  content_type: string;
  links?: string[];
  [key: string]: unknown;
}

export interface PressReport {
  items: PressItem[];
  mentions_scanned: number;
  note: string;
}

export interface QuestionItem {
  question: string;
  content_type: string; // faq | blog | video | thread
  answer_angle: string;
  why: string;
  links?: string[];
  [key: string]: unknown;
}

export interface QuestionsReport {
  questions: QuestionItem[];
  results_scanned: number;
  note: string;
}

export interface AppearanceItem {
  title: string;
  kind: string; // podcast | conference | show | panel | interview
  fit: string;
  approach: string;
  links?: string[];
  [key: string]: unknown;
}

export interface AppearancesReport {
  appearances: AppearanceItem[];
  results_scanned: number;
  note: string;
}

export type EyeReport =
  | ContentRadarReport
  | TrendsReport
  | PressReport
  | QuestionsReport
  | AppearancesReport;

// ── algorithm + audit ───────────────────────────────────────────────────────

export interface AlgorithmPlaybook {
  platform: string;
  version: number;
  changed: boolean;
  [key: string]: unknown;
}

export interface AlgorithmReport {
  platforms: string[];
  briefs: Record<string, unknown>;
  playbooks: AlgorithmPlaybook[];
  results_scanned: number;
  note: string;
}

/** auditor.run: contracts.BaselineReport + import counters. */
export interface AuditReport {
  brand_id: string;
  captured_at: string;
  platforms: Record<string, Record<string, unknown>>;
  top_posts: Record<string, unknown>[];
  bottom_posts: Record<string, unknown>[];
  notes: string[];
  posts_imported: number;
  exemplars_promoted: number;
}

// ── peers: discover → approve → track ───────────────────────────────────────

/** One tenants.config['watchlist'] entry (peers.py module docstring). */
export interface WatchlistEntry {
  handle: string;
  platform: string;
  display_name: string;
  status: "candidate" | "tracked" | "rejected";
  kind: PeerKind | string;
  reason: string;
  discovered_at?: string;
  [key: string]: unknown;
}

export interface PeerCandidatesResponse {
  candidates: WatchlistEntry[];
}

export interface PeerActionResponse {
  peer: WatchlistEntry;
}

// ── collaboration ───────────────────────────────────────────────────────────

export interface CollabPlay {
  peer_handle: string;
  kind: PeerKind | string;
  mutual_interest: string;
  play_type: string;
  play: string;
  outreach_path: string;
  outreach_confidence: OutreachConfidence | string;
  [key: string]: unknown;
}

export interface VisibilityPlay {
  title: string;
  action: string;
  why: string;
  [key: string]: unknown;
}

export interface CollabReport {
  brand_id?: string;
  generated_at?: string;
  tracked_peer_count?: number;
  plays: CollabPlay[];
  visibility_plays: VisibilityPlay[];
  note?: string;
}

/** GET /manager/collab/latest: {generated:false, plays:[], visibility_plays:[]}
 * before any run; the generated report is spread in once one succeeded. */
export interface CollabLatest extends CollabReport {
  generated: boolean;
}

// ── planning (strategist) ───────────────────────────────────────────────────

export interface PlanItem {
  content_type: string;
  platform: string;
  topic: string;
  count: number;
  format_spec: Record<string, unknown>;
  rationale: string;
  evidence: CitationLike[];
  predicted_metrics: Record<string, unknown>;
  [key: string]: unknown;
}

export interface GrowthAction {
  action?: string;
  why?: string;
  evidence?: string[];
  [key: string]: unknown;
}

/** POST /manager/plan/weekly result: the persisted proposed prescription. */
export interface WeeklyPlanDraft {
  plan_id: string;
  week_of: string;
  status: string; // "proposed"
  plan: {
    rationale: string;
    goals_snapshot: Record<string, unknown>;
    period_start: string;
    period_end: string;
    items: PlanItem[];
  };
  growth_actions: GrowthAction[];
}

/** GET /manager/plan/latest inner shape (null when no prescription yet). */
export interface LatestPlan {
  plan_id: string;
  week_of: string;
  status: string; // proposed | accepted | partial | expired | superseded
  items: Record<string, unknown>[];
  growth_actions: GrowthAction[];
  accepted_items: Record<string, unknown>[];
  created_at: string;
}

export interface LatestPlanResponse {
  plan: LatestPlan | null;
}

export interface WorkOrderRef {
  id: string;
  status: string;
  payload: Record<string, unknown>;
}

export interface ActivateResult {
  plan_id: string;
  plan_status: string;
  activated_items: number[];
  accepted_items: Record<string, unknown>[];
  work_orders_created: number;
  work_orders_reattached: number;
  work_orders_superseded: number;
  work_orders: WorkOrderRef[];
}

export interface BriefSection {
  title: string;
  body: string;
  citations?: CitationLike[];
  [key: string]: unknown;
}

export interface MorningBrief {
  date: string;
  headline: string;
  sections: BriefSection[];
  recommended_actions: string[];
  queue_counts: Record<string, number>;
  plan: { plan_id: string; week_of: string; status: string; item_count: number } | null;
}

// ── execution spine ─────────────────────────────────────────────────────────

/** Asset formats the executor drafts or routes. blog/email/social_post are
 * drafted in-process; video/image are queued for the production pipelines. */
export type ContentFormat = "blog" | "email" | "social_post" | "video" | "image";

export interface ExecuteResult {
  action_id: string;
  work_order_id: string;
  content_type: string;
  status: WorkOrderStatus;
  drafted: boolean;
  /** "production" = video/image handed to the in-process pipelines */
  routed_to?: string;
  review_passed?: boolean;
  voice_score?: number;
  content_action_id?: string | null;
}

/** publish.publish_work_order — ok:false is an HONEST failure (surface
 * `detail`/`error`, don't treat as done; the outbox retries). */
export interface PublishResult {
  work_order_id?: string;
  ok: boolean;
  status?: string;
  url?: string | null;
  ref?: string | null;
  provider?: string;
  detail?: Record<string, unknown> | string;
  error?: string;
  outbox_id?: string;
  attempts?: number;
  retry_exhausted?: boolean;
}

export interface ApproveResult {
  work_order_id: string;
  status: string;
  approved: true;
  learned_event_id: string | null;
  publish: PublishResult;
}

export interface RejectResult {
  work_order_id: string;
  status: string;
  rejected: true;
  learned_event_id: string | null;
}

export interface WorkOpportunity {
  action_id: string;
  title: string;
  kind: string;
  source: string | null;
}

export interface WorkArtifact {
  version: number;
  kind: string; // text | script | image_prompt | clip_spec
  content: string;
  media: Record<string, unknown>;
  review: { voice_score?: number; passed?: boolean | null; drift?: string[] };
  angle?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface PublishedRef {
  at?: string;
  url?: string | null;
  ref?: string | null;
  provider?: string;
  detail?: unknown;
  content_type?: string;
}

export interface ApprovalEvent {
  action: "approve" | "reject";
  reason: string;
  actor: string;
  at: string;
}

export interface WorkRow {
  work_order_id: string;
  content_type: string | null;
  platform: string | null;
  topic: string | null;
  status: string;
  routed_to: string | null;
  opportunity: WorkOpportunity | null;
  artifact: WorkArtifact | null;
  predicted_metrics: Record<string, unknown> | null;
  actual_metrics: Record<string, unknown> | null;
  published_ref: PublishedRef | null;
  publish_attempts: Record<string, unknown>[];
  approvals: ApprovalEvent[];
  measured_at: string | null;
  created_at: string;
}

export interface WorkResponse {
  work: WorkRow[];
}

// ── intake (the deep interview) ─────────────────────────────────────────────

export interface InterviewQuestion {
  id: string;
  text: string;
  dimension: string;
  field_key: string;
  why: string;
}

export interface InterviewNext {
  questions: InterviewQuestion[];
  onboarding_status: string;
}

export interface AnswerSuggestion {
  question_id: string;
  field_key: string;
  question: string;
  suggestion: string;
  rationale: string;
  grounded: boolean;
  citations: string[];
}

export interface AnswerResult {
  id: string;
  status: "confirmed";
  field_key: string;
}

/** One AI-drafted answer from the batch run (open questions only — the
 * status route filters out anything settled since the batch started). */
export interface AutoAnswerDraft extends AnswerSuggestion {}

export type AutoAnswerState = "none" | "running" | "succeeded" | "failed";

export interface AutoAnswerStatus {
  state: AutoAnswerState;
  drafts: AutoAnswerDraft[];
  count: number;
  considered?: number;
  started_at?: string;
  finished_at?: string | null;
  error?: string; // "" when no error
}

export interface ResearchSeed {
  name?: string;
  entity_type?: string;
  website?: string | null;
  socials?: string[];
  location?: string | null;
  [key: string]: unknown;
}

export interface ResearchSeedResponse {
  research_seed: ResearchSeed;
}

// ── research (discover → confirmed fan-out) ─────────────────────────────────

export interface EntityCandidate {
  name: string;
  description: string;
  urls: string[];
  score: number;
  [key: string]: unknown;
}

export interface DiscoverResponse {
  candidates: EntityCandidate[];
  failures: string[];
}

export type ResearchRunState = "none" | "running" | "succeeded" | "failed";

export interface ResearchStatus {
  state: ResearchRunState;
  mode: string | null; // discover | confirmed
  started_at: string | null;
  finished_at: string | null;
  lane_stats: Record<string, number>; // lane -> fields contributed
  fields_written: number;
  failures: string[];
  error: string; // "" when no error
}

// ── voice ───────────────────────────────────────────────────────────────────

export type VoiceState = "running" | "ready" | "empty";

/** Every field optional; only present once a harvest has distilled them. */
export interface VoiceProfile {
  register?: string;
  tone?: string;
  sentence_style?: string;
  summary?: string;
  signature_phrases?: string[];
  vocabulary?: string[];
  dos?: string[];
  donts?: string[];
  [key: string]: unknown;
}

export interface VoiceProfileResponse {
  state: VoiceState;
  voice: VoiceProfile;
  exemplar_count: number;
  exemplars_by_origin: Record<string, number>; // harvested / uploaded / approved / audited
}

// ── accounts + next steps + brand profile ───────────────────────────────────

export type Platform =
  | "instagram"
  | "facebook"
  | "youtube"
  | "linkedin"
  | "tiktok"
  | "x"
  | "twitter" // accepted, normalized to "x"
  | "threads";

/** connections._row_out */
export interface ConnectedAccount {
  platform: string;
  handle: string;
  enabled: boolean;
  status: string; // connected | not_connected | ...
  aggregator_profile_key: string;
  connected_at: string | null;
  updated_at: string | null;
}

export interface AccountsResponse {
  profile_key: string;
  accounts: ConnectedAccount[];
}

export interface AccountGroup {
  group_id: string;
  name: string;
  account_count: number;
  accounts: string[]; // "platform:@handle"
}

export interface AccountGroupsResponse {
  bound_group: string | null;
  groups: AccountGroup[];
}

export interface SyncResponse {
  profile_key: string;
  synced: number;
  accounts: ConnectedAccount[];
}

export interface ConnectUrlResponse {
  profile_key: string;
  platform: string;
  connect_url: string;
}

export type NextStepState = "done" | "running" | "pending";

/** Known step keys: research_profile | answer_questions | connect_accounts |
 * build_voice | track_peers | weekly_prescription | review_queue — render
 * unknown keys without an action. */
export interface NextStep {
  step: string;
  label: string;
  state: NextStepState;
  count: number | null;
  hint: string;
}

/** brands.profile_projection — the bm2.0 handoff-export shape served as this
 * system's internal projection (envelope over brand_profiles; superset). */
export interface BrandProfileProjection {
  tenant_id: string;
  kind: string | null;
  identity: Record<string, unknown>;
  positioning: Record<string, unknown>;
  audience: Record<string, unknown>;
  voice: Record<string, unknown>;
  goals: string[];
  pillars: string[];
  taboos: string[];
  platforms: string[];
  peers: string[];
  constraints: Record<string, unknown>;
  intake_done: boolean;
  connected_accounts: { platform: string; handle: string; auth_status: string }[];
  tracked_peers: { platform: string; handle: string; kind: string; name: string; why: string }[];
  fields: Record<string, unknown>;
}

// ─────────────────────────────────────────────────────────────── the client

export const managerApi = {
  // sources (D12: one key, one job, additive intelligence)
  systemSources: () => get<SystemSourcesResponse>("/system/sources"),
  tenantSources: () => get<TenantSourcesResponse>("/manager/sources"),

  // action follow-ups
  listActions: (status?: string) =>
    get<ActionsResponse>(`/manager/actions${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  setActionStatus: (actionId: string, status: ActionStatus, snoozeDays?: number) =>
    post<{ action: ActionItem }>(`/manager/actions/${actionId}/status`, {
      status,
      ...(snoozeDays !== undefined ? { snooze_days: snoozeDays } : {}),
    }),
  addActionNote: (actionId: string, note: string) =>
    post<{ action: ActionItem }>(`/manager/actions/${actionId}/note`, { note }),
  deleteAction: (actionId: string) => del<{ deleted: string }>(`/manager/actions/${actionId}`),
  researchContact: (actionId: string) =>
    post<ContactResearchResult>(`/manager/actions/${actionId}/research-contact`),

  // the daily heartbeat
  runDailyCycle: () => post<DailyCycleReport>("/manager/daily-cycle"),
  dailyDigest: () => get<DailyDigest>("/manager/daily-digest"),

  // the five eyes (each POST runs the scanning agent and returns its report;
  // every finding also lands as a deduped follow-up ActionItem)
  scan: (eye: EyeName) => post<EyeReport>(`/manager/scan/${eye}`),
  contentRadar: () => post<ContentRadarReport>("/manager/scan/content-radar"),
  trends: () => post<TrendsReport>("/manager/scan/trends"),
  press: () => post<PressReport>("/manager/scan/press"),
  searchQuestions: () => post<QuestionsReport>("/manager/scan/questions"),
  appearances: () => post<AppearancesReport>("/manager/scan/appearances"),

  // algorithm playbooks + baseline audit
  refreshAlgorithm: () => post<AlgorithmReport>("/manager/algorithm/refresh"),
  runAudit: () => post<AuditReport>("/manager/audit"),

  // peers: DISCOVER (202 + poll, 409 while in flight) → APPROVE → TRACK
  startPeerDiscovery: () => post<JobAccepted>("/manager/peers/discover"),
  peerDiscoveryStatus: () => get<JobRunStatus>("/manager/peers/discover/status"),
  /** Poll discovery until the run lands (succeeded/failed). */
  awaitPeerDiscovery: (opts?: PollOptions) =>
    pollUntil(() => get<JobRunStatus>("/manager/peers/discover/status"), jobDone, opts),
  peerCandidates: () => get<PeerCandidatesResponse>("/manager/peers/candidates"),
  approvePeer: (handle: string) =>
    post<PeerActionResponse>("/manager/peers/approve", { handle }),
  rejectPeer: (handle: string) =>
    post<PeerActionResponse>("/manager/peers/reject", { handle }),
  snapshotPeers: () => post<Record<string, unknown>>("/manager/peers/snapshot"),

  // collaboration strategy
  generateCollab: () => post<CollabReport>("/manager/collab/generate"),
  latestCollab: () => get<CollabLatest>("/manager/collab/latest"),

  // planning: weekly prescription + morning brief
  draftWeeklyPlan: () => post<WeeklyPlanDraft>("/manager/plan/weekly"),
  latestPlan: () => get<LatestPlanResponse>("/manager/plan/latest"),
  /** 404 unknown plan, 409 a newer prescription overtook it, 422 bad indices.
   * Omit itemIndices for accept-all (PRD R2.3 partial acceptance). */
  activatePlan: (planId: string, itemIndices?: number[]) =>
    post<ActivateResult>(`/manager/plan/${planId}/activate`, {
      item_indices: itemIndices ?? null,
    }),
  brief: () => get<MorningBrief>("/manager/brief"),

  // execution spine: opportunity → draft → approve/reject → publish → impact
  executeOpportunity: (actionId: string, format?: ContentFormat) =>
    post<ExecuteResult>(
      `/manager/opportunities/${actionId}/execute`,
      format ? { format } : {},
    ),
  approveWorkOrder: (workOrderId: string, reason = "") =>
    post<ApproveResult>(`/manager/work-orders/${workOrderId}/approve`, { reason }),
  rejectWorkOrder: (workOrderId: string, reason: string) =>
    post<RejectResult>(`/manager/work-orders/${workOrderId}/reject`, { reason }),
  publishWorkOrder: (workOrderId: string) =>
    post<PublishResult>(`/manager/work-orders/${workOrderId}/publish`),
  listWork: () => get<WorkResponse>("/manager/work"),

  // intake: the interview loop + batch auto-answer + the research seed
  interviewNext: (budget = 3) =>
    get<InterviewNext>(`/manager/intake/interview/next?budget=${budget}`),
  suggestAnswer: (questionId: string) =>
    post<AnswerSuggestion>(`/manager/intake/questions/${questionId}/suggest`),
  answerQuestion: (questionId: string, answer: string) =>
    post<AnswerResult>(`/manager/intake/questions/${questionId}/answer`, { answer }),
  startAutoAnswer: () => post<JobAccepted>("/manager/intake/questions/auto-answer"),
  autoAnswerStatus: () => get<AutoAnswerStatus>("/manager/intake/questions/auto-answer/status"),
  awaitAutoAnswer: (opts?: PollOptions) =>
    pollUntil(
      () => get<AutoAnswerStatus>("/manager/intake/questions/auto-answer/status"),
      (s) => s.state === "succeeded" || s.state === "failed" || s.state === "none",
      opts,
    ),
  setResearchSeed: (seed: ResearchSeed) =>
    post<ResearchSeedResponse>("/manager/intake/research-seed", seed),
  getResearchSeed: () => get<ResearchSeedResponse>("/manager/intake/research-seed"),

  // research: sync discover ("is this your brand?"), async confirmed fan-out
  discover: (body: {
    name: string;
    entity_type?: EntityType;
    website?: string | null;
    socials?: string[];
    location?: string | null;
  }) => post<DiscoverResponse>("/manager/research/discover", body),
  confirmResearch: (confirmedUrls: string[]) =>
    post<JobAccepted>("/manager/research/confirmed", { confirmed_urls: confirmedUrls }),
  researchStatus: () => get<ResearchStatus>("/manager/research/status"),
  awaitResearch: (opts?: PollOptions) =>
    pollUntil(
      () => get<ResearchStatus>("/manager/research/status"),
      (s) => s.state === "succeeded" || s.state === "failed",
      opts,
    ),

  // brand voice: harvest (202 + poll, 409 while running) + one-URL add-source
  harvestVoice: () => post<JobAccepted>("/manager/voice/harvest"),
  addVoiceSource: (url: string) => post<JobAccepted>("/manager/voice/add-source", { url }),
  voiceStatus: () => get<JobRunStatus>("/manager/voice/status"),
  awaitVoiceHarvest: (opts?: PollOptions) =>
    pollUntil(() => get<JobRunStatus>("/manager/voice/status"), jobDone, opts),
  voiceProfile: () => get<VoiceProfileResponse>("/manager/voice/profile"),

  // connected accounts (one aggregator group per tenant)
  listAccounts: () => get<AccountsResponse>("/manager/accounts"),
  addAccount: (platform: Platform | string, handle: string) =>
    post<ConnectedAccount>("/manager/accounts", { platform, handle }), // 201; 409 dup
  accountGroups: () => get<AccountGroupsResponse>("/manager/accounts/groups"),
  bindGroup: (groupId: string) => post<SyncResponse>("/manager/accounts/bind", { group_id: groupId }),
  syncAccounts: () => post<SyncResponse>("/manager/accounts/sync"),
  connectUrl: (platform: Platform | string = "instagram") =>
    get<ConnectUrlResponse>(`/manager/accounts/connect-url?platform=${encodeURIComponent(platform)}`),

  // checklist + profile projection
  nextSteps: () => get<NextStep[]>("/manager/next-steps"),
  brandProfile: () => get<BrandProfileProjection>("/manager/brand-profile"),
};
