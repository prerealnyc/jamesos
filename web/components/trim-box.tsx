"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, Spinner } from "@/components/ui";

/** Inline trim control for a finished render: set start/end over the video's
 *  duration and apply. The backend re-hosts the trimmed clip and repoints the
 *  production (+ its queued item) at it. `url` is the current final_url; `onDone`
 *  should reload so the new (cache-busting) URL is picked up. */
export function TrimBox({
  id, url, onDone,
}: { id: string; url: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [dur, setDur] = useState(0);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open || !url) return;
    const v = document.createElement("video");
    v.preload = "metadata";
    v.src = url;
    v.onloadedmetadata = () => {
      const d = Number.isFinite(v.duration) ? v.duration : 0;
      setDur(d);
      setStart(0);
      setEnd(d);
    };
  }, [open, url]);

  async function apply() {
    if (end - start < 0.5) { setErr("Keep at least 0.5s."); return; }
    setBusy(true);
    setErr("");
    try {
      await api.trimProduction(id, { start_s: start, end_s: end });
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "trim failed");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Cut excess footage off the start/end of this video"
        className="text-[12px] px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors"
      >
        ✂ Trim
      </button>
    );
  }

  return (
    <div className="w-full border border-border rounded-md p-2.5 flex flex-col gap-2">
      <div className="text-[12px] font-medium flex items-center justify-between">
        <span>Trim output</span>
        <button type="button" onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">×</button>
      </div>
      {dur > 0 ? (
        <>
          <label className="text-[11px] text-muted-foreground">Start — {start.toFixed(1)}s</label>
          <input
            type="range" min={0} max={dur} step={0.1} value={start}
            onChange={(e) => setStart(Math.min(Number(e.target.value), end - 0.5))}
            className="w-full"
          />
          <label className="text-[11px] text-muted-foreground">End — {end.toFixed(1)}s</label>
          <input
            type="range" min={0} max={dur} step={0.1} value={end}
            onChange={(e) => setEnd(Math.max(Number(e.target.value), start + 0.5))}
            className="w-full"
          />
          <div className="text-[11px] text-muted-foreground">
            Keeps {(end - start).toFixed(1)}s of {dur.toFixed(1)}s
          </div>
          <div className="flex gap-2">
            <Button onClick={apply} disabled={busy} className="text-[12px] !px-3 !py-1">
              {busy ? <Spinner /> : "Apply trim"}
            </Button>
            <Button variant="secondary" onClick={() => setOpen(false)} className="text-[12px] !px-3 !py-1">
              Cancel
            </Button>
          </div>
        </>
      ) : (
        <div className="text-[11px] text-muted-foreground flex items-center gap-2"><Spinner /> loading video…</div>
      )}
      {err && <p className="text-[11px] text-destructive">{err}</p>}
    </div>
  );
}
