"use client";

import { useEffect, useRef, useState } from "react";
import { api, type PlugIn } from "@/lib/api";
import { Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner } from "@/components/ui";

const SLOTS = [
  { v: "identity", d: "identity — who/what the brand is, the voice" },
  { v: "guideline", d: "guideline — a rule to follow" },
  { v: "protocol", d: "protocol — a hard, non-negotiable rule" },
  { v: "framework", d: "framework — a playbook (pillars, EOS…)" },
  { v: "frustration", d: "frustration — what NOT to do / past mistakes" },
];

export default function BrandPage() {
  const [slot, setSlot] = useState("identity");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [items, setItems] = useState<PlugIn[]>([]);
  const [drop, setDrop] = useState<{ msg: string; tone: "muted" | "ok" | "bad" }>({ msg: "", tone: "muted" });
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setItems(await api.listPlugIns());
    } catch {
      /* empty state handles it */
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function add() {
    if (!name.trim() || !content.trim()) return;
    setSaving(true);
    try {
      await api.addPlugIn(slot, name.trim(), content.trim());
      setName("");
      setContent("");
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : "failed");
    } finally {
      setSaving(false);
    }
  }

  async function upload(f: File) {
    setBusy(true);
    setDrop({ msg: `Ingesting “${f.name}”…`, tone: "muted" });
    try {
      const d = await api.uploadDocument(f);
      setDrop({ msg: `✓ “${d.filename}” → ${d.chunks_created} memory chunk(s).`, tone: "ok" });
    } catch (e) {
      setDrop({ msg: `✗ ${e instanceof Error ? e.message : "failed"}`, tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  const dropClass =
    drop.tone === "ok" ? "text-accent" : drop.tone === "bad" ? "text-destructive" : "text-muted-foreground";

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Brand voice &amp; rules</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Everything here is loaded into the model on <b>every</b> answer and cannot be
          overridden by a prompt. This is how the brand&apos;s tone and strict guidelines are enforced.
        </p>
      </header>

      <Card>
        <CardTitle>Add a voice rule</CardTitle>
        <Label>Type</Label>
        <Select value={slot} onChange={(e) => setSlot(e.target.value)}>
          {SLOTS.map((s) => (
            <option key={s.v} value={s.v}>
              {s.d}
            </option>
          ))}
        </Select>
        <Label>Name</Label>
        <Input placeholder="e.g. Voice spine, Banned phrases" value={name} onChange={(e) => setName(e.target.value)} />
        <Label>Content</Label>
        <Textarea
          rows={4}
          placeholder="The actual voice rule / guideline / lesson. Specific — enforced verbatim."
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="mt-3">
          <Button onClick={add} disabled={saving}>
            {saving ? <Spinner /> : "Add to brand rules"}
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle>Ingest a brand document</CardTitle>
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
          }}
          className="border-2 border-dashed border-border rounded-lg p-7 text-center text-muted-foreground cursor-pointer hover:border-primary hover:bg-secondary transition-colors"
        >
          {busy ? <Spinner /> : <b className="text-foreground">Drag a file here</b>} — .txt .md .pdf
          .docx and audio (.mp3 .m4a .wav → Whisper). Chunked, embedded, becomes brand memory.
          <input
            ref={fileRef}
            type="file"
            hidden
            accept=".txt,.md,.markdown,.csv,.json,.pdf,.docx,.mp3,.m4a,.wav,.mp4,.webm,.flac,.ogg"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </div>
        {drop.msg && <div className={`mt-3 text-sm ${dropClass}`}>{drop.msg}</div>}
      </Card>

      <Card>
        <CardTitle>Rules governing the voice now</CardTitle>
        {items.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Nothing yet. Add a rule or ingest a document above.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((p) => {
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
    </div>
  );
}
