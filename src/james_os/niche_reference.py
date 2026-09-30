"""Niche references — the pictures the niche is rewarding, kept as options.

A tracked competitor is an ACCOUNT we scrape: we know the handle, the follower
count, and every post belongs to somebody we chose to watch. Media monitoring
answers a different question — "what is winning in this niche right now" —
and the answer arrives as posts from accounts nobody tracks, often anonymised
by the platform (Instagram hands over a picture and an interaction count, never
a username).

Those pictures are still worth having. They are the same raw material as a
competitor still: a real post, in this brand's niche, with real engagement
behind it, whose LAYOUT can be read into the design library and redrawn in the
brand's own words. What they are not is a competitor, and pretending otherwise
would corrupt three things that read the competitor roster: the follower ladder,
the per-account profiles, and every scraper loop (there is no account to scrape).

So they land on their own shelf:

  * one `competitors` row per tenant, `status='reference'` — a status the
    scraping loops never ask for (they all ask for 'tracked'), so nothing tries
    to sync, download or profile it, and no change to those loops is needed;
  * one `competitor_posts` row per picture, with our own durable copy, so the
    studio can show it and the layout learner can read it;
  * one `competitor_post_analysis` row carrying the read the CALLER already
    paid for — the eye that judged the picture on-niche also named its format
    and its words, and re-reading it here would be paying twice for the same
    sentence.

The caller sends BYTES, not a URL: a monitoring vendor's image host usually
needs that vendor's own credentials, which live with the caller and must not
be handed around. See `design_templates.learn_from_competitors`, which picks
these up on its next run and files them as `source_kind='niche'`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

from .db import acquire

logger = logging.getLogger(__name__)

# The shelf. One per tenant: a second one would split the same idea across two
# headers in the studio for no gain.
SHELF_HANDLE = "niche"
SHELF_PLATFORM = "niche"
SHELF_STATUS = "reference"
VIA = "onclusive"

# A reference is a still. Videos are a different pipeline (perception, not the
# design eye) and nothing here would know what to do with one.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def sniff(data: bytes) -> str:
    """The image's real type from its bytes, or "" if it is not an image.

    The caller's label is not evidence: whatever a form says, only the file
    itself decides whether we store it and serve it back to a browser."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def key_for(ref: str, source_url: str) -> str:
    """A stable id for one picture, so a daily scan re-filing the same top
    image updates nothing instead of stacking duplicates."""
    raw = (ref or source_url or "").strip()
    if not raw:
        return ""
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_:.")[:60]
    return safe or hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _when(value: str) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def shelf(tenant_id: UUID | str | None, *, niche: str = "") -> dict:
    """This tenant's reference shelf, created on first use."""
    name = "Niche references"
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO competitors (platform, handle, name, niche, status,
                                        discovered_via, why)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (tenant_id, platform, lower(handle)) DO UPDATE
                    SET niche = CASE WHEN EXCLUDED.niche <> '' THEN EXCLUDED.niche
                                     ELSE competitors.niche END
                 RETURNING id, handle, name, status, niche""",
            SHELF_PLATFORM, SHELF_HANDLE, name, niche.strip()[:120], SHELF_STATUS, VIA,
            "high-engagement posts in this brand's niche, kept as layout references",
        )
    return {"id": str(row["id"]), "handle": row["handle"], "name": row["name"],
            "status": row["status"], "niche": row["niche"]}


async def save_reference(
    tenant_id: UUID | str | None,
    *,
    image: bytes,
    source_url: str = "",
    platform: str = "",
    author: str = "",
    interactions: int = 0,
    posted_at: str = "",
    fmt: str = "",
    hook: str = "",
    why: str = "",
    recipe: str = "",
    niche: str = "",
    ref: str = "",
) -> dict:
    """File one picture on the reference shelf. Idempotent per picture.

    Returns {stored, post_id, competitor_id, reason}. `stored` is False for a
    picture we already hold — the common case on a daily scan, and not an
    error. Never raises for a storage or database hiccup: a reference is a
    bonus, and the scan that produced it must not fail because one image
    could not be kept.
    """
    key = key_for(ref, source_url)
    if not key:
        return {"stored": False, "reason": "no id for this picture"}
    if not image:
        return {"stored": False, "reason": "empty image"}
    if len(image) > MAX_IMAGE_BYTES:
        return {"stored": False, "reason": f"over cap ({len(image) // 1024 // 1024}MB)"}
    ext = sniff(image)
    if not ext:
        return {"stored": False, "reason": "not an image"}

    post_key = f"{VIA}:{key}"
    plat = (platform or "").strip().lower() or SHELF_PLATFORM
    async with acquire(tenant_id) as conn:
        held = await conn.fetchval(
            "SELECT id FROM competitor_posts WHERE platform = $1 AND post_id = $2",
            plat, post_key)
    if held:
        # Already on the shelf. Stop BEFORE storing the bytes again: the daily
        # scan re-reads the same top images, and re-uploading each one would
        # grow storage every day for pictures we already have.
        return {"stored": False, "post_id": str(held), "reason": "already held"}

    where = await shelf(tenant_id, niche=niche)
    try:
        from .media import storage as media_storage
        durable, _path = await asyncio.to_thread(
            media_storage().save, str(tenant_id or ""), image, f"niche-{key}.{ext}")
    except Exception:  # noqa: BLE001 — a reference is never worth an outage
        logger.warning("could not store a niche reference (%s)", source_url[:120],
                       exc_info=True)
        return {"stored": False, "reason": "could not store the picture"}

    # The caption is what the studio shows under the picture. Whoever posted it
    # leads when the platform tells us (Facebook and YouTube do); Instagram and
    # LinkedIn anonymise, and an honest "the niche" beats a made-up handle.
    caption = " · ".join(x for x in (author.strip(), hook.strip()) if x)[:600]
    async with acquire(tenant_id) as conn:
        post_id = await conn.fetchval(
            """INSERT INTO competitor_posts
                   (competitor_id, platform, post_id, url, caption, media_type,
                    media_url, stored_media_url, likes, posted_at)
                   VALUES ($1::uuid, $2, $3, $4, $5, 'image', $6, $7, $8, $9)
              ON CONFLICT (tenant_id, platform, post_id)
                 WHERE post_id <> '' DO UPDATE
                     SET stored_media_url = EXCLUDED.stored_media_url,
                         last_synced_at = now()
                RETURNING id""",
            where["id"], plat, post_key, source_url[:1000], caption,
            source_url[:1000], durable, max(0, int(interactions or 0)), _when(posted_at))
        if fmt.strip() or hook.strip() or why.strip() or recipe.strip():
            # The read the caller already paid for, kept next to the picture so
            # the studio shows "what it is" without a second vision call.
            await conn.execute(
                """INSERT INTO competitor_post_analysis
                       (post_id, kind, status, format, hook, why_it_works,
                        transferable_pattern, model, rubric_version)
                       VALUES ($1, 'image', 'ok', $2, $3, $4, $5, $6, 'niche-1')
                  ON CONFLICT (post_id) DO UPDATE
                      SET format = EXCLUDED.format, hook = EXCLUDED.hook,
                          why_it_works = EXCLUDED.why_it_works,
                          transferable_pattern = EXCLUDED.transferable_pattern,
                          analyzed_at = now()""",
                post_id, fmt.strip()[:200], hook.strip()[:500], why.strip()[:1000],
                recipe.strip()[:1000], f"listening/{VIA}")
    return {"stored": True, "post_id": str(post_id), "competitor_id": where["id"]}


async def listing(
    tenant_id: UUID | str | None, *, limit: int = 200, offset: int = 0,
) -> dict:
    """Every picture on the shelf, newest first, with what became of it.

    `count` answers "how many"; this answers "which ones, and did each one
    actually turn into a layout" — the question an operator asks when deciding
    whether the monitoring feed is earning its keep.

    The template is LEFT-joined: a picture that has been read but produced no
    usable layout (the read found no text to learn from, or its fingerprint
    matched one we already held) must still appear, marked read with no
    template, because a shelf that silently hid those would overstate the
    yield. `read_at` and `template_id` are therefore independent — read
    without a template is the honest "we looked, and there was nothing new".
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT p.id::text            AS id,
                      p.stored_media_url    AS image,
                      p.url                 AS source_url,
                      p.platform            AS platform,
                      p.caption             AS caption,
                      p.likes               AS interactions,
                      p.posted_at           AS posted_at,
                      p.first_seen_at       AS filed_at,
                      p.template_read_at    AS read_at,
                      a.format              AS format,
                      a.hook                AS hook,
                      a.why_it_works        AS why,
                      d.id::text            AS template_id,
                      d.kind                AS template_kind,
                      d.status              AS template_status,
                      d.times_used          AS times_used
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
            LEFT JOIN design_templates d ON d.source_post_id = p.id
                WHERE c.status = $1 AND c.discovered_via = $2
             ORDER BY p.first_seen_at DESC, p.id DESC
                LIMIT $3 OFFSET $4""",
            SHELF_STATUS, VIA, max(1, min(int(limit), 500)), max(0, int(offset)))
        held = await conn.fetchval(
            """SELECT count(*) FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
                WHERE c.status = $1 AND c.discovered_via = $2""",
            SHELF_STATUS, VIA)
    return {
        "held": int(held or 0),
        "shown": len(rows),
        "source": VIA,
        "references": [
            {
                "id": r["id"],
                "image": r["image"] or "",
                "source_url": r["source_url"] or "",
                "platform": r["platform"] or "",
                "caption": r["caption"] or "",
                "interactions": int(r["interactions"] or 0),
                "posted_at": r["posted_at"].isoformat() if r["posted_at"] else "",
                "filed_at": r["filed_at"].isoformat() if r["filed_at"] else "",
                "read": r["read_at"] is not None,
                "format": r["format"] or "",
                "hook": r["hook"] or "",
                "why": r["why"] or "",
                "template_id": r["template_id"],
                "template_kind": r["template_kind"],
                "template_status": r["template_status"],
                "times_used": int(r["times_used"] or 0) if r["template_id"] else 0,
            }
            for r in rows
        ],
    }


async def count(tenant_id: UUID | str | None) -> dict:
    """How many references this tenant holds, and how many are already layouts."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT count(*) AS held,
                      count(*) FILTER (WHERE p.template_read_at IS NOT NULL) AS read
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
                WHERE c.status = $1 AND c.discovered_via = $2""",
            SHELF_STATUS, VIA)
    return {"held": int(row["held"] or 0), "read": int(row["read"] or 0)}


__all__ = ["SHELF_HANDLE", "SHELF_PLATFORM", "SHELF_STATUS", "VIA", "MAX_IMAGE_BYTES",
           "sniff", "key_for", "shelf", "save_reference", "count", "listing"]
