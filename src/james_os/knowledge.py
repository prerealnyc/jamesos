"""Knowledge Base — the common entry point for a brand's company documents.

Ported feature-for-feature from the PreReal Intelligence ingest pipeline:

  * single-file AND batch-ZIP ingest (≤60 files / ≤50 MB, 2 concurrent
    workers, junk entries filtered, per-file results);
  * "originality first" — EVERY uploaded file is preserved + filed, no
    matter the type; text extraction is best-effort (unreadable types are
    stored with indexing_status='skipped', never dropped);
  * full-format extraction (PDF, Word, PowerPoint, Excel, HTML, RTF, text,
    audio/video→Whisper, image→vision OCR) via documents.extract_any;
  * collision-safe versioning — re-uploading a name walks v2→v3… (cap 99)
    instead of overwriting;
  * private storage + signed-URL downloads (company docs are never public);
  * chunks + embeddings land in the tenant's `events` memory (the same
    substrate Ask / retrieval / the content engine already read), so an
    uploaded doc is immediately askable and groundable.

The per-file ledger is `document_metadata`; the searchable text lives as
`events` rows tagged payload.category (default 'company_doc').
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from uuid import UUID

from .db import acquire
from .documents import CATEGORIES, ExtractResult, _build_events, extract_any
from .ingestion import ingest_many

# Files we won't try to read as text (originals are still stored). Audio +
# video Whisper can read are allowed via is_transcribable in extract_any.
SKIP_EXT = {
    "bmp", "tiff", "svg", "ico", "heic",
    "mov", "avi", "mkv", "wmv", "flv",
    "exe", "dll", "bin", "dmg", "iso", "app", "msi",
    "zip", "rar", "7z", "tar", "gz", "tgz",
    "ttf", "otf", "woff", "woff2", "psd", "ai", "sketch", "fig",
}

MIME_BY_EXT = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain", "md": "text/markdown", "csv": "text/csv",
    "json": "application/json", "html": "text/html", "htm": "text/html",
    "xml": "application/xml", "rtf": "application/rtf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
}

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_FILES_PER_ZIP = 60
CONCURRENCY = 2
_VERSION_TRIES = 99
_COMMIT_TASKS: set = set()   # strong refs for detached commitment-mining tasks


def guess_mime(name: str) -> str:
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    return MIME_BY_EXT.get(ext, "application/octet-stream")


def _junk_entry(path: str) -> bool:
    """Worthless zip entries we never try to ingest."""
    if path.endswith("/"):
        return True                      # directory
    if path.startswith("__MACOSX/"):
        return True                      # mac resource forks
    base = path.rsplit("/", 1)[-1]
    return not base or base.startswith(".")   # dotfiles (.DS_Store, ._x)


def _knowledge_storage():
    """PRIVATE bucket for company documents — reachable only via signed URLs."""
    from .storage_supabase import SupabaseMediaStorage

    return SupabaseMediaStorage(bucket="knowledge", public=False)


async def _find_free_version(
    original_name: str, tenant_id: UUID | None,
    build=None,
) -> tuple[str, int, bool]:
    """Collision-safe versioning (cap 99 — needing v100+ is a 'rename the
    file' signal, same as PreReal). Default builder keeps the original name
    for v1 and walks `stem_v2.ext`, `stem_v3.ext`, …; a custom `build(v)`
    (the canonical 8-slot namer) renders the version into the name itself."""
    if build is None:
        stem, dot, ext = original_name.rpartition(".")
        if not dot:
            stem, ext = original_name, ""

        def build(v: int) -> str:  # noqa: PLR0206
            return (original_name if v == 1
                    else f"{stem}_v{v}{('.' + ext) if ext else ''}")

    async with acquire(tenant_id) as conn:
        for v in range(1, _VERSION_TRIES + 1):
            candidate = build(v)
            hit = await conn.fetchval(
                "SELECT 1 FROM document_metadata WHERE filename = $1 LIMIT 1",
                candidate,
            )
            if not hit:
                return candidate, v, v != 1
    raise ValueError("no free version slot after 99 tries — rename the file")


async def ingest_knowledge_document(
    *,
    data: bytes,
    original_name: str,
    mime: str = "",
    category: str = "company_doc",
    notes: str = "",
    auto_classify: bool = True,
    classify_ctx: dict | None = None,   # {'silos': [...], 'entities': [...]} — batch reuse
    tenant_id: UUID | None = None,
) -> dict:
    """One document in → one filed, searchable row out (the PreReal contract).

    With `auto_classify` (default), the AI filing clerk assigns metadata from
    the controlled vocabularies (BU / asset class / doc type / descriptor /
    date / status / SENSITIVITY / silo / entity — auto-registering a new
    entity when the doc clearly names an unknown subject), and the file gets
    the canonical 8-slot name. Low-confidence filings are flagged for review.

    Returns {originalName, ok, skipped?, reason?, filename, fileId, chunks,
    classified?, confidence?, entityId?, siloId?, docType?, flagged?}.
    Failures return ok=False with a reason — they never raise, so one bad
    file can't kill a batch."""
    name = original_name.rsplit("/", 1)[-1] or original_name
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    mime = mime or guess_mime(name)
    if category not in CATEGORIES:
        category = "company_doc"

    try:
        # 1. Extract text (best-effort). Non-extractable types are stored anyway.
        if ext in SKIP_EXT:
            extraction = ExtractResult(
                "", True, f"original preserved; .{ext} is not text-extractable")
        else:
            extraction = await extract_any(name, data, mime)
        text = (extraction.text or "").strip()

        # 2. AI auto-filing (best-effort; None → defaults + no flag).
        cls = None
        entity_id: str | None = None
        review: list[str] = []
        flagged = False
        if auto_classify:
            try:
                from .classify import LOW_CONFIDENCE, classify_document
                from .entities import find_or_create_entity, list_entities
                from .silos import list_silos

                # Batch callers (ZIP) fetch the silo/entity context ONCE and
                # pass it in — the PreReal pattern; per-file fetches would be
                # 2 extra queries per document for identical data.
                if classify_ctx is None:
                    classify_ctx = {
                        "silos": await list_silos(tenant_id),
                        "entities": await list_entities(tenant_id),
                    }
                cls = await classify_document(
                    filename=name, text_snippet=text, notes=notes,
                    silos=classify_ctx.get("silos") or [],
                    entities=classify_ctx.get("entities") or [],
                    tenant_id=tenant_id,
                )
                if cls:
                    entity_id = cls.entity_id
                    # Doc clearly names an unknown subject → register a real
                    # entity for it (confidence-gated to avoid junk).
                    if not entity_id and cls.entity_name and cls.confidence >= 0.4:
                        entity_id = await find_or_create_entity(
                            business_unit=cls.business_unit,
                            entity_type_code=cls.entity_type_code,
                            display_name=cls.entity_name,
                            tenant_id=tenant_id,
                        )
                    if cls.doc_type == "Other":
                        flagged = True
                        review.append("DocType=Other")
                    if cls.confidence < LOW_CONFIDENCE:
                        flagged = True
                        review.append(
                            f"Low-confidence auto-file "
                            f"({round(cls.confidence * 100)}%)")
            except Exception:  # noqa: BLE001 — classification must never block
                cls = None

        # 3. Collision-safe filename: canonical 8-slot name when classified,
        #    else the original name with a _vN walk.
        if cls:
            from .naming import generate_filename, safe_descriptor

            _desc = (safe_descriptor(cls.descriptor)
                     or safe_descriptor(name.rsplit(".", 1)[0]) or "Doc")
            _status = "Pending" if (flagged and cls.doc_type == "Other") else cls.status

            def _build(v: int) -> str:
                return generate_filename(
                    business_unit=cls.business_unit, asset_class=cls.asset_class,
                    entity_id=entity_id or "UNASSIGNED", doc_type=cls.doc_type,
                    descriptor=_desc, date=cls.date, version=v,
                    status=_status, extension=ext or "bin",
                )

            filename, version, _bumped = await _find_free_version(
                name, tenant_id, build=_build)
        else:
            filename, version, _bumped = await _find_free_version(name, tenant_id)

        # 4. Store the ORIGINAL bytes (private bucket, signed-URL access only).
        storage_path: str | None = None
        try:
            store = _knowledge_storage()
            from .config import settings
            tenant = str(tenant_id or settings.default_tenant_id)
            _, storage_path = await asyncio.to_thread(store.save, tenant, data, filename)
        except Exception as e:  # noqa: BLE001 — row still filed; flag for review
            flagged = True
            review.append(f"Storage upload failed: {e}")

        # 5. File the ledger row (classification metadata when available).
        from .vocab import DEFAULT_SENSITIVITY
        file_hash = hashlib.sha256(data).hexdigest()[:16]
        async with acquire(tenant_id) as conn:
            row_id = await conn.fetchval(
                """INSERT INTO document_metadata
                     (filename, original_name, category, version,
                      flagged_for_review, review_reason, storage_path,
                      mime_type, size_bytes, file_hash, notes, extracted_text,
                      indexing_status, indexing_error,
                      business_unit, asset_class, doc_type, descriptor,
                      file_date, status, sensitivity, entity_id, silo_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                           $15,$16,$17,$18,$19::date,$20,$21,$22,$23)
                   RETURNING id""",
                filename, name, category, version,
                flagged, "; ".join(review) or None, storage_path,
                mime, len(data), file_hash, (notes or "").strip() or None,
                text or None,
                "pending" if text else "skipped",
                None if text else (extraction.reason or "no extractable text"),
                cls.business_unit if cls else None,
                cls.asset_class if cls else None,
                cls.doc_type if cls else None,
                cls.descriptor if cls else None,
                cls.date if cls else None,
                (("Pending" if (flagged and cls.doc_type == "Other")
                  else cls.status) if cls else "Current"),
                cls.sensitivity if cls else DEFAULT_SENSITIVITY,
                entity_id,
                cls.silo_id if cls else None,
            )

        # 5. Index: chunk + embed into the tenant's memory (events).
        chunks = 0
        if text:
            try:
                events = _build_events(filename, data, text, "document", category)
                # Re-key dedupe to THIS ledger row (kb-{row_id}-{chunk}) — a
                # content hash alone would collide when two documents contain
                # identical bytes, making one doc's delete destroy the other's
                # chunks. Row-scoped keys keep every document independent.
                for ev in events:
                    idx = ev.source.raw_metadata.get("chunk_index", 0)
                    ev.source.dedupe_key = f"kb-{row_id}-{idx}"
                stored = await ingest_many(events, tenant_id=tenant_id)
                chunks = len(stored)
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE document_metadata SET chunks=$2, "
                        "indexing_status='indexed', indexed_at=now() WHERE id=$1",
                        row_id, chunks,
                    )
                # Light action-item pass for note-like docs (meeting notes,
                # transcripts, calls) — fire-and-forget, never blocks ingest.
                try:
                    from .commitments import (
                        extract_commitments_from_text,
                        looks_like_meeting_doc,
                    )
                    if looks_like_meeting_doc(mime, name):
                        task = asyncio.create_task(extract_commitments_from_text(
                            file_id=str(row_id), filename=filename, text=text,
                            entity_id=entity_id, tenant_id=tenant_id,
                        ))
                        _COMMIT_TASKS.add(task)
                        task.add_done_callback(_COMMIT_TASKS.discard)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001 — stored but not searchable
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE document_metadata SET indexing_status='failed', "
                        "indexing_error=$2 WHERE id=$1",
                        row_id, str(e)[:500],
                    )

        return {
            "originalName": original_name, "ok": True,
            "skipped": not text,
            "reason": None if text else (extraction.reason or "no extractable text"),
            "filename": filename, "fileId": str(row_id), "chunks": chunks,
            "classified": cls is not None,
            "confidence": cls.confidence if cls else None,
            "entityId": entity_id,
            "siloId": cls.silo_id if cls else None,
            "docType": cls.doc_type if cls else None,
            "flagged": flagged,
        }
    except Exception as e:  # noqa: BLE001 — batch resilience
        return {"originalName": original_name, "ok": False, "reason": str(e)}


