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

import asyncio
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
    speaker: str = ""   # diarization label ("A"/"B"/…); "" when not diarized


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


_AAI_BASE = "https://api.assemblyai.com/v2"


async def transcribe_assemblyai(
    data: bytes, *, max_wait_s: float = 2700.0,
) -> TranscriptionWithWords:
    """Transcribe WITH speaker diarization via AssemblyAI — word-level
    timestamps + a speaker label per word, in one job (no 25 MB chunking).

    Upload → create transcript (speaker_labels) → poll → words[].speaker.
    Returns punctuated text + words carrying .speaker so the reel cutter can
    avoid crossing into the next speaker's turn.

    `max_wait_s` caps the poll: ~45 min for a full podcast SOURCE, but pass a
    short value (e.g. 240s) when transcribing a short reel CUT so a hung job
    falls back to Whisper in minutes, not the better part of an hour."""
    key = (settings.assemblyai_api_key or "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is not set")
    headers = {"authorization": key}
    # Generous write timeout: uploading a 50-min podcast's extracted audio
    # (tens–hundreds of MB) can take a while on a slow link; reads are quick.
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=600.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        up = await client.post(f"{_AAI_BASE}/upload", headers=headers, content=data)
        up.raise_for_status()
        audio_url = up.json()["upload_url"]
        cr = await client.post(
            f"{_AAI_BASE}/transcript", headers=headers,
            json={
                "audio_url": audio_url,
                # speech_models is OPTIONAL (the server defaults to this exact
                # list if omitted); we send it explicitly to PIN the models so a
                # future default change can't silently alter our output. Ordered
                # fallback list (latest → stable). Raw strings — the SDK enum
                # aliases don't exist over the REST API.
                "speech_models": ["universal-3-pro", "universal-2"],
                "speaker_labels": True,   # ← diarization: per-word speaker tags
                "punctuate": True,
            },
        )
        cr.raise_for_status()
        tid = cr.json()["id"]
        body: dict = {}
        # Diarized jobs run ~20-40% of audio length; a 50-min file can take
        # ~20 min. Poll up to ~45 min before giving up (each GET is cheap).
        # Tolerate transient network/5xx/429 blips during the long poll — one
        # hiccup shouldn't abandon a 20-min transcription (which would silently
        # fall back to Whisper and lose every speaker label). Only give up after
        # 5 consecutive failures or a real `status: error`.
        poll_fails = 0
        for _ in range(max(2, int(max_wait_s / 5))):   # 5s polls, capped by max_wait_s
            await asyncio.sleep(5)
            try:
                pr = await client.get(f"{_AAI_BASE}/transcript/{tid}", headers=headers)
                pr.raise_for_status()
            except httpx.HTTPError as e:
                poll_fails += 1
                if poll_fails >= 5:
                    raise TranscriptionError(
                        f"AssemblyAI polling failed {poll_fails}× in a row: {e}"
                    )
                continue
            poll_fails = 0
            body = pr.json()
            st = body.get("status")
            if st == "completed":
                break
            if st == "error":
                raise TranscriptionError(f"AssemblyAI error: {body.get('error')}")
        else:
            raise TranscriptionError("AssemblyAI transcription timed out")

    words: list[TranscribedWord] = []
    # Prefer utterances (guaranteed speaker per word); fall back to flat words.
    for u in (body.get("utterances") or []):
        spk = str(u.get("speaker") or "")
        for w in (u.get("words") or []):
            words.append(TranscribedWord(
                word=str(w.get("text") or "").strip(),
                start=float(w.get("start") or 0) / 1000.0,
                end=float(w.get("end") or 0) / 1000.0,
                speaker=str(w.get("speaker") or spk),
            ))
    if not words:
        for w in (body.get("words") or []):
            words.append(TranscribedWord(
                word=str(w.get("text") or "").strip(),
                start=float(w.get("start") or 0) / 1000.0,
                end=float(w.get("end") or 0) / 1000.0,
                speaker=str(w.get("speaker") or ""),
            ))
    return TranscriptionWithWords(
        text=(body.get("text") or "").strip(),
        words=words,
        duration=float(body.get("audio_duration") or 0.0),
    )


__all__ = [
    "is_audio", "transcribe", "transcribe_words", "transcribe_assemblyai",
    "TranscriptionError", "TranscribedWord", "TranscriptionWithWords",
    "AUDIO_EXT", "WHISPER_MAX_BYTES",
]
