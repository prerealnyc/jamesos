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
import math
import os
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
# Hard cap on a source video, checked before download. The big-file temp dir
# is the mounted volume (BIG_FILE_TMP=/data, ~50 GB); cap under that to leave
# room for ffmpeg output.
_MAX_SOURCE_BYTES = 40 * 1024**3   # 40 GB


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

    from .drive import big_file_tmp_dir, drive_file_size, fetch_drive_file_to_path

    # Reject a too-big import BEFORE streaming gigabytes onto disk. The temp
    # files live on the mounted volume (BIG_FILE_TMP, e.g. /data, ~50 GB), so
    # cap a bit under that to leave headroom for ffmpeg output.
    size = await drive_file_size(drive_file_id)
    if size and size > _MAX_SOURCE_BYTES:
        return await _fail(
            source_id,
            f"Video is {size / 1024**3:.1f} GB — over the "
            f"{_MAX_SOURCE_BYTES // 1024**3} GB import limit.",
            tenant_id,
        )

    with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
        # basename only: a Drive object name can contain '/' or '..' and would
        # otherwise escape the temp dir (arbitrary-write path traversal).
        local_path = os.path.join(td, os.path.basename(filename) or "source")
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


import re as _re

# Watch, shorts, live, embed, youtu.be — the shapes a user might paste.
_YOUTUBE_RE = _re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?|shorts/|live/|embed/|v/)|youtu\.be/)",
    _re.I,
)


def is_youtube_url(url: str) -> bool:
    return bool(_YOUTUBE_RE.match((url or "").strip()))


async def _apify_youtube_resolve(youtube_url: str) -> tuple[str, str, int]:
    """Run the Apify YouTube-download actor and return
    (download_url_with_token, title, size_bytes).

    Apify's residential proxy fetches the video where a datacenter yt-dlp is
    IP-blocked. Uses an async run + poll (not run-sync) so a long podcast download
    isn't capped by the ~280s run-sync limit. The actor drops the mp4 into its run
    key-value store and reports a `downloadUrl` to it; that URL needs the API token
    appended to authorize the fetch.
    """
    import httpx

    key = (settings.apify_api_key or "").strip()
    if not key:
        raise RuntimeError("Apify is not configured (APIFY_API_KEY) — can't import from YouTube")
    actor = (settings.youtube_download_actor or "memo23~youtube-video-downloader").strip()
    base = "https://api.apify.com/v2"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as c:
        r = await c.post(
            f"{base}/acts/{actor}/runs",
            params={"token": key},
            json={"videoUrls": [youtube_url]},
        )
        r.raise_for_status()
        run = r.json().get("data") or {}
        run_id = run.get("id")
        if not run_id:
            raise RuntimeError("Apify did not start a run")
        status = str(run.get("status") or "")
        waited = 0.0
        deadline = 20 * 60  # a long download can take minutes; cap so we never hang
        while status in ("", "READY", "RUNNING") and waited < deadline:
            await asyncio.sleep(5)
            waited += 5
            g = await c.get(f"{base}/actor-runs/{run_id}", params={"token": key})
            g.raise_for_status()
            status = str((g.json().get("data") or {}).get("status") or "")
        if status != "SUCCEEDED":
            raise RuntimeError(f"Apify download did not complete (status: {status or 'timed out'})")
        d = await c.get(f"{base}/actor-runs/{run_id}/dataset/items", params={"token": key})
        d.raise_for_status()
        items = d.json()
    if not items:
        raise RuntimeError("Apify returned no video for that YouTube URL")
    it = items[0] if isinstance(items, list) else items
    download = ""
    for field in ("downloadUrl", "download_url", "downloadable_video_link", "mediaUrl", "videoUrl"):
        v = it.get(field)
        if isinstance(v, str) and v.startswith("http"):
            download = v
            break
    if not download:
        raise RuntimeError("Apify item had no downloadable video URL")
    sep = "&" if "?" in download else "?"
    download_auth = f"{download}{sep}token={key}"
    title = str(it.get("title") or "").strip()
    try:
        size = int(it.get("fileSizeBytes") or 0)
    except (TypeError, ValueError):
        size = 0
    return download_auth, title, size