def is_zip(filename: str, mime: str = "") -> bool:
    return filename.lower().endswith(".zip") or mime in (
        "application/zip", "application/x-zip-compressed",
    )


async def ingest_zip(
    *,
    data: bytes,
    category: str = "company_doc",
    notes: str = "",
    auto_classify: bool = True,
    tenant_id: UUID | None = None,
) -> dict:
    """Unpack a ZIP and ingest every document inside — up to 60 files, 2
    concurrent workers, junk entries (dirs / __MACOSX / dotfiles) skipped.
    Returns {ok, expanded, total, filed, skipped, failed, results[]}."""
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError(
            f"zip too large ({len(data) / 1024 / 1024:.0f} MB; cap is 50 MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ValueError("could not read the zip file") from e

    names = [n for n in zf.namelist() if not _junk_entry(n)]
    if not names:
        return {"ok": True, "expanded": True, "total": 0,
                "filed": 0, "skipped": 0, "failed": 0, "results": []}
    if len(names) > MAX_FILES_PER_ZIP:
        raise ValueError(
            f"zip has {len(names)} files; cap is {MAX_FILES_PER_ZIP}. "
            "Split it into smaller zips.")

    sem = asyncio.Semaphore(CONCURRENCY)

    # Fetch the classification context ONCE for the whole batch (the PreReal
    # loadContext pattern) — not 2 queries per file for identical data.
    ctx: dict | None = None
    if auto_classify:
        try:
            from .entities import list_entities
            from .silos import list_silos

            ctx = {"silos": await list_silos(tenant_id),
                   "entities": await list_entities(tenant_id)}
        except Exception:  # noqa: BLE001
            ctx = None

    async def _one(entry: str) -> dict:
        async with sem:
            try:
                blob = zf.read(entry)
            except Exception as e:  # noqa: BLE001
                return {"originalName": entry, "ok": False, "reason": str(e)}
            return await ingest_knowledge_document(
                data=blob, original_name=entry, category=category,
                notes=notes, auto_classify=auto_classify, classify_ctx=ctx,
                tenant_id=tenant_id,
            )

    results = list(await asyncio.gather(*(_one(n) for n in names)))
    filed = sum(1 for r in results if r.get("ok"))
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if not r.get("ok") and not r.get("skipped"))
    return {"ok": True, "expanded": True, "total": len(names),
            "filed": filed, "skipped": skipped, "failed": failed,
            "results": results}


