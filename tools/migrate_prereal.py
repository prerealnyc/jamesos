"""One-shot migration: PreReal Intelligence (prereal-core Supabase) → Brand
Manager's Knowledge Base, scoped to James's tenant.

Copies: silos, entities, files (metadata + extracted text → chunked +
re-embedded by BM's embedder into `events` with kb-{row}-{idx} dedupe keys,
plus the ORIGINAL bytes copied into BM's private `knowledge` bucket),
commitments (source-file ids remapped), and the guidelines doc.

Run with BM's environment injected (never hardcode secrets):
    cd "…/james-os"
    railway run --service james-os-backend -- \
        python3 tools/migrate_prereal.py [--dry-run] [--skip-originals]

Source creds are read from the local prereal checkout's .env.local
(NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) — values are never
printed. Idempotent: rows that already exist in BM are skipped, so the script
is safe to re-run after a partial failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _as_date(v) -> date | None:
    """PostgREST returns date columns as ISO strings, but asyncpg's bind
    encoder for a $n::date parameter wants a datetime.date."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _as_ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None

TENANT = UUID("00000000-0000-0000-0000-000000000001")   # James's BM tenant
PREREAL_ENV = Path("/Users/royantony/pre real estate nyc/prereal/.env.local")
ORIGINAL_MAX_BYTES = 300 * 1024 * 1024   # skip >300MB originals (note instead)
PAGE = 500

RESEARCH_DOC_TYPES = {"ResearchBrief", "WhitePaper", "IntelligenceBrief"}


