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

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
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

export const api = {
  health: () => jget<{ status: string }>("/health"),
  generate: (brief: Partial<ContentBrief> & { topic: string }) =>
    jpost<ContentDraft>("/generate", brief),
  getCredentials: () => jget<CredentialStatus>("/api/credentials"),
  setCredentials: (updates: Record<string, string>) =>
    jpost<{ ok: boolean } & CredentialStatus>("/api/credentials", { updates }),
  checkIntegrations: () => jget<IntegrationCheck>("/api/integrations/check"),
  queue: () => jget<QueueItem[]>("/api/queue"),
  queueStats: () => jget<QueueStats>("/api/queue/stats"),
  approve: (id: string, reason = "approved via dashboard") =>
    jpost(`/api/queue/${id}/approve`, { reason }),
  reject: (id: string, reason = "rejected via dashboard") =>
    jpost(`/api/queue/${id}/reject`, { reason }),
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
    return d as { filename: string; chunks_created: number };
  },
};
