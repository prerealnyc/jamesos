"""Story-video mode — caption-pinned B-roll from a HeyGen voiceover.

Pipeline (the `story_audio` production mode):

    script
      ↓ HeyGen renders avatar video (we only want the audio track from it)
    avatar.mp4
      ↓ ffmpeg strips the audio track
    voice.mp3
      ↓ Whisper with word-level timestamps
    [TranscribedWord, …]
      ↓ heuristic sentence-aware chunking, ~4s per beat
    [Beat(start, end, text), …]
      ↓ one LLM call for visual prompts of every beat
    [Beat with .image_prompt, …]
      ↓ gpt-image-1 in parallel, one still per beat
    [Beat with .image_url (Supabase Storage), …]
      ↓ Creatomate source: audio on track 1, stills on track 2 with
        subtle Ken-Burns zoom, word-pinned captions on track 3, optional
        music underneath
    final.mp4

The audio is the timing source of truth — every image pins to exact
word timestamps from Whisper. No drift. No "the planner said 5s but the
speech took 7s" gap.

Honest scope:
  * We pay for one HeyGen video render even though we only use the
    audio. Cheaper alternative is HeyGen's TTS-only endpoint or
    ElevenLabs — flagged future swap, not silently faked.
  * gpt-image-1 can still go uncanny on close-up faces. The visual
    prompts steer toward environments and objects; for a real-estate
    brand that's the right bias anyway.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .audio_trim import extract_audio_mp3
from .config import settings
from .imagegen import POST_STYLES, generate_post_image
from .llm import get_llm
from .media import storage as media_storage
from .transcription import TranscribedWord, transcribe_words

# Targets for beat segmentation. Tuned for TikTok/Reels — 3-6 seconds
# per still keeps the visual pace fast enough to feel like a story, not
# a slideshow, while leaving room for a sentence to land.
_TARGET_BEAT_SECONDS = 4.0
_MIN_BEAT_SECONDS = 2.0
_MAX_BEAT_SECONDS = 7.0
# Cap total beats so a long script doesn't blow up image-gen cost.
_MAX_BEATS = 18

_HTTP_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


@dataclass
class Beat:
    index: int
    start: float                       # seconds into the audio
    end: float                         # seconds into the audio
    text: str                          # spoken words in this beat
    image_prompt: str = ""             # LLM-generated visual idea
    image_url: str | None = None       # Supabase Storage URL once rendered
    image_error: str = ""              # honest marker if a beat failed


@dataclass
class StoryAudioResult:
    audio_url: str                     # durable mp3 URL
    audio_duration: float              # total seconds
    beats: list[Beat]
    captions: list[dict] = field(default_factory=list)  # [{start,end,text}]
    error: str = ""


# ── audio sourcing ────────────────────────────────────────────────────


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


async def _strip_to_mp3(video_bytes: bytes) -> bytes | None:
    """Run ffmpeg in a temp dir. Returns the MP3 bytes or None if ffmpeg
    failed (caller decides how to surface the error to the production)."""
    with tempfile.TemporaryDirectory() as td:
        in_path = f"{td}/in.mp4"
        out_path = f"{td}/out.mp3"
        Path(in_path).write_bytes(video_bytes)
        ok = await extract_audio_mp3(in_path, out_path)
        if not ok:
            return None
        try:
            return Path(out_path).read_bytes()
        except OSError:
            return None


async def persist_audio(
    audio_bytes: bytes, label: str, tenant_id: str | None = None
) -> str | None:
    """Push MP3 bytes to Supabase Storage and return the public URL.
    Creatomate needs a fetchable URL — local /media-files paths don't
    reach their CDN."""
    try:
        url, _ = await asyncio.to_thread(
            media_storage().save,
            tenant_id or str(settings.default_tenant_id),
            audio_bytes,
            f"{label}.mp3",
        )
        return url
    except Exception:  # noqa: BLE001
        return None


# ── beat segmentation ─────────────────────────────────────────────────


_SENT_END = re.compile(r"[.!?…]$")


def _is_sentence_end(token: str) -> bool:
    # Whisper word tokens include trailing punctuation in some langs.
    return bool(_SENT_END.search(token.strip()))


def segment_beats(words: list[TranscribedWord]) -> list[Beat]:
    """Chunk a word stream into 8-18 visual beats.

    Two pressures balance each other:
      * prefer to cut at sentence boundaries (so a caption never breaks
        mid-thought)
      * keep each beat between MIN and MAX seconds (so the slideshow
        feels like a story, not a strobe or a still)

    Algorithm:
      1. Walk the words. Open a beat; accumulate words until either
         we hit a sentence end AND have ≥ TARGET_BEAT_SECONDS, OR we
         pass MAX_BEAT_SECONDS regardless of punctuation.
      2. If after step 1 we ended up with > _MAX_BEATS beats, greedily
         merge the shortest adjacent pair until we're under the cap.

    Returns beats with index/start/end/text filled; image_prompt and
    image_url stay empty until the next pipeline stage.
    """
    if not words:
        return []
    beats: list[Beat] = []
    bs = words[0].start
    bw: list[str] = []
    for i, w in enumerate(words):
        bw.append(w.word)
        elapsed = w.end - bs
        last = i == len(words) - 1
        cut = (
            last
            or (_is_sentence_end(w.word) and elapsed >= _TARGET_BEAT_SECONDS)
            or elapsed >= _MAX_BEAT_SECONDS
        )
        if cut:
            beats.append(Beat(
                index=len(beats),
                start=round(bs, 3),
                end=round(w.end, 3),
                text=_clean_text(" ".join(bw)),
            ))
            if not last:
                bs = words[i + 1].start
                bw = []

    # Coalesce tiny tail beats with their predecessor (e.g. a trailing
    # "Right?" of < MIN_BEAT_SECONDS would otherwise flash for 0.4s).
    coalesced: list[Beat] = []
    for b in beats:
        if (
            coalesced
            and (b.end - b.start) < _MIN_BEAT_SECONDS
        ):
            prev = coalesced[-1]
            prev.end = b.end
            prev.text = _clean_text(f"{prev.text} {b.text}")
            continue
        coalesced.append(b)

    # Enforce the beat-count cap (image-gen cost guard). Merge the
    # shortest adjacent pair repeatedly until we're under the cap.
    while len(coalesced) > _MAX_BEATS:
        # Find shortest adjacent pair by combined duration
        shortest = 0
        shortest_dur = float("inf")
        for i in range(len(coalesced) - 1):
            d = coalesced[i + 1].end - coalesced[i].start
            if d < shortest_dur:
                shortest_dur = d
                shortest = i
        a = coalesced[shortest]
        b = coalesced[shortest + 1]
        a.end = b.end
        a.text = _clean_text(f"{a.text} {b.text}")
        del coalesced[shortest + 1]

    # Re-index after the merges so the caller sees 0..N-1.
    for i, b in enumerate(coalesced):
        b.index = i
    return coalesced


_WS = re.compile(r"\s+")


def _clean_text(s: str) -> str:
    return _WS.sub(" ", s).strip()


# ── caption lines pinned to word groups ───────────────────────────────


def caption_lines(
    words: list[TranscribedWord], group: int = 4
) -> list[dict]:
    """Group words into 3-4-word caption flashes (TikTok-style) pinned
    to the actual spoken-word timestamps. Each entry is
    {start, end, text}. Used by the Creatomate assembler to drop one
    text element per group."""
    out: list[dict] = []
    if not words:
        return out
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        out.append({
            "start": round(chunk[0].start, 3),
            "end": round(chunk[-1].end, 3),
            "text": _clean_text(" ".join(w.word for w in chunk)),
        })
    return out


# ── visual prompts for each beat (one LLM call) ───────────────────────


_PROMPT_SYSTEM = """You are the art director for a personal brand's
short-form video. The brand voice is being read aloud by an AI clone of
the brand owner; the video plays this audio over a slideshow of
AI-generated still images, one per "beat" of the script.