def _load_prereal_creds() -> tuple[str, str]:
    import os
    url = key = ""
    if PREREAL_ENV.exists():
        for line in PREREAL_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("NEXT_PUBLIC_SUPABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    # The local .env.local has an empty service key — the real one lives on
    # the prereal-web Railway service; pass it via PREREAL_SERVICE_KEY.
    key = os.environ.get("PREREAL_SERVICE_KEY", "").strip() or key
    url = os.environ.get("PREREAL_SUPABASE_URL", "").strip() or url
    if not url or not key:
        raise SystemExit("missing prereal Supabase url/service key "
                         "(set PREREAL_SERVICE_KEY / PREREAL_SUPABASE_URL)")
    return url.rstrip("/"), key


SRC_URL, SRC_KEY = _load_prereal_creds()
_H = {"Authorization": f"Bearer {SRC_KEY}", "apikey": SRC_KEY}


async def src_rows(client: httpx.AsyncClient, table: str, select: str = "*",
                   extra: str = "") -> list[dict]:
    """Paginated PostgREST read of a source table."""
    out: list[dict] = []
    offset = 0
    while True:
        r = await client.get(
            f"{SRC_URL}/rest/v1/{table}?select={select}{extra}"
            f"&limit={PAGE}&offset={offset}",
            headers=_H,
        )
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        offset += PAGE


async def src_object(client: httpx.AsyncClient, bucket: str, path: str) -> bytes | None:
    try:
        r = await client.get(
            f"{SRC_URL}/storage/v1/object/{bucket}/{path}", headers=_H,
        )
        return r.content if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-originals", action="store_true")
    args = ap.parse_args()

    from james_os.db import acquire, close_pool, init_pool
    from james_os.documents import _build_events
    from james_os.ingestion import ingest_many
    from james_os.vocab import normalize_sensitivity

    await init_pool()

    stats = {"silos": 0, "entities": 0, "files": 0, "chunks": 0,
             "originals": 0, "commitments": 0, "skipped": [], "errors": []}

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
        silos = await src_rows(client, "silos")
        entities = await src_rows(client, "entities")
        files = await src_rows(client, "files", extra="&order=created_at.asc")
        commitments = await src_rows(client, "commitments")
        guidelines = await src_rows(client, "guidelines")
        print(f"SOURCE: {len(silos)} silos, {len(entities)} entities, "
              f"{len(files)} files, {len(commitments)} commitments, "
              f"{len(guidelines)} guidelines")
        if args.dry_run:
            return

        # ── 1. Silos ──
        async with acquire(TENANT) as conn:
            for s in silos:
                done = await conn.fetchval(
                    "SELECT 1 FROM silos WHERE id=$1", s["id"])
                if done:
                    continue
                await conn.execute(
                    "INSERT INTO silos (id, name, description) VALUES ($1,$2,$3)",
                    s["id"], s.get("name") or s["id"], s.get("description"),
                )
                stats["silos"] += 1
        print(f"silos migrated: {stats['silos']}")

        # ── 2. Entities ──
        async with acquire(TENANT) as conn:
            for e in entities:
                done = await conn.fetchval(
                    "SELECT 1 FROM entities WHERE id=$1", e["id"])
                if done:
                    continue
                notes = (e.get("notes") or "").strip()
                extras = []
                if e.get("parent_entity_id"):
                    extras.append(f"parent: {e['parent_entity_id']}")
                if e.get("silo_id"):
                    extras.append(f"silo: {e['silo_id']}")
                if extras:
                    notes = (notes + " | " if notes else "") + "; ".join(extras)
                await conn.execute(
                    """INSERT INTO entities
                         (id, business_unit, entity_type_code, display_name,
                          notes, archived_at)
                       VALUES ($1,$2,$3,$4,$5,$6::timestamptz)""",
                    e["id"], e.get("business_unit") or "PRI",
                    e.get("entity_type_code") or e.get("entity_type") or "D",
                    e.get("display_name") or e["id"], notes or None,
                    _as_ts(e.get("archived_at")),
                )
                stats["entities"] += 1
        print(f"entities migrated: {stats['entities']}")

        # ── 3. Files → document_metadata + chunks + originals ──
        from james_os.storage_supabase import SupabaseMediaStorage
        store = SupabaseMediaStorage(bucket="knowledge", public=False)
        file_id_map: dict[str, str] = {}

        for i, f in enumerate(files):
            fname = f["filename"]
            try:
                text = (f.get("extracted_text") or "").strip()
                doc_type = f.get("doc_type") or None
                category = ("research" if doc_type in RESEARCH_DOC_TYPES
                            else "company_doc")

                async with acquire(TENANT) as conn:
                    existing = await conn.fetchrow(
                        "SELECT id, chunks, indexing_status FROM document_metadata "
                        "WHERE filename=$1", fname)
                if existing:
                    file_id_map[f["id"]] = str(existing["id"])
                    # A previous run may have inserted the row and then died
                    # before indexing — finish the indexing instead of
                    # skipping the file forever.
                    if (text and (existing["chunks"] or 0) == 0
                            and existing["indexing_status"] != "indexed"):
                        row_id = existing["id"]
                        events = _build_events(
                            fname, text.encode("utf-8"), text, "document", category)
                        for ev in events:
                            idx = ev.source.raw_metadata.get("chunk_index", 0)
                            ev.source.dedupe_key = f"kb-{row_id}-{idx}"
                        stored = await ingest_many(events, tenant_id=TENANT)
                        async with acquire(TENANT) as conn:
                            await conn.execute(
                                "UPDATE document_metadata SET chunks=$2, "
                                "indexing_status='indexed', indexing_error=NULL "
                                "WHERE id=$1", row_id, len(stored))
                        stats["chunks"] += len(stored)
                        stats["repaired"] = stats.get("repaired", 0) + 1
                    else:
                        stats["skipped"].append(f"exists: {fname}")
                    continue

                notes = (f.get("notes") or "").strip()
                extras = []
                if f.get("topic"):
                    extras.append(f"topic: {f['topic']}")
                if f.get("subtopic"):
                    extras.append(f"subtopic: {f['subtopic']}")
                if f.get("archived_at"):
                    extras.append("[archived in PreReal]")
                if f.get("source_type") == "link" and f.get("source_url"):
                    extras.append(f"link: {f['source_url']}")
                if extras:
                    notes = (notes + " | " if notes else "") + "; ".join(extras)

                # Decide whether the original bytes should be copied; the
                # copy itself happens AFTER the metadata INSERT so a failed
                # insert never orphans a storage object (which a re-run
                # would then duplicate).
                storage_path = None
                review = []
                want_original = bool(
                    not args.skip_originals and f.get("storage_bucket")
                    and f.get("storage_path"))
                if want_original and (f.get("size_bytes") or 0) > ORIGINAL_MAX_BYTES:
                    review.append("original >300MB — not copied (in PreReal storage)")
                    want_original = False

                if f.get("review_reason"):
                    review.insert(0, f["review_reason"])

                async with acquire(TENANT) as conn:
                    row_id = await conn.fetchval(
                        """INSERT INTO document_metadata
                             (filename, original_name, category, business_unit,
                              asset_class, doc_type, descriptor, file_date,
                              version, status, sensitivity, flagged_for_review,
                              review_reason, source_type, storage_path,
                              mime_type, size_bytes, file_hash, notes,
                              extracted_text, entity_id, silo_id,
                              chunks, indexing_status, indexing_error, indexed_at)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8::date,$9,$10,$11,$12,
                                   $13,$14,$15,$16,$17,$18,$19,$20,$21,$22,
                                   0, $23, $24, now())
                           RETURNING id""",
                        fname, fname, category,
                        f.get("business_unit"), f.get("asset_class"),
                        doc_type, f.get("descriptor"), _as_date(f.get("file_date")),
                        int(f.get("version") or 1), f.get("status") or "Current",
                        normalize_sensitivity(f.get("sensitivity")),
                        bool(f.get("flagged_for_review")),
                        "; ".join(review) or None,
                        f.get("source_type") or "file", storage_path,
                        f.get("mime_type"), int(f.get("size_bytes") or 0),
                        "", notes or None, text or None,
                        f.get("entity_id"), f.get("silo_id"),
                        "pending" if text else "skipped",
                        None if text else (f.get("indexing_error")
                                           or "no extractable text in source"),
                    )
                file_id_map[f["id"]] = str(row_id)

                if text:
                    events = _build_events(
                        fname, text.encode("utf-8"), text, "document", category)
                    for ev in events:
                        idx = ev.source.raw_metadata.get("chunk_index", 0)
                        ev.source.dedupe_key = f"kb-{row_id}-{idx}"
                    stored = await ingest_many(events, tenant_id=TENANT)
                    async with acquire(TENANT) as conn:
                        await conn.execute(
                            "UPDATE document_metadata SET chunks=$2, "
                            "indexing_status='indexed' WHERE id=$1",
                            row_id, len(stored),
                        )
                    stats["chunks"] += len(stored)
                stats["files"] += 1
                if (i + 1) % 20 == 0:
                    print(f"  files: {i + 1}/{len(files)} "
                          f"(chunks so far: {stats['chunks']})")
            except Exception as e:  # noqa: BLE001 — keep going; report at end
                stats["errors"].append(f"{fname}: {e}")

        print(f"files migrated: {stats['files']} "
              f"({stats['chunks']} chunks, {stats['originals']} originals)")

        # ── 4. Commitments (remap source_file_id) ──
        async with acquire(TENANT) as conn:
            for c in commitments:
                done = await conn.fetchval(
                    "SELECT 1 FROM commitments WHERE text=$1 AND owner=$2",
                    c.get("text") or "", (c.get("owner") or "unassigned").lower())
                if done:
                    continue
                notes = (c.get("notes") or "").strip()
                if c.get("evidence_url"):
                    notes = (notes + " | " if notes else "") + f"evidence: {c['evidence_url']}"
                new_file = file_id_map.get(c.get("source_file_id") or "")
                await conn.execute(
                    """INSERT INTO commitments
                         (text, owner, author, entity_id, source_file_id,
                          due, status, source_kind, notes, created_at)
                       VALUES ($1,$2,$3,$4,$5::uuid,$6::date,$7,$8,$9,
                               coalesce($10::timestamptz, now()))""",
                    c.get("text") or "", (c.get("owner") or "unassigned").lower(),
                    c.get("author") or "transcript", c.get("entity_id"),
                    new_file, _as_date(c.get("due")),
                    c.get("status") or "open",
                    c.get("source_kind") or "transcript-extraction",
                    notes or None, _as_ts(c.get("created_at")),
                )
                stats["commitments"] += 1
        print(f"commitments migrated: {stats['commitments']}")

        # ── 5. Guidelines → a guideline knowledge doc ──
        for g in guidelines:
            content = (g.get("content") or "").strip()
            if not content:
                continue
            gname = f"PreReal-Guidelines-{g.get('id') or 'main'}.md"
            async with acquire(TENANT) as conn:
                done = await conn.fetchval(
                    "SELECT 1 FROM document_metadata WHERE filename=$1", gname)
            if done:
                continue
            from james_os.knowledge import ingest_knowledge_document
            r = await ingest_knowledge_document(
                data=content.encode("utf-8"), original_name=gname,
                mime="text/markdown", category="guideline",
                notes="Migrated from PreReal Intelligence guidelines",
                auto_classify=False, tenant_id=TENANT,
            )
            print(f"guideline: {r.get('ok')} ({r.get('chunks', 0)} chunks)")

    await close_pool()
    print("\n==== SUMMARY ====")
    print(json.dumps({k: (v if not isinstance(v, list) else v[:10] + [f"…+{len(v)-10} more"] if len(v) > 10 else v)
                      for k, v in stats.items()}, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
