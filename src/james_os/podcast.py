"""Podcast engine — thesis / white paper → spoken episode.

  1. script    — an episode script written in the brand voice from a source
                 Knowledge-Base document (the weekly thesis or the white
                 paper that argues it)
  2. narrate   — synthesized with the brand's cloned ElevenLabs voice
                 (the first real consumer of elevenlabs_api_key)
  3. publish   — mp3 persisted to the media store; the episode lands in the
                 approval queue as an actions row (action_type='podcast') —
                 zero new DDL, same review pipeline as every other artifact

Runs as a background job (script + synthesis take minutes); poll for stages.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid as _uuid
from pathlib import Path
from uuid import UUID

from .config import settings
from .db import acquire
from .llm import get_llm
from .media import storage as media_storage
from .tts import synthesize_speech, tts_configured

_SCRIPT_SYSTEM = """You are {brand}'s podcast producer. Write a SOLO-HOST
podcast episode from the source document below — the host is the author,
speaking in FIRST PERSON, arguing their point of view conversationally.

Rules:
* script = ONLY the words spoken aloud. No headers, no markdown, no
  "[intro music]", no stage directions, no "Section 1:". Natural spoken
  cadence: short sentences, rhetorical questions, signposting ("here's the
  thing…", "so what does that mean?").
* Open with a hook (the most provocative claim), close with a takeaway and
  a simple sign-off.
* Target about {words} words (a tight 7-8 minute listen). Never pad.
* Keep every factual claim faithful to the source document.
* description = show notes: 2-3 sentences + 3-5 bullet takeaways (≤ 1200
  chars, plain text).

Return STRICT JSON:
{{"title": str, "description": str, "script": str}}
"""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "episode").lower()).strip("-")
    return s[:60] or "episode"


async def generate_podcast(
    doc_id: UUID, tenant_id: UUID | None = None, on_stage=None,
) -> dict:
    def _stage(s: str) -> None:
        if on_stage:
            on_stage(s)

    if not tts_configured():
        raise RuntimeError(
            "The brand voice isn't configured yet — add the ElevenLabs API "
            "key and voice id in Settings, then retry.")

    _stage("scripting")
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT filename, extracted_text FROM document_metadata "
            "WHERE id=$1", doc_id)
    if not row or not (row["extracted_text"] or "").strip():
        raise ValueError("source document not found or has no text")

    from .whitepaper import _brand_label, _guidelines_block
    brand = await _brand_label(tenant_id)
    guidelines = await _guidelines_block(tenant_id)
    out = await get_llm().complete_json(
        system=_SCRIPT_SYSTEM.format(
            brand=brand, words=settings.podcast_words_target) + guidelines,
        messages=[{"role": "user", "content":
                   f'<source filename="{row["filename"]}">\n'
                   f'{row["extracted_text"][:28000]}\n</source>\n\n'
                   f"Write the episode as JSON now."}],
        max_tokens=4000, temperature=0.5,
    )
    if not isinstance(out, dict):
        raise RuntimeError("the model did not return a valid episode script")
    title = str(out.get("title") or f"Episode: {row['filename']}")[:200]
    description = str(out.get("description") or "")[:1500]
    script = str(out.get("script") or "").strip()
    if len(script) < 400:
        raise RuntimeError("the episode script came back too short — retry")

    _stage("narrating")
    audio = await synthesize_speech(script)

    _stage("publishing")
    # Duration probe needs a file on disk; probe BEFORE upload.
    duration_s = 0.0
    try:
        from .audio_trim import probe_duration
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ep.mp3"
            p.write_bytes(audio)
            duration_s = await probe_duration(str(p))
    except Exception:  # noqa: BLE001 — duration is nice-to-have
        duration_s = 0.0

    tid_str = str(tenant_id or settings.default_tenant_id)
    uri, _ = await asyncio.to_thread(
        media_storage().save, tid_str, audio, f"podcast-{_slug(title)}.mp3")

    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('podcast_producer', 'podcast', $1::jsonb, 'pending')
               RETURNING id""",
            json.dumps({
                "platform": "podcast", "format": "audio",
                "content": title,
                "description": description,
                "audio_url": uri,
                "duration_s": round(duration_s, 1),
                "script": script[:8000],
                "source_doc_id": str(doc_id),
                "source_filename": row["filename"],
            }),
        )
    return {
        "title": title,
        "description": description,
        "audio_url": uri,
        "duration_s": round(duration_s, 1),
        "action_id": str(action_id),
        "source_filename": row["filename"],
    }


async def list_podcasts(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, payload, status, created_at FROM actions "
            "WHERE action_type='podcast' ORDER BY created_at DESC LIMIT 50")
    out = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        p = p or {}
        out.append({
            "id": str(r["id"]),
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "title": p.get("content") or "",
            "description": p.get("description") or "",
            "audio_url": p.get("audio_url") or "",
            "duration_s": float(p.get("duration_s") or 0),
            "source_filename": p.get("source_filename") or "",
        })
    return out


# ── background job wrapper (start/poll, per-doc dedupe) ──

_POD_JOBS: dict[str, dict] = {}
_POD_TASKS: set = set()
_POD_JOBS_MAX = 40


def _prune_jobs() -> None:
    if len(_POD_JOBS) <= _POD_JOBS_MAX:
        return
    finished = [k for k, v in _POD_JOBS.items() if v.get("status") != "running"]
    for k in finished[: len(_POD_JOBS) - _POD_JOBS_MAX]:
        _POD_JOBS.pop(k, None)


def start_podcast_job(doc_id: UUID, tenant_id: UUID | None = None) -> str:
    """Kick a detached episode generation; returns a job id to poll. Tenant
    resolved HERE (request context alive) and bound into the job."""
    from .db import _request_tenant
    tenant_id = tenant_id or _request_tenant.get()
    for jid, j in _POD_JOBS.items():
        if j.get("doc_id") == str(doc_id) and j.get("status") == "running":
            return jid
    job_id = _uuid.uuid4().hex[:12]
    _POD_JOBS[job_id] = {"status": "running", "stage": "scripting",
                         "doc_id": str(doc_id)}
    _prune_jobs()

    def _on_stage(stage: str) -> None:
        j = _POD_JOBS.get(job_id)
        if j and j.get("status") == "running":
            j["stage"] = stage

    async def _run() -> None:
        try:
            result = await generate_podcast(doc_id, tenant_id, on_stage=_on_stage)
            _POD_JOBS[job_id] = {"status": "done", "doc_id": str(doc_id),
                                 "result": result}
        except Exception as e:  # noqa: BLE001 — surfaced via the poll
            _POD_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                 "error": str(e)[:500]}
        finally:
            # Never leave a cancelled task stuck "running" — the per-doc
            # dedupe would block this document until a restart.
            j = _POD_JOBS.get(job_id)
            if j and j.get("status") == "running":
                _POD_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                     "error": "run was canceled — retry"}

    task = asyncio.create_task(_run())
    _POD_TASKS.add(task)
    task.add_done_callback(_POD_TASKS.discard)
    return job_id


def get_podcast_job(job_id: str) -> dict | None:
    return _POD_JOBS.get(job_id)


__all__ = [
    "generate_podcast", "list_podcasts",
    "start_podcast_job", "get_podcast_job",
]
