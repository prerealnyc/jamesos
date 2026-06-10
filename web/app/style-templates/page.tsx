"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  mediaUrl,
  type MediaAsset,
  type Production,
  type StyleTemplate,
  type TemplateSegment,
} from "@/lib/api";
import { PageHeader, Card, Button, Badge, Spinner } from "@/components/ui";
import { MediaTabs } from "@/components/media-tabs";

export default function StyleTemplatesPage() {
  const [templates, setTemplates] = useState<StyleTemplate[]>([]);
  const [refs, setRefs] = useState<MediaAsset[]>([]);
  const [comps, setComps] = useState<
    { layout_type: string; count: number; example: string; description: string; supported: boolean; status: string }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, m, c] = await Promise.all([
        api.listTemplates(),
        api.listMedia("style_reference"),
        api.listCompositions(),
      ]);
      setTemplates(t.templates);
      setRefs(m.media);
      setComps(c.compositions);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Reference assets that already have a template (so we can show what's
  // still awaiting inspection).
  const templatedRefIds = useMemo(
    () => new Set(templates.map((t) => t.reference_media_id).filter(Boolean)),
    [templates],
  );
  const refById = useMemo(() => {
    const m = new Map<string, MediaAsset>();
    refs.forEach((r) => m.set(r.id, r));
    return m;
  }, [refs]);

  // Uploaded references with no template yet (URL refs can't be inspected).
  const uninspected = useMemo(
    () =>
      refs.filter(
        (r) => r.source_type === "upload" && !templatedRefIds.has(r.id),
      ),
    [refs, templatedRefIds],
  );

  // Poll while anything is mid-inspection (media pending, or template pending).
  const anyPending =
    refs.some((r) => r.analysis_status === "pending") ||
    templates.some((t) => t.status === "pending");
  useEffect(() => {
    if (!anyPending) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [anyPending, load]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Style Templates"
        sub="Reference videos, reverse-engineered into reusable production templates — your library of trending video styles. Upload a video under Media Library → Style references and the Design Inspector watches the whole clip, then names and stores the style here."
      />
      <MediaTabs />

      <div className="rounded-md border border-border bg-secondary/40 px-4 py-3 text-[12px] text-muted-foreground">
        Each template captures <b>where every element sits</b> (speaker,
        captions, on-screen text, logo), the pacing, the transitions, and the
        sound.{" "}
        <span className="text-foreground">
          Hit <b>Replicate this style</b> on any template to produce a new brand
          video in that style — give it a topic (we write the script in your
          voice) or paste your own.
        </span>
      </div>

      {comps.some((c) => c.status === "queued" || c.status === "unverified") && (
        <Card>
          <div className="text-[15px] font-semibold">Composition build queue</div>
          <p className="text-[12px] text-muted-foreground mt-1 mb-2">
            Layouts the inspector found in your style library. <b className="text-accent">live</b> = we
            render it today (split-screen is live now); <b className="text-primary">queued</b> = a
            composition to build — it renders in the closest mode meanwhile and goes live here once
            built; <b className="text-muted-foreground">unverified</b> = captured before layout
            analysis — re-inspect it to confirm its composition before replicating.
          </p>
          <div className="flex flex-col gap-1.5">
            {comps.map((c) => (
              <div
                key={c.layout_type}
                className="flex items-center justify-between gap-3 border border-border rounded-md px-3 py-2 bg-background text-[12px]"
              >
                <div className="min-w-0">
                  <span className="font-semibold">{c.layout_type.replace(/_/g, " ")}</span>
                  <span className="text-muted-foreground">
                    {" · "}{c.count} template{c.count === 1 ? "" : "s"}
                    {c.example ? ` · e.g. ${c.example}` : ""}
                  </span>
                  {c.description && (
                    <div className="text-[11px] text-muted-foreground truncate">{c.description}</div>
                  )}
                </div>
                <Badge
                  tone={
                    c.status === "live" ? "ok"
                      : c.status === "unverified" ? "muted"
                        : "primary"
                  }
                >
                  {c.status === "live" ? "live"
                    : c.status === "unverified" ? "re-inspect to verify"
                      : "queued to build"}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {err && <div className="text-sm text-destructive">{err}</div>}

      {loading ? (
        <div className="text-muted-foreground text-sm flex items-center gap-2">
          <Spinner /> loading…
        </div>
      ) : (
        <>
          <UninspectedSection items={uninspected} onChange={load} />

          {templates.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              No style templates yet. Upload a reference video in the{" "}
              <b>Media Library → Style references</b> tab — it&apos;s inspected
              automatically and the extracted style appears here.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              {templates.map((t) => (
                <TemplateCard
                  key={t.id}
                  t={t}
                  ref_={t.reference_media_id ? refById.get(t.reference_media_id) : undefined}
                  onChange={load}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function UninspectedSection({
  items,
  onChange,
}: {
  items: MediaAsset[];
  onChange: () => void;
}) {
  if (items.length === 0) return null;
  return (
    <Card>
      <div className="text-[13px] font-semibold mb-2">
        Reference videos awaiting a template
      </div>
      <div className="flex flex-col gap-2">
        {items.map((m) => (
          <UninspectedRow key={m.id} m={m} onChange={onChange} />
        ))}
      </div>
    </Card>
  );
}

function UninspectedRow({ m, onChange }: { m: MediaAsset; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const pending = m.analysis_status === "pending" || busy;
  async function inspect() {
    setBusy(true);
    try {
      await api.inspectTemplate(m.id);
      onChange();
    } catch {
      /* status reflects it on reload */
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex items-center justify-between gap-3 border border-border rounded-md px-3 py-2 bg-background">
      <span className="text-[13px] truncate">{m.title || "untitled"}</span>
      <div className="flex items-center gap-3">
        {m.analysis_status === "failed" && (
          <Badge tone="destructive">inspection failed</Badge>
        )}
        <Button variant="secondary" onClick={inspect} disabled={pending}>
          {pending ? (
            <span className="flex items-center gap-2">
              <Spinner /> inspecting…
            </span>
          ) : (
            "Inspect → build template"
          )}
        </Button>
      </div>
    </div>
  );
}

const STATUS_TONE: Record<string, "ok" | "primary" | "destructive" | "muted"> = {
  ready: "ok",
  pending: "primary",
  failed: "destructive",
};

function TemplateCard({
  t,
  ref_,
  onChange,
}: {
  t: StyleTemplate;
  ref_?: MediaAsset;
  onChange: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [rep, setRep] = useState(false);
  const tpl = t.template || {};
  const caps = tpl.captions || {};
  const logo = tpl.logo || {};
  const music = tpl.audio?.music || {};
  const pacing = tpl.pacing || {};

  async function rename() {
    const name = window.prompt("Rename this style template:", t.name);
    if (!name || name.trim() === t.name) return;
    await api.renameTemplate(t.id, { name: name.trim() });
    onChange();
  }
  async function del() {
    if (!confirm(`Delete the “${t.name}” template? The reference video stays.`)) return;
    await api.deleteTemplate(t.id);
    onChange();
  }

  return (
    <div className="border border-border rounded-lg bg-card overflow-hidden">
      <div className="flex flex-col md:flex-row">
        {/* Reference preview */}
        <div className="md:w-64 shrink-0 bg-background aspect-video md:aspect-auto flex items-center justify-center border-b md:border-b-0 md:border-r border-border">
          {ref_ && ref_.source_type === "upload" ? (
            <video
              src={mediaUrl(ref_.uri)}
              controls
              className="w-full h-full object-contain"
            />
          ) : (
            <span className="text-[11px] text-muted-foreground px-3 text-center">
              reference video removed
            </span>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0 p-4 flex flex-col gap-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-[15px] font-semibold truncate">{t.name}</h3>
                <Badge tone={STATUS_TONE[t.status] || "muted"}>{t.status}</Badge>
              </div>
              {t.summary && (
                <p className="text-[13px] text-muted-foreground mt-1">{t.summary}</p>
              )}
            </div>
            <div className="flex items-center gap-3 text-[11px] shrink-0">
              <button onClick={rename} className="text-muted-foreground hover:text-foreground">
                rename
              </button>
              <button onClick={del} className="text-muted-foreground hover:text-destructive">
                delete
              </button>
            </div>
          </div>

          {/* Quick facts */}
          <div className="flex flex-wrap gap-1.5">
            {t.format_type && <Pill k="Format" v={t.format_type} />}
            {tpl.aspect_ratio && <Pill k="Aspect" v={tpl.aspect_ratio} />}
            {t.duration > 0 && <Pill k="Length" v={`${t.duration}s`} />}
            {pacing.energy && <Pill k="Energy" v={pacing.energy} />}
            {music.present && <Pill k="Music" v={music.type || "yes"} />}
            {caps.present && <Pill k="Captions" v={caps.position || "yes"} />}
            {logo.present && <Pill k="Logo" v={logo.position || "yes"} />}
            {t.production_mode && <Pill k="Best made with" v={t.production_mode} tone="primary" />}
          </div>

          {/* What makes this style distinctive — the point of the inspector */}
          {(Boolean(tpl.distinctive_features?.length) ||
            Boolean(tpl.layout?.type && tpl.layout.type !== "full_frame")) && (
            <div className="rounded-md border border-accent/30 bg-accent/5 p-2.5 text-[12px]">
              {Boolean(tpl.layout?.type && tpl.layout!.type !== "full_frame") && (
                <div className="mb-1.5 flex items-start gap-2">
                  <Badge tone="accent">{tpl.layout!.type!.replace(/_/g, " ")}</Badge>
                  {tpl.layout!.description && (
                    <span className="text-muted-foreground">{tpl.layout!.description}</span>
                  )}
                </div>
              )}
              {tpl.distinctive_features && tpl.distinctive_features.length > 0 && (
                <>
                  <div className="uppercase tracking-[.4px] text-muted-foreground text-[10px] mb-1">
                    What makes it distinctive
                  </div>
                  <ul className="list-disc pl-4 flex flex-col gap-0.5">
                    {tpl.distinctive_features.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}

          {/* Replicate this style — Phase 2 */}
          {t.status === "ready" && (
            <div>
              <Button onClick={() => setRep((r) => !r)}>
                {rep ? "Close" : "⚡ Replicate this style"}
              </Button>
              {rep && (
                <div className="mt-2">
                  <ReplicatePanel t={t} />
                </div>
              )}
            </div>
          )}

          {/* Replication recipe */}
          {tpl.replication_recipe && tpl.replication_recipe.length > 0 && (
            <div className="rounded-md bg-background border border-border p-3 text-[12px]">
              <div className="uppercase tracking-[.4px] text-muted-foreground text-[10px] mb-1.5">
                How to reproduce this style
              </div>
              <ol className="list-decimal pl-4 flex flex-col gap-1">
                {tpl.replication_recipe.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            </div>
          )}

          <button
            onClick={() => setOpen((o) => !o)}
            className="text-[12px] text-primary self-start hover:underline"
          >
            {open ? "Hide breakdown ▲" : "View element breakdown ▼"}
          </button>

          {open && <Breakdown t={t} />}
        </div>
      </div>
    </div>
  );
}

function Pill({ k, v, tone }: { k: string; v: string; tone?: "primary" }) {
  return (
    <span
      className={`text-[11px] rounded px-2 py-0.5 border ${
        tone === "primary"
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border bg-background text-muted-foreground"
      }`}
    >
      <span className="opacity-60">{k}:</span> {v}
    </span>
  );
}

type ReplicateResult = {
  production: Production;
  applied: Record<string, string | number>;
  approximations: string[];
  script_source?: string;
};

function ReplicatePanel({ t }: { t: StyleTemplate }) {
  const tpl = t.template || {};
  const [output, setOutput] = useState<"avatar" | "broll">("avatar");
  const [contentMode, setContentMode] = useState<"topic" | "script">("topic");
  const [text, setText] = useState("");
  const [platform, setPlatform] = useState("instagram");
  // Ditto replication: default to the reference's MEASURED aspect (the inspector
  // probes real pixels/DAR now, not a guess). The user can still override to
  // recut to another shape; a hint shows when their pick differs from the source.
  const [aspect, setAspect] = useState(tpl.aspect_ratio || "9:16");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReplicateResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const willApply = {
    mode: t.production_mode || "engaging_avatar",
    captions: tpl.captions?.preset_guess || "auto",
    music: tpl.audio?.music?.type || "none",
    logo: tpl.logo?.present ? tpl.logo?.position || "yes" : "none",
  };

  async function go() {
    if (!text.trim()) {
      setErr("Add a topic or paste a script.");
      return;
    }
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const body = contentMode === "topic" ? { topic: text.trim() } : { script: text.trim() };
      const r =
        output === "broll"
          ? await api.brollReel(t.id, { ...body, platform, seconds: 20, engine: "higgsfield" })
          : await api.replicateTemplate(t.id, { ...body, platform, aspect });
      setResult(r as ReplicateResult);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-3 flex flex-col gap-3 text-[12px]">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-muted-foreground">This style will set:</span>
        <Pill k="mode" v={willApply.mode} tone="primary" />
        <Pill k="captions" v={willApply.captions} />
        <Pill k="music" v={willApply.music} />
        <Pill k="logo" v={willApply.logo} />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <span className="text-muted-foreground">Output:</span>
        <button
          onClick={() => setOutput("avatar")}
          className={output === "avatar" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          Avatar replica
        </button>
        <span className="text-muted-foreground">·</span>
        <button
          onClick={() => setOutput("broll")}
          className={output === "broll" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          B-roll reel · Higgsfield (20s)
        </button>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => setContentMode("topic")}
          className={contentMode === "topic" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          Topic → auto-script
        </button>
        <span className="text-muted-foreground">·</span>
        <button
          onClick={() => setContentMode("script")}
          className={contentMode === "script" ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}
        >
          Paste a script
        </button>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={contentMode === "topic" ? 2 : 5}
        placeholder={
          contentMode === "topic"
            ? "What's the video about? e.g. 'why Staten Island waterfront is undervalued right now'"
            : "Paste the full script — exactly what James says…"
        }
        className="w-full bg-background border border-input rounded-md px-2 py-1.5 resize-y outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          className="bg-background border border-input rounded-md px-2 py-1"
        >
          {["instagram", "tiktok", "youtube", "linkedin"].map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <select
          value={aspect}
          onChange={(e) => setAspect(e.target.value)}
          className="bg-background border border-input rounded-md px-2 py-1"
        >
          {["9:16", "1:1", "16:9"].map((a) => (
            <option key={a} value={a}>{a}{a === "9:16" ? " (vertical)" : ""}</option>
          ))}
        </select>
        <Button onClick={go} disabled={busy}>
          {busy ? (
            <span className="flex items-center gap-2"><Spinner /> producing…</span>
          ) : (
            "Produce video"
          )}
        </Button>
      </div>
      {tpl.aspect_ratio && tpl.aspect_ratio !== aspect && (
        <p className="text-[11px] text-muted-foreground">
          Reference was filmed {tpl.aspect_ratio}; your post will be {aspect}. The
          style (layout, captions, pacing) is reproduced regardless of shape.
        </p>
      )}

      {err && <div className="text-destructive">{err}</div>}

      {result && (
        <div className="rounded-md bg-background border border-border p-2.5 flex flex-col gap-1.5">
          <div className="text-accent font-semibold">
            ✓ Producing “{result.production.title}”{result.script_source ? ` (${result.script_source} script)` : ""}
          </div>
          {result.approximations.length > 0 && (
            <div className="text-[11px] text-muted-foreground">
              <b className="text-foreground">What&apos;s approximated:</b>
              <ul className="list-disc pl-4 mt-0.5">
                {result.approximations.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}
          <Link href="/library" className="text-primary hover:underline">
            Watch it render in Output Library →
          </Link>
        </div>
      )}
    </div>
  );
}

function Breakdown({ t }: { t: StyleTemplate }) {
  const tpl = t.template || {};
  const caps = tpl.captions || {};
  const logo = tpl.logo || {};
  const audio = tpl.audio || {};
  const music = audio.music || {};
  const pacing = tpl.pacing || {};
  const segs = tpl.segments || [];

  return (
    <div className="flex flex-col gap-3 text-[12px]">
      {tpl.hook && (
        <Row label="Hook">{tpl.hook}</Row>
      )}
      {(pacing.notes || pacing.avg_cut_seconds != null) && (
        <Row label="Pacing">
          {pacing.notes}
          {pacing.avg_cut_seconds != null && (
            <span className="text-muted-foreground"> · ~{pacing.avg_cut_seconds}s cuts</span>
          )}
        </Row>
      )}
      {caps.present && (
        <Row label="Captions">
          {[caps.position, caps.animation, caps.preset_guess && `preset: ${caps.preset_guess}`]
            .filter(Boolean)
            .join(" · ")}
          {caps.look && <div className="text-muted-foreground">{caps.look}</div>}
        </Row>
      )}
      {logo.present && (
        <Row label="Logo">
          {[logo.position, logo.persistence].filter(Boolean).join(" · ")}
        </Row>
      )}
      <Row label="Sound">
        {music.present
          ? `Music: ${music.type || ""}${music.mood ? ` (${music.mood})` : ""}`
          : "No music"}
        {audio.voiceover ? " · voiceover" : ""}
        {audio.sfx && audio.sfx !== "none" ? ` · sfx: ${audio.sfx}` : ""}
        {audio.sound_signature && (
          <div className="text-muted-foreground">{audio.sound_signature}</div>
        )}
      </Row>
      {(tpl.vibe || tpl.color_palette) && (
        <Row label="Look">
          {[tpl.vibe, tpl.color_palette].filter(Boolean).join(" · ")}
        </Row>
      )}

      {segs.length > 0 && (
        <div>
          <div className="uppercase tracking-[.4px] text-muted-foreground text-[10px] mb-1.5">
            Timeline — what goes where, over time
          </div>
          <div className="flex flex-col gap-1.5">
            {segs.map((s, i) => (
              <SegmentRow key={i} s={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2">
      <div className="text-muted-foreground">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function fmtTime(s?: number): string {
  if (s == null || isNaN(s)) return "";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function SegmentRow({ s }: { s: TemplateSegment }) {
  const sp = s.speaker || {};
  const ost = s.on_screen_text || {};
  const lg = s.logo || {};
  const meta = [
    sp.present && `speaker ${sp.position || ""} ${sp.framing ? `(${sp.framing})` : ""}`.trim(),
    ost.present && `text ${ost.position || ""}`.trim(),
    lg.present && `logo ${lg.position || ""}`.trim(),
    s.transition_out && s.transition_out !== "none" && `${s.transition_out} out`,
  ].filter(Boolean);
  return (
    <div className="flex gap-3 border border-border rounded-md px-2.5 py-1.5 bg-background">
      <span className="text-[11px] text-muted-foreground tabular-nums shrink-0 w-[78px]">
        {fmtTime(s.start)}–{fmtTime(s.end)}
      </span>
      <span className="shrink-0">
        <Badge tone="muted">{s.role || "segment"}</Badge>
      </span>
      <span className="min-w-0 flex-1">
        {s.visual && <div className="truncate">{s.visual}</div>}
        {meta.length > 0 && (
          <div className="text-[11px] text-muted-foreground">{meta.join(" · ")}</div>
        )}
        {ost.present && ost.example && (
          <div className="text-[11px] text-primary truncate">“{ost.example}”</div>
        )}
      </span>
    </div>
  );
}
