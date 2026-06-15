"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  type PlugIn,
  type CredentialField,
  type IntegrationCheck,
} from "@/lib/api";
import {
  Button,
  Card,
  CardTitle,
  Input,
  Textarea,
  Select,
  Label,
  Badge,
  Spinner,
  PageHeader,
} from "@/components/ui";

const ACCEPT = ".txt,.md,.markdown,.csv,.json,.pdf,.docx,.mp3,.m4a,.wav,.mp4,.webm,.flac,.ogg";

const CATEGORIES: { key: string; title: string; blurb: string; examples: string }[] = [
  {
    key: "thesis",
    title: "Founder thesis & vision",
    blurb: "The point of view. Without this, content is generic.",
    examples: "→ JAMES_THE_FULL_VISION",
  },
  {
    key: "guideline",
    title: "Brand guidelines & voice rules",
    blurb: "How content must sound — pillars, voice spine, identity.",
    examples: "→ JP_BRAND_MANAGER_MASTER_SPEC",
  },
  {
    key: "frustration",
    title: "Frustration ledger (what NOT to do)",
    blurb: "Negative guardrails that stop the voice from drifting.",
    examples: "→ JAMES_FRUSTRATION_DRIFT_LEDGER",
  },
  {
    key: "voice_corpus",
    title: "Voice corpus (how James actually sounds)",
    blurb: "Podcast / academy transcripts, audio. The strongest voice signal.",
    examples: "→ podcast_full_context, academy transcripts (the ~10MB)",
  },
  {
    key: "reference",
    title: "Reference & strategy (context, not voice)",
    blurb: "Plans, inventories, specs. Useful context; not voice training.",
    examples: "→ LLM_INGESTION_PLAN, HANDOFF_INVENTORY, etc.",
  },
];

type Staged = {
  file: File;
  status: "staged" | "uploading" | "done" | "error";
  detail: string;
};

