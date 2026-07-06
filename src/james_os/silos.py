"""Silos — project/topic corpus groupings (ported from the PreReal
Intelligence platform's silos API). A silo scopes retrieval, white papers,
and synthesis to one project's documents."""

from __future__ import annotations

import re
from uuid import UUID

from .db import acquire


def slugify(name: str) -> str:
    """Stable silo id from a name: lowercase, alphanumeric, no spaces."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())[:40]


async def list_silos(tenant_id: UUID | None = None, stats: bool = False) -> list[dict]:
    async with acquire(tenant_id) as conn:
        if stats:
            rows = await conn.fetch(
                """SELECT s.id, s.name, s.description,
                          count(d.id) AS files
                     FROM silos s
                     LEFT JOIN document_metadata d ON d.silo_id = s.id
                    GROUP BY s.id, s.name, s.description
                    ORDER BY s.name""",
            )
        else:
            rows = await conn.fetch(
                "SELECT id, name, description FROM silos ORDER BY name",
            )
    return [dict(r) for r in rows]


async def create_silo(
    name: str, silo_id: str = "", description: str = "",
    tenant_id: UUID | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    sid = (silo_id or "").strip() or slugify(name)
    if not sid:
        raise ValueError("could not derive a silo id from the name")
    async with acquire(tenant_id) as conn:
        exists = await conn.fetchval("SELECT 1 FROM silos WHERE id = $1", sid)
        if exists:
            raise ValueError(f'A silo with id "{sid}" already exists.')
        row = await conn.fetchrow(
            "INSERT INTO silos (id, name, description) VALUES ($1, $2, $3) "
            "RETURNING id, name, description",
            sid, name, (description or "").strip() or None,
        )
    return dict(row)


async def delete_silo(silo_id: str, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        # Unlink documents (they stay; just lose the grouping), then drop.
        await conn.execute(
            "UPDATE document_metadata SET silo_id = NULL WHERE silo_id = $1",
            silo_id,
        )
        status = await conn.execute("DELETE FROM silos WHERE id = $1", silo_id)
    return status.rsplit(" ", 1)[-1] != "0"


__all__ = ["list_silos", "create_silo", "delete_silo", "slugify"]
