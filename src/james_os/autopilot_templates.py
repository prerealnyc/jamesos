"""Pick DISTINCT style templates for an Autopilot video batch.

The video half of a bulk batch should look varied: each reel rendered in a
different library style (5 videos → 5 styles). This hands out distinct,
cycling template choices in trending order. An empty library returns [] and
the caller falls back to normal, no-template generation. Reuses the same
templates store and the same 'ready'-only guard the /replicate endpoint uses;
it never mutates anything.
"""

from uuid import UUID

from . import templates as T


async def list_ready_templates(tenant_id: UUID | None = None) -> list[dict]:
    """Ready, replicable templates — highest trending_score first (the order
    templates.list_templates already returns)."""
    rows = await T.list_templates(tenant_id)
    return [t for t in rows if t.get("status") == "ready" and t.get("template")]


def assign_distinct(templates: list[dict], n: int) -> list[dict]:
    """`n` template choices: distinct within each pass through the library,
    cycling once exhausted. Empty library → [] (caller does normal
    generation). Deterministic, so it's reproducible and testable."""
    if not templates or n <= 0:
        return []
    return [templates[i % len(templates)] for i in range(n)]


async def pick_distinct_templates(
    n: int, tenant_id: UUID | None = None
) -> list[dict]:
    """Fetch the ready library once and hand out `n` distinct/cycling choices."""
    return assign_distinct(await list_ready_templates(tenant_id), n)


__all__ = ["list_ready_templates", "assign_distinct", "pick_distinct_templates"]
