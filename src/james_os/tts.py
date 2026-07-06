"""ElevenLabs text-to-speech — the brand's cloned voice, as audio bytes.

The key has lived in settings since day one but nothing ever called the API;
this is the first real client. Long scripts are synthesized in chunks (the
API degrades on very long inputs) and stitched with ffmpeg.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx

from .config import settings

_API = "https://api.elevenlabs.io/v1"
_MODEL = "eleven_multilingual_v2"
_CHUNK_CHARS = 4500   # stay well under the per-request text limit


def tts_configured() -> bool:
    return bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id)


def _split_script(text: str) -> list[str]:
    """Split on paragraph boundaries into <= _CHUNK_CHARS pieces so each
    request ends on a natural pause (no mid-sentence voice seams)."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 > _CHUNK_CHARS and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    # A single paragraph longer than the cap still has to go somewhere.
    return chunks or [text[:_CHUNK_CHARS]]


async def _synthesize_chunk(client: httpx.AsyncClient, text: str) -> bytes:
    r = await client.post(
        f"{_API}/text-to-speech/{settings.elevenlabs_voice_id}",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={
            "text": text,
            "model_id": _MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=httpx.Timeout(300.0, connect=15.0),
    )
    r.raise_for_status()
    return r.content


async def synthesize_speech(text: str) -> bytes:
    """Full script → one mp3 (bytes). Raises RuntimeError when the voice
    isn't configured; HTTP errors propagate to the caller's job wrapper."""
    if not tts_configured():
        raise RuntimeError(
            "ElevenLabs voice not configured — set elevenlabs_api_key and "
            "elevenlabs_voice_id in Settings")
    chunks = _split_script(text)
    async with httpx.AsyncClient() as client:
        parts: list[bytes] = []
        for c in chunks:   # sequential — keeps voice pacing + avoids rate caps
            parts.append(await _synthesize_chunk(client, c))
    if len(parts) == 1:
        return parts[0]

    # Stitch with ffmpeg (concat demuxer is fine: same model/voice/encoder).
    with tempfile.TemporaryDirectory() as td:
        list_path = Path(td) / "list.txt"
        lines = []
        for i, b in enumerate(parts):
            p = Path(td) / f"part{i}.mp3"
            p.write_bytes(b)
            lines.append(f"file '{p}'")
        list_path.write_text("\n".join(lines))
        out = Path(td) / "episode.mp3"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path), "-c", "copy", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError("ffmpeg failed to stitch the narration parts")
        return out.read_bytes()


__all__ = ["synthesize_speech", "tts_configured"]
