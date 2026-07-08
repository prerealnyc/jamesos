"""Profile service — the only writer of profile_fields (D1/D2). Ported from
bm2.0 backend/app/services/profile.py onto the asyncpg/RLS pattern.

Rules enforced here so no agent can get them wrong:
- append-only versioning; re-writes supersede, never update
- confidence computed via the D2 rubric, never accepted from callers' LLMs
- contradiction detection between source classes, auto-materializing an
  open brand_questions row for the interviewer
- tenancy comes from the connection (db.acquire binds app.current_tenant;
  RLS scopes every statement) — no per-call brand_id threading
"""

import json
from datetime import datetime, timezone

import asyncpg

from .contracts import FieldStatus, FieldWrite, Source, compute_confidence, staleness_ttl_days

_HUMAN_SOURCES = {Source.USER_STATED, Source.NEGOTIATED}

_ROW_COLS = (
    "id, section, field_key, item_key, value, source, confidence, citations, "
    "status, version, superseded_by, updated_by, created_at"
)


def _record(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k in ("value", "citations"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["id"] = str(d["id"])
    if d.get("superseded_by"):
        d["superseded_by"] = str(d["superseded_by"])
    return d


async def current_fields(conn: asyncpg.Connection, section: str | None = None) -> list[dict]:
    if section:
        rows = await conn.fetch(
            f"SELECT {_ROW_COLS} FROM profile_fields WHERE status <> 'superseded' AND section = $1 "
            "ORDER BY field_key, version DESC",
            section,
        )
    else:
        rows = await conn.fetch(
            f"SELECT {_ROW_COLS} FROM profile_fields WHERE status <> 'superseded' "
            "ORDER BY field_key, version DESC"
        )
    return [_record(r) for r in rows]


async def _current_rows(conn: asyncpg.Connection, field_key: str, item_key: str | None) -> list[dict]:
    """ALL current rows for the key, newest version first. More than one row
    is only expected while a contradiction is open (D1)."""
    rows = await conn.fetch(
        f"SELECT {_ROW_COLS} FROM profile_fields "
        "WHERE field_key = $1 AND item_key IS NOT DISTINCT FROM $2 AND status <> 'superseded' "
        "ORDER BY version DESC",
        field_key,
        item_key,
    )
    return [_record(r) for r in rows]


def _is_human(source: Source | str) -> bool:
    return Source(source) in _HUMAN_SOURCES


def _conflicts(existing: dict, incoming: FieldWrite) -> bool:
    same_value = existing["value"].get("v") == incoming.value
    different_class = _is_human(existing["source"]) != _is_human(incoming.source)
    return (not same_value) and different_class


async def _set_status(
    conn: asyncpg.Connection, ids: list[str], status: FieldStatus, superseded_by: str | None = None
) -> None:
    if not ids:
        return
    if superseded_by is not None:
        await conn.execute(
            "UPDATE profile_fields SET status = $1, superseded_by = $2 WHERE id = ANY($3::uuid[])",
            status.value, superseded_by, ids,
        )
    else:
        await conn.execute(
            "UPDATE profile_fields SET status = $1 WHERE id = ANY($2::uuid[])", status.value, ids
        )


async def write_field(conn: asyncpg.Connection, fw: FieldWrite) -> dict:
    current = await _current_rows(conn, fw.field_key, fw.item_key)
    confidence = compute_confidence(fw.source, [c.url or c.ref for c in fw.citations], fw.primary)

    status = FieldStatus.CONFIRMED if fw.source in _HUMAN_SOURCES else FieldStatus.UNCONFIRMED
    row = await conn.fetchrow(
        f"""INSERT INTO profile_fields
              (section, field_key, item_key, value, source, confidence, citations,
               status, version, updated_by)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::jsonb, $8, $9, $10)
            RETURNING {_ROW_COLS}""",
        fw.section,
        fw.field_key,
        fw.item_key,
        json.dumps({"v": fw.value}),
        fw.source.value,
        confidence,
        json.dumps([c.model_dump() for c in fw.citations]),
        status.value,
        (current[0]["version"] + 1) if current else 1,
        fw.updated_by,
    )
    new = _record(row)

    if not current:
        return new

    same_class = [r for r in current if _is_human(r["source"]) == _is_human(fw.source)]
    conflicting = [r for r in current if _conflicts(r, fw)]

    if fw.source in _HUMAN_SOURCES:
        # human input wins: supersede EVERY current row for the key — including
        # both sides of an open contradiction — so no stale 'contradicted' row
        # can survive a user answer (D1 resolution rule).
        await _set_status(conn, [r["id"] for r in current], FieldStatus.SUPERSEDED, new["id"])
    elif conflicting:
        # D1 contradiction: the human-class row(s) it disagrees with stay
        # current and flagged; same-class predecessors are a normal refresh.
        new["status"] = FieldStatus.CONTRADICTED.value
        await _set_status(conn, [new["id"]], FieldStatus.CONTRADICTED)
        await _set_status(conn, [r["id"] for r in conflicting], FieldStatus.CONTRADICTED)
        await _set_status(conn, [r["id"] for r in same_class], FieldStatus.SUPERSEDED, new["id"])
        await _queue_contradiction_question(conn, fw.section, fw.field_key)
    else:
        # same source class refresh (researched drift): supersede quietly
        await _set_status(conn, [r["id"] for r in same_class], FieldStatus.SUPERSEDED, new["id"])
        # agreeing rows from the human class stay authoritative; archive the
        # non-human duplicate so the key keeps a single current row
        agreeing_human = [r for r in current if r not in same_class]
        if agreeing_human:
            new["status"] = FieldStatus.SUPERSEDED.value
            new["superseded_by"] = agreeing_human[0]["id"]
            await _set_status(conn, [new["id"]], FieldStatus.SUPERSEDED, agreeing_human[0]["id"])

    return new


# brand_questions.dimension vocabulary is james-os's (051_brand_questions.sql);
# map the envelope's sections onto it so the interviewer asks in its own terms.
_SECTION_DIMENSION = {
    "identity": "identity",
    "audience": "audience",
    "voice": "voice",
    "positioning": "pov",
    "products": "offerings",
    "competitors": "competitors",
    "goals": "operations",
    "guardrails": "style",
    "channels": "operations",
    "performance": "operations",
}


async def _queue_contradiction_question(
    conn: asyncpg.Connection, section: str, field_key: str
) -> None:
    """Materialize ONE open question for the contradicted field. Dedupe: an
    open/answered question already tagged with this field_key is never
    duplicated (covers the instance materialized at onboarding too)."""
    existing = await conn.fetchval(
        "SELECT id FROM brand_questions WHERE field_key = $1 AND status IN ('open','answered') LIMIT 1",
        field_key,
    )
    if existing is not None:
        return
    pretty = field_key.split(".")[-1].replace("_", " ")
    await conn.execute(
        """INSERT INTO brand_questions (dimension, question, source, status, field_key)
           VALUES ($1, $2, 'open', 'open', $3)""",
        _SECTION_DIMENSION.get(section, "identity"),
        f"We found conflicting information about your {pretty} — which is correct?",
        field_key,
    )


async def mark_stale(conn: asyncpg.Connection) -> int:
    """Nightly job: flip past-TTL current fields to stale (D2)."""
    now = datetime.now(timezone.utc)
    count = 0
    for row in await current_fields(conn):
        if row["status"] not in (FieldStatus.UNCONFIRMED.value, FieldStatus.CONFIRMED.value):
            continue
        created = row["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (now - created).days > staleness_ttl_days(row["section"]):
            await _set_status(conn, [row["id"]], FieldStatus.STALE)
            count += 1
    return count


def projection(fields: list[dict]) -> dict:
    """Flat {field_key: value} snapshot of the current envelope — the shape
    brand_profiles-style consumers read. Contradicted keys surface the
    human-class value when one exists (human answers stay authoritative)."""
    flat: dict = {}
    flat_source: dict = {}
    for row in sorted(fields, key=lambda r: (r["field_key"], r["version"])):
        key = row["field_key"] if row["item_key"] is None else f"{row['field_key']}[{row['item_key']}]"
        if key in flat and _is_human(flat_source.get(key, "")) and not _is_human(row["source"]):
            continue
        flat[key] = row["value"].get("v")
        flat_source[key] = row["source"]
    return flat