For each beat I give you, write ONE concrete visual prompt — what
the image SHOWS, not what it SYMBOLIZES. Specific subjects, real-world
settings, an interesting angle when natural. Continuity matters: the
prompts should feel like the same world / same documentary, not 12
random stock photos.

Hard rules:
  * Single clear focal point. Uncluttered. Plenty of negative space.
  * No text, no captions, no logos in the image (we burn captions over
    the top later).
  * No close-up faces unless the beat absolutely demands one.
  * Don't repeat the beat's spoken text — describe what the viewer SEES.
  * Each prompt is one sentence, 12-30 words.

Return STRICT JSON:
{"beats": [{"index": int, "prompt": str}, ...]}
The list must have exactly one entry per input beat, in the same order,
with the same `index`.
"""


async def write_image_prompts(
    beats: list[Beat],
    brand_context: str,
    style: str,
) -> None:
    """Mutates `beats` in place — fills each beat's `image_prompt`.

    If the LLM call fails, every beat keeps an empty prompt — the caller
    refuses to render any beats and surfaces the error rather than
    paying for 12 random images.
    """
    if not beats:
        return
    payload = {
        "brand_context": brand_context[:600],
        "style_note": POST_STYLES.get(style, POST_STYLES["editorial"])[:240],
        "beats": [
            {"index": b.index, "text": b.text} for b in beats
        ],
    }
    try:
        out = await get_llm().complete_json(
            system=_PROMPT_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=1400, temperature=0.5,
        )
    except Exception:  # noqa: BLE001
        return
    by_idx = {
        int(p["index"]): str(p.get("prompt") or "").strip()
        for p in (out.get("beats") or [])
        if isinstance(p, dict) and "index" in p
    }
    for b in beats:
        b.image_prompt = by_idx.get(b.index, "")


# ── image generation per beat (parallel) ──────────────────────────────


async def _gen_one_beat_image(
    beat: Beat, aspect: str, platform: str, style: str,
    tenant_id: str,
) -> None:
    """Generate, persist, attach. Mutates `beat` in place. A single
    failure doesn't crash the whole run — the beat just keeps an empty
    image_url and the caller can decide whether to fail the production
    or render with placeholders."""
    if not beat.image_prompt:
        beat.image_error = "no prompt"
        return
    png, _meta, err = await generate_post_image(
        topic=beat.image_prompt,
        platform=platform,
        brief="",
        aspect=aspect,
        style=style,
    )
    if not png:
        beat.image_error = err or "image gen failed"
        return
    try:
        url, _ = await asyncio.to_thread(
            media_storage().save,
            tenant_id,
            png,
            f"story-beat-{beat.index:02d}-{uuid.uuid4().hex[:8]}.png",
        )
        beat.image_url = url
    except Exception as e:  # noqa: BLE001
        beat.image_error = f"storage save failed: {e}"


async def gen_beat_images(
    beats: list[Beat],
    aspect: str,
    platform: str,
    style: str,
    tenant_id: str,
    *,
    concurrency: int = 4,
) -> None:
    """Parallel fill of every beat's image_url. The concurrency cap
    keeps us under OpenAI's images RPM ceiling and from blowing the
    Supabase Storage upload pool at the same time."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(b: Beat) -> None:
        async with sem:
            await _gen_one_beat_image(b, aspect, platform, style, tenant_id)

    await asyncio.gather(*(_one(b) for b in beats))


