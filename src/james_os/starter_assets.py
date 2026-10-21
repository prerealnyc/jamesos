"""What a brand's first posts are built FROM — and a plain reason when it lacks it.

The starter pipelines (template_clone.generate_template_samples,
hero_templatize.generate_samples, competitor_kickoff.generate_first_posts) all
degrade quietly when a brand has no logo or too few photos: fallbacks draw a
scene, drop the logo slot, render solid. The owner then sees weaker posts with
no hint why. This module counts the assets once, cheaply (two media_assets
reads, no bytes fetched, no AI), and states the gap in a machine-readable way
the job status carries from its first emit to its final result:

    skipped_reason  'missing_logo' | 'missing_photos' | 'missing_logo_and_photos' | None
    assets          {logo_count, photo_count, photos_needed}

"skipped" names the reason the brand's own assets were skipped over, not that
the job did nothing — posts may still have been built on fallbacks.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PHOTOS_NEEDED = 3
LOGO_ROLE = "brand_logo"
PHOTO_ROLE = "hero_photo"


def skipped_reason(logo_count: int, photo_count: int, *, photos_needed: int = PHOTOS_NEEDED) -> str | None:
    """Pure: the reason a brand's own assets could not carry its first posts."""
    no_logo = int(logo_count or 0) < 1
    few_photos = int(photo_count or 0) < photos_needed
    if no_logo and few_photos:
        return "missing_logo_and_photos"
    if no_logo:
        return "missing_logo"
    if few_photos:
        return "missing_photos"
    return None


def asset_fields(logo_count: int, photo_count: int) -> dict:
    """The two job-status fields, from counts. Pure."""
    return {
        "skipped_reason": skipped_reason(logo_count, photo_count),
        "assets": {"logo_count": int(logo_count or 0), "photo_count": int(photo_count or 0),
                   "photos_needed": PHOTOS_NEEDED},
    }


async def asset_status(tenant_id, *, photo_count: int | None = None) -> dict:
    """Count the brand's logo and photo assets and state the gap.

    `photo_count` lets a caller that already holds the render pool pass its
    size, so a library photo the media shelf does not list (a scraped own-post
    picture) still counts. The larger of the two numbers is used. Best-effort:
    a failed read counts as zero rather than failing the job."""
    from .media import list_media

    logos = photos = 0
    try:
        logos = len(await list_media(role=LOGO_ROLE, tenant_id=tenant_id))
    except Exception:  # noqa: BLE001 — a count must never cost the job
        logger.warning("could not count logos", exc_info=True)
    try:
        photos = len(await list_media(role=PHOTO_ROLE, tenant_id=tenant_id))
    except Exception:  # noqa: BLE001
        logger.warning("could not count photos", exc_info=True)
    if photo_count is not None:
        photos = max(photos, int(photo_count))
    return asset_fields(logos, photos)


__all__ = ["PHOTOS_NEEDED", "skipped_reason", "asset_fields", "asset_status"]
