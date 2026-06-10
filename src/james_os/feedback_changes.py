"""The feedback → change store + the "What's changing next" roadmap.

Each interpreted feedback item becomes a feedback_changes row:
  * kind='live_config' (high confidence, a known knob, value in range) →
    the knob is APPLIED instantly via render_tuning and status='applied'
    (shown struck-off as "live" on the board).
  * kind='code_change' → status='queued' (a plain-English roadmap item a
    coding session ships; only a human flips it to 'done').

Honesty: a queued code change is NEVER auto-marked done; live tweaks are
clamped + confidence-gated and are reversible (reset render_tuning).
"""

import hashlib
import json
from uuid import UUID

from .db import acquire
from .render_tuning import set_render_tuning

_MIN_CONFIDENCE = 0.75


def _dedupe(area: str, config_key: str, plain_english: str) -> str:
    return hashlib.sha256(
        f"{area}|{config_key}|{plain_english[:60].lower()}".encode()
    ).hexdigest()[:24]


def _row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id", "source_event_id", "production_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    d.pop("tenant_id", None)
    if isinstance(d.get("config_value"), str):
        try:
            d["config_value"] = json.loads(d["config_value"])
        except Exception:  # noqa: BLE001
            pass
    for k in ("created_at", "applied_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def record_change(
    *,
    area: str,
    diagnosis: str,
    plain_english: str,
    kind: str,
    config_key: str | None = None,
    config_value=None,
    confidence: float = 0.0,
    production_id: UUID | None = None,
    source_event_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> dict | None:
    """Insert (or refresh) a roadmap item. When kind='live_config' and it
    passes the gate, apply the knob now and mark it 'applied'."""
    dedupe = _dedupe(area, config_key or "", plain_english)
    status = "queued"
    if (
        kind == "live_config"
        and config_key
        and config_value is not None
        and float(confidence) >= _MIN_CONFIDENCE
    ):
        try:
            await set_render_tuning({config_key: config_value}, tenant_id)
            status = "applied"
        except Exception:  # noqa: BLE001 — never fake an apply; fall back to queued
            status = "queued"
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            """
            INSERT INTO feedback_changes
              (source_event_id, production_id, area, diagnosis, plain_english,
               kind, config_key, config_value, confidence, status, dedupe_key,
               applied_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,
                    CASE WHEN $10='applied' THEN now() ELSE NULL END)
            ON CONFLICT (tenant_id, dedupe_key) DO UPDATE
              SET updated_at = now()
            RETURNING *
            """,
            source_event_id, production_id, area, diagnosis, plain_english,
            kind, config_key, json.dumps(config_value), float(confidence),
            status, dedupe,
        )
    return _row(r) if r else None


async def list_changes(tenant_id: UUID | None = None, limit: int = 100) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM feedback_changes WHERE status <> 'dismissed' "
            "ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [_row(r) for r in rows]


async def _set_status(change_id: UUID, status: str, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "UPDATE feedback_changes SET status=$2, updated_at=now() "
            "WHERE id=$1 RETURNING id",
            change_id, status,
        )
    return r is not None


async def mark_done(change_id: UUID, tenant_id: UUID | None = None) -> bool:
    return await _set_status(change_id, "done", tenant_id)


async def dismiss(change_id: UUID, tenant_id: UUID | None = None) -> bool:
    return await _set_status(change_id, "dismissed", tenant_id)


__all__ = ["record_change", "list_changes", "mark_done", "dismiss"]
