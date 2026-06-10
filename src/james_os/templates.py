"""Style template store — the "library of trending video styles".

A style template is the Design Inspector's structured read of a reference
video (see design_inspector.py), stored as a NAMED, reusable record. This
module owns the CRUD + the orchestration that turns an uploaded
style_reference asset into a stored template:

    media asset (style_reference)
        → design_inspector.inspect_file(file)        # watch the whole clip
        → save_analysis(media)                        # refresh the card
        → upsert style_templates row                  # the named library entry

One template per reference video (re-inspection updates in place via the
unique index on reference_media_id).
"""

import json
import re
from uuid import UUID

from .config import settings
from .db import acquire

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return s[:60] or "style"


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    if d.get("reference_media_id") is not None:
        d["reference_media_id"] = str(d["reference_media_id"])
    d.pop("tenant_id", None)
    if isinstance(d.get("template"), str):
        d["template"] = json.loads(d["template"])
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def list_templates(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM style_templates ORDER BY trending_score DESC, created_at DESC"
        )
    return [_row(r) for r in rows]


async def get_template(template_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow("SELECT * FROM style_templates WHERE id = $1", template_id)
    return _row(r) if r else None


async def rename_template(
    template_id: UUID,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    trending_score: float | None = None,
    tenant_id: UUID | None = None,
) -> dict | None:
    sets, args = [], []
    if name is not None and name.strip():
        args.append(name.strip()[:120]); sets.append(f"name = ${len(args)}")
        args.append(_slugify(name)); sets.append(f"slug = ${len(args)}")
    if tags is not None:
        args.append(tags); sets.append(f"tags = ${len(args)}::text[]")
    if trending_score is not None:
        args.append(float(trending_score)); sets.append(f"trending_score = ${len(args)}")
    if not sets:
        return None
    sets.append("updated_at = now()")
    args.append(template_id)
    sql = f"UPDATE style_templates SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *"
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(sql, *args)
    return _row(r) if r else None


async def delete_template(template_id: UUID, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "DELETE FROM style_templates WHERE id = $1 RETURNING id", template_id
        )
    return r is not None


async def _upsert_for_media(
    *,
    reference_media_id: UUID,
    name: str,
    summary: str,
    format_type: str,
    production_mode: str,
    duration: int,
    template: dict,
    transcript: str,
    status: str,
    tenant_id: UUID | None,
) -> dict:
    """Insert a template for this reference, or update the existing one
    (one live template per reference video)."""
    async with acquire(tenant_id) as conn:
        existing = await conn.fetchval(
            "SELECT id FROM style_templates WHERE reference_media_id = $1",
            reference_media_id,
        )
        if existing:
            r = await conn.fetchrow(
                """
                UPDATE style_templates SET
                  name = $2, slug = $3, summary = $4, format_type = $5,
                  production_mode = $6, duration = $7, template = $8::jsonb,
                  transcript = $9, status = $10, updated_at = now()
                WHERE id = $1 RETURNING *
                """,
                existing, name, _slugify(name), summary, format_type,
                production_mode, duration, json.dumps(template), transcript, status,
            )
        else:
            r = await conn.fetchrow(
                """
                INSERT INTO style_templates
                  (reference_media_id, name, slug, summary, format_type,
                   production_mode, duration, template, transcript, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)
                RETURNING *
                """,
                reference_media_id, name, _slugify(name), summary, format_type,
                production_mode, duration, json.dumps(template), transcript, status,
            )
    return _row(r)


async def build_template_from_media(
    media_id: UUID,
    *,
    tenant_id: UUID | None = None,
    file_path: str | None = None,
) -> dict | None:
    """Run the Design Inspector on a style_reference asset → refresh its
    Media-Library analysis AND upsert the named style template.

    Returns {"status", "template"|None, "note"} or None when the asset can't
    be analyzed (missing, or a URL reference without a local file)."""
    # Imported here to avoid a heavy import chain at module load.
    from . import design_inspector as DI
    from .media import get_media_for_analysis, save_analysis, set_analysis_status

    tenant = tenant_id or settings.default_tenant_id
    if file_path is None:
        asset = await get_media_for_analysis(media_id, tenant)
        if asset is None:
            return None
        if asset.get("source_type") != "upload" or not asset.get("file_path"):
            await set_analysis_status(media_id, "unsupported", tenant)
            return None
        file_path = asset["file_path"]

    await set_analysis_status(media_id, "pending", tenant)
    result = await DI.inspect_file(file_path)
    status = result.get("status", "failed")
    template = result.get("template") or {}

    # Refresh the Media-Library card so the "What it sees" panel still renders
    # for style references (legacy fingerprint shape) + carry the full template.
    await save_analysis(
        media_id,
        status=status,
        transcript=result.get("transcript", ""),
        analysis={
            "fingerprint": DI.template_to_fingerprint(template),
            "template": template,
            "design_inspector": True,
        },
        notes=DI.template_to_notes(template),
        duration=result.get("duration", 0),
        tenant_id=tenant,
    )

    if status != "done" or not template:
        return {"status": status, "template": None, "note": result.get("note", "")}

    row = await _upsert_for_media(
        reference_media_id=media_id,
        name=(template.get("style_name") or "Untitled style").strip()[:120],
        summary=str(template.get("summary", ""))[:500],
        format_type=str(template.get("format_type", "")),
        production_mode=str(template.get("production_mode", "")),
        duration=int(result.get("duration", 0) or 0),
        template=template,
        transcript=result.get("transcript", ""),
        status="ready",
        tenant_id=tenant,
    )
    return {"status": "done", "template": row}


__all__ = [
    "list_templates",
    "get_template",
    "rename_template",
    "delete_template",
    "build_template_from_media",
]
