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

export const api = {
  health: () => jget<{ status: string }>("/health"),
  ask: (question: string) => jpost<AskResponse>("/ask", { question }),
  listPlugIns: () => jget<PlugIn[]>("/plug-ins"),
  addPlugIn: (slot: string, name: string, rule: string) =>
    jpost<PlugIn>("/plug-ins", { slot, name, content: { rule }, applies_to: [] }),
  async uploadDocument(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch("/ingest/document", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d as { filename: string; chunks_created: number };
  },
};
