"""What a designed post falls back to when the learned layout it asked for
cannot be drawn — and the record of why.

BM2 gives a share of its image orders the "learned" slot (a layout from the
brand's design-template library). When that misses — the library is too thin
to rotate, the render fails design QA, or something throws — the post used to
fall to an art-director FREE pick, throwing away the format BM2's rotation
would have chosen for that order. BM2 now sends that pick as
`fallback_format`, and this module decides whether it may be used: only a
format the renderer can draw, and only one the brand allows.

It also stamps the post with `requested_format` and `fallback_reason`, so the
learned-miss rate and the real format mix per brand are measurable instead of
inferred from the served format alone.

An EMPTY allowed set (an admin save that turned every designed template off,
or a learned-only list — "learned" is a slot, not a layout, so it is stripped
before this point) means "photo layouts + learned", never "no designed image":
that empty set is what was handing one brand a bare hero photo on 11 of 12 posts.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from .designed_render import ALL_FORMATS, PHOTO_FORMATS

log = logging.getLogger(__name__)

# The formats a fallback may name: the nine single-image layouts plus the two
# carousel decks. "learned" is the slot that just missed, so never a fallback.
DRAWABLE = frozenset(ALL_FORMATS | {"carousel", "text_carousel"})

# Why a requested learned layout was not served. "none" = it was served. This
# is SPEC3's closed vocabulary — BM2 reads it; add a value only with BM2.
REASONS = ("none", "thin_library", "qa_failed", "error")

# What a brand with nothing enabled is drawn in after a learned miss: a photo
# layout over its own photo, a stock photo, or a palette ground.
EMPTY_SET_FALLBACK = "full_bleed"

# Clock allowance between this process and the database when matching a QA
# strike to the attempt that just ran.
_SKEW = timedelta(seconds=5)


def now() -> datetime:
    return datetime.now(UTC)


def effective_allowed(allowed: set[str] | None) -> set[str] | None:
    """None stays None (no restriction). An EMPTY set becomes the photo layouts:
    a brand with nothing enabled gets a photo-led post, not a bare photo."""
    if allowed is not None and not allowed:
        return set(PHOTO_FORMATS)
    return allowed


def usable_fallback(fmt: str, allowed: set[str] | None) -> str:
    """`fmt` if it can stand in for a missed learned layout, else "".

    It must be a format the renderer can draw, and one this brand allows — a
    fallback the brand turned off is no better than the free pick it replaces."""
    f = str(fmt or "").strip().lower()
    if f not in DRAWABLE:
        return ""
    allowed = effective_allowed(allowed)
    if allowed is not None and f not in allowed:
        return ""
    return f


async def fallback_for(tenant_id, fallback_format: str) -> str:
    """The format to pin after a learned miss: BM2's rotation pick when the brand
    allows it, else "" (the art director chooses, inside the allowed set).
    A brand with NOTHING enabled always gets full_bleed — never a free pick."""
    try:
        from .brand_identity import get_enabled_formats
        allowed = await get_enabled_formats(tenant_id)
    except Exception:  # noqa: BLE001 — unknown restriction → only the format check
        allowed = None
    got = usable_fallback(fallback_format, allowed)
    if not got and allowed is not None and not allowed:
        return EMPTY_SET_FALLBACK
    return got


async def miss_reason(tenant_id, since: datetime) -> str:
    """Why a learned attempt that returned nothing missed: "qa_failed" when one
    of this brand's layouts took a design-QA strike during the attempt, else
    "thin_library" (the picker had nothing it could draw).

    Read from the strike itself (mark_qa bumps qa_fails and updated_at without
    touching last_used_at) because the learned renderer returns a bare None for
    both. Measurement only — any failure reads as "thin_library"."""
    from .config import settings
    if not settings.design_qa_enabled:
        return "thin_library"
    try:
        from .db import acquire
        async with acquire(tenant_id) as conn:
            hit = await conn.fetchval(
                "SELECT 1 FROM design_templates WHERE qa_fails > 0 AND updated_at >= $1 "
                "AND (last_used_at IS NULL OR last_used_at < updated_at) LIMIT 1",
                since - _SKEW)
        return "qa_failed" if hit else "thin_library"
    except Exception:  # noqa: BLE001
        log.info("could not classify a learned miss for %s", tenant_id, exc_info=True)
        return "thin_library"


async def served_layout(action_id, tenant_id) -> tuple[str, bool]:
    """(design_template_id, pinned) of the learned layout just drawn onto this
    action — ("", False) when it cannot be read. `pinned` is True for a house
    layout the caller pinned (house_layout_pinned), which is never redrawn."""
    try:
        from .db import acquire
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT payload->>'design_template_id' AS tid, "
                "coalesce(payload->'house_layout_pinned' = 'true'::jsonb, false) AS pinned "
                "FROM actions WHERE id = $1", action_id)
        if not row:
            return "", False
        return str(row["tid"] or ""), bool(row["pinned"])
    except Exception:  # noqa: BLE001 — measurement only; never costs the post
        log.info("could not read the served layout of %s", action_id, exc_info=True)
        return "", False


async def unrepeated(action_id, tenant_id, avoid_template_id: str, served, redraw):
    """BM2's `avoid_template_id` (SPEC3 P4c): the brand's last two image posts
    were both drawn from that learned layout. If the render just made for this
    post is that layout a THIRD time, draw once more: the render that just ran
    marked this layout used, and the picker favours layouts not used recently,
    so the redraw normally lands on another one (e.g. when several orders of one
    batch ran before any of them marked its layout used).

    The learned slot is never given up (a post that cannot avoid the layout —
    a one-row lane, a redraw that misses or throws — keeps the render it has),
    and a pinned house layout is the owner's choice, so it is never redrawn."""
    avoid = str(avoid_template_id or "").strip()
    if not avoid or not served:
        return served
    tid, pinned = await served_layout(action_id, tenant_id)
    if pinned or tid != avoid:
        return served
    try:
        again = await redraw()
    except Exception:  # noqa: BLE001 — the first render still stands
        log.warning("redraw avoiding layout %s failed for %s", avoid, action_id, exc_info=True)
        again = None
    await stamp(action_id, tenant_id, {"avoided_template_id": avoid,
                                       "avoid_redrawn": bool(again)})
    return again or served


async def stamp(action_id, tenant_id, data: dict) -> None:
    """Merge `data` into the action's payload. Never allowed to cost the post."""
    try:
        from .db import acquire
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                action_id, json.dumps(data))
    except Exception:  # noqa: BLE001
        log.warning("could not stamp %s on %s", sorted(data), action_id, exc_info=True)


__all__ = ["DRAWABLE", "REASONS", "EMPTY_SET_FALLBACK", "effective_allowed",
           "usable_fallback", "fallback_for", "miss_reason", "served_layout", "unrepeated",
           "stamp", "now"]
