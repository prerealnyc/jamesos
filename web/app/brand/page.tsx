"use client";

import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type PlugIn } from "@/lib/api";
import { Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner } from "@/components/ui";
import { HelpButton } from "@/components/help-drawer";

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
      // This is the Voice Rules section — documents dropped here are brand
      // guidelines, so tag them 'guideline' (→ voice grounding) rather than
      // the backend's 'reference' default (which lands in the facts bucket).
      const d = await api.uploadDocument(f, "guideline");
      setDrop({ msg: `✓ “${d.filename}” → ${d.chunks_created} guideline chunk(s) added to the brand voice.`, tone: "ok" });
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
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Brand voice &amp; rules</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Everything here is loaded into the model on <b>every</b> answer and cannot be
            overridden by a prompt. This is how the brand&apos;s tone and strict guidelines are enforced.
          </p>
        </div>
        <div className="shrink-0 pt-1">
          <HelpButton />
        </div>
      </header>

      <BrandKitCard />

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


// ── Brand kit ─────────────────────────────────────────────────────────
// The identity every RENDER carries: lower-third name plate (first ~3s),
// top-right logo watermark, and the "FOLLOW FOR MORE + handle + logo"
// end card. Saved per-tenant; renders read it automatically.
function BrandKitCard() {
  const [kit, setKit] = useState<{ display_name: string; tagline: string; handle: string; logo_url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const logoRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getBrandKit().then(setKit).catch((e) => setErr(e instanceof Error ? e.message : "load failed"));
  }, []);

  async function save() {
    if (!kit) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      setKit(await api.putBrandKit(kit));
      setMsg("saved — every new render now carries this");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function onLogo(files: FileList | null) {
    if (!files || files.length === 0 || !kit) return;
    setBusy(true);
    setErr(null);
    try {
      const m = await api.uploadMedia(files[0], "brand_logo", { title: files[0].name });
      const updated = await api.putBrandKit({ logo_url: m.uri });
      setKit(updated);
      setMsg("logo uploaded — watermark + end card will use it");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "logo upload failed");
    } finally {
      setBusy(false);
      if (logoRef.current) logoRef.current.value = "";
    }
  }

  if (!kit) return null;
  return (
    <Card>
      <CardTitle>Brand kit — what every video carries</CardTitle>
      <p className="text-[12px] text-muted-foreground mt-1 mb-2">
        Name plate (first 3s, lower third) · logo watermark (top-right, whole video) ·
        end card (&quot;FOLLOW FOR MORE&quot; + handle + logo, last 2.6s). Renders pick these up
        automatically — leave a field empty to skip that element.
      </p>
      <div className="grid md:grid-cols-3 gap-3">
        <div>
          <Label>Display name (name plate)</Label>
          <Input value={kit.display_name} onChange={(e) => setKit({ ...kit, display_name: e.target.value })} placeholder="James Prendamano" />
        </div>
        <div>
          <Label>Tagline</Label>
          <Input value={kit.tagline} onChange={(e) => setKit({ ...kit, tagline: e.target.value })} placeholder="PreReal · Staten Island" />
        </div>
        <div>
          <Label>Handle (end card)</Label>
          <Input value={kit.handle} onChange={(e) => setKit({ ...kit, handle: e.target.value })} placeholder="@jamesprendamano" />
        </div>
      </div>
      <div className="flex items-center gap-4 mt-3 flex-wrap">
        <div className="flex items-center gap-3">
          {kit.logo_url ? (
            <img src={mediaUrl(kit.logo_url)} alt="brand logo" className="h-10 w-auto rounded bg-secondary p-1" />
          ) : (
            <span className="text-[11px] text-muted-foreground">no logo yet — watermark + end-card logo are skipped</span>
          )}
          <input
            ref={logoRef}
            type="file"
            accept="image/png,image/webp,image/svg+xml,image/jpeg"
            onChange={(e) => onLogo(e.target.files)}
            className="text-[12px] text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-primary file:text-primary-foreground file:cursor-pointer"
          />
        </div>
        <Button onClick={save} disabled={busy}>
          {busy ? <span className="flex items-center gap-2"><Spinner /> saving…</span> : "Save brand kit"}
        </Button>
        {msg && <span className="text-[12px] text-accent">✓ {msg}</span>}
        {err && <span className="text-[12px] text-destructive">✗ {err}</span>}
      </div>
    </Card>
  );
}