async def fetch_from_youtube_then_ingest(
    source_id: UUID,
    youtube_url: str,
    tenant_id: UUID | None = None,
) -> None:
    """Background worker for YouTube imports.

    Resolve + download the video through Apify (residential proxy — not IP-blocked
    like a server-side yt-dlp), stream it to /tmp, persist it to Supabase for a
    durable re-fetch at render time (parity with uploads), then run the shared
    audio + Whisper + LLM candidate pass. Sets status='failed' with the stage on
    any error so the user sees where it broke.
    """
    from pathlib import Path as _P

    from .drive import big_file_tmp_dir

    # 'uploading' is this table's word for "getting the file into our hands" —
    # the Drive worker stays on it while it streams, too. It used to say
    # "downloading", which reads better and is not one of the five statuses the
    # column allows, so EVERY YouTube import died on this line: a CheckViolation
    # thrown before the try below, leaving the row at 'uploading' with no error
    # on it, forever. Three of skelon's sat like that on 2026-09-18.
    try:
        async with acquire(tenant_id) as conn:
            await _set(conn, source_id, status="uploading")
    except Exception as e:  # noqa: BLE001
        return await _fail(source_id, f"could not start the import: {e}", tenant_id)

    try:
        download_url, resolved_title, size = await _apify_youtube_resolve(youtube_url)
    except Exception as e:  # noqa: BLE001
        return await _fail(source_id, f"YouTube fetch failed: {e}", tenant_id)

    if size and size > _MAX_SOURCE_BYTES:
        return await _fail(
            source_id,
            f"Video is {size / 1024**3:.1f} GB — over the "
            f"{_MAX_SOURCE_BYTES // 1024**3} GB import limit.",
            tenant_id,
        )
    if resolved_title:
        async with acquire(tenant_id) as conn:
            await _set(conn, source_id, title=resolved_title[:200])

    with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
        local_path = os.path.join(td, "youtube-source.mp4")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=15.0)) as c:
                async with c.stream("GET", download_url) as r:
                    r.raise_for_status()
                    with open(local_path, "wb") as fh:
                        async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
        except Exception as e:  # noqa: BLE001
            return await _fail(source_id, f"YouTube download failed: {e}", tenant_id)
        if not _P(local_path).exists() or _P(local_path).stat().st_size == 0:
            return await _fail(source_id, "YouTube returned an empty file", tenant_id)

        # Persist to Supabase so a later reel-render can re-fetch the source (parity
        # with the upload path). Best-effort: even if it fails we still process the
        # file we already have on disk.
        try:
            tenant = str(tenant_id or settings.default_tenant_id)
            served_uri, _ = await asyncio.to_thread(
                media_storage().save_from_path, tenant, local_path, "youtube-source.mp4",
            )
            await set_source_url(source_id, served_uri, tenant_id)
        except Exception:  # noqa: BLE001
            pass

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
        junk = hallucinated(full_text, duration_s or 0.0)
        if junk:
            return await _fail(source_id, junk, tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(
            conn, source_id,
            status="analyzing",
            audio_url=persisted_audio,
            duration_s=duration_s or await probe_duration(video_path),
            full_text=full_text[:200_000],
            words=json.dumps([
                {"w": w.word, "t": round(w.start, 3), "e": round(w.end, 3),
                 **({"sp": w.speaker} if w.speaker else {})}
                for w in words
            ]),
        )

    candidates = await find_candidates(
        full_text=full_text, words=words, duration_s=duration_s,
    )
    await save_candidates(source_id, candidates, tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(conn, source_id, status="ready", error=None)

    # The clipper works on its own: auto-render the top candidates into finished
    # reels (approval queue) so nobody has to search + click Render themselves.
    try:
        await auto_clip_source(source_id, tenant_id)
    except Exception as e:  # noqa: BLE001 — auto-clip must never fail the ingest
        print(f"[auto-clip] skipped for {source_id}: {e}")

    # New footage changes what's buildable — refresh the topic suggestions.
    try:
        refresh_topics_detached(tenant_id)
    except Exception as e:  # noqa: BLE001
        print(f"[topics] refresh skipped for {source_id}: {e}")


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

    # Diarized path: when AssemblyAI is configured, transcribe the WHOLE audio
    # in one job with SPEAKER LABELS (no 25 MB chunking) so the cutter knows
    # who said what. Best-effort — fall back to Whisper on any failure.
    if (settings.assemblyai_api_key or "").strip():
        try:
            from .transcription import transcribe_assemblyai
            r = await transcribe_assemblyai(Path(audio_path).read_bytes())
            if r.words:
                return r.text, r.words, (r.duration or total_duration)
            print("[long_form] AssemblyAI returned no words; falling back to Whisper")
        except Exception as e:  # noqa: BLE001 — fall back to Whisper
            print(f"[long_form] AssemblyAI failed ({e}); falling back to Whisper")

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
transcript of a long-form podcast or interview. Your job is to find
the BEST 30-45 second clips inside this transcript that would work
as Instagram / TikTok Reels.

PICK GENEROUSLY — surface AS MANY strong standalone moments as exist,
not a fixed few. From any source over 5 minutes, find at least 5. From
a 30+ minute podcast, find 20-40. Real podcasts don't have all perfect
moments — your job is to surface every one that's RELATIVELY strong, not
only the perfect ones.

PRIORITISE WHAT KEEPS PEOPLE WATCHING + DRIVES COMMENTS. The strongest
reels are CONTROVERSIAL, contrarian, or emotionally charged — the moments
that make someone stop scrolling, react, and tag a friend:
  * A hot take / contrarian opinion that challenges conventional wisdom.
  * A controversial or taboo statement (politics, money, religion, status)
    said with conviction — the stuff that sparks debate in the comments.
  * Raw emotion — anger, passion, vulnerability, a blunt truth, profanity.
  * A surprising claim or shocking stat that makes you go "wait, what?"
  * A vivid story or reveal with a clear turn.

A great candidate has:
  * A HOOK in the first 1-2 seconds — a provocative question or claim that
    creates an open loop you NEED resolved.
  * A self-contained idea in 30-60 seconds that lands without the rest of
    the podcast.
  * Strong point of view — the more debate-worthy / polarising, the better.

A decent candidate (still worth picking) has a reasonable hook and a
complete thought a creator could caption and post.

Avoid only:
  * "Thanks for having me" / introductions / outros.
  * Long stretches of "yeah, mm-hmm" backchanneling.
  * Anything that's literally <25s or >60s of usable content.

SPEAKERS: when the transcript is diarized it is broken into turns marked
`[12.3s SPEAKER A] …`. Each clip must capture ONE speaker's point. END the clip
on that speaker's OWN closing line — never let end_s spill into the NEXT
speaker's turn (the other person's reply, question-back, or "so with the…"
follow-up belongs to a DIFFERENT clip). A reply from the other speaker is the
single most common way a clip drifts; cut before it. (Short "yeah / right"
backchannels inside one person's turn are fine to keep.)

For each candidate return:
  * start_s — the EXACT second the hook's FIRST WORD is spoken. The clip MUST
    open ON the hook — no preamble, no "so", no throat-clearing, no setup
    sentence before it. If there's a lead-in, skip it and start on the hook
    word (the 3-second rule: the first line has to grab the scroller).
  * end_s — decimal seconds at the END of the strongest CLOSING line: the
    punchline / the line that LANDS the point (often a question or a hard
    statement). STOP there. Do NOT include the next sentence if it starts a
    NEW topic, a tangent, trails off ("so with the…", "anyway…", "and the
    other thing…"), OR is the NEXT SPEAKER talking — a tight clip that ENDS on
    the point outperforms a longer one that drifts. Aim for a 30-60s window,
    but a clean 32s ending beats a padded 50s one.
  * hook_quote   — the literal opening line (≤ 80 chars).
  * summary      — one sentence describing what's in this clip and
                   why it works as a Reel (≤ 140 chars).
  * score        — 1-10, weighted toward ENGAGEMENT: 9-10 = controversial /
                   highly emotional / debate-sparking banger; 6-7 = strong
                   opinion or story; 4-5 = decent fallback; <4 = skip.
                   RETURN scores 4 and up — don't self-censor; the user can
                   dismiss weak ones.

Return STRICT JSON:
{"candidates": [{"start_s": float, "end_s": float, "hook_quote": str,
                 "summary": str, "score": int}, ...]}

Return as MANY entries as the source genuinely supports (a long podcast
can be 20-40). NEVER return an empty array — pick the relatively best
moments even if nothing is perfect. Highest-score-first.
"""


# Second-pass fallback: when the strict picker returns zero, run a
# loosened pass asking for "anything usable". This catches the case
# where the LLM was too strict for a real (imperfect) podcast.
_CANDIDATE_SYSTEM_LOOSE = """You are a short-form Reels editor. The
strict pass found nothing — that almost always means you were too
picky.

This time, pick the best 30-60-second moments from this transcript (as
many as exist), even if none of them are perfect. Every podcast has
quotable moments; surface them. Use your judgement — a moment that
expresses a real opinion or tells a real micro-story is enough.

Avoid only: backchannels, pleasantries, hello/goodbye, dead air.

Return STRICT JSON in the same shape:
{"candidates": [{"start_s": float, "end_s": float, "hook_quote": str,
                 "summary": str, "score": int}, ...]}

5-10 entries minimum. Highest-score-first.
"""


# Window scan: a single LLM pass over a 50-min transcript only surfaces its
# top ~10 picks. To harvest the MAX clips across the WHOLE video we scan in
# overlapping windows and pick from each, then de-dupe.
_SCAN_WINDOW_S = 480.0   # 8-min windows
_SCAN_OVERLAP_S = 60.0   # so a moment on a boundary isn't missed
_MAX_CANDIDATES = 40


def _diarized_text(words: list[TranscribedWord]) -> str:
    """Transcript with `[SPEAKER A]` turn markers + start-time stamps, so the
    LLM can SEE where one person stops and the next begins and keep each clip
    inside a single speaker's point. Empty string when not diarized."""
    if not any(w.speaker for w in words):
        return ""
    lines: list[str] = []
    cur: str | None = None
    buf: list[str] = []
    t0 = 0.0
    for w in words:
        tok = (w.word or "").strip()
        if not tok:
            continue
        if w.speaker != cur:
            if buf:
                lines.append(f"[{t0:.1f}s SPEAKER {cur or '?'}] " + " ".join(buf))
            cur, buf, t0 = w.speaker, [tok], w.start
        else:
            buf.append(tok)
    if buf:
        lines.append(f"[{t0:.1f}s SPEAKER {cur or '?'}] " + " ".join(buf))
    return "\n".join(lines)


def _payload_for(words: list[TranscribedWord], full_text: str, duration_s: float) -> dict:
    """Compact {t, w} token payload the LLM grounds its start_s/end_s on.
    When the transcript is diarized, the speaker label rides on each token and
    a turn-marked transcript is supplied so the LLM keeps clips single-speaker."""
    diar = _diarized_text(words)
    return {
        "duration_s": round(duration_s, 1),
        "diarized": bool(diar),
        # Prefer the turn-marked transcript when we have speakers; it's what
        # lets the LLM end a clip on the right person's closing line.
        "transcript_text": (diar or full_text or " ".join(w.word for w in words))[:60000],
        "word_count": len(words),
        "tokens": [
            {"t": round(w.start, 2), "w": w.word,
             **({"sp": w.speaker} if w.speaker else {})}
            for w in words
        ],
    }


def _dedupe_candidates(cands: list[dict]) -> list[dict]:
    """Highest-score-first, dropping any window that overlaps an already-kept
    one by >50% of the shorter clip (windows overlap, so neighbours collide)."""
    out: list[dict] = []
    for c in sorted(cands, key=lambda x: x.get("score", 0), reverse=True):
        s, e = c["start_s"], c["end_s"]
        if any(
            max(0.0, min(e, k["end_s"]) - max(s, k["start_s"]))
            > 0.5 * min(e - s, k["end_s"] - k["start_s"])
            for k in out
        ):
            continue
        out.append(c)
    return out


# Whisper invents speech when there is none. Fed a silent drone shot or a
# music-only promo it does not return nothing — it returns something, and the
# something comes from what it was trained on: "Thanks for watching!", Chinese
# subscribe-spam, or pages of Khmer. On 2026-09-18 three of Turtleback's videos
# transcribed as Khmer and one as "请不吝点赞 订阅 转发", and reels were cut at
# moments chosen from those words, captioned with them, and put in front of the
# owner as their own content.
#
# Real speech in these videos runs 1.4 to 2.1 words a second. Every hallucinated
# transcript measured 0.02 to 0.73. The gap is not close, so the test is simply
# whether anybody was talking.
MIN_WORDS_PER_SECOND = 0.8
# Below this a video is too short for the rate to mean anything.
_RATE_FLOOR_S = 8.0
# Whole transcripts Whisper emits for silence, near-verbatim.
_EMPTY_HALLUCINATIONS = {
    "thank you", "thanks", "thanks.", "thank you.", "you", "bye", "bye.",
    "thanks for watching", "thanks for watching!", "thank you for watching",
    "thank you for watching!", "please subscribe", "subscribe", ".", "!",
}


def hallucinated(full_text: str, duration_s: float) -> str:
    """Why this transcript cannot be cut on — or "" when it can.

    Not a quality judgement: a transcript this sparse means nobody was talking,
    and a reel cut from words nobody said is worse than no reel at all."""
    text = " ".join((full_text or "").split())
    if not text:
        return "Whisper returned no transcript"
    if text.strip().lower().strip("\"'") in _EMPTY_HALLUCINATIONS:
        return ("no speech in this video — the transcript is what Whisper writes "
                "for silence")
    if duration_s >= _RATE_FLOOR_S:
        rate = len(text.split()) / duration_s
        if rate < MIN_WORDS_PER_SECOND:
            return (f"no usable speech — {rate:.2f} words a second over "
                    f"{duration_s:.0f}s reads as music or silence, not talking")
    return ""


async def find_candidates(
    *, full_text: str, words: list[TranscribedWord], duration_s: float,
) -> list[dict]:
    """Pick reel candidates across the WHOLE source. Long sources are scanned
    in overlapping windows (so we don't just get the LLM's top-10 from one
    giant pass); short ones use a single pass. Honest fallback: empty list on
    LLM failure — the row goes to status='ready' and the user can retry."""
    if not full_text or duration_s <= 30:
        return []

    # ── Long source: scan in overlapping windows, pick from each, de-dupe ──
    if words and duration_s > _SCAN_WINDOW_S * 1.6:
        collected: list[dict] = []
        start = 0.0
        while start < duration_s:
            end = min(duration_s, start + _SCAN_WINDOW_S)
            seg = [w for w in words if start <= w.start < end]
            if len(seg) >= 5:
                collected.extend(await _llm_pick_candidates(
                    _CANDIDATE_SYSTEM,
                    _payload_for(seg, "", duration_s),
                    duration_s, words,
                ))
            if end >= duration_s:
                break
            start += _SCAN_WINDOW_S - _SCAN_OVERLAP_S
        deduped = _dedupe_candidates(collected)
        if deduped:
            print(f"[long_form] windowed scan: {len(collected)} raw → "
                  f"{len(deduped)} candidates across {duration_s / 60:.0f} min")
            return deduped[:_MAX_CANDIDATES]
        # windows found nothing → fall through to a single loose pass

    # ── Short source (or scan came up empty): single pass ──
    payload = _payload_for(words, full_text, duration_s)
    cleaned = await _llm_pick_candidates(_CANDIDATE_SYSTEM, payload, duration_s, words)
    if not cleaned and duration_s >= 120:
        cleaned = await _llm_pick_candidates(
            _CANDIDATE_SYSTEM_LOOSE, payload, duration_s, words,
        )
    if not cleaned:
        print(
            f"[long_form] zero candidates for source duration={duration_s:.1f}s "
            f"transcript={len(full_text)}c — picker prompt may need tuning"
        )
    return _dedupe_candidates(cleaned)[:_MAX_CANDIDATES]


# ── snapping clips to natural sentence / thought boundaries ───────────
#
# The LLM marks roughly where a clip should start and end, but its raw
# timestamps (and the fixed-window snap below) land on arbitrary seconds
# — which chops clips off mid-sentence. We snap the START to the top of a
# sentence (so the hook is clean) and the END to the close of a complete
# sentence or a clear spoken pause (so the speaker finishes their thought
# instead of being cut off). Word-level Whisper timestamps make this
# exact; with no timestamps we fall back to the old fixed-window snap.

_REEL_MIN_S = 28.0          # never ship a clip shorter than this (~30s floor)
_REEL_TARGET_S = 42.0       # the sweet spot we aim the end toward (30-45-60)
_REEL_HARD_MAX_S = 62.0     # allow stretching to finish a thought, up to ~60s
_PAUSE_GAP_S = 0.45         # silence between words that reads as a thought break
# When the transcript is diarized, how long a DIFFERENT speaker must hold the
# floor before we treat it as a real hand-off (so a clip won't trail into the
# next person). Short "yeah / mm-hmm" backchannels stay under this and don't cap.
_SPEAKER_TURN_MIN_S = 1.3
_SENTENCE_FINAL = ".!?…"


def _is_sentence_final(token: str) -> bool:
    """True if a word token closes a sentence (ignoring trailing quotes)."""
    t = (token or "").rstrip("\"'”’)]")
    return bool(t) and t[-1] in _SENTENCE_FINAL


def _thought_starts(words) -> list[float]:
    """Start times of words that begin a sentence or follow a clear pause."""
    out: list[float] = []
    for i, w in enumerate(words):
        if not (w.word or "").strip():
            continue
        if i == 0:
            out.append(w.start)
            continue
        prev = words[i - 1]
        if _is_sentence_final(prev.word) or (w.start - prev.end) >= _PAUSE_GAP_S:
            out.append(w.start)
    return out


def _thought_ends(words) -> list[tuple[float, bool]]:
    """(end_time, is_sentence) for every natural cut point: a word that
    closes a sentence (strong) or sits right before a clear pause (soft)."""
    out: list[tuple[float, bool]] = []
    n = len(words)
    for i, w in enumerate(words):
        if not (w.word or "").strip():
            continue
        strong = _is_sentence_final(w.word)
        gap = (words[i + 1].start - w.end) if i + 1 < n else 99.0
        if strong or gap >= _PAUSE_GAP_S:
            out.append((w.end, strong))
    return out


def _speaker_cap(words, start_s: float, hi: float) -> float:
    """Diarized clips should stay within ONE speaker's point. Find the first
    SUSTAINED hand-off to a different speaker after the clip starts and cap the
    end at the primary speaker's last word before it — so a clip never trails
    into the next person's sentence (the "...so with the 6-out-of-10 people"
    problem). No-op when the transcript carries no speaker labels.

    Short backchannels ("yeah", "right") from another voice DON'T cap: the
    other speaker must hold the floor for >= _SPEAKER_TURN_MIN_S to count."""
    seg = [w for w in words if start_s <= w.start <= hi and (w.word or "").strip()]
    labeled = [w for w in seg if w.speaker]
    if len(labeled) < 2:
        return hi                       # not diarized / single speaker → no cap
    primary = labeled[0].speaker
    last_primary_end = labeled[0].end
    run_start: float | None = None      # start of the current other-speaker run
    for w in labeled:
        if w.speaker == primary:
            last_primary_end = w.end
            run_start = None
            continue
        if run_start is None:
            run_start = w.start
        if (w.end - run_start) >= _SPEAKER_TURN_MIN_S:
            return last_primary_end      # real hand-off → end on primary's words
    return hi


def _finalize_window(start: float, end: float, words, duration_s: float):
    """Snap the LLM's rough [start, end] so the clip BEGINS at a sentence
    start and ENDS on a complete sentence / thought — targeting ~30-45s but
    never chopping a thought mid-word. Falls back to a fixed-window snap
    when there are no usable word timestamps."""
    ends = _thought_ends(words) if words else []

    # Fallback: no usable word timestamps → original behaviour (extend a
    # short clip to 30s around the anchor; trim a long one to 45s).
    if not ends:
        dur = end - start
        if dur < 30.0:
            slack = (30.0 - dur) / 2.0
            start = max(0.0, start - slack)
            end = min(duration_s, start + 30.0)
            if end - start < 30.0:
                start = max(0.0, end - 30.0)
        elif dur > 45.0:
            end = start + 45.0
        return round(start, 2), round(end, 2)

    starts = _thought_starts(words)

    # START → snap to a sentence/thought start near the anchor, but only a
    # SMALL correction. The LLM's start_s is already the hook; snapping BACK far
    # (the old ~6s) buried the hook behind setup and broke the 3-second rule.
    # Cap the backward snap at ~1s so the clip OPENS on the hook, and still allow
    # a small forward nudge onto the exact sentence top.
    s = max(0.0, start)
    near = [t for t in starts if -1.5 <= (start - t) <= 1.0]
    if near:
        s = max(0.0, min(near, key=lambda t: abs(t - start)))

    # END → land on the sentence boundary nearest WHERE THE LLM CHOSE TO END
    # (the LLM is the only layer with editor sense — it knows the punchline).
    # We only floor at the minimum length, so a short hook-only mark still gets
    # extended, but a clip the LLM ended on its closing beat is NOT dragged
    # toward a fixed duration (which used to pull in the next, off-topic line).
    lo = s + _REEL_MIN_S
    hi = min(s + _REEL_HARD_MAX_S, duration_s)
    # Diarization cap: never let the end cross into the next speaker's turn.
    # Floor at s+12 so a quick hand-off still yields a (short) clean clip
    # rather than a sliver; the re-validate step downstream drops true slivers.
    hi = max(s + 12.0, min(hi, _speaker_cap(words, s, hi)))
    target = min(max(end, s + _REEL_MIN_S), hi)
    fits = [(t, strong) for (t, strong) in ends if lo <= t <= hi]
    chosen = None
    if fits:
        strong_ends = [t for (t, st) in fits if st]
        pool = strong_ends or [t for (t, _st) in fits]
        # nearest to target; ties → the later (more complete) boundary
        chosen = min(pool, key=lambda t: (abs(t - target), -t))
    if chosen is None:
        # Window held no boundary — relax the minimum and take the nearest
        # SENTENCE end we can. A slightly short clip that ends cleanly beats
        # a full-length one chopped mid-thought.
        relaxed = [t for (t, st) in ends if st and (s + 12.0) <= t <= hi]
        if relaxed:
            chosen = min(relaxed, key=lambda t: abs(t - target))
    if chosen is None:
        # Still nothing usable — at least land on a word end so the cut
        # never falls mid-word.
        word_ends = [w.end for w in words if lo <= w.end <= hi]
        chosen = (
            min(word_ends, key=lambda t: abs(t - target))
            if word_ends else min(target, duration_s)
        )
    e = min(duration_s, chosen + 0.30)   # tiny tail so the last word breathes
    if e - s < _REEL_MIN_S:
        # The end hit the source's end before reaching the minimum length —
        # anchor the window to the TAIL by pulling the START back to a thought
        # start ~target before the end, so a near-end candidate still ships a
        # full clip instead of a sliver (mirrors the no-timestamp fallback).
        want = max(0.0, e - _REEL_TARGET_S)
        prior = [t for t in starts if t <= want + 2.0]
        s = min(prior, key=lambda t: abs(t - want)) if prior else want
        s = max(0.0, min(s, e - _REEL_MIN_S))
    return round(s, 2), round(e, 2)


async def _llm_pick_candidates(
    system: str, payload: dict, duration_s: float,
    words: list["TranscribedWord"] | None = None,
) -> list[dict]:
    """Single LLM call + parse + clamp. Shared by the strict and loose
    candidate passes. Returns up to 15 cleaned candidates sorted by
    descending score."""
    try:
        out = await get_llm().complete_json(
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=3000, temperature=0.4,
        )
    except Exception:  # noqa: BLE001
        return []
    raw = out.get("candidates") or []
    if not raw and out:
        # Some models return the array at the top level instead.
        if isinstance(out, list):
            raw = out
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
        # In practice, the LLM often marks the HOOK only (a 1-12 s
        # sentence) rather than a full 30-45 s arc, regardless of how
        # much the prompt insists. We snap any usable timestamp up to
        # a 30 s window centered on the LLM's mark, and trim windows
        # that are too long. Only reject if the timestamps are wildly
        # out of range (negative, past the source, or > 90 s — a sign
        # the LLM hallucinated).
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        if dur <= 0 or dur > 90.0:
            continue
        if start < 0 or end > duration_s + 0.5:
            continue
        # Snap the rough window to natural sentence / thought boundaries so
        # the clip starts clean and the speaker finishes their thought
        # rather than getting cut off mid-sentence.
        start, end = _finalize_window(start, end, words, duration_s)
        # Re-validate the RESHAPED window — never persist a sliver or an
        # out-of-range clip if the snapping degenerated near a source edge.
        if not (12.0 <= (end - start) and end <= duration_s + 0.5 and start >= 0):
            continue
        cleaned.append({
            "start_s": round(start, 2),
            "end_s": round(end, 2),
            "hook_quote": str(entry.get("hook_quote") or "")[:200],
            "summary": str(entry.get("summary") or "")[:280],
            "score": max(1, min(10, score)),
        })
    cleaned.sort(key=lambda c: c["score"], reverse=True)
    return cleaned


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
    from .drive import big_file_tmp_dir

    if drive_file_id:
        from .drive import fetch_drive_file_to_path
        with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
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

    with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
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
            """SELECT c.*, vp.status AS production_status
                 FROM reel_candidates c
                 LEFT JOIN video_productions vp ON vp.id = c.production_id
                WHERE c.source_id = $1 AND c.dismissed = false
                ORDER BY c.score DESC, c.start_s ASC""",
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


async def reanalyze_source(
    source_id: UUID, tenant_id: UUID | None = None,
) -> int:
    """Re-run the candidate picker on an already-ingested source —
    NO re-download, NO re-transcribe. Returns the number of new
    candidates inserted.

    Used after a picker prompt change so the user can refresh the
    tile grid without paying for another Whisper pass.
    """
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, full_text, words, duration_s FROM long_sources "
            "WHERE id = $1", source_id,
        )
        if row is None:
            return 0
    raw_words = row["words"]
    if isinstance(raw_words, str):
        raw_words = json.loads(raw_words)
    words_list = [
        TranscribedWord(
            word=str(w.get("word") or w.get("w") or ""),
            start=float(w.get("start") or w.get("t") or 0.0),
            # Stored words use "e" for the end timestamp; fall back to the
            # start so a missing end never collapses the word to t=0 (which
            # would blind the sentence/pause snapping).
            end=float(
                w.get("end") or w.get("e") or w.get("start") or w.get("t") or 0.0
            ),
            # Diarization label persisted as "sp"; absent on Whisper-era rows
            # (re-analyze then simply has no speaker cap — same as before).
            speaker=str(w.get("speaker") or w.get("sp") or ""),
        )
        for w in (raw_words or []) if isinstance(w, dict)
    ]
    new = await find_candidates(
        full_text=row["full_text"] or "",
        words=words_list,
        duration_s=float(row["duration_s"] or 0.0),
    )
    # Replace the un-rendered picks with the fresh batch so re-analyze yields a
    # clean set (no duplicate pile-up). Anything already rendered (production_id
    # set) is kept so its history/link survives.
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "DELETE FROM reel_candidates WHERE source_id = $1 AND production_id IS NULL",
            source_id,
        )
    await save_candidates(source_id, new, tenant_id)
    return len(new)


async def create_whole_source_candidate(
    source_id: UUID, tenant_id: UUID | None = None,
) -> dict | None:
    """Synthesize a candidate row covering the entire source.

    For short talking clips (1-2 min content already shaped for social),
    we don't need the LLM picker — the whole clip IS the reel. The
    /render-whole endpoint calls this, then hands the new candidate to
    the existing per-candidate render path so all the engaging-avatar
    treatment (captions, B-roll cutaways, music) applies unchanged.

    Idempotent-ish: if a 'whole' candidate already exists for this
    source (hook starts with the marker), reuse it.

    Returns the candidate row, or None if the source isn't ready or
    has no duration_s yet.
    """
    async with acquire(tenant_id) as conn:
        src = await conn.fetchrow(
            "SELECT duration_s, full_text, title FROM long_sources WHERE id = $1",
            source_id,
        )
        if src is None or not src["duration_s"] or src["duration_s"] <= 0:
            return None
        # Reuse existing whole-source candidate if there is one.
        existing = await conn.fetchrow(
            """SELECT * FROM reel_candidates
                WHERE source_id = $1
                  AND hook_quote LIKE '[WHOLE]%'
                  AND dismissed = false
                ORDER BY created_at DESC LIMIT 1""",
            source_id,
        )
        if existing is not None:
            return _row(existing)
        # First ~80 chars of the transcript stand in as a hook so the
        # downstream prompt isn't empty.
        ft = (src["full_text"] or "").strip().split("\n", 1)[0][:240]
        hook = f"[WHOLE] {ft}" if ft else f"[WHOLE] {src['title']}"
        summary = (src["title"] or "Talking clip")[:160]
        row = await conn.fetchrow(
            """INSERT INTO reel_candidates
                 (source_id, start_s, end_s, hook_quote, summary, score)
               VALUES ($1, 0, $2, $3, $4, 1.0)
               RETURNING *""",
            source_id, float(src["duration_s"]), hook, summary,
        )
    return _row(row)


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


async def get_source(source_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    """One source row (incl. its speaker_tags assignment), JSON-safe."""
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow("SELECT * FROM long_sources WHERE id=$1", source_id)
    if r is None:
        return None
    d = _row(r)
    st = d.get("speaker_tags")
    if isinstance(st, str):
        try:
            d["speaker_tags"] = json.loads(st)
        except Exception:  # noqa: BLE001
            d["speaker_tags"] = []
    elif not isinstance(st, list):
        d["speaker_tags"] = []
    return d


async def set_speaker_tags(
    source_id: UUID, tags: list[dict], tenant_id: UUID | None = None,
) -> bool:
    """Save the per-source speaker assignment ([{face_x,handle,subtitle}])."""
    clean = [
        {
            "face_x": float(t.get("face_x", 0.5)),
            "handle": str(t.get("handle") or "").strip(),
            "subtitle": str(t.get("subtitle") or "").strip(),
        }
        for t in (tags or [])
        if isinstance(t, dict) and (t.get("handle") or "").strip()
    ]
    async with acquire(tenant_id) as conn:
        st = await conn.execute(
            "UPDATE long_sources SET speaker_tags=$2::jsonb, updated_at=now() "
            "WHERE id=$1",
            source_id, json.dumps(clean),
        )
    return st.rsplit(" ", 1)[-1] != "0"


async def detect_speakers_for_source(
    source_id: UUID, tenant_id: UUID | None = None,
) -> list[dict]:
    """Download the source and enumerate its distinct on-camera speakers for the
    'who is this?' step. Returns [{face_x, label, preview_url}] (best-effort)."""
    src = await get_source(source_id, tenant_id)
    if not src:
        return []
    url = (src.get("source_url") or "").strip()
    if not url.startswith("http"):
        return []
    import httpx

    from .perception import detect_source_speakers
    with tempfile.TemporaryDirectory() as td:
        vp = f"{td}/src.mp4"
        cap = 220 * 1024 * 1024   # ~220MB — enough to enumerate the speakers
        try:
            async with httpx.AsyncClient(timeout=240.0, follow_redirects=True) as c:
                async with c.stream("GET", url) as resp:
                    resp.raise_for_status()
                    written = 0
                    with open(vp, "wb") as fh:
                        async for chunk in resp.aiter_bytes():
                            fh.write(chunk)
                            written += len(chunk)
                            if written >= cap:
                                break
        except Exception as e:  # noqa: BLE001
            print(f"[speaker-detect] source download failed: {e}")
            return []
        return await detect_source_speakers(vp, tenant_id)


_IN_PROGRESS = ("queued", "planning", "rendering_clips", "assembling")


def _candidate_state(production_id, production_status: str | None) -> str:
    """Map a candidate → its clip lifecycle state for the content library."""
    if not production_id:
        return "clippable"
    st = (production_status or "").lower()
    if st in _IN_PROGRESS:
        return "clipping"
    if st == "succeeded":
        return "clipped"
    return "clippable"   # failed/canceled/missing → re-clippable


async def content_library(tenant_id: UUID | None = None) -> dict:
    """The unified Content Library: every uploaded source with the clippable
    topic-reels found inside it + each one's live status (clippable / clipping /
    clipped). This is the 'what can be clipped, and what's been done' dashboard —
    the display layer over the auto-clipper."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT s.id AS source_id, s.title, s.status AS source_status,
                      s.duration_s, s.created_at,
                      c.id AS cand_id, c.hook_quote, c.summary, c.score,
                      c.start_s, c.end_s, c.production_id,
                      vp.status AS production_status, vp.final_url,
                      vp.review_status
                 FROM long_sources s
                 LEFT JOIN reel_candidates c
                        ON c.source_id = s.id AND c.dismissed = false
                 LEFT JOIN video_productions vp ON vp.id = c.production_id
                ORDER BY s.created_at DESC, c.score DESC NULLS LAST"""
        )
        trows = await conn.fetch(
            """SELECT t.id, t.title, t.hook, t.why, t.score, t.segments,
                      t.production_id,
                      vp.status AS production_status, vp.final_url
                 FROM clip_topics t
                 LEFT JOIN video_productions vp ON vp.id = t.production_id
                WHERE t.status <> 'dismissed'
                ORDER BY t.score DESC, t.created_at DESC"""
        )
    by_source: dict[str, dict] = {}
    counts = {"clippable": 0, "clipping": 0, "clipped": 0}
    for r in rows:
        sid = str(r["source_id"])
        src = by_source.get(sid)
        if src is None:
            src = by_source[sid] = {
                "id": sid,
                "title": r["title"] or "(untitled)",
                "status": r["source_status"],
                "duration_s": float(r["duration_s"] or 0),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "candidates": [],
            }
        if r["cand_id"] is None:
            continue   # source with no (non-dismissed) candidates yet
        state = _candidate_state(r["production_id"], r["production_status"])
        counts[state] = counts.get(state, 0) + 1
        src["candidates"].append({
            "id": str(r["cand_id"]),
            "hook_quote": r["hook_quote"] or "",
            "summary": r["summary"] or "",
            "score": int(r["score"] or 0),
            "start_s": float(r["start_s"] or 0),
            "end_s": float(r["end_s"] or 0),
            "state": state,
            "production_id": str(r["production_id"]) if r["production_id"] else None,
            "final_url": r["final_url"] if state == "clipped" else None,
            "review_status": r["review_status"],
        })
    sources = list(by_source.values())
    topics: list[dict] = []
    for t in trows:
        segs = t["segments"]
        if isinstance(segs, str):
            segs = json.loads(segs)
        tstate = _topic_state(t["production_id"], t["production_status"])
        topics.append({
            "id": str(t["id"]),
            "title": t["title"] or "",
            "hook": t["hook"] or "",
            "why": t["why"] or "",
            "score": int(t["score"] or 0),
            "segments": segs or [],
            "state": tstate,
            "production_id": str(t["production_id"]) if t["production_id"] else None,
            "final_url": t["final_url"] if tstate == "built" else None,
        })
    return {
        "summary": {
            "sources": len(sources),
            "clippable": counts["clippable"],
            "clipping": counts["clipping"],
            "clipped": counts["clipped"],
        },
        "sources": sources,
        "topics": topics,
        # Same key derivation as refresh_topics_detached, so the spinner
        # tracks exactly the passes that helper starts.
        "topics_mining": _mining_key(tenant_id) in _TOPIC_MINING,
    }


_AUTOCLIP_TASKS: set = set()   # strong refs so detached auto-clip renders aren't GC'd


async def auto_clip_source(
    source_id: UUID, tenant_id: UUID | None = None, top_n: int | None = None,
) -> int:
    """The clipper working ON ITS OWN: auto-render a source's top-N scored
    candidates into reels (they land in the approval queue) — no manual click.
    Skips candidates already linked to a production. Returns how many renders it
    kicked. Best-effort — never raises into the ingest flow."""
    from .config import settings
    if not settings.auto_clip_enabled:
        return 0
    n = settings.auto_clip_top_n if top_n is None else top_n
    if n <= 0:
        return 0
    src = await get_source_with_candidates(source_id, tenant_id)
    if not src:
        return 0
    cands = [c for c in (src.get("candidates") or []) if not c.get("production_id")]
    cands.sort(key=lambda c: (c.get("score") or 0), reverse=True)
    picks = cands[:n]
    if not picks:
        return 0

    import asyncio

    from .video_pipeline import run_production, start_production
    kicked = 0
    for cand in picks:
        try:
            payload = [{
                "source_id": str(cand.get("source_id") or source_id),
                "candidate_id": str(cand["id"]),
                "source_url": src.get("source_url") or "",
                "drive_file_id": src.get("drive_file_id") or "",
                "start_s": cand["start_s"], "end_s": cand["end_s"],
                "hook_quote": cand.get("hook_quote") or "",
                "summary": cand.get("summary") or "",
            }]
            prod = await start_production(
                (cand.get("hook_quote") or "")[:200],
                "instagram", "9:16",
                (cand.get("summary") or "Reel from long-form")[:120],
                payload, "long_form_reel",
                settings.auto_clip_caption_style or "",
                "",  # image_style
                broll_style=settings.auto_clip_broll_style or "",
                tenant_id=tenant_id,
            )
            await link_candidate_to_production(
                UUID(str(cand["id"])), UUID(str(prod["id"])),
            )
            task = asyncio.create_task(run_production(UUID(str(prod["id"])), tenant_id))
            _AUTOCLIP_TASKS.add(task)
            task.add_done_callback(_AUTOCLIP_TASKS.discard)
            kicked += 1
        except Exception as e:  # noqa: BLE001 — one bad clip can't block the rest
            print(f"[auto-clip] candidate {cand.get('id')}: {e}")
    if kicked:
        print(f"[auto-clip] source {source_id}: kicked {kicked} render(s)")
    return kicked


# ── topic suggestions: what should the clipper build next? ────────────
#
# Candidates are per-source. Topics sit ABOVE them: an LLM pass over every
# candidate across ALL footage groups the strongest moments into named,
# buildable reels — including multi-segment edits stitched across sources.
# They render in the Content Library as "here's what I can make for you".

_TOPIC_SYSTEM = """You are the content strategist for a short-form clipping
system. You are given every clip candidate the system has found across ALL
of the user's long-form footage (podcasts, interviews, talks). Each
candidate has an id, its source title, a hook quote, a summary, and an
engagement score 1-10.

Group them into TOPIC suggestions — the reels the system should build next.
A topic is a specific, punchy angle, not a category: "Why NYC landlords are
trapped by their own leases", never "Real estate".

Rules:
* 5-10 topics, strongest expected engagement first.
* Each topic cites 1-4 candidate ids as its segments. One GREAT segment is
  a valid topic. Use multiple segments ONLY when they genuinely build one
  narrative (setup → escalation → payoff) — order them for storytelling,
  not chronology. Segments MAY come from different footages.
* Never reuse the same candidate id in two topics.
* Prefer candidates not already clipped (already_clipped=false), but a
  brilliant already-clipped moment can anchor a NEW angle.
* Judge virality like a clipper: controversy, strong opinions, specific
  numbers, stories with stakes, contrarian takes, emotional moments.
* title — what the reel IS (≤ 70 chars, punchy internal label).
* hook — the literal opening line the reel should lead with (lift it from
  a segment's hook quote, lightly trimmed).
* why — one sentence on why this will perform (≤ 140 chars).
* score — 1-10 expected engagement.

Return STRICT JSON:
{"topics": [{"title": str, "hook": str, "why": str, "score": int,
             "segment_ids": [str, ...]}, ...]}
"""

_TOPIC_TASKS: set = set()        # strong refs so detached mining isn't GC'd
_TOPIC_MINING: set[str] = set()  # tenant ids with a mining pass in flight
_TOPIC_RERUN: set[str] = set()   # tenants that asked again mid-pass → run once more
_TOPIC_BUILDING: set[str] = set()  # topic ids with a build claim in flight


async def mine_clip_topics(tenant_id: UUID | None = None) -> int:
    """One LLM pass over every (non-dismissed) candidate across all ready
    sources → replace the current 'suggested' topics with a fresh ranked
    list. Topics already tied to a render (production_id) are kept."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT c.id, c.hook_quote, c.summary, c.score, c.start_s,
                      c.end_s, c.production_id,
                      s.id AS source_id, s.title AS source_title
                 FROM reel_candidates c
                 JOIN long_sources s ON s.id = c.source_id
                WHERE c.dismissed = false AND s.status = 'ready'
                ORDER BY c.score DESC, c.created_at DESC
                LIMIT 120"""
        )
    if not rows:
        # No live candidates → any remaining suggestions cite dead ids.
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "DELETE FROM clip_topics "
                "WHERE status = 'suggested' AND production_id IS NULL"
            )
        return 0
    by_id = {str(r["id"]): r for r in rows}
    payload = {"candidates": [
        {"id": str(r["id"]),
         "source": (r["source_title"] or "")[:120],
         "hook": (r["hook_quote"] or "")[:200],
         "summary": (r["summary"] or "")[:280],
         "score": int(r["score"] or 0),
         "dur_s": round(float(r["end_s"] or 0) - float(r["start_s"] or 0), 1),
         "already_clipped": bool(r["production_id"])}
        for r in rows
    ]}
    out = await get_llm().complete_json(
        system=_TOPIC_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload)}],
        max_tokens=2500, temperature=0.5,
    )
    raw = out.get("topics") if isinstance(out, dict) else out
    if not isinstance(raw, list):
        raw = []
    used_ids: set[str] = set()
    cleaned: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        seg_ids = [str(s) for s in (t.get("segment_ids") or [])
                   if str(s) in by_id and str(s) not in used_ids][:4]
        if not seg_ids or not (t.get("title") or "").strip():
            continue
        used_ids.update(seg_ids)
        segments = [{
            "candidate_id": sid,
            "source_id": str(by_id[sid]["source_id"]),
            "start_s": float(by_id[sid]["start_s"] or 0),
            "end_s": float(by_id[sid]["end_s"] or 0),
            "quote": (by_id[sid]["hook_quote"] or "")[:200],
            "source_title": (by_id[sid]["source_title"] or "")[:120],
        } for sid in seg_ids]
        try:
            score = max(1, min(10, int(t.get("score") or 5)))
        except (TypeError, ValueError):
            score = 5
        cleaned.append({
            "title": str(t["title"]).strip()[:140],
            "hook": str(t.get("hook") or "").strip()[:240],
            "why": str(t.get("why") or "").strip()[:280],
            "score": score,
            "segments": segments,
        })
        if len(cleaned) >= 10:
            break
    if not cleaned:
        # A garbage-but-parseable LLM response must not wipe the board —
        # keep whatever suggestions the user already has.
        print(f"[topics] LLM returned no usable topics from {len(rows)} "
              f"candidates — keeping existing suggestions")
        return 0
    async with acquire(tenant_id) as conn:
        # Fresh suggestions replace stale ones; anything the user already
        # built (or is building — claim in flight, production_id not yet
        # written) keeps its row + history.
        building = [UUID(x) for x in _TOPIC_BUILDING]
        await conn.execute(
            "DELETE FROM clip_topics "
            "WHERE status = 'suggested' AND production_id IS NULL "
            "AND NOT (id = ANY($1::uuid[]))",
            building,
        )
        for t in cleaned:
            await conn.execute(
                """INSERT INTO clip_topics (title, hook, why, score, segments)
                   VALUES ($1, $2, $3, $4, $5::jsonb)""",
                t["title"], t["hook"], t["why"], t["score"],
                json.dumps(t["segments"]),
            )
    print(f"[topics] mined {len(cleaned)} topic suggestion(s) "
          f"from {len(rows)} candidates")
    return len(cleaned)


