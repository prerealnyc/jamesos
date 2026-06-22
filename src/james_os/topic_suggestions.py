"""Persisted post-topic suggestions — so the composer doesn't re-ideate (and
re-spend) on every page load, and the user can curate with keep/reject.

Stored at tenants.config->'topic_suggestions' as a list of:
    {id, topic, pillar, trend_basis, title, status}
status ∈ {"pending", "accepted"}. Rejected items are removed outright.

A fresh batch (regenerate) PRESERVES accepted items (pinned at the top) and
replaces the rest with the new pending ideas, deduped by topic text so an
accepted topic never reappears as a duplicate pending one.
"""

import json
from uuid import UUID, uuid4

from .db import acquire


def _norm(it: dict) -> dict:
    return {
        "id": str(it.get("id") or uuid4()),
        "topic": str(it.get("topic") or "").strip(),
        "pillar": str(it.get("pillar") or "").strip(),
        "trend_basis": str(it.get("trend_basis") or "").strip(),
        "title": str(it.get("title") or "").strip(),
        "status": it.get("status") if it.get("status") in ("pending", "accepted") else "pending",
    }


async def get_saved(tenant_id: UUID | None = None) -> list[dict]:
    """The persisted suggestion list (may be empty)."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchval(
            "SELECT config->'topic_suggestions' FROM tenants "
            "WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
    if not row:
        return []
    try:
        data = json.loads(row) if isinstance(row, str) else row
    except Exception:  # noqa: BLE001
        return []
    return [_norm(it) for it in data if isinstance(it, dict) and (it.get("topic"))]


async def _write(tenant_id: UUID | None, items: list[dict]) -> list[dict]:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{topic_suggestions}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(items),
        )
    return items


async def save_batch(tenant_id: UUID | None, new_ideas: list[dict]) -> list[dict]:
    """Persist a fresh batch — keep accepted items (pinned first), replace the
    rest with the new ideas (deduped by topic against the accepted set)."""
    existing = await get_saved(tenant_id)
    accepted = [i for i in existing if i["status"] == "accepted"]
    accepted_topics = {i["topic"].lower() for i in accepted}
    fresh = [
        _norm({**idea, "status": "pending"})
        for idea in (new_ideas or [])
        if str(idea.get("topic") or "").strip()
        and str(idea.get("topic")).strip().lower() not in accepted_topics
    ]
    return await _write(tenant_id, accepted + fresh)


async def set_status(tenant_id: UUID | None, idea_id: str, status: str) -> list[dict]:
    """Accept (pin/keep), reject (remove), or reset a single suggestion."""
    items = await get_saved(tenant_id)
    if status == "rejected":
        items = [i for i in items if i["id"] != idea_id]
    elif status in ("accepted", "pending"):
        items = [
            {**i, "status": status} if i["id"] == idea_id else i for i in items
        ]
    return await _write(tenant_id, items)


__all__ = ["get_saved", "save_batch", "set_status"]
