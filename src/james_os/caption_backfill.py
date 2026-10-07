"""Rebuild the captionless cut for a reel that was made before we kept one.

Migration 062 made moving a finished reel's captions cheap: the pipeline stashes
the cut the captions were drawn on, plus the flashes themselves, so a change is
one local libass pass. Every reel rendered before that has empty columns and the
editor can only say so — which is exactly the wall the owner hits, because it is
every reel they already have.

It turns out the stash is recoverable for `long_form_reel`, because everything
that went into the cut was persisted for other reasons:

  * `scenes[0].source_url` — the source video, on durable storage
  * `scenes[0].start_s` / `end_s` — the window that was cut from it
  * `long_sources.words` — the source transcript, with per-word timings

The one step that is NOT persisted is clip-tightening: the pipeline excises
internal silence from the window and never records which intervals it kept, so
the finished reel is shorter than its window and its timeline is shifted.
`compute_kept_intervals` is pure, though, so feeding it the SAME words over the
SAME window reproduces the same excisions.

Measured on Turtleback production 52039bd4 (window 0-15.45s, finished file
12.22s): the recomputed intervals total 12.14s — 0.08s from the real thing, two
frames at 24fps — and their boundaries match, to within a sampling interval, the
excision points found independently by frame-aligning the finished reel against
its source. Two different methods, same answer.

Because it IS a reconstruction, every rebuild is checked against the finished
file before it is trusted: if the duration does not match, nothing is stored and
the reel keeps saying it needs a full re-render. A cut that is silently a second
out would put every caption on the wrong words.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from typing import Any
from uuid import UUID

from .config import settings
from .db import acquire

_log = logging.getLogger(__name__)

# How far the rebuilt cut may be from the finished reel before we refuse it.
# The reconstruction is exact in principle; this catches the cases where it is
# not (a different transcript, a changed setting, a source that was re-uploaded).
MAX_DRIFT_S = 0.75
# A window longer than this is not a reel and rebuilding it would be a big,
# pointless download.
MAX_WINDOW_S = 15 * 60
# Words per second below which the transcript is not speech we can caption.
# Normal delivery runs 2-3; a Turtleback source in this library is 99 seconds
# transcribed as ONE word of Khmer. The drift check cannot catch that — a
# near-empty transcript produces a cut of the right LENGTH carrying one piece of
# nonsense — so it is caught here instead, and the reel is left alone.
MIN_WORDS_PER_S = 0.5


def _scene(prod: dict) -> dict:
    scenes = prod.get("scenes")
    if isinstance(scenes, str):
        try:
            scenes = json.loads(scenes)
        except (TypeError, ValueError):
            scenes = []
    if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
        return scenes[0]
    return {}


def can_rebuild(prod: dict) -> tuple[bool, str]:
    """Whether this production's cut can be reconstructed, and why not if not."""
    if str(prod.get("mode") or "") != "long_form_reel":
        return False, ("this video wasn't cut from one of your own source videos, "
                       "so there's no footage to rebuild it from")
    if str(prod.get("status") or "") != "succeeded":
        return False, "this video never finished rendering"
    sc = _scene(prod)
    # A reel's footage is EITHER a Drive file (the source-of-truth path, re-fetched
    # on every cut) or an uploaded URL (the legacy path). `source_url` is set to
    # the literal "pending://" on the Drive path, so testing it for non-emptiness
    # accepts a placeholder and fails later at ffmpeg with "Protocol not found" —
    # which is how 43 of James's reels first looked like they had a source.
    if not (str(sc.get("drive_file_id") or "").strip()
            or str(sc.get("source_url") or "").strip().startswith(("http://", "https://"))):
        return False, "the source video for this reel wasn't kept"
    try:
        start, end = float(sc.get("start_s") or 0.0), float(sc.get("end_s") or 0.0)
    except (TypeError, ValueError):
        return False, "the reel's in and out points weren't kept"
    if end - start <= 0 or end - start > MAX_WINDOW_S:
        return False, "the reel's in and out points don't make sense"
    return True, ""


