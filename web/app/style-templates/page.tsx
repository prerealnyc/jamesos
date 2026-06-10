"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  mediaUrl,
  type MediaAsset,
  type StyleTemplate,
  type TemplateSegment,
} from "@/lib/api";
import { PageHeader, Card, Button, Badge, Spinner } from "@/components/ui";
import { MediaTabs } from "@/components/media-tabs";

export default function StyleTemplatesPage() {
  const [templates, setTemplates] = useState<StyleTemplate[]>([]);
  const [refs, setRefs] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, m] = await Promise.all([
        api.listTemplates(),
        api.listMedia("style_reference"),
      ]);
      setTemplates(t.templates);
      setRefs(m.media);
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
        sound — in the video engine&apos;s own vocabulary.{" "}
        <span className="text-foreground">
          Replication — turning a template + a new script into a matching video
          — is the next phase.
        </span>
      </div>

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
