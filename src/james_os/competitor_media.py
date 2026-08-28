"""Apify-backed media fetch for competitor posts.

The division of labour, settled empirically rather than by preference:

    Xpoz  → metadata and analytics. Post text, likes, comments, views,
            follower counts, and the keyword/user discovery that finds the
            accounts in the first place. It is good at all of that.
    Apify → the media FILES. Xpoz's `image_url` / `video_url` are signed to
            its own session and answer HTTP 403 to us within hours.

That was worth proving before building on it. Posts DcgxWz7xf_i and
DceNc4-xvF3 (@ryanserhant) returned 403 on every Xpoz-supplied URL, with and
without browser headers. The SAME two posts, scraped through
apify/instagram-scraper, returned HTTP 200 and 12MB and 15MB video files.
Instagram was never blocking us — we were fetching stale signed links.

So this module re-scrapes a competitor's recent posts through Apify purely to
obtain fresh media URLs, downloads them immediately (they go stale fast), and
copies the bytes into our own storage. Posts are matched to rows we already
hold by SHORTCODE, which is the one identifier both providers agree on.

Cost, at the official actor's rate: roughly $1.50-$2.70 per 1,000 posts, so a
full media refresh of ten competitors at thirty posts each lands under a
dollar. `apidojo/instagram-scraper` advertises $0.47 and is a drop-in
alternative through _ACTOR_OVERRIDE if that matters later.
"""

from __future__ import annotations

import asyncio
import re
from uuid import UUID

import httpx

from .config import settings
from .db import acquire

_BASE = "https://api.apify.com/v2"
_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "tiktok": "clockworks~tiktok-scraper",
}
# A profile scrape takes ~35s for a handful of posts, so this runs as a
# background job and never inside a request.
_RUN_TIMEOUT = httpx.Timeout(600.0, connect=15.0)
_POLL_SECONDS = 6
_MAX_POLLS = 60


def shortcode(url: str) -> str:
    """The post id both providers agree on.

    Xpoz returns https://instagram.com/p/ABC123 and Apify returns
    https://www.instagram.com/p/ABC123/ plus a `shortCode` field; TikTok
    URLs end in a numeric id. Matching on the code sidesteps every
    difference in host, scheme and trailing slash.
    """
    u = (url or "").strip()
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", u)
    if m:
        return m.group(1)
    m = re.search(r"/video/(\d+)", u)
    if m:
        return m.group(1)
    m = re.search(r"/([A-Za-z0-9_-]{6,})/?$", u)
    return m.group(1) if m else ""


def configured() -> bool:
    return bool((settings.apify_api_key or "").strip())


async def _run_actor(actor: str, run_input: dict) -> tuple[list[dict], str]:
    """Start an actor, wait for it, return its dataset items.

    Deliberately NOT run-sync-get-dataset-items: that endpoint returns an
    empty list for a failed run and hides the reason, which cost real time
    when an actor was silently producing nothing.
    """
    key = (settings.apify_api_key or "").strip()
    if not key:
        return [], "Apify is not configured (APIFY_API_KEY)"
    async with httpx.AsyncClient(timeout=_RUN_TIMEOUT) as c:
        r = await c.post(f"{_BASE}/acts/{actor}/runs",
                         params={"token": key}, json=run_input)
        if r.status_code >= 400:
            return [], f"actor start HTTP {r.status_code}: {r.text[:160]}"
        run_id = r.json()["data"]["id"]

        status = "READY"
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_SECONDS)
            g = await c.get(f"{_BASE}/actor-runs/{run_id}", params={"token": key})
            if g.status_code >= 400:
                continue
            status = g.json()["data"]["status"]
            if status not in ("RUNNING", "READY"):
                break
        if status == "RUNNING":
            # Stop paying for a run that has outlived its usefulness.
            await c.post(f"{_BASE}/actor-runs/{run_id}/abort", params={"token": key})
            return [], "actor timed out (aborted)"
        if status != "SUCCEEDED":
            return [], f"actor finished {status}"

        d = await c.get(f"{_BASE}/actor-runs/{run_id}/dataset/items",
                        params={"token": key})
        if d.status_code >= 400:
            return [], f"dataset HTTP {d.status_code}"
        items = d.json()
    return (items if isinstance(items, list) else []), ""


def _media_urls(platform: str, item: dict) -> tuple[str, str, str]:
    """(code, media_url, cover_url) from one actor result."""
    if platform == "instagram":
        code = item.get("shortCode") or shortcode(item.get("url") or "")
        is_video = (item.get("type") or "").lower() == "video"
        return (code,
                (item.get("videoUrl") or "") if is_video else (item.get("displayUrl") or ""),
                item.get("displayUrl") or "")
    # tiktok
    code = shortcode(item.get("webVideoUrl") or item.get("url") or "")
    video = item.get("videoMeta") or {}
    return code, (item.get("videoUrl") or video.get("downloadAddr") or ""), \
        (video.get("coverUrl") or "")


