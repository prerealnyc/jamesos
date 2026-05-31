"""Long Form Cutter — chop a 50-60 min podcast/long video into Reels.

Pipeline (state machine on the long_sources row):

  uploading → file persisted to Supabase Storage
       ↓
  transcribing → ffmpeg extract audio (mono 16 kHz 32 kbps mp3 to
       ↓        fit Whisper's 25 MB cap), chunked if a 60-min file
       ↓        is still over the limit, transcribed with word-level
       ↓        timestamps, chunks stitched with N × chunk_s offsets
       ↓
  analyzing → LLM reads the full transcript and returns 3-5
       ↓     standalone reel candidates (start_s, end_s, hook quote,
       ↓     summary, score). Persisted to reel_candidates.
       ↓
  ready → user reviews candidates on /long-form, clicks Render on
          the ones worth shipping. Each Render kicks a video_productions
          row in `long_form_reel` mode → engaging_avatar-style treatment
          on the cut clip (captions, B-roll inserts, music) → approval
          queue.

Honest scope:
  * Whisper's 25 MB cap is real. We extract at 32 kbps mono = ~14 MB
    for 60 min. Longer or higher-fidelity sources get split.
  * Candidate selection is an LLM call on the full transcript. Very
    long transcripts (>30k tokens) need a chunked-then-merged pass;
    flagged but not built today.
  * For a Drive URL source, we reuse drive.py to download the file
    first, then proceed as if it were uploaded. No streaming.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .audio_trim import (
    extract_audio_lowbit,
    probe_duration,
    split_audio_chunks,
)
from .config import settings
from .db import acquire
from .llm import get_llm
from .media import storage as media_storage
from .transcription import (
    TranscribedWord,
    transcribe_words,
    WHISPER_MAX_BYTES,
)

# Each transcript chunk gets up to ~10 min of audio. Long Whisper jobs
# would otherwise time out; this keeps each call under 30s.
_CHUNK_SECONDS = 600
# Lower-quality audio extract — STT-only, listener never hears it.
_LOWBIT_BITRATE = "32k"


def _row(r) -> dict:
    """asyncpg Record → JSON-safe dict with UUIDs and timestamps stringified."""
    d = dict(r)
    for k in ("id", "tenant_id", "source_id", "production_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("words",):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


# ── source ingestion ──────────────────────────────────────────────────


async def create_source(
    *, title: str, source_url: str, tenant_id: UUID | None = None
) -> dict:
    """Insert a long_sources row at status='uploading'. The caller has
    already pushed the mp4 to Supabase Storage and is supplying its URL."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO long_sources (title, source_url, status)
               VALUES ($1, $2, 'uploading') RETURNING *""",
            title[:200], source_url,
        )
    return _row(row)


async def create_source_placeholder(
    *, title: str, tenant_id: UUID | None = None,
    drive_file_id: str = "",
) -> dict:
    """Create a long_sources row with no source_url yet — caller will
    fill it in via a background task after ingest. Used by the async
    drive-import flow so the HTTP request returns instantly.

    When drive_file_id is set, the row carries it on the dedicated
    column. The Drive download then happens in the background worker
    and the file is NEVER uploaded to Supabase Storage — we keep Drive
    as the canonical store and re-fetch when needed. Skips the
    HTTP-413 size-cap class entirely for any video Drive will hold
    (which is anything; Drive doesn't cap individual file size).

    source_url stays at the 'pending://' sentinel for Drive sources;
    the cut step reads drive_file_id and refetches.
    """
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO long_sources
                 (title, source_url, status, drive_file_id)
               VALUES ($1, 'pending://', 'uploading', $2)
               RETURNING *""",
            title[:200], drive_file_id or None,
        )
    return _row(row)


async def set_source_url(
    source_id: UUID, source_url: str, tenant_id: UUID | None = None,
) -> None:
    async with acquire(tenant_id) as conn:
        await _set(conn, source_id, source_url=source_url)