def words_in_window(words: Any, start: float, end: float) -> list:
    """The source transcript's words inside the cut window, shifted to zero.

    Stored as {"w": word, "t": start, "e": end, "sp": speaker} — not the
    TranscribedWord field names, which is a trap worth writing down once.
    """
    from .transcription import TranscribedWord

    if isinstance(words, str):
        try:
            words = json.loads(words)
        except (TypeError, ValueError):
            return []
    out = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        try:
            s0 = float(w.get("t", w.get("start")))
            e0 = float(w.get("e", w.get("end")))
        except (TypeError, ValueError):
            continue
        if e0 <= start or s0 >= end or e0 <= s0:
            continue
        out.append(TranscribedWord(
            word=str(w.get("w") or w.get("word") or ""),
            start=max(0.0, s0 - start), end=min(end - start, e0 - start),
            speaker=str(w.get("sp") or w.get("speaker") or ""),
        ))
    out.sort(key=lambda x: x.start)
    return out


async def _probe(path_or_url: str) -> tuple[float, int, int]:
    """(duration, width, height) of a local file or a public URL."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path_or_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    vals = [v for v in out.decode().split() if v.strip()]
    try:
        return float(vals[2]), int(float(vals[0])), int(float(vals[1]))
    except (IndexError, ValueError):
        return 0.0, 0, 0


async def _probe_duration(path_or_url: str) -> float:
    return (await _probe(path_or_url))[0]


async def _cut_window(src: str, dst: str, start: float, end: float,
                      size: tuple[int, int] | None = None) -> bool:
    """The window, re-encoded. `-ss` before `-i` seeks fast; the re-encode is
    what makes the cut frame-accurate, which matters because every caption time
    is measured from frame zero of this file.

    `size` is the finished reel's own dimensions. The assembly upscaled the
    source on its way out — a Turtleback source is 360x640 and its reel is
    1080x1920 — so rebuilding at the source's size would hand the owner a reel
    a third the resolution of the one they had, for the crime of moving a
    caption. Scaled, not cropped: same framing, same pixels, more of them.
    """
    vf = []
    if size and size[0] > 0 and size[1] > 0:
        vf = ["-vf", f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1"]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-i", src, "-t", f"{max(0.0, end - start):.3f}",
        *vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", dst,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        _log.warning("backfill: could not cut the window: %s", err.decode()[-300:])
        return False
    return True


async def _remember(tenant_id, pid, reason: str) -> None:
    """Record why this reel could not be rebuilt, so the editor can say so
    instead of offering a rebuild that fails every time it is clicked."""
    try:
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE video_productions SET caption_rebuild_error=$2, "
                "updated_at=now() WHERE id=$1", pid, reason[:400])
    except Exception:  # noqa: BLE001 — a note is not worth failing over
        _log.info("backfill: could not record the refusal for %s", pid, exc_info=True)


async def rebuild(production_id: str, tenant_id, *, source_path: str | None = None) -> dict:
    """Reconstruct and store this reel's captionless cut and caption flashes.

    Returns {ok: True, cues, duration, drift} on success, or {ok: False, reason}.
    Nothing is written unless the rebuilt cut matches the finished reel.
    """
    from .clip_tighten import compute_kept_intervals, maybe_drop_fillers, remap_words
    from .audio_trim import tighten_clip
    from .media import storage as media_storage
    from .story_video import caption_lines
    from .transcription import TranscriptionWithWords

    pid = UUID(str(production_id))
    async with acquire(tenant_id) as conn:
        prod = await conn.fetchrow(
            "SELECT id, mode, status, scenes, final_url, clean_cut_url "
            "FROM video_productions WHERE id=$1", pid)
    if prod is None:
        return {"ok": False, "reason": "no such reel"}
    prod = dict(prod)

    ok, why = can_rebuild(prod)
    if not ok:
        await _remember(tenant_id, pid, why)
        return {"ok": False, "reason": why}

    sc = _scene(prod)
    start, end = float(sc.get("start_s") or 0.0), float(sc.get("end_s") or 0.0)
    source_url = str(sc.get("source_url") or "")
    drive_id = str(sc.get("drive_file_id") or "").strip()
    source_id = str(sc.get("source_id") or "")

    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT words FROM long_sources WHERE id=$1", UUID(source_id)) if source_id else None
    words = words_in_window((row or {}).get("words"), start, end)
    if not words:
        why = ("the transcript for this reel's source is empty, so there are no "
               "captions to rebuild")
        await _remember(tenant_id, pid, why)
        return {"ok": False, "reason": why}

    total = end - start
    rate = len(words) / total if total > 0 else 0.0
    if rate < MIN_WORDS_PER_S:
        why = (f"this reel's source was transcribed as only {len(words)} word"
               f"{'' if len(words) == 1 else 's'} in {total:.0f} seconds — the transcript "
               f"is broken, and captions rebuilt from it would be nonsense")
        await _remember(tenant_id, pid, why)
        return {"ok": False, "words_per_s": round(rate, 2), "reason": why}
    kept = compute_kept_intervals(
        maybe_drop_fillers(words, settings.clip_tighten_remove_fillers), total,
        max_gap=settings.clip_tighten_max_gap_s)
    removed = total - sum(e - s for s, e in kept)
    # The same gate the pipeline applied when it made the cut — without it we
    # would tighten a clip the original never tightened, and every caption after
    # the first silence would land on the wrong words.
    tighten = (settings.clip_tighten_enabled and len(kept) >= 2
               and removed >= settings.clip_tighten_min_savings_s
               and removed <= total * 0.45)

    target, tw, th = await _probe(str(prod.get("final_url") or ""))

    with tempfile.TemporaryDirectory() as td:
        # Drive is the source of truth where it is set; ffmpeg cannot read a
        # Drive id, so fetch it to disk first and cut from there. The URL path
        # is cut straight over HTTP (range requests — no full download).
        cut_from = source_path or source_url
        if source_path:
            pass    # already on disk — a batch fetched it once for the whole group
        elif drive_id:
            from .drive import fetch_drive_file_to_path

            cut_from = f"{td}/source.mp4"
            try:
                await fetch_drive_file_to_path(drive_id, cut_from)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False,
                        "reason": f"could not fetch this reel's source from Drive: "
                                  f"{type(exc).__name__}"}

        window = f"{td}/window.mp4"
        # Match the finished reel's frame exactly, so a caption move never costs
        # the owner resolution.
        if not await _cut_window(cut_from, window, start, end,
                                 (tw, th) if tw > 0 and th > 0 else None):
            return {"ok": False, "reason": "could not re-cut the source video"}
        cut_path = window
        if tighten:
            with open(window, "rb") as fh:
                tightened = await tighten_clip(fh.read(), kept)
            if tightened:
                cut_path = f"{td}/tight.mp4"
                with open(cut_path, "wb") as fh:
                    fh.write(tightened)
            else:
                tighten = False

        built, bw, bh = await _probe(cut_path)
        drift = abs(built - target) if target > 0 else 0.0
        if target > 0 and drift > MAX_DRIFT_S:
            # Refusing beats storing a cut that is out of step: every caption
            # would sit on the wrong words, and it would look like OUR bug.
            why = (f"the rebuilt cut came out {drift:.1f}s from the finished reel, so it "
                   f"wouldn't line up — this one needs a full re-render")
            await _remember(tenant_id, pid, why)
            return {"ok": False, "drift": round(drift, 2), "reason": why}

        tenant = str(tenant_id or settings.default_tenant_id)
        url, _ = await asyncio.to_thread(
            media_storage().save_from_path, tenant, cut_path,
            f"rebuilt-cut-{str(pid)[:8]}.mp4")

    tr = TranscriptionWithWords(text="", words=words, duration=total)
    if tighten:
        tr = remap_words(tr, kept)
    cues = caption_lines(tr.words)

    # The headline. The original was written by gen_video_hook at render time
    # and never stored, so it cannot be recovered exactly — but leaving it out
    # would mean that moving a caption silently DELETES the reel's headline,
    # which is a worse answer than a close paraphrase. Same function, same kind
    # of input, and the editor shows the text so the owner can see what it says.
    hook: dict = {}
    try:
        from .caption_styles import hook_hold_seconds
        from .content import gen_video_hook

        spoken = " ".join(str(c.get("raw_text") or c.get("text") or "") for c in cues).strip()
        text = (await gen_video_hook(spoken, tenant_id) or "").strip()
        if text:
            hook = {"text": text[:80], "hold": hook_hold_seconds(built or total),
                    "rebuilt": True}
    except Exception:  # noqa: BLE001 — a reel without its headline still beats no rebuild
        _log.info("backfill: could not rewrite the headline for %s", pid, exc_info=True)

    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE video_productions SET clean_cut_url=$2, caption_cues=$3, "
            "caption_hook=$4, caption_rebuild_error='', updated_at=now() WHERE id=$1",
            pid, url, json.dumps(cues), json.dumps(hook))
    _log.info("backfill: rebuilt the cut for %s (%d cues, drift %.2fs)",
              pid, len(cues), drift)
    return {"ok": True, "clean_cut_url": url, "cues": len(cues),
            "duration": round(built, 2), "drift": round(drift, 2),
            "tightened": tighten, "width": bw, "height": bh,
            "hook": hook.get("text", "")}


async def candidates(tenant_id, *, limit: int = 500) -> list[dict]:
    """Every reel in this brand whose captionless cut could be rebuilt.

    Already-rebuilt reels are excluded, so this is safe to re-run: it is the
    work still outstanding, not everything.
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, mode, status, scenes, title FROM video_productions "
            "WHERE coalesce(clean_cut_url, '') = '' "
            "ORDER BY created_at DESC LIMIT $1", limit)
    out = []
    for r in rows:
        prod = dict(r)
        ok, _why = can_rebuild(prod)
        if not ok:
            continue
        sc = _scene(prod)
        out.append({
            "id": str(prod["id"]),
            "title": str(prod.get("title") or "")[:60],
            "drive_file_id": str(sc.get("drive_file_id") or "").strip(),
            "source_url": str(sc.get("source_url") or "").strip(),
        })
    return out


