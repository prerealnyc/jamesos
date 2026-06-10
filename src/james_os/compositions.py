"""Composition capability registry + build queue.

The Design Inspector captures a reference's LAYOUT (full_frame |
split_horizontal | split_vertical | pip | grid | other). The renderer can
only reproduce some of them faithfully today. Rather than fake an unsupported
composition, the library SURFACES it as a build request on the dashboard — the
library of trending styles drives the render roadmap. As each composition's
Creatomate build lands, add its layout key to SUPPORTED_LAYOUTS and it goes
live automatically for every template that needs it.
"""

from uuid import UUID

from .db import acquire

# Layouts the renderer reproduces faithfully TODAY. 'full_frame' covers the
# standard single-frame modes (avatar / mixed / story). As new compositions
# are built (e.g. a real split-screen render), add their layout key here and
# the dashboard flips them from "queued" → "live" with no other change.
SUPPORTED_LAYOUTS: set[str] = {"", "full_frame", "none"}


def is_supported(layout_type: str | None) -> bool:
    return (layout_type or "").strip().lower() in SUPPORTED_LAYOUTS


async def composition_queue(tenant_id: UUID | None = None) -> list[dict]:
    """Distinct layouts seen across ready templates, each tagged live (we can
    render it) or queued (a composition to build), with an example + count.
    Old templates with no captured layout count as 'full_frame'."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT
              coalesce(nullif(template->'layout'->>'type', ''), 'full_frame') AS layout_type,
              count(*) AS n,
              (array_agg(name ORDER BY created_at DESC))[1] AS example,
              (array_agg(coalesce(template->'layout'->>'description', '')
                         ORDER BY created_at DESC))[1] AS description
            FROM style_templates
            WHERE status = 'ready'
            GROUP BY 1
            ORDER BY n DESC
            """
        )
    out: list[dict] = []
    for r in rows:
        lt = r["layout_type"]
        supported = is_supported(lt)
        out.append({
            "layout_type": lt,
            "count": int(r["n"]),
            "example": r["example"] or "",
            "description": r["description"] or "",
            "supported": supported,
            "status": "live" if supported else "queued",
        })
    return out


__all__ = ["SUPPORTED_LAYOUTS", "is_supported", "composition_queue"]
