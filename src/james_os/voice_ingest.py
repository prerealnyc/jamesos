"""Voice Studio — feed the brand voice from Google Drive.

Drop a Drive FOLDER url (or individual file links) → list the files → for
each one:
  * audio / video  → Whisper transcript (any length: low-bitrate audio
                     extract + chunked transcription via long_form)
  * pdf / docx / txt / md / csv / json → text extraction
  * Google Doc     → exported as plain text
…then ingested as `voice_corpus` events so the content engine immediately
writes in that voice. Re-running supersedes the prior version of each file.

This is THIN GLUE over pieces that already exist:
  drive.py        — service-account auth + downloads (Shared-Drive aware)
  audio_trim.py   — extract_audio_lowbit (32 kbps mono → fits Whisper)
  long_form.py    — transcribe_long (one-shot or chunked Whisper)
  documents.py    — extract_text / chunk_text / document_to_events
  ingestion.py    — embed + store + supersede prior versions

The only genuinely new bits are the folder lister and a durable job row
(voice_ingest_jobs) so progress is observable and a restart never reports
fake 'running' state — it's reaped to 'interrupted' on startup.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from uuid import UUID

from .config import settings
from .db import acquire

# ── Drive ids / listing ──────────────────────────────────────────────

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_GOOGLE_FOLDER = "application/vnd.google-apps.folder"
_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_NATIVE = "application/vnd.google-apps."
_MEDIA_PREFIXES = ("video/", "audio/")


def extract_drive_folder_id(url: str) -> str | None:
    """Pull a folder id out of a Drive folder URL, or accept a bare id."""
    if not url:
        return None
    s = url.strip()
    m = _FOLDER_RE.search(s)
    if m:
        return m.group(1)
    if "/" not in s and re.fullmatch(r"[A-Za-z0-9_-]{10,}", s):
        return s
    return None


def _list_folder_files_sync(folder_id: str, limit: int = 300) -> list[dict]:
    from .drive import _SHARED_DRIVE_LIST, _service

    svc = _service()
    q = (
        f"'{folder_id}' in parents and trashed = false "
        f"and mimeType != '{_GOOGLE_FOLDER}'"
    )
    files: list[dict] = []
    page_token = None
    while True:
        res = svc.files().list(
            q=q,
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=min(limit, 1000),
            pageToken=page_token,
            orderBy="name",
            **_SHARED_DRIVE_LIST,
        ).execute()
        files.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token or len(files) >= limit:
            break
    return files[:limit]


async def list_drive_folder_files(folder_id: str) -> list[dict]:
    """All non-folder files in a Drive folder (audio, video, docs)."""
    return await asyncio.to_thread(_list_folder_files_sync, folder_id)


def _is_media(name: str, mime: str) -> bool:
    from .transcription import is_audio

    return is_audio(name) or any(mime.startswith(p) for p in _MEDIA_PREFIXES)


# ── download / export ────────────────────────────────────────────────


def _download_to_path_sync(file_id: str, mime: str, out_path: str) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    from .drive import _SHARED_DRIVE, _service

    svc = _service()
    if mime == _GOOGLE_DOC:
        # Native Google Doc → export plain text (get_media doesn't work).
        request = svc.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        request = svc.files().get_media(fileId=file_id, **_SHARED_DRIVE)
    with open(out_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


# ── transcription (any length) ───────────────────────────────────────


async def _transcribe_media(in_path: str) -> str:
    """Media file → transcript. Extract a low-bitrate mono mp3 first so
    even multi-hour sources fit Whisper (chunked by transcribe_long)."""
    from .audio_trim import extract_audio_lowbit
    from .long_form import transcribe_long

    work = tempfile.mkdtemp(prefix="voice_")
    try:
        mp3 = os.path.join(work, "audio.mp3")
        ok = await extract_audio_lowbit(in_path, mp3)
        src = mp3 if (ok and os.path.exists(mp3) and os.path.getsize(mp3) > 0) else in_path
        text, _words, _dur = await transcribe_long(src, work)
        return text or ""
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)


async def _file_to_events(name: str, path: str, mime: str, category: str):
    """Build voice_corpus EventCreate list for one downloaded file.
    Returns (events, filename_used) — filename_used is the supersede key."""
    from .documents import document_to_events

    if _is_media(name, mime):
        text = await _transcribe_media(path)
        if not text.strip():
            raise ValueError("empty transcript (no speech detected / extract failed)")
        # Use a .txt filename so document_to_events takes the TEXT path
        # (the transcript IS the text — don't re-run Whisper on it).
        used = f"{name}.txt"
        events = document_to_events(
            used, text.encode("utf-8"), event_type="voice_memo", category=category
        )
        return events, used
    # Doc: read bytes, extract text (pdf/docx/txt/md/csv/json) inside.
    with open(path, "rb") as fh:
        data = fh.read()
    events = document_to_events(name, data, event_type="document", category=category)
    return events, name


# ── durable job row ──────────────────────────────────────────────────


async def create_job(
    source: str, category: str, files: list[dict], tenant_id: UUID | None = None
) -> str:
    async with acquire(tenant_id) as conn:
        return str(
            await conn.fetchval(
                """INSERT INTO voice_ingest_jobs (source, category, total, stage)
                   VALUES ($1, $2, $3, 'processing') RETURNING id""",
                source, category, len(files),
            )
        )


async def _bump(job_id: str, tenant_id: UUID | None, *, processed: int, chunks: int) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE voice_ingest_jobs SET processed=$2, chunks=$3 WHERE id=$1",
            UUID(job_id), processed, chunks,
        )


async def _finish(
    job_id: str, tenant_id: UUID | None, status: str,
    files: list[dict], errors: list[dict], chunks: int, processed: int,
) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """UPDATE voice_ingest_jobs
               SET status=$2, stage='done', files=$3::jsonb, errors=$4::jsonb,
                   chunks=$5, processed=$6, completed_at=now()
               WHERE id=$1""",
            UUID(job_id), status, json.dumps(files), json.dumps(errors), chunks, processed,
        )


async def recent_jobs(tenant_id: UUID | None = None, limit: int = 10) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, source, category, status, stage, total, processed,
                      chunks, files, errors, created_at, completed_at
               FROM voice_ingest_jobs ORDER BY created_at DESC LIMIT $1""",
            limit,
        )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        for k in ("files", "errors"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        for k in ("created_at", "completed_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


async def reap_orphaned_jobs(tenant_id: UUID | None = None) -> int:
    """Mark interrupted jobs failed on startup so a restart never leaves a
    fake 'running' row (frustration-ledger Pattern E/K honesty)."""
    async with acquire(tenant_id) as conn:
        n = await conn.fetchval(
            """UPDATE voice_ingest_jobs
               SET status='interrupted', stage='reaped', completed_at=now()
               WHERE status='running'
               RETURNING (SELECT count(*)::int FROM voice_ingest_jobs WHERE stage='reaped')"""
        )
    return int(n or 0)


async def corpus_summary(tenant_id: UUID | None = None) -> dict:
    """What's currently in the voice corpus, grouped by source file."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT coalesce(payload->>'filename','(unknown)') AS filename,
                      count(*) AS chunks,
                      sum(length(coalesce(raw_content,''))) AS chars
               FROM events
               WHERE payload->>'category'='voice_corpus' AND superseded_by IS NULL
               GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 100"""
        )
        total = await conn.fetchval(
            "SELECT count(*) FROM events WHERE payload->>'category'='voice_corpus' "
            "AND superseded_by IS NULL"
        )
    return {
        "total_chunks": int(total or 0),
        "sources": [
            {"filename": r["filename"], "chunks": int(r["chunks"]), "chars": int(r["chars"] or 0)}
            for r in rows
        ],
    }


# ── orchestrator (runs in a background task) ─────────────────────────


async def run_ingest(
    job_id: str, files_meta: list[dict], category: str, tenant_id: UUID | None = None
) -> None:
    from .ingestion import ingest_many, supersede_prior_document_versions

    processed = 0
    total_chunks = 0
    done_files: list[dict] = []
    errors: list[dict] = []
    try:
        for f in files_meta:
            name = f.get("name") or f.get("id") or "file"
            mime = f.get("mimeType") or ""
            if mime == _GOOGLE_FOLDER or mime.startswith(_GOOGLE_NATIVE) and mime != _GOOGLE_DOC:
                errors.append({"source": name, "error": f"skipped unsupported type {mime}"})
                processed += 1
                await _bump(job_id, tenant_id, processed=processed, chunks=total_chunks)
                continue
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(prefix="vdl_")
                os.close(fd)
                await asyncio.to_thread(_download_to_path_sync, f["id"], mime, tmp)
                events, used = await _file_to_events(name, tmp, mime, category)
                if not events:
                    raise ValueError("no text extracted")
                stored = await ingest_many(events, tenant_id)
                keep_ids = [s.id for s in stored]
                await supersede_prior_document_versions(used, category, keep_ids, tenant_id)
                total_chunks += len(stored)
                done_files.append({
                    "name": name,
                    "chunks": len(stored),
                    "type": "media" if _is_media(name, mime) else "doc",
                })
            except Exception as e:  # noqa: BLE001 — one bad file can't kill the batch
                errors.append({"source": name, "error": f"{type(e).__name__}: {str(e)[:160]}"})
            finally:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            processed += 1
            await _bump(job_id, tenant_id, processed=processed, chunks=total_chunks)

        status = "succeeded" if done_files else "failed"
        await _finish(job_id, tenant_id, status, done_files, errors, total_chunks, processed)
    except Exception as e:  # noqa: BLE001 — always land the job in a terminal state
        errors.append({"source": "batch", "error": str(e)[:200]})
        await _finish(job_id, tenant_id, "failed", done_files, errors, total_chunks, processed)


async def list_sources(
    folder_url: str = "", links: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """Resolve a folder URL + individual links into a flat file list.
    Returns (files_meta, errors)."""
    from .drive import _file_metadata_sync, extract_drive_file_id

    files: list[dict] = []
    errors: list[dict] = []
    fid = extract_drive_folder_id(folder_url) if folder_url else None
    if fid:
        try:
            files.extend(await list_drive_folder_files(fid))
        except Exception as e:  # noqa: BLE001
            errors.append({"source": folder_url, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    for url in (links or []):
        u = (url or "").strip()
        if not u:
            continue
        file_id = extract_drive_file_id(u)
        if not file_id:
            errors.append({"source": u, "error": "could not parse a Drive file id"})
            continue
        try:
            meta = await asyncio.to_thread(_file_metadata_sync, file_id)
            files.append({
                "id": file_id,
                "name": meta.get("name") or file_id,
                "mimeType": meta.get("mimeType") or "",
                "size": meta.get("size"),
            })
        except Exception as e:  # noqa: BLE001
            errors.append({"source": u, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    return files, errors


__all__ = [
    "extract_drive_folder_id", "list_drive_folder_files", "list_sources",
    "create_job", "run_ingest", "recent_jobs", "reap_orphaned_jobs",
    "corpus_summary",
]