async def fetch_from_drive_then_ingest(
    source_id: UUID,
    drive_file_id: str,
    filename: str,
    tenant_id: UUID | None = None,
) -> None:
    """End-to-end background worker for Drive imports.

    Drive-as-source-of-truth: stream the file to /tmp, run audio
    extract + Whisper + LLM candidate selection all against the local
    file in ONE pass, then delete the temp file. The full video stays
    in Drive — never uploaded to Supabase, never double-handled.

    State transitions:
      uploading  → fetching the file from Drive
      transcribing → ffmpeg audio extract + Whisper word stamps
      analyzing  → LLM finds reel candidates
      ready

    Sets status='failed' on any error so the user sees the stage at
    which it broke (not just a stuck row).
    """
    import tempfile
    from pathlib import Path as _P

    from .drive import fetch_drive_file_to_path

    with tempfile.TemporaryDirectory() as td:
        local_path = f"{td}/{filename}"
        try:
            await fetch_drive_file_to_path(drive_file_id, local_path)
        except Exception as e:  # noqa: BLE001
            return await _fail(
                source_id, f"Drive download failed: {e}", tenant_id,
            )
        if not _P(local_path).exists() or _P(local_path).stat().st_size == 0:
            return await _fail(
                source_id, "Drive returned empty file", tenant_id,
            )
        # Run audio extract + Whisper + LLM against the SAME local
        # file we just downloaded — no Supabase round-trip, no
        # double-download.
        await _process_local_video(source_id, local_path, tenant_id)


async def _process_local_video(
    source_id: UUID,
    video_path: str,
    tenant_id: UUID | None = None,
) -> None:
    """Audio extract + chunked Whisper + LLM candidate selection
    against a video file already on local disk. Shared between
    fetch_from_drive_then_ingest (where the file came from Drive) and
    ingest_source (where it was downloaded from source_url).

    Persists the extracted audio mp3 to Supabase Storage so the LLM
    prompt LLM doesn't redo the extract on re-analyze; the original
    video stays in Drive or wherever it lives.
    """
    import tempfile

    from .audio_trim import extract_audio_lowbit, probe_duration

    async with acquire(tenant_id) as conn:
        await _set(conn, source_id, status="transcribing")

    with tempfile.TemporaryDirectory() as td:
        audio_path = f"{td}/audio.mp3"
        chunk_dir = f"{td}/chunks"

        if not await extract_audio_lowbit(video_path, audio_path, _LOWBIT_BITRATE):
            return await _fail(
                source_id, "ffmpeg audio extract failed", tenant_id,
            )

        tid_str = str(tenant_id or settings.default_tenant_id)
        try:
            audio_bytes = Path(audio_path).read_bytes()
            persisted_audio, _ = await asyncio.to_thread(
                media_storage().save,
                tid_str, audio_bytes,
                f"long-audio-{uuid.uuid4().hex[:8]}.mp3",
            )
        except Exception:  # noqa: BLE001
            persisted_audio = ""

        full_text, words, duration_s = await transcribe_long(
            audio_path, chunk_dir,
        )
        if not full_text:
            return await _fail(
                source_id, "Whisper returned no transcript", tenant_id,
            )

    async with acquire(tenant_id) as conn:
        await _set(
            conn, source_id,
            status="analyzing",
            audio_url=persisted_audio,
            duration_s=duration_s or await probe_duration(video_path),
            full_text=full_text[:200_000],
            words=json.dumps([
                {"w": w.word, "t": round(w.start, 3), "e": round(w.end, 3)}
                for w in words
            ]),
        )

    candidates = await find_candidates(
        full_text=full_text, words=words, duration_s=duration_s,
    )
    await save_candidates(source_id, candidates, tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(conn, source_id, status="ready", error=None)


async def _set(conn, source_id: UUID, **cols) -> None:
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))
    await conn.execute(
        f"UPDATE long_sources SET {sets}, updated_at = now() "
        f"WHERE id = $1",
        source_id, *cols.values(),
    )


