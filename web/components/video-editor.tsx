"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, mediaUrl, type Scene, type Production } from "@/lib/api";
import { Button, Card, CardTitle, Input, Textarea, Select, Label, Badge, Spinner } from "@/components/ui";

const KINDS = [
  { v: "broll", label: "B-roll (AI)" },
  { v: "avatar", label: "Avatar (HeyGen)" },
  { v: "james_clip", label: "James clip" },
];

// Scene "kind+source" collapses into one selector for the editor.
function sceneType(s: Scene): string {
  if (s.kind === "broll") return "broll";
  return s.source === "james_clip" ? "james_clip" : "avatar";
}
function applyType(s: Scene, t: string): Scene {
  if (t === "broll") return { ...s, kind: "broll", source: null };
  return { ...s, kind: "talking_head", source: t === "james_clip" ? "james_clip" : "avatar" };
}

export default function VideoEditor() {
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [aspect, setAspect] = useState("9:16");
  const [mode, setMode] = useState<"mixed" | "avatar_only">("mixed");
  const [composing, setComposing] = useState(false);
  const [script, setScript] = useState("");
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [title, setTitle] = useState("");
  const [intel, setIntel] = useState<{ provider?: string; summary?: string; sources?: string[] } | null>(null);
  const [sel, setSel] = useState(0);
  const [err, setErr] = useState("");
  const [prod, setProd] = useState<Production | null>(null);
  const [rendering, setRendering] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // Handoff from the Approval Queue: if a script was stashed in sessionStorage,
  // pre-fill it and immediately plan it into scenes.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stashed = sessionStorage.getItem("pipeline.from_script");
    if (!stashed) return;
    sessionStorage.removeItem("pipeline.from_script");
    (async () => {
      setComposing(true); setErr("");
      try {
        const r = await api.planVideo(stashed, platform, aspect);
        setScript(stashed);
        setScenes((r.scenes || []).map((s, i) => ({ ...s, index: i })));
        setTitle(r.title || "From approved script");
        setSel(0);
      } catch (e) { setErr(e instanceof Error ? e.message : "auto-plan failed"); }
      finally { setComposing(false); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function compose() {
    setComposing(true); setErr(""); setProd(null);
    try {
      const r = await api.composeVideo(topic.trim(), platform, aspect);
      if (r.error) { setErr(r.error); return; }
      setScript(r.script || "");
      setScenes((r.scenes || []).map((s, i) => ({ ...s, index: i })));
      setTitle(r.title || "");
      setIntel(r.intel || null);
      setSel(0);
    } catch (e) { setErr(e instanceof Error ? e.message : "compose failed"); }
    finally { setComposing(false); }
  }

  function patchScene(i: number, p: Partial<Scene>) {
    setScenes((cur) => cur.map((s, n) => (n === i ? { ...s, ...p } : s)));
  }
  function move(i: number, dir: -1 | 1) {
    setScenes((cur) => {
      const j = i + dir;
      if (j < 0 || j >= cur.length) return cur;
      const next = [...cur];
      [next[i], next[j]] = [next[j], next[i]];
      return next.map((s, n) => ({ ...s, index: n }));
    });
    setSel((s) => Math.max(0, Math.min(scenes.length - 1, s + dir)));
  }
  function del(i: number) {
    setScenes((cur) => cur.filter((_, n) => n !== i).map((s, n) => ({ ...s, index: n })));
    setSel(0);
  }
  async function renderScene(i: number) {
    setRendering(i); setErr("");
    try {
      const r = await api.renderScene({ ...scenes[i], index: i }, aspect);
      patchScene(i, { url: r.url, clip_status: r.clip_status, note: r.note });
      setSel(i);
    } catch (e) { setErr(e instanceof Error ? e.message : "render failed"); }
    finally { setRendering(null); }
  }
  function addScene() {
    setScenes((cur) => [...cur, {
      index: cur.length, label: "scene", kind: "broll", source: null,
      voiceover: "", on_screen_text: "", visual_prompt: "", duration: 4,
    }]);
  }

  async function produce() {
    setErr("");
    try {
      // In avatar_only mode the backend uses the script directly and ignores
      // scenes; otherwise we send the edited scene plan.
      const p = await api.produceVideo(
        mode === "avatar_only"
          ? { title, platform, aspect, mode, script }
          : { title, platform, aspect, mode, scenes }
      );
      setProd(p);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const u = await api.getProduction(p.id).catch(() => null);
        if (u) {
          setProd(u);
          if (u.status === "succeeded" || u.status === "failed") { if (pollRef.current) clearInterval(pollRef.current); }
        }
      }, 2000);
    } catch (e) { setErr(e instanceof Error ? e.message : "produce failed"); }
  }

  const total = scenes.reduce((a, s) => a + (s.duration || 0), 0);
  const cur = scenes[sel];
  const producing = prod && prod.status !== "succeeded" && prod.status !== "failed";

  return (
    <div className="flex flex-col gap-4">
      {/* Compose bar */}
      <Card>
        <CardTitle>Auto-compose from trends</CardTitle>
        <p className="text-[12px] text-muted-foreground mt-1">
          Researches what&apos;s working now, writes an on-voice script, and breaks it into editable scenes
          (matched to your reference-video style). Then arrange below and produce.
        </p>
        <div className="flex flex-wrap items-end gap-2 mt-2">
          <div className="flex-1 min-w-[240px]">
            <Label>Topic / space</Label>
            <Input placeholder="e.g. Staten Island commercial real estate" value={topic}
              onChange={(e) => setTopic(e.target.value)} />
          </div>
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {["instagram", "tiktok", "youtube", "linkedin"].map((p) => <option key={p}>{p}</option>)}
          </Select>
          <Select value={aspect} onChange={(e) => setAspect(e.target.value)}>
            {["9:16", "16:9", "1:1"].map((a) => <option key={a}>{a}</option>)}
          </Select>
          <Button onClick={compose} disabled={composing}>
            {composing ? <Spinner /> : "Auto-generate"}
          </Button>
        </div>
        {/* Mode toggle — avatar_only renders one HeyGen video of the full
            script (continuous voice, no gaps); mixed uses the full plan. */}
        <div className="mt-3 flex gap-2">
          <button
            onClick={() => setMode("mixed")}
            className={`text-[12px] rounded-md px-3 py-1.5 border transition-colors ${
              mode === "mixed" ? "border-primary text-foreground bg-primary/10" : "border-border text-muted-foreground"
            }`}
            title="Per-scene mix: B-roll + avatar + James clips, assembled by Creatomate"
          >
            Mixed (B-roll + avatar + clips)
          </button>
          <button
            onClick={() => setMode("avatar_only")}
            className={`text-[12px] rounded-md px-3 py-1.5 border transition-colors ${
              mode === "avatar_only" ? "border-primary text-foreground bg-primary/10" : "border-border text-muted-foreground"
            }`}
            title="One HeyGen render of the full script — continuous voice, no gaps"
          >
            Avatar only (one HeyGen render)
          </button>
        </div>
        {err && <p className="text-destructive text-sm mt-2">✗ {err}</p>}
        {intel?.summary && (
          <div className="mt-2 rounded-md bg-background border border-border p-2 text-[11px] text-muted-foreground">
            <b className="text-foreground">Researched ({intel.provider}):</b> {intel.summary.slice(0, 280)}…
          </div>
        )}
      </Card>

      {scenes.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
          {/* Left: scene list + script */}
          <div className="flex flex-col gap-3">
            <Card>
              <div className="flex items-center justify-between">
                <CardTitle>Scenes</CardTitle>
                <span className="text-[12px] text-muted-foreground">total ~{total}s · {scenes.length} scenes</span>
              </div>
              {/* timeline strip */}
              <div className="flex gap-1 mt-2 mb-3">
                {scenes.map((s, i) => (
                  <button key={i} onClick={() => setSel(i)} title={s.label}
                    style={{ flexGrow: Math.max(1, s.duration) }}
                    className={`h-8 rounded text-[10px] font-semibold truncate px-1 ${
                      i === sel ? "ring-2 ring-primary" : ""
                    } ${s.kind === "broll" ? "bg-secondary text-muted-foreground" : "bg-primary/20 text-foreground"}`}>
                    {s.label}
                  </button>
                ))}
              </div>

              <div className="flex flex-col gap-2">
                {scenes.map((s, i) => (
                  <div key={i} onClick={() => setSel(i)}
                    className={`border rounded-md p-3 cursor-pointer ${i === sel ? "border-primary" : "border-border"}`}>
                    <div className="flex items-center gap-2 mb-2">
                      <Input value={s.label} onChange={(e) => patchScene(i, { label: e.target.value })}
                        className="w-24 h-7 text-[12px]" />
                      <Select value={sceneType(s)} onChange={(e) => patchScene(i, applyType(s, e.target.value))}
                        className="h-7 text-[12px] w-40">
                        {KINDS.map((k) => <option key={k.v} value={k.v}>{k.label}</option>)}
                      </Select>
                      <Input type="number" min={2} max={30} value={s.duration}
                        onChange={(e) => patchScene(i, { duration: Math.max(2, Math.min(30, +e.target.value || 2)) })}
                        className="w-16 h-7 text-[12px]" />
                      <span className="text-[11px] text-muted-foreground">s</span>
                      <div className="ml-auto flex items-center gap-2 text-[12px] text-muted-foreground">
                        <button onClick={(e) => { e.stopPropagation(); move(i, -1); }}>↑</button>
                        <button onClick={(e) => { e.stopPropagation(); move(i, 1); }}>↓</button>
                        <button onClick={(e) => { e.stopPropagation(); del(i); }} className="hover:text-destructive">✕</button>
                      </div>
                    </div>
                    {s.kind === "broll" ? (
                      <Input placeholder="B-roll visual prompt (what the AI clip shows)" value={s.visual_prompt}
                        onChange={(e) => patchScene(i, { visual_prompt: e.target.value })} className="text-[12px]" />
                    ) : (
                      <Textarea rows={2} placeholder="voiceover (what James says)" value={s.voiceover}
                        onChange={(e) => patchScene(i, { voiceover: e.target.value })} className="text-[12px]" />
                    )}
                    <Input placeholder="on-screen caption" value={s.on_screen_text}
                      onChange={(e) => patchScene(i, { on_screen_text: e.target.value })} className="text-[12px] mt-2" />
                    {/* production row: music · sfx · logo · transition */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2 text-[11px]">
                      <label className="flex flex-col">
                        <span className="text-muted-foreground uppercase tracking-[.4px]">Music</span>
                        <Select value={s.audio_music || "none"}
                          onChange={(e) => patchScene(i, { audio_music: e.target.value })}
                          className="h-7 text-[12px]">
                          {["upbeat","calm","dramatic","tension","none"].map(m => <option key={m}>{m}</option>)}
                        </Select>
                      </label>
                      <label className="flex flex-col">
                        <span className="text-muted-foreground uppercase tracking-[.4px]">SFX</span>
                        <Input value={s.audio_sfx || ""} placeholder="whoosh, ding…"
                          onChange={(e) => patchScene(i, { audio_sfx: e.target.value })}
                          className="h-7 text-[12px]" />
                      </label>
                      <label className="flex flex-col">
                        <span className="text-muted-foreground uppercase tracking-[.4px]">Logo</span>
                        <Select value={s.branding_logo ? (s.branding_position || "bottom-right") : "none"}
                          onChange={(e) => {
                            const v = e.target.value;
                            patchScene(i, v === "none"
                              ? { branding_logo: false, branding_position: "none" }
                              : { branding_logo: true, branding_position: v });
                          }}
                          className="h-7 text-[12px]">
                          {["none","bottom-right","bottom-center","top-right"].map(p => <option key={p}>{p}</option>)}
                        </Select>
                      </label>
                      <label className="flex flex-col">
                        <span className="text-muted-foreground uppercase tracking-[.4px]">Enter</span>
                        <Select value={s.transition_in || "cut"}
                          onChange={(e) => patchScene(i, { transition_in: e.target.value })}
                          className="h-7 text-[12px]">
                          {["cut","fade","slide"].map(t => <option key={t}>{t}</option>)}
                        </Select>
                      </label>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <button onClick={(e) => { e.stopPropagation(); renderScene(i); }}
                        disabled={rendering === i}
                        className="text-[12px] text-primary hover:underline disabled:opacity-50">
                        {rendering === i ? "rendering…" : s.url ? "re-render clip" : "render clip"}
                      </button>
                      {s.clip_status === "ok" && <Badge tone="ok">clip ready</Badge>}
                      {s.clip_status === "stub" && <Badge tone="muted">stub</Badge>}
                      {s.note && <span className="text-[11px] text-muted-foreground truncate">{s.note}</span>}
                    </div>
                  </div>
                ))}
              </div>
              <Button variant="secondary" onClick={addScene} className="mt-3">+ Add scene</Button>
            </Card>

            <Card>
              <CardTitle>Script</CardTitle>
              <Textarea rows={4} value={script} onChange={(e) => setScript(e.target.value)}
                className="text-[12px] mt-2" />
              <p className="text-[11px] text-muted-foreground mt-1">
                The narrative the scenes came from. Editing scenes above is what drives the video.
              </p>
            </Card>
          </div>

          {/* Right: preview */}
          <div className="flex flex-col gap-3">
            <Card>
              <CardTitle>Preview</CardTitle>
              <div className="mt-2 mx-auto bg-black rounded-lg overflow-hidden relative"
                style={{ aspectRatio: aspect.replace(":", "/"), width: "100%", maxWidth: 280 }}>
                {prod?.final_url && !prod.final_url.startsWith("stub://") ? (
                  <video src={mediaUrl(prod.final_url)} controls className="w-full h-full object-contain" />
                ) : cur?.url && cur.url.startsWith("http") ? (
                  <>
                    <video src={mediaUrl(cur.url)} controls className="w-full h-full object-contain" />
                    {cur.on_screen_text && (
                      <div className="absolute bottom-6 left-2 right-2 text-center pointer-events-none">
                        <span className="bg-black/60 text-white text-[13px] font-bold px-2 py-1 rounded">
                          {cur.on_screen_text}
                        </span>
                      </div>
                    )}
                  </>
                ) : cur ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center p-3 text-center">
                    <Badge tone={cur.kind === "broll" ? "muted" : "primary"}>
                      {cur.kind === "broll" ? "B-roll" : cur.source}
                    </Badge>
                    <p className="text-white/50 text-[11px] mt-2 line-clamp-4">
                      {cur.kind === "broll" ? cur.visual_prompt : cur.voiceover}
                    </p>
                    {cur.on_screen_text && (
                      <div className="absolute bottom-6 left-2 right-2">
                        <span className="bg-black/60 text-white text-[13px] font-bold px-2 py-1 rounded">
                          {cur.on_screen_text}
                        </span>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
              <p className="text-[11px] text-muted-foreground text-center mt-2">
                {prod?.final_url?.startsWith("stub://")
                  ? "Stub render (add render keys for a real cut)"
                  : `Scene ${sel + 1}/${scenes.length} · storyboard preview`}
              </p>
            </Card>

            <Card>
              <Button onClick={produce} disabled={!!producing} className="w-full">
                {producing ? <><Spinner /> {prod?.status}…</> : "Produce video"}
              </Button>
              {prod && (
                <div className="mt-3 text-[12px]">
                  <Badge tone={prod.status === "succeeded" ? "ok" : prod.status === "failed" ? "destructive" : "accent"}>
                    {prod.status}
                  </Badge>
                  {prod.error && <p className="text-destructive mt-1">{prod.error}</p>}
                  {prod.queued_action_id && (
                    <Link href="/queue" className="text-primary hover:underline block mt-2">
                      Review in approval queue →
                    </Link>
                  )}
                </div>
              )}
              <p className="text-[11px] text-muted-foreground mt-2">
                Renders each scene (avatar / B-roll / your clips) and assembles a cut into the approval queue.
              </p>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
