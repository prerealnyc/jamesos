"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Caption-style picker.
 *
 * Renders a chip strip:
 *   [ AI pick ✨ ] [ tiktok yellow ] [ clean white ] [ bold pop ] …
 *
 * The "AI pick" chip is locally synthesized — it doesn't come from the
 * backend list. Selecting it returns the empty string to the caller,
 * which the produce endpoint forwards to pick_caption_style() in the
 * pipeline. Selecting any other chip pins that preset by name.
 *
 * Each chip carries a tiny color swatch in the preset's signature
 * fill/stroke so the styles are recognisable at a glance without
 * rendering live previews (which would need WebFont loading + canvas).
 */

const SWATCH: Record<string, { fg: string; stroke: string; bg?: string }> = {
  tiktok_yellow: { fg: "#FFE600", stroke: "#000000" },
  clean_white:   { fg: "#FFFFFF", stroke: "transparent", bg: "#222222" },
  bold_pop:      { fg: "#FFFFFF", stroke: "#000000" },
  subtle_minimal:{ fg: "#F5F5F5", stroke: "transparent", bg: "#0a0a0a" },
  branded_red:   { fg: "#FFFFFF", stroke: "#C8102E" },
};

export function CaptionPicker({
  value,
  onChange,
}: {
  value: string;                          // "" → AI pick
  onChange: (next: string) => void;
}) {
  const [presets, setPresets] = useState<
    { name: string; label: string; description: string }[]
  >([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .listCaptionStyles()
      .then((r) => setPresets(r.presets || []))
      .catch((e) =>
        setErr(e instanceof Error ? e.message : "could not load caption styles"),
      );
  }, []);

  function Swatch({ name }: { name: string }) {
    const s = SWATCH[name];
    if (!s) return null;
    return (
      <span
        className="inline-block w-3 h-3 rounded-sm align-middle mr-1.5"
        style={{
          background: s.bg || "transparent",
          border: `1.5px solid ${s.stroke === "transparent" ? s.fg : s.stroke}`,
          boxShadow: `inset 0 0 0 1px ${s.fg}`,
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onChange("")}
          className={`text-[12px] px-3 py-1.5 rounded-full border transition-colors ${
            value === ""
              ? "border-primary text-primary bg-primary/10"
              : "border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
          }`}
          title="Let the AI pick a preset based on the script's energy + platform"
        >
          AI pick ✨
        </button>
        {presets.map((p) => (
          <button
            key={p.name}
            type="button"
            onClick={() => onChange(p.name)}
            className={`text-[12px] px-3 py-1.5 rounded-full border transition-colors ${
              value === p.name
                ? "border-primary text-primary bg-primary/10"
                : "border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
            }`}
            title={p.description}
          >
            <Swatch name={p.name} />
            {p.label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {err && <span className="text-destructive">✗ {err}</span>}
        {!err && value === "" && "AI picks based on script energy + platform."}
        {!err && value !== "" && presets.find((p) => p.name === value)?.description}
      </p>
    </div>
  );
}