async def _fail(source_id: UUID, msg: str, tenant_id: UUID | None = None) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE long_sources SET status='failed', error=$2, "
            "updated_at=now() WHERE id=$1",
            source_id, msg[:500],
        )


# ── chunked transcription ─────────────────────────────────────────────


@dataclass
class _ChunkResult:
    text: str
    words: list[TranscribedWord]
    duration: float


async def _transcribe_one_chunk(
    path: str, time_offset: float,
) -> _ChunkResult:
    """Whisper one chunk and shift its word timestamps by the chunk's
    position in the source. Returns empty on failure so the caller can
    decide whether to retry / fail the source."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return _ChunkResult("", [], 0.0)
    if len(data) > WHISPER_MAX_BYTES:
        return _ChunkResult("", [], 0.0)
    try:
        tr = await transcribe_words(Path(path).name, data)
    except Exception:  # noqa: BLE001
        return _ChunkResult("", [], 0.0)
    shifted = [
        TranscribedWord(
            word=w.word,
            start=w.start + time_offset,
            end=w.end + time_offset,
        )
        for w in tr.words
    ]
    return _ChunkResult(text=tr.text, words=shifted, duration=tr.duration)


async def transcribe_long(
    audio_path: str, work_dir: str, *, chunk_seconds: int = _CHUNK_SECONDS,
) -> tuple[str, list[TranscribedWord], float]:
    """Transcribe a long audio file by splitting into chunks, calling
    Whisper on each, and reassembling with time offsets.

    Returns (full_text, words, total_duration_s). Empty tuple values
    on full failure — caller marks the source row as failed."""
    total_duration = await probe_duration(audio_path)
    if total_duration <= 0:
        return "", [], 0.0

    # If the whole file fits under Whisper's cap, do it in one shot —
    # no chunking overhead. 60 min at 32 kbps is ~14 MB so this is the
    # usual path; chunking is the fallback for unusually long or
    # higher-quality sources.
    size = Path(audio_path).stat().st_size
    if size <= WHISPER_MAX_BYTES:
        r = await _transcribe_one_chunk(audio_path, time_offset=0.0)
        return r.text, r.words, total_duration

    chunks = await split_audio_chunks(audio_path, work_dir, chunk_seconds)
    if not chunks:
        return "", [], total_duration

    full_text_parts: list[str] = []
    all_words: list[TranscribedWord] = []
    for i, chunk_path in enumerate(chunks):
        offset = i * chunk_seconds
        r = await _transcribe_one_chunk(chunk_path, time_offset=offset)
        if r.text:
            full_text_parts.append(r.text)
        all_words.extend(r.words)
    return " ".join(full_text_parts), all_words, total_duration


# ── LLM candidate selection ───────────────────────────────────────────


_CANDIDATE_SYSTEM = """You are a short-form Reels editor reading the
transcript of a long-form podcast or interview. Your job is to find the
3-5 BEST 30-45 second clips inside this transcript that would work as
STANDALONE Instagram / TikTok Reels.

A good candidate has:
  * A strong HOOK in the first 1-2 seconds — a question, a claim, a
    surprising statement, the start of a story. Something that makes
    a scroller stop.
  * A complete arc inside 30-45 seconds — the hook leads to an
    insight, a punchline, a reveal, or a memorable line. Not just a
    fragment of a bigger conversation.
  * Either the hero answering an engaging question, or the hero
    delivering a punchy take / personal story. The clip stands on
    its own without the rest of the podcast for context.
  * Conversational energy — momentum, specificity, emotional weight.
    Skip "let me think about that…" preambles and meandering setup.

Avoid:
  * Cold opens that need the previous 2 minutes to make sense.
  * Banter / pleasantries / "thanks for having me".
  * Long pauses or filler-heavy stretches.
  * Anything <25s or >50s — those are different formats.

