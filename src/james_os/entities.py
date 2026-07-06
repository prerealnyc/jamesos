"""Entity registry — ported from the PreReal Intelligence platform
(entity-id.ts + resolve-entity.ts): stable EntityIDs in the
`[BU]-[TypeCode]-[ZeroPaddedNumber]` family, auto-numbered per family, and
`find_or_create_entity` so auto-filing converges repeated documents about the
same subject onto ONE entity instead of spawning duplicates."""

from __future__ import annotations

import re
from uuid import UUID

from .db import acquire
from .vocab import BU_CODES, ENTITY_ID_REGEX, ENTITY_TYPE_CODES

_PARSE_RE = re.compile(r"^([A-Z]{3})-([A-Za-z]+)-([0-9]+)$")


def is_valid_entity_id(entity_id: str) -> bool:
    return bool(ENTITY_ID_REGEX.match(entity_id or ""))


def parse_entity_id(entity_id: str) -> dict | None:
    m = _PARSE_RE.match(entity_id or "")
    if not m:
        return None
    return {"bu": m.group(1), "type_code": m.group(2),
            "number": int(m.group(3)), "width": len(m.group(3))}


def format_entity_id(bu: str, type_code: str, num: int, width: int = 3) -> str:
    return f"{bu}-{type_code}-{str(num).zfill(width)}"


async def suggest_next_entity_id(
    bu: str, type_code: str, tenant_id: UUID | None = None,
) -> str:
    """Next available EntityID for a BU+type family. Width matches the widest
    existing ID in the family (default 3; LND defaults to 4 per spec)."""
    prefix = f"{bu}-{type_code}-"
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id FROM entities WHERE id LIKE $1", f"{prefix}%",
        )
    max_num = 0
    max_width = 4 if bu == "LND" else 3
    for r in rows:
        parsed = parse_entity_id(r["id"])
        if not parsed:
            continue
        max_num = max(max_num, parsed["number"])
        max_width = max(max_width, parsed["width"])
    return format_entity_id(bu, type_code, max_num + 1, max_width)


async def find_or_create_entity(
    *,
    business_unit: str,
    entity_type_code: str,
    display_name: str,
    tenant_id: UUID | None = None,
) -> str | None:
    """Reuse an existing (non-archived) entity with the same name in this BU
    (case-insensitive), else mint a new one with the next free ID. Returns the
    EntityID, or None when the inputs can't form a valid entity."""
    bu = (business_unit or "").upper()
    type_code = (entity_type_code if entity_type_code in ENTITY_TYPE_CODES else "D")
    name = (display_name or "").strip()[:120]
    if bu not in BU_CODES or not name:
        return None

    async with acquire(tenant_id) as conn:
        existing = await conn.fetchval(
            "SELECT id FROM entities WHERE business_unit = $1 "
            "AND lower(display_name) = lower($2) AND archived_at IS NULL LIMIT 1",
            bu, name,
        )
        if existing:
            return existing

    try:
        new_id = await suggest_next_entity_id(bu, type_code, tenant_id)
        async with acquire(tenant_id) as conn:
            await conn.execute(
                """INSERT INTO entities
                     (id, business_unit, entity_type_code, display_name, notes)
                   VALUES ($1, $2, $3, $4, 'Auto-created during ingest')""",
                new_id, bu, type_code, name,
            )
        return new_id
    except Exception:  # noqa: BLE001 — most likely a concurrent-ingest race:
        # re-resolve by name and use whatever landed.
        try:
            async with acquire(tenant_id) as conn:
                return await conn.fetchval(
                    "SELECT id FROM entities WHERE business_unit = $1 "
                    "AND lower(display_name) = lower($2) "
                    "AND archived_at IS NULL LIMIT 1",
                    bu, name,
                )
        except Exception:  # noqa: BLE001
            return None


async def list_entities(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, business_unit, entity_type_code, display_name, notes, "
            "archived_at, created_at FROM entities "
            "WHERE archived_at IS NULL ORDER BY id",
        )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("archived_at", "created_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


__all__ = [
    "is_valid_entity_id", "parse_entity_id", "format_entity_id",
    "suggest_next_entity_id", "find_or_create_entity", "list_entities",
]
