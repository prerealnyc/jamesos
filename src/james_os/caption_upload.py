"""Captions on a video the owner recorded — and nothing else.

The finished-reel door (`/video/reel`) takes a talking head and directs it: AI
B-roll cut in every few seconds, a music bed, a headline. That is the right tool
for a raw clip someone wants turned into a reel, and the wrong one for a script
the owner has just read to camera: what they asked for there is their own
footage, as recorded, with what they said written on it.

So this is the short path: store the recording, listen to it (word timings),
group the words into the same 2-3-word flashes every reel uses
(story_video.caption_lines), and burn them on with the same libass pass the
caption editor uses (caption_burn.render). One ffmpeg encode; no provider
spend beyond the transcription.

It is saved as a production that is already finished and already approved —
`clean_cut_url` is the recording itself and `caption_cues` the flashes — so the
caption editor can move, restyle or remove the captions later exactly as it
does for an engine-cut reel, and the engine's own review queue never lists it
(it was approved as a script before it was recorded).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# Phones record 1080x1920 or 2160x3840. Captions are drawn for the frame the
# video is shown in, and a 4K encode costs four times the time for a picture
# every network scales down anyway — so anything larger is brought down to this
# long side (aspect kept).
MAX_LONG_SIDE = 1920
# A recording longer than this is not a script read to camera; the word list
# would also crowd the caption track (caption_burn caps it at 600 flashes).
MAX_SECONDS = 15 * 60
DEFAULT_STYLE = "bold_pop"


def target_size(width: int, height: int) -> tuple[int, int] | None:
    """The frame to draw at, or None to keep the recording's own.

    Even numbers only: libx264 refuses odd dimensions."""
    if width <= 0 or height <= 0:
        return None
    long_side = max(width, height)
    if long_side <= MAX_LONG_SIDE:
        return None
    scale = MAX_LONG_SIDE / long_side
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return (w, h)


def aspect_of(width: int, height: int) -> str:
    """The production's aspect label, nearest of the three the engine knows."""
    if width <= 0 or height <= 0:
        return "9:16"
    r = width / height
    if r < 0.8:
        return "9:16"
    if r > 1.25:
        return "16:9"
    return "1:1"


async def run(job: dict, *, tenant, data: bytes, filename: str, title: str,
              platform: str, caption_style: str) -> None:
    """Store → transcribe → burn → save, updating `job` as it goes.

    Never raises: a failure is written onto the job with the reason, the same
    contract as every other job in reel_api."""
    from .audio_trim import extract_audio_mp3
    from .caption_burn import probe_size, render
    from .db import acquire, set_request_tenant
    from .media import storage
    from .story_video import caption_lines
    from .transcription import transcribe_words

    set_request_tenant(str(tenant))

    def stage(name: str) -> None:
        import time
        job["stage"] = name
        job["_stage_at"] = time.monotonic()

    try:
        stage("uploading")
        name = filename or "recording.mp4"
        source_url, _ = await asyncio.to_thread(storage().save, str(tenant), data, name)
        job["source_url"] = source_url

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "source" + (os.path.splitext(name)[1] or ".mp4"))
            with open(src, "wb") as fh:
                fh.write(data)
            width, height, duration = await probe_size(src)
            if width <= 0 or height <= 0:
                job.update(status="failed", error="that file has no picture we can read — "
                           "upload the video as an mp4 or mov")
                return
            if duration > MAX_SECONDS:
                job.update(status="failed", error=(
                    f"the recording is {round(duration / 60)} minutes long — captions are for "
                    f"a script read to camera (up to {MAX_SECONDS // 60} minutes)"))
                return

            stage("transcribing")
            mp3 = os.path.join(td, "voice.mp3")
            if not await extract_audio_mp3(src, mp3):
                job.update(status="failed", error="the recording has no sound we can read — "
                           "check it was recorded with audio")
                return
            with open(mp3, "rb") as fh:
                audio = fh.read()
            words = await transcribe_words("voice.mp3", audio)
            cues = caption_lines(words.words)
            if not cues:
                job.update(status="failed", error="no speech was found in the recording, so "
                           "there is nothing to caption")
                return

            stage("captioning")
            style = (caption_style or "").strip() or DEFAULT_STYLE
            frame = target_size(width, height)
            out = await render(src, cues, y_pct=None, style=style,
                               target_size=frame, work_dir=td)
            if not out.get("ok"):
                job.update(status="failed",
                           error=str(out.get("reason") or "the captions could not be drawn"))
                return

            stage("saving")
            final_url, _ = await asyncio.to_thread(
                storage().save_from_path, str(tenant), out["path"], "captioned.mp4")

        async with acquire(tenant, require_tenant=True) as conn:
            row = await conn.fetchrow(
                """INSERT INTO video_productions
                     (status, title, platform, aspect, script, scenes, mode,
                      caption_style, final_url, clean_cut_url, caption_cues,
                      review_status, reviewed_at, completed_at, options)
                   VALUES ('succeeded', $1, $2, $3, $4, '[]'::jsonb, 'long_form_reel',
                           $5, $6, $7, $8::jsonb, 'approved', now(), now(), $9::jsonb)
                   RETURNING id""",
                (title or "Recording")[:200], platform or "instagram",
                aspect_of(out["width"], out["height"]), words.text[:20000],
                style, final_url, source_url, json.dumps(cues),
                json.dumps({"captioned_upload": True}),
            )
        job.update(
            status="succeeded", production_id=str(row["id"]), final_url=final_url,
            duration=out["duration"], width=out["width"], height=out["height"],
            caption_style=style, transcript=words.text[:4000], cue_count=len(cues),
        )
        stage("succeeded")
    except Exception as e:  # noqa: BLE001 — reported on the job, never raised
        logger.exception("caption upload failed")
        job.update(status="failed", error=f"{type(e).__name__}: {e}")


_LABEL = {
    "uploading": "Saving your recording",
    "transcribing": "Listening to what you said",
    "captioning": "Writing the captions on",
    "saving": "Saving the captioned video",
    "succeeded": "Done",
    "failed": "Failed",
}
_TYPICAL_S = {"uploading": 6, "transcribing": 30, "captioning": 60, "saving": 6}


def progress(job: dict[str, Any]) -> dict:
    """One bar across store → listen → burn → save."""
    import time
    stage = job.get("stage") or "uploading"
    if job.get("status") in ("failed", "succeeded"):
        st = job["status"]
        return {"stage": st, "label": _LABEL[st], "pct": 100}
    order = list(_TYPICAL_S)
    idx = order.index(stage) if stage in order else 0
    before = sum(_TYPICAL_S[s] for s in order[:idx])
    cur = _TYPICAL_S.get(stage, 30)
    in_stage = max(0.0, time.monotonic() - float(job.get("_stage_at") or 0))
    total = sum(_TYPICAL_S.values())
    pct = max(1, min(99, round((before + min(in_stage, cur)) / total * 100)))
    return {"stage": stage, "label": _LABEL.get(stage, stage), "pct": pct}


__all__ = ["DEFAULT_STYLE", "MAX_LONG_SIDE", "MAX_SECONDS", "aspect_of", "progress",
           "run", "target_size"]
