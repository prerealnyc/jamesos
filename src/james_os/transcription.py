"""Audio/video → text via OpenAI Whisper.

Called directly with httpx (same pattern as embedder.py / rerank.py — no
extra SDK). This is the bridge that turns the voice corpus (podcasts,
academy recordings) into ingestible text — Head 1 of the four-headed beast.

Whisper hard-limits uploads to 25 MB. Larger files need ffmpeg-split
first; that's a deliberate future step, not silently handled here.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".mp4", ".mpeg", ".mpga", ".webm", ".flac", ".ogg")
WHISPER_MAX_BYTES = 25 * 1024 * 1024


def is_audio(filename: str) -> bool:
    return filename.lower().endswith(AUDIO_EXT)


class TranscriptionError(RuntimeError):
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
async def transcribe(filename: str, data: bytes) -> str:
    if not settings.openai_api_key:
        raise TranscriptionError(
            "OPENAI_API_KEY is not set — audio transcription unavailable"
        )
    if len(data) > WHISPER_MAX_BYTES:
        raise TranscriptionError(
            f"{filename} is {len(data) // (1024 * 1024)} MB; Whisper limit is "
            f"25 MB. Split with ffmpeg before ingesting (not yet automated)."
        )
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            files={"file": (filename, data)},
            data={"model": "whisper-1", "response_format": "text"},
        )
        if r.status_code == 429:
            raise TranscriptionError("OpenAI rate limited; retrying")
        r.raise_for_status()
        return r.text.strip()
