"use client";

/**
 * The reel-template builder.
 *
 * Until now a style template could only be born from an uploaded reference
 * video. This is the other door: state the format — layout, captions, music,
 * beats, hook — and save it as a reusable template. It is also the EDIT door,
 * so a style the Design Inspector reverse-engineered from a reference reel can
 * be opened here, adjusted, and saved rather than staying a locked artifact.
 *
 * Two things keep it honest:
 *   - every choice comes from `api.templateCapabilities()`, which the backend
 *     derives from what the renderer can actually produce, so the form cannot
 *     offer an unrenderable layout, caption preset or music bed;
 *   - the live preview panel shows the ACTUAL render params and every
 *     approximation before anything is saved, instead of after a production.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type TemplateBeat,
  type TemplateBuilderSpec,
  type TemplateCapabilities,
  type TemplatePreview,
} from "@/lib/api";
import { Button, Badge, Input, Label, Select, Spinner, Textarea } from "@/components/ui";

const EMPTY: TemplateBuilderSpec = {
  name: "",
  summary: "",
  layout: "full_frame",
  production_mode: "engaging_avatar",
  aspect: "9:16",
  format_type: "talking_head",
  caption_preset: "",
  music: "",
  logo: { present: false, position: "bottom-right" },
  hook: "",
  energy: "medium",
  beats: [],
  distinctive_features: [],
  replication_recipe: [],
};

const linesToList = (s: string) =>
  s.split("\n").map((l) => l.trim()).filter(Boolean);

export function TemplateBuilder({
  caps,
  initial,
  templateId,
  initialScope,
  onSaved,
  onCancel,
}: {
  caps: TemplateCapabilities;
  initial?: TemplateBuilderSpec;
  /** set = editing an existing template; unset = creating a new one */
  templateId?: string;
  /** editing a house template keeps it in the house library */
  initialScope?: "brand" | "platform";
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [spec, setSpec] = useState<TemplateBuilderSpec>({ ...EMPTY, ...(initial || {}) });
  const [scope, setScope] = useState<"brand" | "platform">(initialScope || "brand");
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Free-text list fields are edited as textareas; keep the raw text separate so
  // typing a blank line doesn't drop what's below it.
  const [featuresText, setFeaturesText] = useState(
    (initial?.distinctive_features || []).join("\n"),
  );
  const [recipeText, setRecipeText] = useState(
    (initial?.replication_recipe || []).join("\n"),
  );

  const set = useCallback(
    <K extends keyof TemplateBuilderSpec>(k: K, v: TemplateBuilderSpec[K]) =>
      setSpec((s) => ({ ...s, [k]: v })),
    [],
  );

  // A split layout IMPLIES its renderer — the backend enforces it, so reflect
  // that here rather than letting the author pick a mode that will be ignored.
  const layoutSetsMode = spec.layout.startsWith("split_");
  // Cards are placed from the transcript, so they only mean anything in a mode
  // where somebody is speaking on camera.
  const effectiveMode = layoutSetsMode ? spec.layout : (spec.production_mode || "");
  const cardsPossible = caps.cards_supported_in.includes(effectiveMode);
  const beatsMatter = useMemo(
    () => caps.beats_drive_render_in.includes(spec.production_mode || ""),
    [caps.beats_drive_render_in, spec.production_mode],
  );

  const fullSpec = useMemo<TemplateBuilderSpec>(
    () => ({
      ...spec,
      distinctive_features: linesToList(featuresText),
      replication_recipe: linesToList(recipeText),
    }),
    [spec, featuresText, recipeText],
  );

  // Live "what will this actually render as" — debounced so typing a name
  // doesn't fire a request per keystroke.
  useEffect(() => {
    if (!fullSpec.name.trim()) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewing(true);
    const id = setTimeout(async () => {
      try {
        const p = await api.previewTemplateSpec(fullSpec);
        if (!cancelled) setPreview(p);
      } catch (e) {
        if (!cancelled) setPreview({ valid: false, errors: [e instanceof Error ? e.message : "preview failed"] });
      } finally {
        if (!cancelled) setPreviewing(false);
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(id);
      setPreviewing(false);
    };
  }, [fullSpec]);

  function setBeat(i: number, patch: Partial<TemplateBeat>) {
    setSpec((s) => {
      const beats = [...(s.beats || [])];
      beats[i] = { ...beats[i], ...patch };
      return { ...s, beats };
    });
  }
  function addBeat() {
    setSpec((s) => ({
      ...s,
      beats: [...(s.beats || []), { role: "b_roll", seconds: 4, visual: "" }],
    }));
  }
  function removeBeat(i: number) {
    setSpec((s) => ({ ...s, beats: (s.beats || []).filter((_, j) => j !== i) }));
  }

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      if (templateId) await api.putTemplateSpec(templateId, fullSpec);
      else await api.createTemplate({ spec: fullSpec, scope });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  const beatCount = (spec.beats || []).length;
  const atBeatCap = beatCount >= caps.limits.max_beats;
  const canSave = !!fullSpec.name.trim() && preview?.valid !== false && !saving;

  return (
    <div className="flex flex-col gap-4">
      {/* ── identity ── */}
      <div className="grid md:grid-cols-2 gap-3">
        <div>
          <Label>Name</Label>
          <Input
            value={spec.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="e.g. Split 50/50 — speaker over proof"
          />
        </div>
        <div>
          <Label>One-line summary</Label>
          <Input
            value={spec.summary || ""}
            onChange={(e) => set("summary", e.target.value)}
            placeholder="What makes this format work"
          />
        </div>
      </div>

      {/* ── the shape ── */}
      <div className="grid md:grid-cols-3 gap-3">
        <div>
          <Label>Layout</Label>
          <Select value={spec.layout} onChange={(e) => set("layout", e.target.value)}>
            {caps.layouts.map((l) => (
              <option key={l.value} value={l.value}>{l.label}</option>
            ))}
          </Select>
        </div>
        <div>
          <Label>How it&apos;s produced</Label>
          <Select
            value={layoutSetsMode ? spec.layout : spec.production_mode || ""}
            onChange={(e) => set("production_mode", e.target.value)}
            disabled={layoutSetsMode}
          >
            {layoutSetsMode ? (
              <option value={spec.layout}>Set by the layout</option>
            ) : (
              caps.modes.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))
            )}
          </Select>
          {layoutSetsMode && (
            <p className="text-[11px] text-muted-foreground mt-1">
              A split layout has exactly one renderer, so it&apos;s set for you —
              speaker {spec.layout === "split_horizontal" ? "top" : "left"}, B-roll
              in the other half.
            </p>
          )}
        </div>
        <div>
          <Label>Aspect</Label>
          <Select value={spec.aspect || "9:16"} onChange={(e) => set("aspect", e.target.value)}>
            {caps.aspects.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </Select>
        </div>
      </div>

      {/* ── look & sound ── */}
      <div className="grid md:grid-cols-3 gap-3">
        <div>
          <Label>Captions</Label>
          <Select
            value={spec.caption_preset || ""}
            onChange={(e) => set("caption_preset", e.target.value)}
          >
            {caps.caption_presets.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </Select>
          {(() => {
            const d = caps.caption_presets.find((c) => c.value === spec.caption_preset)?.description;
            return d ? <p className="text-[11px] text-muted-foreground mt-1">{d}</p> : null;
          })()}
        </div>
        <div>
          <Label>Music bed</Label>
          <Select value={spec.music || ""} onChange={(e) => set("music", e.target.value)}>
            {caps.music_moods.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </Select>
        </div>
        <div>
          <Label>Energy</Label>
          <Select value={spec.energy || "medium"} onChange={(e) => set("energy", e.target.value)}>
            {caps.energies.map((en) => (
              <option key={en} value={en}>{en}</option>
            ))}
          </Select>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-3 items-end">
        <div>
          <Label>Format</Label>
          <Select
            value={spec.format_type || "talking_head"}
            onChange={(e) => set("format_type", e.target.value)}
          >
            {caps.format_types.map((f) => (
              <option key={f} value={f}>{f.replace(/_/g, " ")}</option>
            ))}
          </Select>
        </div>
        <label className="flex items-center gap-2 text-[13px] pb-2">
          <input
            type="checkbox"
            checked={!!spec.logo?.present}
            onChange={(e) =>
              set("logo", { present: e.target.checked, position: spec.logo?.position || "bottom-right" })
            }
          />
          Watermark the logo
        </label>
        {spec.logo?.present && (
          <div>
            <Label>Logo corner</Label>
            <Select
              value={spec.logo?.position || "bottom-right"}
              onChange={(e) => set("logo", { present: true, position: e.target.value })}
            >
              {caps.logo_positions.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </Select>
          </div>
        )}
      </div>

      {/* ── the hook ── */}
      <div>
        <Label>The hook — how the first two seconds open</Label>
        <Textarea
          rows={2}
          value={spec.hook || ""}
          onChange={(e) => set("hook", e.target.value)}
          placeholder="e.g. Lead with the claim, not the greeting — the first line is the whole hook."
        />
      </div>

      {/* ── designed cards ── */}
      <div className="rounded-md border border-border bg-secondary/30 px-3 py-3">
        <label className="flex items-center gap-2 text-[13px] font-semibold">
          <input
            type="checkbox"
            checked={!!spec.cards?.enabled}
            disabled={!cardsPossible}
            onChange={(e) =>
              set("cards", { enabled: e.target.checked, styles: spec.cards?.styles || [] })
            }
          />
          Cut away to designed cards
        </label>
        <p className="text-[11px] text-muted-foreground mt-1">
          {cardsPossible ? (
            <>
              The reel is transcribed, and the moments worth a graphic — a
              number, a named thing, a hard claim — become full-frame cards that
              animate in. Each card shows the speaker&apos;s <b>own words</b>,
              never invented copy.
            </>
          ) : (
            <>
              Cards are placed from what is spoken, so they need a mode with
              somebody on camera — not available in{" "}
              <b>{effectiveMode || "this mode"}</b>.
            </>
          )}
        </p>
        {cardsPossible && spec.cards?.enabled && (
          <div className="mt-2">
            <Label>Card styles to allow (none ticked = all)</Label>
            <div className="flex flex-wrap gap-2 mt-1">
              {caps.card_styles.map((st) => {
                const on = (spec.cards?.styles || []).includes(st);
                return (
                  <button
                    key={st}
                    onClick={() =>
                      set("cards", {
                        enabled: true,
                        styles: on
                          ? (spec.cards?.styles || []).filter((x) => x !== st)
                          : [...(spec.cards?.styles || []), st],
                      })
                    }
                    className={`text-[11px] rounded px-2 py-1 border ${
                      on
                        ? "border-primary/50 bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground"
                    }`}
                  >
                    {st}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── beats ── */}
      <div>
        <div className="flex items-center justify-between gap-3">
          <Label>Beats</Label>
          <span className="text-[11px] text-muted-foreground">
            {beatCount}/{caps.limits.max_beats}
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground mb-2">
          {beatsMatter ? (
            <>
              <b className="text-accent">These beats drive the cut</b> in this
              production mode — each one becomes a scene.
            </>
          ) : (
            <>
              Heads up: in this mode the renderer drives its own cut from the
              script, so beats are documentation of the format rather than the
              edit. They become real scenes in{" "}
              <b>{caps.beats_drive_render_in.join(", ")}</b>.
            </>
          )}
        </p>
        <div className="flex flex-col gap-2">
          {(spec.beats || []).map((b, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 border border-border rounded-md px-2 py-2 bg-background">
              <span className="text-[11px] text-muted-foreground w-4">{i + 1}</span>
              <Select
                value={b.role}
                onChange={(e) => setBeat(i, { role: e.target.value })}
              >
                {caps.beat_roles.map((r) => (
                  <option key={r} value={r}>{r.replace(/_/g, " ")}</option>
                ))}
              </Select>
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  min={caps.limits.beat_min_seconds}
                  max={caps.limits.beat_max_seconds}
                  value={b.seconds}
                  onChange={(e) => setBeat(i, { seconds: Number(e.target.value) })}
                  className="w-16"
                />
                <span className="text-[11px] text-muted-foreground">s</span>
              </div>
              <Input
                value={b.visual || ""}
                onChange={(e) => setBeat(i, { visual: e.target.value })}
                placeholder="what's on screen"
                className="flex-1 min-w-[140px]"
              />
              <button
                onClick={() => removeBeat(i)}
                className="text-[11px] text-muted-foreground hover:text-destructive px-1"
              >
                remove
              </button>
            </div>
          ))}
        </div>
        <Button variant="secondary" onClick={addBeat} disabled={atBeatCap} className="mt-2">
          {atBeatCap ? `Beat cap reached (${caps.limits.max_beats})` : "+ Add a beat"}
        </Button>
      </div>

      {/* ── the copyable part ── */}
      <div className="grid md:grid-cols-2 gap-3">
        <div>
          <Label>What defines this format (one per line)</Label>
          <Textarea
            rows={4}
            value={featuresText}
            onChange={(e) => setFeaturesText(e.target.value)}
            placeholder={"speaker pinned to the top half\ncaptions on the seam"}
          />
        </div>
        <div>
          <Label>How to reproduce it (one step per line)</Label>
          <Textarea
            rows={4}
            value={recipeText}
            onChange={(e) => setRecipeText(e.target.value)}
            placeholder={"Open on the strongest sentence.\nCut to B-roll only when a noun needs showing."}
          />
        </div>
      </div>

      {/* ── live preview ── */}
      <div className="rounded-md border border-border bg-secondary/40 px-3 py-3">
        <div className="flex items-center gap-2 text-[12px] font-semibold">
          What this will actually render as
          {previewing && <Spinner />}
        </div>
        {!fullSpec.name.trim() ? (
          <p className="text-[12px] text-muted-foreground mt-1">Name it to see the preview.</p>
        ) : preview?.valid === false ? (
          <ul className="mt-2 flex flex-col gap-1">
            {preview.errors.map((e, i) => (
              <li key={i} className="text-[12px] text-destructive">• {e}</li>
            ))}
          </ul>
        ) : preview?.applied ? (
          <>
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
              <Badge tone="primary">{preview.applied.mode}</Badge>
              <Badge tone="muted">captions: {preview.applied.caption_style}</Badge>
              <Badge tone="muted">music: {preview.applied.music_mood}</Badge>
              <Badge tone="muted">{preview.applied.aspect}</Badge>
              <Badge tone="muted">logo: {preview.applied.logo}</Badge>
              {preview.beats_drive_render && (
                <Badge tone="ok">{preview.applied.scenes} scenes from your beats</Badge>
              )}
            </div>
            {!!preview.approximations?.length && (
              <ul className="mt-2 flex flex-col gap-1">
                {preview.approximations.map((a, i) => (
                  <li key={i} className="text-[11px] text-muted-foreground">— {a}</li>
                ))}
              </ul>
            )}
          </>
        ) : null}
      </div>

      {err && <div className="text-[13px] text-destructive">{err}</div>}

      {/* ── save ── */}
      <div className="flex flex-wrap items-center gap-3">
        {!templateId && caps.can_curate_platform && (
          <div>
            <Label>Save to</Label>
            <Select value={scope} onChange={(e) => setScope(e.target.value as "brand" | "platform")}>
              <option value="brand">This brand only</option>
              <option value="platform">The house library — every brand</option>
            </Select>
          </div>
        )}
        <div className="flex items-center gap-2 pt-4">
          <Button onClick={save} disabled={!canSave}>
            {saving ? (
              <span className="flex items-center gap-2"><Spinner /> saving…</span>
            ) : templateId ? (
              "Save changes"
            ) : scope === "platform" ? (
              "Save to the house library"
            ) : (
              "Save template"
            )}
          </Button>
          <Button variant="secondary" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