For each candidate, return:
  * start_s, end_s — decimal seconds into the source.
  * hook_quote   — the literal opening line (≤ 80 chars) — the first
                   sentence the viewer hears.
  * summary      — one sentence describing what's in this clip and
                   why it works as a Reel (≤ 140 chars).
  * score        — 1-10 confidence that this is a STRONG standalone
                   Reel. 9-10 = obvious banger; 5-6 = decent but not
                   the strongest; <5 = don't return it.

Return STRICT JSON:
{"candidates": [{"start_s": float, "end_s": float, "hook_quote": str,
                 "summary": str, "score": int}, ...]}

Exactly 3-5 entries. Highest-score-first.
"""


async def find_candidates(
    *, full_text: str, words: list[TranscribedWord], duration_s: float,
) -> list[dict]:
    """Send the transcript + word timestamps to the LLM, return the
    list of candidate dicts. Honest fallback: empty list on LLM
    failure — the source row goes to status='ready' but the user
    sees no candidates and is prompted to retry."""
    if not full_text or duration_s <= 30:
        return []
    # Word timestamps are how the LLM grounds its start_s/end_s in real
    # transcript time. We compact to {t: float, w: str} so the call
    # fits comfortably in the context window — a 60 min podcast can
    # easily run to ~8k words.
    tokens = [{"t": round(w.start, 2), "w": w.word} for w in words]
    payload = {
        "duration_s": round(duration_s, 1),
        "transcript_text": full_text[:60000],
        "word_count": len(words),
        "tokens": tokens,
    }
    try:
        out = await get_llm().complete_json(
            system=_CANDIDATE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=2200, temperature=0.3,
        )
    except Exception:  # noqa: BLE001
        return []
    raw = out.get("candidates") or []
    cleaned: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["start_s"])
            end = float(entry["end_s"])
            score = int(entry.get("score", 0))
        except (TypeError, ValueError, KeyError):
            continue
        dur = end - start
        # Sanity-clamp: refuse anything outside the 25-50s window so we
        # don't store garbage rows from a hallucinated response.
        if not 25.0 <= dur <= 50.0:
            continue
        if start < 0 or end > duration_s + 0.5:
            continue
        cleaned.append({
            "start_s": round(start, 2),
            "end_s": round(end, 2),
            "hook_quote": str(entry.get("hook_quote") or "")[:200],
            "summary": str(entry.get("summary") or "")[:280],
            "score": max(1, min(10, score)),
        })
    cleaned.sort(key=lambda c: c["score"], reverse=True)
    return cleaned[:5]


async def save_candidates(
    source_id: UUID, candidates: list[dict],
    tenant_id: UUID | None = None,
) -> None:
    if not candidates:
        return
    async with acquire(tenant_id) as conn:
        for c in candidates:
            await conn.execute(
                """INSERT INTO reel_candidates
                     (source_id, start_s, end_s, hook_quote, summary, score)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                source_id, c["start_s"], c["end_s"],
                c["hook_quote"], c["summary"], c["score"],
            )


# ── async ingest worker ───────────────────────────────────────────────


