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

# Already-shipped code fixes — the "what's been built" ledger. When new
# feedback maps to a fix that's ALREADY in the codebase, we record it as
# 'done' (with the ship note) instead of re-queuing it, so the same work
# is never requested twice. Add an entry the moment a code change ships.
# Match = same `area` AND any keyword present in the plain-English summary.
SHIPPED_FIXES: list[dict] = [
    {
        "area": "captions",
        "keywords": [
            "placement", "position", "white space", "whitespace", "centered",
            "centre", "safe zone", "safe-zone", "overlap", "cut off", "edge",
            "too high", "too low", "on the side", "top", "bottom",
        ],
        "note": "shipped 2026-06-14: platform safe-zone caption placement (title top, "
                "subtitle at chin, off the side rails) + auto-fit so hooks never wrap.",
    },
    {
        "area": "voice",
        "keywords": [
            "story arc", "story-arc", "numbered list", "listicle", "flowing",
            "single narrative", "continuous narrative", "n tips", "bullet",
        ],
        "note": "shipped 2026-05: single-arc story scripts with a concrete insight "
                "(no listicles / numbered lists).",
    },
    {
        "area": "broll",
        "keywords": ["too short", "duration", "longer", "2s", "3s", "flash", "uncanny", "motion"],
        "note": "shipped 2026-06-13: B-roll pacing presets (illustrative 4-5s holds default) "
                "+ raised insert duration.",
    },
]


def _already_shipped(area: str, plain_english: str) -> str | None:
    """Return the ship-note if this feedback matches an already-built fix."""
    text = (plain_english or "").lower()
    for fix in SHIPPED_FIXES:
        if fix["area"] == area and any(k in text for k in fix["keywords"]):
            return fix["note"]
    return None


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
    # Already built? Resolve to 'done' with the ship note instead of asking
    # for the same work again. (Skipped for live_config, which self-applies.)
    shipped_note = _already_shipped(area, plain_english)
    if shipped_note and kind != "live_config":
        status = "done"
        diagnosis = f"{diagnosis}  ✓ Already shipped — {shipped_note}".strip()
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