def _mining_key(tenant_id: UUID | None) -> str:
    """Same tenant resolution as db.acquire (explicit → request contextvar →
    default), so the in-flight flag tracks the tenant that actually mines."""
    from .db import _request_tenant
    return str(tenant_id or _request_tenant.get() or settings.default_tenant_id)


def refresh_topics_detached(tenant_id: UUID | None = None) -> bool:
    """Kick a topic-mining pass in the background (one per tenant at a
    time). A refresh requested while a pass is running is coalesced into
    ONE follow-up pass (new footage mid-pass isn't silently dropped).
    Returns False when a pass was already running."""
    tid = _mining_key(tenant_id)
    if tid in _TOPIC_MINING:
        _TOPIC_RERUN.add(tid)
        return False
    _TOPIC_MINING.add(tid)

    async def _run() -> None:
        try:
            await mine_clip_topics(tenant_id)
        except Exception as e:  # noqa: BLE001 — mining must never crash a caller
            print(f"[topics] mining failed: {e}")
        finally:
            _TOPIC_MINING.discard(tid)
            if tid in _TOPIC_RERUN:
                _TOPIC_RERUN.discard(tid)
                refresh_topics_detached(tenant_id)

    task = asyncio.create_task(_run())
    _TOPIC_TASKS.add(task)
    task.add_done_callback(_TOPIC_TASKS.discard)
    return True


