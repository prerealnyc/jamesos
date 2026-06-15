"""Saved trending posts — the curation shelf behind Social Listening.

Save interesting posts from a search, then pick which ones to turn into
content. Tenant-scoped via RLS (see migration 037). Generating a draft
reuses the proven content engine + voice-QA path.
"""

from __future__ import annotations

from uuid import UUID

from .db import acquire


def _row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id", "action_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    return d


async def save_post(post: dict, niche: str = "", tenant_id: UUID | None = None) -> dict:
    """Save (idempotent) a post from a search result. Re-saving the same
    post returns the existing row rather than duplicating."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO social_saved_posts
                 (source_platform, post_id, author, text, likes, comments, url, niche)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (tenant_id, source_platform, post_id)
               DO UPDATE SET niche = EXCLUDED.niche
               RETURNING *""",
            str(post.get("platform") or ""), str(post.get("id") or ""),
            str(post.get("author") or ""), str(post.get("text") or "")[:4000],
            int(post.get("likes") or 0), int(post.get("comments") or 0),
            str(post.get("url") or ""), niche[:200],
        )
    return _row(row)


async def list_saved(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM social_saved_posts ORDER BY created_at DESC LIMIT 200"
        )
    return [_row(r) for r in rows]


async def delete_saved(saved_id: UUID, tenant_id: UUID | None = None) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute("DELETE FROM social_saved_posts WHERE id = $1", saved_id)


async def get_saved(saved_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT * FROM social_saved_posts WHERE id = $1", saved_id)
    return _row(row) if row else None


async def mark_drafted(saved_id: UUID, action_id: str | None, tenant_id: UUID | None = None) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE social_saved_posts SET status='drafted', action_id=$2 WHERE id=$1",
            saved_id, UUID(action_id) if action_id else None,
        )


__all__ = ["save_post", "list_saved", "delete_saved", "get_saved", "mark_drafted"]
