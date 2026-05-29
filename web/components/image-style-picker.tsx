"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Image-style picker — what the AI-generated B-roll stills look like.
 *
 * "Story default" (returns "") = let the pipeline pick: cinematic for
 * story modes, photoreal elsewhere. The other chips pin a specific
 * style by name (cinematic / photoreal / editorial / minimal / bw_photo).
 *
 * Renders side-by-side with the caption picker on /story-video and
 * /story-mix.
 */

const SWATCH: Record<string, { hue: string; bg: string }> = {
  cinematic: { hue: "linear-gradient(135deg,#1a2a3e 0%,#0d141d 60%,#7a3a1a 100%)", bg: "#0a0a14" },
  photoreal: { hue: "linear-gradient(135deg,#a09480 0%,#5b4a36 100%)", bg: "#221b14" },
  editorial: { hue: "linear-gradient(135deg,#e8a35b 0%,#c5763a 100%)", bg: "#f6efe5" },
  minimal:   { hue: "linear-gradient(135deg,#111 50%,#fff 50%)", bg: "#cccccc" },
  bw_photo:  { hue: "linear-gradient(135deg,#888 0%,#222 100%)", bg: "#444" },
};

export function ImageStylePicker({
  value,
  onChange,
}: {
  value: string;                          // "" → story default
  onChange: (next: string) => void;
}) {
  const [presets, setPresets] = useState<
    { name: string; label: string; description: string }[]
  >([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .listImageStyles()
      .then((r) => setPresets(r.presets || []))
      .catch((e) =>
        setErr(e instanceof Error ? e.message : "could not load image styles"),
      );
  }, []);

  function Swatch({ name }: { name: string }) {
    const s = SWATCH[name];
    if (!s) return null;
    return (
      <span
        className="inline-block w-3 h-3 rounded-sm align-middle mr-1.5 border border-border"
        style={{ background: s.hue || s.bg }}
      />
    );
  }

  // Put cinematic first since it's now the story default.
  const ordered = [...presets].sort((a, b) =>
    a.name === "cinematic" ? -1 : b.name === "cinematic" ? 1 : 0,
  );

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
          title="Use the recommended style for this mode (cinematic for story modes)"
        >
          Story default ✨
        </button>
        {ordered.map((p) => (
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
        {!err && value === "" && "Cinematic (film-still drama) for story modes."}
        {!err && value !== "" && presets.find((p) => p.name === value)?.description}
      </p>
    </div>
  );
}