# ── public entrypoint used by the production pipeline ─────────────────


async def build_story_audio_assets(
    *,
    avatar_video_url: str,
    aspect: str,
    style: str,
    brand_context: str,
    platform: str = "instagram",
    tenant_id: str | None = None,
) -> StoryAudioResult:
    """End-to-end: HeyGen URL → durable audio + beats + captions, ready
    for the Creatomate story_audio assembler.

    Steps run sequentially because each depends on the previous. The
    only parallelism is gen_beat_images inside the final stage.
    """
    tid = tenant_id or str(settings.default_tenant_id)
    if not avatar_video_url or not avatar_video_url.startswith("http"):
        return StoryAudioResult(
            audio_url="", audio_duration=0.0, beats=[],
            error="story_audio needs a real HeyGen video URL to strip audio from",
        )

    try:
        video_bytes = await _download(avatar_video_url)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult("", 0.0, [], error=f"could not fetch HeyGen video: {e}")

    audio_bytes = await _strip_to_mp3(video_bytes)
    if not audio_bytes:
        return StoryAudioResult("", 0.0, [], error="ffmpeg failed to extract audio")

    audio_url = await persist_audio(audio_bytes, f"story-audio-{uuid.uuid4().hex[:8]}", tid)
    if not audio_url:
        return StoryAudioResult("", 0.0, [], error="could not persist audio to storage")

    try:
        tr = await transcribe_words("voice.mp3", audio_bytes)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult(audio_url, 0.0, [], error=f"whisper failed: {e}")

    if not tr.words:
        return StoryAudioResult(
            audio_url, tr.duration, [],
            error="whisper returned no word timestamps — story_audio needs them",
        )

    beats = segment_beats(tr.words)
    if not beats:
        return StoryAudioResult(
            audio_url, tr.duration, [],
            error="could not segment words into beats",
        )

    await write_image_prompts(beats, brand_context, style)
    missing = [b for b in beats if not b.image_prompt]
    if missing:
        return StoryAudioResult(
            audio_url, tr.duration, beats,
            error=f"LLM did not return visual prompts for {len(missing)} beat(s)",
        )

    await gen_beat_images(beats, aspect, platform, style, tid)
    failed = [b for b in beats if not b.image_url]
    if failed and len(failed) > len(beats) // 2:
        return StoryAudioResult(
            audio_url, tr.duration, beats,
            error=(
                f"{len(failed)}/{len(beats)} beat images failed — refusing to "
                f"assemble. First reason: {failed[0].image_error}"
            ),
        )

    captions = caption_lines(tr.words)
    return StoryAudioResult(
        audio_url=audio_url,
        audio_duration=tr.duration or (beats[-1].end if beats else 0.0),
        beats=beats,
        captions=captions,
    )


def beats_to_dict(beats: list[Beat]) -> list[dict[str, Any]]:
    """Serialize for storage in video_productions.scenes (jsonb)."""
    return [
        {
            "index": b.index, "start": b.start, "end": b.end,
            "text": b.text, "image_prompt": b.image_prompt,
            "image_url": b.image_url, "image_error": b.image_error,
        }
        for b in beats
    ]


__all__ = [
    "Beat", "StoryAudioResult",
    "segment_beats", "caption_lines", "write_image_prompts",
    "gen_beat_images", "build_story_audio_assets", "beats_to_dict",
]