async def list_documents(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, filename, original_name, category, version, status,
                      sensitivity, flagged_for_review, review_reason,
                      mime_type, size_bytes, notes, chunks,
                      business_unit, asset_class, doc_type, descriptor,
                      entity_id, silo_id,
                      indexing_status, indexing_error, indexed_at, created_at
                 FROM document_metadata ORDER BY created_at DESC""",
        )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        for k in ("indexed_at", "created_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


async def delete_document(doc_id: UUID, tenant_id: UUID | None = None) -> bool:
    """Remove a document everywhere: ledger row, stored file, and its chunk
    events (matched by the row-scoped kb-{id}-… dedupe prefix, so identical
    content in another document is never touched; RLS scopes it to the tenant)."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "DELETE FROM document_metadata WHERE id=$1 "
            "RETURNING storage_path, file_hash", doc_id,
        )
        if row is None:
            return False
        await conn.execute(
            "DELETE FROM events WHERE source->>'dedupe_key' LIKE $1",
            f"kb-{doc_id}-%",
        )
    if row["storage_path"]:
        try:
            await asyncio.to_thread(_knowledge_storage().delete, row["storage_path"])
        except Exception:  # noqa: BLE001 — best-effort storage cleanup
            pass
    return True


async def document_download_url(
    doc_id: UUID, tenant_id: UUID | None = None, expires_in: int = 3600,
) -> str | None:
    """Signed, time-limited download URL (the only way to reach the private
    knowledge bucket)."""
    async with acquire(tenant_id) as conn:
        path = await conn.fetchval(
            "SELECT storage_path FROM document_metadata WHERE id=$1", doc_id,
        )
    if not path:
        return None
    return await asyncio.to_thread(_knowledge_storage().signed_url, path, expires_in)


__all__ = [
    "ingest_knowledge_document", "ingest_zip", "is_zip",
    "list_documents", "delete_document", "document_download_url",
    "guess_mime", "SKIP_EXT", "MAX_ZIP_BYTES", "MAX_FILES_PER_ZIP",
]