async def ingest_source(source_id: UUID, tenant_id: UUID | None = None) -> None:
    """Move a source through transcribing → analyzing → ready.

    Kicked from /long-form/upload as a BackgroundTask after the user
    uploaded a file (Drive sources go through fetch_from_drive_then_
    ingest which uses Drive-as-source-of-truth).

    For non-Drive sources: download source_url to a temp file, then
    hand off to _process_local_video for the shared audio + Whisper
    + LLM path. Each stage is a separate connection so a 60-min
    Whisper poll doesn't starve the DB pool.
    """
    import httpx

    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM long_sources WHERE id = $1", source_id,
        )
        if row is None:
            return
        if row["status"] in ("ready", "failed"):
            return
        source_url = row["source_url"]
        drive_file_id = row.get("drive_file_id") if hasattr(row, "get") else row["drive_file_id"]

    # Drive sources land here only via a re-analyze on an already-
    # ingested row. Refetch from Drive in that case rather than from
    # the (non-existent) Supabase URL.
    if drive_file_id:
        from .drive import fetch_drive_file_to_path
        with tempfile.TemporaryDirectory() as td:
            local_path = f"{td}/source.mp4"
            try:
                await fetch_drive_file_to_path(drive_file_id, local_path)
            except Exception as e:  # noqa: BLE001
                return await _fail(
                    source_id, f"Drive re-fetch failed: {e}", tenant_id,
                )
            return await _process_local_video(source_id, local_path, tenant_id)

    if not source_url or not source_url.startswith("http"):
        return await _fail(source_id, "source_url is not a real URL", tenant_id)

    with tempfile.TemporaryDirectory() as td:
        src_path = f"{td}/source.mp4"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(900.0, connect=15.0),
            ) as c:
                async with c.stream("GET", source_url) as r:
                    r.raise_for_status()
                    with open(src_path, "wb") as fh:
                        async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
        except Exception as e:  # noqa: BLE001
            return await _fail(
                source_id, f"could not download source: {e}", tenant_id,
            )
        await _process_local_video(source_id, src_path, tenant_id)


# ── reads ─────────────────────────────────────────────────────────────


async def list_sources(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, title, source_url, audio_url, duration_s, status,
                      error, drive_file_id, created_at, updated_at
               FROM long_sources ORDER BY created_at DESC LIMIT 50"""
        )
    return [_row(r) for r in rows]


async def get_source_with_candidates(
    source_id: UUID, tenant_id: UUID | None = None
) -> dict | None:
    async with acquire(tenant_id) as conn:
        src = await conn.fetchrow(
            "SELECT * FROM long_sources WHERE id = $1", source_id,
        )
        if src is None:
            return None
        cands = await conn.fetch(
            """SELECT * FROM reel_candidates
               WHERE source_id = $1 AND dismissed = false
               ORDER BY score DESC, start_s ASC""",
            source_id,
        )
    src_d = _row(src)
    src_d["candidates"] = [_row(c) for c in cands]
    return src_d


async def get_candidate(
    candidate_id: UUID, tenant_id: UUID | None = None
) -> dict | None:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT * FROM reel_candidates WHERE id = $1", candidate_id,
        )
    return _row(r) if r else None


async def link_candidate_to_production(
    candidate_id: UUID, production_id: UUID,
    tenant_id: UUID | None = None,
) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE reel_candidates SET production_id = $2 WHERE id = $1",
            candidate_id, production_id,
        )


async def dismiss_candidate(
    candidate_id: UUID, tenant_id: UUID | None = None,
) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE reel_candidates SET dismissed = true WHERE id = $1",
            candidate_id,
        )


async def reap_orphaned_sources(tenant_id: UUID | None = None) -> int:
    """Flip in-flight long_sources rows to 'failed' on process restart.
    A 1.4 GB Drive import takes 20+ minutes; if the dev server reloads
    mid-ingest the background task dies but the row stays at status
    'uploading' / 'transcribing' / 'analyzing' forever — looks alive in
    the UI, never moves. Reap them at startup the same way the
    autopilot does its own runs."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """UPDATE long_sources
                  SET status = 'failed',
                      error  = 'interrupted — server restarted before '
                               'ingest finished',
                      updated_at = now()
                WHERE status IN ('uploading', 'transcribing', 'analyzing')
                RETURNING id"""
        )
    return len(rows)


__all__ = [
    "create_source", "create_source_placeholder", "set_source_url",
    "ingest_source", "fetch_from_drive_then_ingest",
    "list_sources", "get_source_with_candidates", "get_candidate",
    "link_candidate_to_production", "dismiss_candidate",
    "find_candidates", "transcribe_long", "reap_orphaned_sources",
]
