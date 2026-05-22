"""Perception layer — make the reference library actually *see* a video.

On upload (or on demand) we watch the clip and turn it into a structured
"style fingerprint" the scene-plan generator can replicate:

    ffmpeg ──┬── audio  → Whisper  → transcript (the hook + script)
             └── frames → GPT-4o vision → structure / pacing / captions / framing
                                   │
                                   ▼
                         style fingerprint (JSON)

Honesty rules:
  * Replication targets FORMAT, never verbatim content — the fingerprint
    describes how a video is built (hook pattern, cut rhythm, caption style),
    not words to copy. The brand-voice QA gate downstream rejects copies.
  * No OpenAI key, or an un-downloadable URL reference → status is reported
    plainly ("unsupported"), never a faked analysis.
  * Frame sampling covers the opening window (short-form is the target);
    that scope limit is stated, not hidden.
"""

import asyncio
import base64
import json
import re
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from .config import settings

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_MAX_FRAMES = 8
_WHISPER_MODEL = "whisper-1"


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "ignore")


def _parse_duration(ffmpeg_log: str) -> int:
    m = _DUR_RE.search(ffmpeg_log)
    if not m:
        return 0
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return int(h * 3600 + mnt * 60 + s)


async def _extract_audio(src: Path, dst: Path) -> bool:
    rc, _ = await _run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", str(dst)]
    )
    return rc == 0 and dst.is_file() and dst.stat().st_size > 0


async def _extract_frames(src: Path, outdir: Path) -> tuple[list[Path], int]:
    rc, log = await _run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", "fps=1/2",
         "-frames:v", str(_MAX_FRAMES), "-q:v", "4",
         str(outdir / "f_%02d.jpg")]
    )
    frames = sorted(outdir.glob("f_*.jpg"))
    return frames, _parse_duration(log)


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


async def _transcribe(client: AsyncOpenAI, audio: Path) -> str:
    try:
        with audio.open("rb") as fh:
            res = await client.audio.transcriptions.create(
                model=_WHISPER_MODEL, file=fh
            )
        return (getattr(res, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — a missing/odd audio track must not sink analysis
        return ""


_VISION_SYSTEM = (
    "You are a short-form video analyst. Given sampled frames (in order) and "
    "the transcript of a clip, describe HOW it is built so another creator "
    "could replicate the FORMAT in their own voice — never to copy its words. "
    "Return STRICT JSON with keys: hook (what grabs attention in the first "
    "seconds), structure (the beat-by-beat flow), pacing (cut rhythm / energy), "
    "captions (on-screen text style, if any), visual_style (framing, setting, "
    "look), replication_tips (3-5 concrete, voice-agnostic tips). Be specific "
    "and concise. If the frames are uninformative, say so honestly in each field."
)


async def _describe(client: AsyncOpenAI, frames: list[Path], transcript: str) -> dict:
    if not frames:
        return {}
    content: list[dict] = [
        {"type": "text",
         "text": f"Transcript:\n{transcript[:4000] or '(no speech detected)'}\n\n"
                 f"{len(frames)} frames follow, in chronological order."}
    ]
    for f in frames:
        b64 = base64.b64encode(f.read_bytes()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    try:
        res = await client.chat.completions.create(
            model=settings.llm_model if "gpt-4o" in settings.llm_model else "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": content},
            ],
            max_tokens=900,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        return json.loads(res.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001
        return {"error": f"vision analysis failed: {e}"}


async def analyze_file(path: str) -> dict:
    """Watch a local video file → {status, transcript, duration, fingerprint}."""
    client = _client()
    if client is None:
        return {"status": "unsupported", "note": "No OpenAI key — add it in Settings to analyze videos."}
    src = Path(path)
    if not src.is_file():
        return {"status": "failed", "note": "file not found on disk"}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = tmp / "audio.mp3"
        have_audio = await _extract_audio(src, audio)
        frames, duration = await _extract_frames(src, tmp)
        transcript = await _transcribe(client, audio) if have_audio else ""
        fingerprint = await _describe(client, frames, transcript)

    if not frames and not transcript:
        return {"status": "failed", "note": "could not extract audio or frames (unsupported format?)"}

    return {
        "status": "done",
        "transcript": transcript,
        "duration": duration,
        "fingerprint": fingerprint,
        "frames_analyzed": len(frames),
    }


def fingerprint_to_notes(analysis: dict) -> str:
    """Flatten a fingerprint into readable notes for the card / generator."""
    fp = analysis.get("fingerprint") or {}
    if not fp:
        return ""
    order = ["hook", "structure", "pacing", "captions", "visual_style"]
    lines = []
    for k in order:
        v = fp.get(k)
        if v:
            lines.append(f"{k.replace('_', ' ').title()}: {v}")
    tips = fp.get("replication_tips")
    if isinstance(tips, list) and tips:
        lines.append("Replicate: " + "; ".join(str(t) for t in tips))
    elif tips:
        lines.append(f"Replicate: {tips}")
    return "\n".join(lines)


__all__ = ["analyze_file", "fingerprint_to_notes"]
