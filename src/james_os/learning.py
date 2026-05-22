"""Rejection → learning loop.

When a manager rejects a queued draft and says why, that reason must change
future output — otherwise the system repeats the same mistake forever. We
turn each rejection into a `frustration`-category memory event (a hard
"never do this" guardrail). The content engine already:

  * retrieves the frustration bucket and injects it as <avoid> rules, and
  * fails voice-QA on any frustration violation,

so a recorded rejection actively steers — and gates — the next attempt. A
human decision is authoritative, so these events carry full confidence and
are always surfaced to the engine (see content.assemble_memory).
"""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .ingestion import ingest_many
from .models import EventCreate, EventSource

FRUSTRATION_CATEGORY = "frustration"


def rejection_to_event(payload: dict, reason: str) -> EventCreate:
    platform = str(payload.get("platform", "") or "")
    fmt = str(payload.get("format", "") or "")
    topic = str(payload.get("topic", "") or payload.get("pillar", "") or "")
    draft = str(payload.get("content") or payload.get("caption") or "")[:600]

    text = (
        f"REJECTED BY A HUMAN — learn from this and never repeat it. "
        f"A {platform or 'social'} {fmt or 'post'}"
        f"{f' about “{topic}”' if topic else ''} was rejected. "
        f"Reason given: {reason}. "
        f"Treat this reason as a hard rule for all future content. "
        f"Rejected draft (so you can recognise and AVOID this pattern — do not "
        f"reproduce it):\n{draft}"
    )
    digest = hashlib.sha256(
        f"{topic}|{reason}|{draft[:80]}".encode()
    ).hexdigest()[:16]

    return EventCreate(
        event_type="note",
        payload={
            "text": text,
            "category": FRUSTRATION_CATEGORY,
            "platform": platform,
            "format": fmt,
            "topic": topic,
            "reason": reason,
            "source": "rejection_feedback",
        },
        raw_content=text,
        source=EventSource(
            adapter="rejection_feedback",
            dedupe_key=f"reject-{digest}",
            raw_metadata={"category": FRUSTRATION_CATEGORY, "reason": reason},
        ),
        entities=[
            f"category:{FRUSTRATION_CATEGORY}",
            *([f"platform:{platform}"] if platform else []),
        ],
        effective_at=datetime.now(UTC),
        confidence=1.0,  # a human's decision is authoritative
    )


async def record_rejection(
    action_id: UUID, reason: str, tenant_id: UUID | None = None
) -> str | None:
    """Load the rejected action and persist its lesson into memory.
    Returns the stored guardrail event id (or None if nothing usable)."""
    reason = (reason or "").strip()
    if not reason or reason.lower() in ("rejected", "reject"):
        # No real reason given → nothing to learn. Don't pollute memory.
        return None
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT payload FROM actions WHERE id = $1", action_id)
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)

    event = rejection_to_event(payload or {}, reason)
    stored = await ingest_many([event], tenant_id)
    return str(stored[0].id) if stored else None


async def recent_guardrails(tenant_id: UUID | None = None, limit: int = 25) -> list[dict]:
    """The learned 'never do this' ledger from rejections, newest first —
    for surfacing in the UI so the team sees what the system has learned."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, payload, created_at FROM events
            WHERE payload ->> 'category' = $1
              AND payload ->> 'source' = 'rejection_feedback'
              AND superseded_by IS NULL
            ORDER BY created_at DESC LIMIT $2
            """,
            FRUSTRATION_CATEGORY, limit,
        )
    import json
    out = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        out.append({
            "id": str(r["id"]),
            "reason": p.get("reason", ""),
            "platform": p.get("platform", ""),
            "topic": p.get("topic", ""),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


__all__ = ["record_rejection", "rejection_to_event", "recent_guardrails",
           "FRUSTRATION_CATEGORY"]