function CategoryUpload({
  category,
  title,
  blurb,
  examples,
}: {
  category: string;
  title: string;
  blurb: string;
  examples: string;
}) {
  const [staged, setStaged] = useState<Staged[]>([]);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    if (!list) return;
    setStaged((s) => [
      ...s,
      ...Array.from(list).map((file) => ({ file, status: "staged" as const, detail: "" })),
    ]);
  }

  async function uploadAll() {
    setBusy(true);
    for (let i = 0; i < staged.length; i++) {
      if (staged[i].status === "done") continue;
      setStaged((s) => s.map((x, j) => (j === i ? { ...x, status: "uploading" } : x)));
      try {
        const d = await api.uploadDocument(staged[i].file, category);
        const detail =
          d.superseded_chunks > 0
            ? `${d.chunks_created} chunks · replaced ${d.superseded_chunks} from old version`
            : `${d.chunks_created} chunks`;
        setStaged((s) =>
          s.map((x, j) => (j === i ? { ...x, status: "done", detail } : x))
        );
      } catch (e) {
        setStaged((s) =>
          s.map((x, j) =>
            j === i
              ? { ...x, status: "error", detail: e instanceof Error ? e.message : "failed" }
              : x
          )
        );
      }
    }
    setBusy(false);
  }

  const pending = staged.filter((s) => s.status === "staged" || s.status === "error").length;

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-3">
        <CardTitle>{title}</CardTitle>
        <span className="text-[11px] text-muted-foreground font-mono">{examples}</span>
      </div>
      <p className="text-muted-foreground text-sm -mt-1 mb-3">{blurb}</p>

      <div
        onClick={() => ref.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          add(e.dataTransfer.files);
        }}
        className="border-2 border-dashed border-border rounded-lg p-6 text-center text-muted-foreground cursor-pointer hover:border-primary hover:bg-secondary transition-colors text-sm"
      >
        <b className="text-foreground">Choose files</b> or drag here — they stage below,
        nothing uploads until you press the button.
        <input
          ref={ref}
          type="file"
          hidden
          multiple
          accept={ACCEPT}
          onChange={(e) => add(e.target.files)}
        />
      </div>

      {staged.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {staged.map((s, i) => (
            <div
              key={i}
              className="flex items-center gap-3 text-sm bg-background border border-border rounded-md px-3 py-2"
            >
              <span className="flex-1 truncate">{s.file.name}</span>
              {s.status === "staged" && (
                <span className="text-muted-foreground text-xs">staged</span>
              )}
              {s.status === "uploading" && <Spinner />}
              {s.status === "done" && <Badge tone="ok">✓ {s.detail}</Badge>}
              {s.status === "error" && (
                <span className="text-destructive text-xs">✗ {s.detail}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <Button onClick={uploadAll} disabled={busy || pending === 0}>
          {busy ? <Spinner /> : `Upload ${pending || ""} → ${category}`}
        </Button>
        <span className="text-xs text-muted-foreground">
          → stored as <b className="text-foreground">{category}</b> memory in Supabase
        </span>
      </div>
    </Card>
  );
}

const SLOTS = [
  { v: "identity", d: "identity — who/what the brand is, the voice" },
  { v: "guideline", d: "guideline — a rule to follow" },
  { v: "protocol", d: "protocol — a hard, non-negotiable rule" },
  { v: "framework", d: "framework — a playbook (pillars, EOS…)" },
  { v: "frustration", d: "frustration — what NOT to do / past mistakes" },
];

// Credential field → live connectivity probe name (only those with a
// real probe in /api/integrations/check).
const PROBE_FOR: Record<string, string> = {
  anthropic_api_key: "anthropic",
  voyage_api_key: "voyage",
  cohere_api_key: "cohere",
  openai_api_key: "openai",
  perplexity_api_key: "perplexity",
  xpoz_api_key: "xpoz",
  google_search_api_key: "google_search",
  heygen_api_key: "heygen",
  runway_api_key: "runway",
};

function statusTone(s: string): "ok" | "destructive" | "accent" | "muted" {
  if (s === "ok") return "ok";
  if (s === "bad_key") return "destructive";
  if (s === "rate_limited") return "accent";
  return "muted";
}

function ApiKeys() {
  const [fields, setFields] = useState<CredentialField[]>([]);
  const [note, setNote] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [checks, setChecks] = useState<IntegrationCheck["results"]>({});

  async function load() {
    try {
      const s = await api.getCredentials();
      setFields(s.fields);
      setNote(s.note);
    } catch {}
  }
  useEffect(() => {
    load();
  }, []);

  async function saveAll() {
    const updates: Record<string, string> = {};
    for (const [k, v] of Object.entries(drafts)) {
      if (v.trim()) updates[k] = v.trim();
    }
    if (Object.keys(updates).length === 0) return;
    setBusy(true);
    try {
      const s = await api.setCredentials(updates);
      setFields(s.fields);
      setNote(s.note);
      setDrafts({});
    } finally {
      setBusy(false);
    }
  }

  async function clearOne(name: string) {
    setBusy(true);
    try {
      const s = await api.setCredentials({ [name]: "" });
      setFields(s.fields);
      setNote(s.note);
      setDrafts((d) => {
        const { [name]: _omit, ...rest } = d;
        return rest;
      });
    } finally {
      setBusy(false);
    }
  }

  async function testConnections() {
    setTesting(true);
    try {
      const r = await api.checkIntegrations();
      setChecks(r.results);
    } finally {
      setTesting(false);
    }
  }

  const groups = Array.from(new Set(fields.map((f) => f.group)));
  const pending = Object.values(drafts).filter((v) => v.trim()).length;

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-3">
        <CardTitle>API keys & connections</CardTitle>
        <Button onClick={testConnections} disabled={testing}>
          {testing ? <Spinner /> : "Test live connections"}
        </Button>
      </div>
      <p className="text-muted-foreground text-sm mb-3">
        Drop in your own keys — Perplexity, Google Custom Search, OpenAI,
        etc. They save to your tenant and connect automatically on the next
        request. No restart, no editing files.
      </p>

      {groups.map((g) => (
        <div key={g} className="mt-4">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            {g}
          </h3>
          <div className="flex flex-col gap-2">
            {fields
              .filter((f) => f.group === g)
              .map((f) => {
                const probe = PROBE_FOR[f.name];
                const chk = probe ? checks[probe] : undefined;
                return (
                  <div
                    key={f.name}
                    className="grid grid-cols-[minmax(180px,1fr)_1.5fr_auto] gap-3 items-center"
                  >
                    <div className="flex flex-col">
                      <span className="text-sm font-medium">{f.label}</span>
                      <span className="text-[11px] text-muted-foreground">
                        {f.configured ? (
                          <>
                            set{" "}
                            <span className="font-mono">{f.masked}</span> ·{" "}
                            {f.source === "ui"
                              ? "from Settings"
                              : f.source === "env"
                              ? "from .env"
                              : ""}
                          </>
                        ) : (
                          "not set"
                        )}
                      </span>
                    </div>
                    <Input
                      type={f.secret ? "password" : "text"}
                      placeholder={
                        f.placeholder ||
                        (f.configured ? "replace…" : "paste key…")
                      }
                      value={drafts[f.name] ?? ""}
                      onChange={(e) =>
                        setDrafts((d) => ({ ...d, [f.name]: e.target.value }))
                      }
                    />
                    <div className="flex items-center gap-2 justify-end">
                      {chk && (
                        <Badge tone={statusTone(chk.status)}>
                          {chk.status}
                        </Badge>
                      )}
                      {f.configured && f.source === "ui" && (
                        <button
                          onClick={() => clearOne(f.name)}
                          disabled={busy}
                          className="text-[11px] text-muted-foreground hover:text-destructive"
                        >
                          clear
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      ))}

      <div className="mt-4 flex items-center gap-3">
        <Button onClick={saveAll} disabled={busy || pending === 0}>
          {busy ? <Spinner /> : `Save ${pending || ""} key${pending === 1 ? "" : "s"}`}
        </Button>
        <span className="text-xs text-muted-foreground">{note}</span>
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const [profile, setProfile] = useState({ brand: "", name: "", email: "" });
  const [savingProfile, setSavingProfile] = useState(false);

  const [slot, setSlot] = useState("guideline");
  const [ruleName, setRuleName] = useState("");
  const [ruleBody, setRuleBody] = useState("");
  const [savingRule, setSavingRule] = useState(false);
  const [plugins, setPlugins] = useState<PlugIn[]>([]);

  const [conns, setConns] = useState<
    { platform: string; handle: string; enabled: boolean; status: string }[]
  >([]);
  const [savingConns, setSavingConns] = useState(false);

  async function loadAll() {
    try {
      setProfile(await api.getProfile());
    } catch {}
    try {
      setPlugins(await api.listPlugIns());
    } catch {}
    try {
      setConns(await api.connections());
    } catch {}
  }
  useEffect(() => {
    loadAll();
  }, []);

  async function saveProfile() {
    setSavingProfile(true);
    try {
      await api.setProfile(profile);
    } finally {
      setSavingProfile(false);
    }
  }
  async function addRule() {
    if (!ruleName.trim() || !ruleBody.trim()) return;
    setSavingRule(true);
    try {
      await api.addPlugIn(slot, ruleName.trim(), ruleBody.trim());
      setRuleName("");
      setRuleBody("");
      setPlugins(await api.listPlugIns());
    } finally {
      setSavingRule(false);
    }
  }
  async function saveConns() {
    setSavingConns(true);
    try {
      for (const c of conns) await api.upsertConnection(c.platform, c.handle, c.enabled);
      setConns(await api.connections());
    } finally {
      setSavingConns(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        sub="Each upload area below is a category. Files stage first, then you press Upload — they go to Supabase as tagged brand memory the content engine weights differently."
      />

      <ApiKeys />

      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Brand guidance — by category
        </h2>
        <p className="text-[12px] text-muted-foreground mb-3">
          Re-uploading a file with the same name into the same category
          replaces the previous version — the old chunks are retired and the
          brand manager uses only the latest. (History is kept for audit, not
          retrieved.)
        </p>
        <div className="flex flex-col gap-4">
          {CATEGORIES.map((c) => (
            <CategoryUpload key={c.key} category={c.key} title={c.title} blurb={c.blurb} examples={c.examples} />
          ))}
        </div>
      </div>

      <Card>
        <CardTitle>Strict voice rules</CardTitle>
        <p className="text-muted-foreground text-sm mb-2">
          Hard rules loaded into the model on <b>every</b> answer — can&apos;t be
          overridden by a prompt. Use for non-negotiables (voice spine, banned phrases).
        </p>
        <Label>Type</Label>
        <Select value={slot} onChange={(e) => setSlot(e.target.value)}>
          {SLOTS.map((s) => (
            <option key={s.v} value={s.v}>
              {s.d}
            </option>
          ))}
        </Select>
        <Label>Name</Label>
        <Input
          placeholder="e.g. Voice spine"
          value={ruleName}
          onChange={(e) => setRuleName(e.target.value)}
        />
        <Label>Rule</Label>
        <Textarea
          rows={3}
          placeholder="The actual rule — specific, enforced verbatim."
          value={ruleBody}
          onChange={(e) => setRuleBody(e.target.value)}
        />
        <div className="mt-3">
          <Button onClick={addRule} disabled={savingRule}>
            {savingRule ? <Spinner /> : "Add rule"}
          </Button>
        </div>
        {plugins.length > 0 && (
          <div className="mt-4 flex flex-col gap-2">
            {plugins.map((p) => {
              const body =
                (p.content && ((p.content.rule as string) || (p.content.text as string))) ||
                JSON.stringify(p.content);
              return (
                <div key={p.id} className="bg-background border border-border rounded-md p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge tone="primary">{p.slot}</Badge>
                    <span className="text-[13px] font-semibold">{p.name}</span>
                  </div>
                  <div className="text-[13px] whitespace-pre-wrap">{body}</div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card>
        <CardTitle>Profile</CardTitle>
        <Label>Brand</Label>
        <Input
          value={profile.brand}
          onChange={(e) => setProfile({ ...profile, brand: e.target.value })}
        />
        <Label>Operator name</Label>
        <Input
          value={profile.name}
          onChange={(e) => setProfile({ ...profile, name: e.target.value })}
        />
        <Label>Email</Label>
        <Input
          value={profile.email}
          onChange={(e) => setProfile({ ...profile, email: e.target.value })}
        />
        <div className="mt-3">
          <Button onClick={saveProfile} disabled={savingProfile}>
            {savingProfile ? <Spinner /> : "Save profile"}
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle>Social connections</CardTitle>
        <p className="text-muted-foreground text-sm mb-3">
          Stores handle + enabled per platform. Real per-platform OAuth posting is a
          separate build — not faked here.
        </p>
        <div className="flex flex-col">
          {conns.map((c, i) => (
            <div
              key={c.platform}
              className="grid grid-cols-[120px_1fr_auto_auto] gap-3 items-center py-2 border-b border-border last:border-0 text-sm"
            >
              <span className="capitalize font-medium">{c.platform}</span>
              <Input
                value={c.handle}
                placeholder="@handle"
                onChange={(e) =>
                  setConns((s) =>
                    s.map((x, j) => (j === i ? { ...x, handle: e.target.value } : x))
                  )
                }
              />
              <label className="flex items-center gap-2 text-muted-foreground">
                <input
                  type="checkbox"
                  checked={c.enabled}
                  onChange={(e) =>
                    setConns((s) =>
                      s.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x))
                    )
                  }
                />
                on
              </label>
              <Badge tone={c.status === "configured" ? "primary" : "muted"}>{c.status}</Badge>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <Button onClick={saveConns} disabled={savingConns}>
            {savingConns ? <Spinner /> : "Save connections"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
