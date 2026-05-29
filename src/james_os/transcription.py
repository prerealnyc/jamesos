"""Audio/video → text via OpenAI Whisper.

Called directly with httpx (same pattern as embedder.py / rerank.py — no
extra SDK). Two surfaces:

  * transcribe()    — plain text. Used by the reference-library ingest
    that turns podcasts/academy recordings into voice-corpus events.
  * transcribe_words() — verbose_json with per-word start/end timestamps.
    Used by the story_audio video mode to pin B-roll stills to the
    exact moments James says each word.

Whisper hard-limits uploads to 25 MB. Larger files need ffmpeg-split
first; that's a deliberate future step, not silently handled here.
"""

from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".mp4", ".mpeg", ".mpga", ".webm", ".flac", ".ogg")
WHISPER_MAX_BYTES = 25 * 1024 * 1024


def is_audio(filename: str) -> bool:
    return filename.lower().endswith(AUDIO_EXT)


class TranscriptionError(RuntimeError):
    pass


@dataclass
class TranscribedWord:
    word: str
    start: float
    end: float


@dataclass
class TranscriptionWithWords:
    text: str                          # full joined transcript
    words: list[TranscribedWord]       # per-word timestamps
    duration: float                    # total audio length in seconds


def _check_audio_size(filename: str, n: int) -> None:
    if not settings.openai_api_key:
        raise TranscriptionError(
            "OPENAI_API_KEY is not set — audio transcription unavailable"
        )
    if n > WHISPER_MAX_BYTES:
        raise TranscriptionError(
            f"{filename} is {n // (1024 * 1024)} MB; Whisper limit is "
            f"25 MB. Split with ffmpeg before ingesting (not yet automated)."
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
async def transcribe(filename: str, data: bytes) -> str:
    _check_audio_size(filename, len(data))
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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
async def transcribe_words(filename: str, data: bytes) -> TranscriptionWithWords:
    """Same Whisper call, but with `verbose_json` + word-level timestamps.

    Returns the per-word start/end so the story_audio assembler can pin
    each B-roll still to the exact spoken-word window. Each word fits on
    a [start, end] timeline that lines up with the original audio. Falls
    back to an empty word list (but real text+duration) if the API
    returns a malformed `words` field — better one missing layer than a
    crashed production.
    """
    _check_audio_size(filename, len(data))
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            files={"file": (filename, data)},
            data={
                "model": "whisper-1",
                "response_format": "verbose_json",
                # Whisper accepts repeated form fields for granularities;
                # httpx encodes a list correctly for `multipart/form-data`.
                "timestamp_granularities[]": "word",
            },
        )
        if r.status_code == 429:
            raise TranscriptionError("OpenAI rate limited; retrying")
        r.raise_for_status()
        body = r.json()
    raw_words = body.get("words") or []
    words: list[TranscribedWord] = []
    for w in raw_words:
        try:
            words.append(TranscribedWord(
                word=str(w.get("word") or "").strip(),
                start=float(w.get("start") or 0.0),
                end=float(w.get("end") or 0.0),
            ))
        except (TypeError, ValueError):
            continue
    return TranscriptionWithWords(
        text=(body.get("text") or "").strip(),
        words=words,
        duration=float(body.get("duration") or 0.0),
    )


__all__ = [
    "is_audio", "transcribe", "transcribe_words",
    "TranscriptionError", "TranscribedWord", "TranscriptionWithWords",
    "AUDIO_EXT", "WHISPER_MAX_BYTES",
]