async def rebuild_all(tenant_id, *, limit: int = 500, concurrency: int = 3) -> dict:
    """Rebuild every outstanding reel for this brand.

    Grouped by SOURCE, because a library is many reels cut from a few videos —
    42 of James's come from 12 files, one of them serving 8 reels. The source is
    a whole YouTube video pulled from Drive, several minutes each, so fetching
    it once per group rather than once per reel is the difference between an
    afternoon and half an hour.

    Bounded concurrency across groups: each rebuild re-encodes video, and a
    library of forty would otherwise open forty ffmpeg processes at once.
    Failures are collected rather than raised — one reel with a broken
    transcript must not stop the other thirty-nine.
    """
    todo = await candidates(tenant_id, limit=limit)
    groups: dict[str, list[dict]] = {}
    for item in todo:
        groups.setdefault(item["drive_file_id"] or item["source_url"], []).append(item)

    gate = asyncio.Semaphore(max(1, concurrency))
    done: list[dict] = []
    skipped: list[dict] = []

    async def _record(item: dict, res: dict) -> None:
        row = {**item, **{k: v for k, v in res.items() if k != "clean_cut_url"}}
        (done if res.get("ok") else skipped).append(row)

    async def _group(key: str, items: list[dict]) -> None:
        async with gate:
            src_path: str | None = None
            with tempfile.TemporaryDirectory() as td:
                drive_id = items[0]["drive_file_id"]
                if drive_id:
                    from .drive import fetch_drive_file_to_path

                    src_path = f"{td}/source.mp4"
                    try:
                        await fetch_drive_file_to_path(drive_id, src_path)
                    except Exception as exc:  # noqa: BLE001
                        for item in items:
                            await _record(item, {"ok": False, "reason":
                                f"could not fetch the source from Drive: {type(exc).__name__}"})
                        return
                for item in items:
                    try:
                        res = await rebuild(item["id"], tenant_id, source_path=src_path)
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("backfill: %s blew up", item["id"], exc_info=True)
                        res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
                    await _record(item, res)

    await asyncio.gather(*(_group(k, v) for k, v in groups.items()))
    return {"considered": len(todo), "sources": len(groups),
            "rebuilt": len(done), "skipped": len(skipped),
            "done": done, "not_done": skipped}