async def fetch_media_for_competitor(
    competitor: dict, limit: int = 30, tenant_id: UUID | None = None
) -> dict:
    """Re-scrape one competitor through Apify and store the media we are
    missing. Only touches rows whose `stored_media_url` is still empty, so
    re-running is cheap and never re-downloads what we already hold.
    """
    platform = (competitor.get("platform") or "").lower()
    handle = (competitor.get("handle") or "").lstrip("@")
    actor = _ACTORS.get(platform)
    if not actor:
        return {"handle": handle, "error": f"no Apify actor for {platform}",
                "stored": 0}
    if not configured():
        return {"handle": handle, "error": "Apify is not configured", "stored": 0}

    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, url, media_type FROM competitor_posts
                WHERE competitor_id = $1::uuid AND stored_media_url = ''
             ORDER BY posted_at DESC NULLS LAST LIMIT $2""",
            competitor["id"], max(1, limit))
    missing = {shortcode(r["url"]): r for r in rows if shortcode(r["url"])}
    if not missing:
        return {"handle": handle, "stored": 0, "note": "nothing missing media"}

    if platform == "instagram":
        run_input = {"directUrls": [f"https://www.instagram.com/{handle}/"],
                     "resultsType": "posts", "resultsLimit": max(limit, len(missing)),
                     "addParentData": False}
    else:
        run_input = {"profiles": [handle], "resultsPerPage": max(limit, len(missing)),
                     "shouldDownloadVideos": False, "shouldDownloadCovers": False}

    items, err = await _run_actor(actor, run_input)
    if err:
        return {"handle": handle, "error": err, "stored": 0}

    from .competitor_sync import _store_media
    tenant = str(tenant_id or "")
    stored = failed = 0
    errors: list[str] = []
    for item in items:
        code, media_url, cover_url = _media_urls(platform, item)
        row = missing.get(code)
        if not row or not media_url:
            continue
        kind = "video" if (row["media_type"] or "") == "video" else "image"
        # Download NOW — these URLs are fresh but go stale within hours.
        durable, e = await _store_media(media_url, tenant, kind, f"{handle}-{code}")
        if not durable:
            failed += 1
            if e and len(errors) < 5:
                errors.append(f"{code}: {e}")
            continue
        # For a reel, keep the cover too: the grid needs a thumbnail, and the
        # design eye can still read a cover when the video is unplayable.
        thumb = ""
        if kind == "video" and cover_url:
            thumb, _ = await _store_media(cover_url, tenant, "image",
                                          f"{handle}-{code}-cover")
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE competitor_posts SET stored_media_url = $2, "
                "thumbnail_url = CASE WHEN $3 <> '' THEN $3 ELSE thumbnail_url END "
                "WHERE id = $1", row["id"], durable, thumb)
        stored += 1

    return {"handle": handle, "stored": stored, "failed": failed,
            "was_missing": len(missing), "scraped": len(items),
            "errors": errors, "error": None}


async def fetch_all_missing_media(
    limit: int = 30, concurrency: int = 2, tenant_id: UUID | None = None
) -> dict:
    """Fill in missing media across every tracked competitor.

    Concurrency is low on purpose: each call is a paid actor run, and a
    burst of them is the easy way to turn a bug into a bill.
    """
    from .competitors import list_competitors
    tracked = await list_competitors(status="tracked", tenant_id=tenant_id)
    if not tracked:
        return {"stored": 0, "results": [], "note": "No tracked competitors."}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c: dict) -> dict:
        async with sem:
            try:
                return await fetch_media_for_competitor(c, limit, tenant_id)
            except Exception as e:  # noqa: BLE001 — one competitor ≠ the batch
                return {"handle": c.get("handle"), "stored": 0,
                        "error": f"{type(e).__name__}: {e}"[:160]}

    results = await asyncio.gather(*[_one(c) for c in tracked])
    return {
        "stored": sum(r.get("stored", 0) for r in results),
        "failed": sum(r.get("failed", 0) for r in results),
        "results": list(results),
    }


async def run_competitor_media(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point. Tenant-bound and explicit."""
    cfg = config or {}
    await fetch_all_missing_media(
        limit=int(cfg.get("limit") or 30), tenant_id=tenant_id)


__all__ = [
    "shortcode", "configured", "fetch_media_for_competitor",
    "fetch_all_missing_media", "run_competitor_media",
]
