"use client";

import { useEffect, useRef, useState } from "react";
import { api, type PlugIn } from "@/lib/api";
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

const SLOTS = [
  { v: "identity", d: "identity — who/what the brand is, the voice" },
  { v: "guideline", d: "guideline — a rule to follow" },
  { v: "protocol", d: "protocol — a hard, non-negotiable rule" },
  { v: "framework", d: "framework — a playbook (pillars, EOS…)" },
  { v: "frustration", d: "frustration — what NOT to do / past mistakes" },
];

type FileState = {
  name: string;
  status: "queued" | "ingesting" | "done" | "error";
  detail: string;
};

export default function SettingsPage() {
  // profile
  const [profile, setProfile] = useState({ brand: "", name: "", email: "" });
  const [savingProfile, setSavingProfile] = useState(false);

  // brand documents
  const [files, setFiles] = useState<FileState[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // structured rules
  const [slot, setSlot] = useState("guideline");
  const [ruleName, setRuleName] = useState("");
  const [ruleBody, setRuleBody] = useState("");
  const [savingRule, setSavingRule] = useState(false);
  const [plugins, setPlugins] = useState<PlugIn[]>([]);

  // connections
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

  async function ingestFiles(list: FileList) {
    const picked = Array.from(list);
    setFiles(picked.map((f) => ({ name: f.name, status: "queued", detail: "" })));
    setUploading(true);
    for (let i = 0; i < picked.length; i++) {
      setFiles((s) => s.map((x, j) => (j === i ? { ...x, status: "ingesting" } : x)));
      try {
        const d = await api.uploadDocument(picked[i]);
        setFiles((s) =>
          s.map((x, j) =>
            j === i
              ? { ...x, status: "done", detail: `${d.chunks_created} memory chunk(s)` }
              : x
          )
        );
      } catch (e) {
        setFiles((s) =>
          s.map((x, j) =>
            j === i
              ? { ...x, status: "error", detail: e instanceof Error ? e.message : "failed" }
              : x
          )
        );
      }
    }
    setUploading(false);
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
      for (const c of conns) {
        await api.upsertConnection(c.platform, c.handle, c.enabled);
      }
      setConns(await api.connections());
    } finally {
      setSavingConns(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        sub="Profile, brand guidance, voice rules, and connections. Everything here governs how the brand manager writes — per brand."
      />

      {/* ── Brand guidance documents (the headline feature) ── */}
      <Card>
        <CardTitle>Brand guidance documents</CardTitle>
        <p className="text-muted-foreground text-sm mb-3">
          Drop the founder thesis, frustration ledger, protocols, voice corpus, master
          spec — anything that should guide this brand. Each file is chunked, embedded,
          and becomes citable brand memory. Multiple files at once. Audio (.mp3/.m4a/.wav)
          is transcribed via Whisper.
        </p>
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (e.dataTransfer.files.length) ingestFiles(e.dataTransfer.files);
          }}
          className="border-2 border-dashed border-border rounded-lg p-8 text-center text-muted-foreground cursor-pointer hover:border-primary hover:bg-secondary transition-colors"
        >
          {uploading ? (
            <Spinner />
          ) : (
            <>
              <b className="text-foreground">Drag files here</b> or click to choose
            </>
          )}
          <div className="text-xs mt-1">.txt .md .pdf .docx .mp3 .m4a .wav — multiple OK</div>
          <input
            ref={fileRef}
            type="file"
            hidden
            multiple
            accept=".txt,.md,.markdown,.csv,.json,.pdf,.docx,.mp3,.m4a,.wav,.mp4,.webm,.flac,.ogg"
            onChange={(e) => e.target.files?.length && ingestFiles(e.target.files)}
          />
        </div>
        {files.length > 0 && (
          <div className="mt-4 flex flex-col gap-1.5">
            {files.map((f, i) => (
              <div
                key={i}
                className="flex items-center gap-3 text-sm bg-background border border-border rounded-md px-3 py-2"
              >
                <span className="flex-1 truncate">{f.name}</span>
                {f.status === "queued" && (
                  <span className="text-muted-foreground text-xs">queued</span>
                )}
                {f.status === "ingesting" && <Spinner />}
                {f.status === "done" && <Badge tone="ok">✓ {f.detail}</Badge>}
                {f.status === "error" && (
                  <span className="text-destructive text-xs">✗ {f.detail}</span>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-muted-foreground text-xs mt-3">
          Large files (e.g. a 400 KB protocol doc) create many chunks and may pace against
          the embedding rate limit — that&apos;s surfaced per-file above, not hidden.
        </p>
      </Card>

      {/* ── Structured voice rules ── */}
      <Card>
        <CardTitle>Strict voice rules</CardTitle>
        <p className="text-muted-foreground text-sm mb-2">
          Hard rules loaded into the model on <b>every</b> answer — can&apos;t be
          overridden by a prompt. Use this for non-negotiables (banned phrases, the voice
          spine, compliance lines).
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
          placeholder="e.g. Voice spine, Banned phrases"
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

      {/* ── Profile ── */}
      <Card>
        <CardTitle>Profile</CardTitle>
        <p className="text-muted-foreground text-sm mb-1">
          Stored per tenant. Not authentication — no login system yet.
        </p>
        <Label>Brand</Label>
        <Input
          value={profile.brand}
          onChange={(e) => setProfile({ ...profile, brand: e.target.value })}
          placeholder="JP Brand Manager"
        />
        <Label>Operator name</Label>
        <Input
          value={profile.name}
          onChange={(e) => setProfile({ ...profile, name: e.target.value })}
          placeholder="e.g. James Prendamano"
        />
        <Label>Email</Label>
        <Input
          value={profile.email}
          onChange={(e) => setProfile({ ...profile, email: e.target.value })}
          placeholder="you@example.com"
        />
        <div className="mt-3">
          <Button onClick={saveProfile} disabled={savingProfile}>
            {savingProfile ? <Spinner /> : "Save profile"}
          </Button>
        </div>
      </Card>

      {/* ── Social connections ── */}
      <Card>
        <CardTitle>Social connections</CardTitle>
        <p className="text-muted-foreground text-sm mb-3">
          Records handle + enabled state per platform. Honest scope: this stores config —
          real per-platform OAuth posting is a separate build, not faked here.
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
              <Badge tone={c.status === "configured" ? "primary" : "muted"}>
                {c.status}
              </Badge>
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