def _topic_state(production_id, production_status: str | None) -> str:
    """suggested → building → built, mirroring _candidate_state; a failed or
    canceled render flips the topic back to buildable."""
    if not production_id:
        return "suggested"
    st = (production_status or "").lower()
    if st in _IN_PROGRESS:
        return "building"
    if st == "succeeded":
        return "built"
    return "suggested"


async def build_topic(topic_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    """Click a topic → the clipper builds it: one long_form_reel production
    whose payload carries EVERY segment (the renderer cuts each window and
    stitches them before the engaging treatment).

    Double-build protection is two-layer: an in-process claim set (fast
    path) plus a `production_id IS NULL` guard on the DB claim, so a racer
    that slips past the first never starts a second paid render."""
    key = str(topic_id)
    if key in _TOPIC_BUILDING:
        return {"production_id": None, "state": "building"}
    _TOPIC_BUILDING.add(key)
    try:
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow(
                """SELECT t.id, t.title, t.hook, t.why, t.segments,
                          t.production_id,
                          vp.status AS production_status
                     FROM clip_topics t
                     LEFT JOIN video_productions vp ON vp.id = t.production_id
                    WHERE t.id = $1 AND t.status <> 'dismissed'""",
                topic_id,
            )
        if not row:
            return None
        state = _topic_state(row["production_id"], row["production_status"])
        if state in ("building", "built"):
            return {"production_id": str(row["production_id"]), "state": state}

        segments = row["segments"]
        if isinstance(segments, str):
            segments = json.loads(segments)
        if not segments:
            return None
        src_ids = {s["source_id"] for s in segments if s.get("source_id")}
        async with acquire(tenant_id) as conn:
            srcs = await conn.fetch(
                "SELECT id, source_url, drive_file_id FROM long_sources "
                "WHERE id = ANY($1::uuid[])",
                [UUID(s) for s in src_ids],
            )
        src_by_id = {str(r["id"]): r for r in srcs}
        payload: list[dict] = []
        for seg in segments:
            src = src_by_id.get(str(seg.get("source_id") or ""))
            if not src:
                continue
            payload.append({
                "source_id": str(src["id"]),
                "candidate_id": str(seg.get("candidate_id") or ""),
                "source_url": src["source_url"] or "",
                "drive_file_id": src["drive_file_id"] or "",
                "start_s": float(seg["start_s"]),
                "end_s": float(seg["end_s"]),
                "hook_quote": (row["hook"] or seg.get("quote") or "")[:200],
                "summary": (row["why"] or "")[:280],
            })
        if not payload:
            return None
        if len(payload) < len(segments):
            print(f"[topics] build {topic_id}: {len(segments) - len(payload)} "
                  f"segment(s) dropped — source deleted since mining")

        from .video_pipeline import run_production, start_production
        prod = await start_production(
            (row["hook"] or row["title"] or "")[:200],   # script slot
            "instagram", "9:16",
            (row["title"] or "Topic reel")[:120],        # title slot
            payload, "long_form_reel",
            settings.auto_clip_caption_style or "",
            "",  # image_style
            broll_style=settings.auto_clip_broll_style or "",
            tenant_id=tenant_id,
        )
        prod_id = UUID(str(prod["id"]))
        async with acquire(tenant_id) as conn:
            tag = await conn.execute(
                "UPDATE clip_topics SET production_id=$2, updated_at=now() "
                "WHERE id=$1 AND production_id IS NULL",
                topic_id, prod_id,
            )
        if not tag.endswith("1"):
            # Lost the claim to a concurrent build — never render twice.
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "DELETE FROM video_productions WHERE id=$1", prod_id)
                winner = await conn.fetchrow(
                    """SELECT t.production_id, vp.status AS production_status
                         FROM clip_topics t
                         LEFT JOIN video_productions vp ON vp.id = t.production_id
                        WHERE t.id = $1""", topic_id)
            wid = winner["production_id"] if winner else None
            return {
                "production_id": str(wid) if wid else None,
                "state": _topic_state(wid, winner["production_status"] if winner else None),
            }

        # Mark every cited candidate as rendered by this production so the
        # dedupe contract holds everywhere (mining's already_clipped flag,
        # auto-clip's skip, content-library counts, re-analyze retention).
        for seg in payload:
            if seg["candidate_id"]:
                try:
                    await link_candidate_to_production(
                        UUID(seg["candidate_id"]), prod_id, tenant_id)
                except Exception as e:  # noqa: BLE001 — linking is best-effort
                    print(f"[topics] candidate link failed: {e}")

        task = asyncio.create_task(run_production(prod_id, tenant_id))
        _AUTOCLIP_TASKS.add(task)
        task.add_done_callback(_AUTOCLIP_TASKS.discard)
        return {"production_id": str(prod_id), "state": "building"}
    finally:
        _TOPIC_BUILDING.discard(key)


async def dismiss_topic(topic_id: UUID, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        res = await conn.execute(
            "UPDATE clip_topics SET status='dismissed', updated_at=now() "
            "WHERE id=$1", topic_id,
        )
    return res.endswith("1")


__all__ = [
    "create_source", "create_source_placeholder", "set_source_url",
    "ingest_source", "fetch_from_drive_then_ingest",
    "list_sources", "get_source_with_candidates", "get_candidate",
    "link_candidate_to_production", "dismiss_candidate",
    "find_candidates", "transcribe_long", "reap_orphaned_sources",
    "create_whole_source_candidate", "reanalyze_source",
    "get_source", "set_speaker_tags", "detect_speakers_for_source",
    "auto_clip_source", "content_library",
    "mine_clip_topics", "refresh_topics_detached", "build_topic",
    "dismiss_topic",
]
