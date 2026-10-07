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
import logging
import re
from uuid import UUID

import httpx

from .config import settings
from .db import acquire

logger = logging.getLogger(__name__)

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

# A post whose media did not store is not a verdict on the post — but each
# attempt is a paid actor run, and a row that stays '' bought another one on
# every refresh, forever: 72 of skelon's 100 posts had no picture and each
# daily refresh re-scraped the profile for them. After this many runs that
# tried and failed (the download 403'd, was over the cap, or the actor did not
# return the post at all) the row is left alone. Same rule as
# design_templates.MAX_READ_ATTEMPTS.
#
# What does NOT count as an attempt: a run that returned nothing usable. A
# login wall, a rate limit or a profile gone private all come back SUCCEEDED
# with [] or with error items that carry no shortCode — the actor ran, but it
# never saw the profile. Charging the posts for that would abandon every one
# of them after three such weeks; so a run with no usable post is an outage,
# same as an actor that failed to start. Only once the actor has shown it can
# read the profile (at least one real post came back) is a post it did not
# return, or a download that failed, counted. `force` resets the counter —
# nothing else does, sync's upsert never touches it — which is the operator's
# way back after a block clears.
MAX_MEDIA_FETCH_ATTEMPTS = 3


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


async def reset_media_attempts(competitor_id: str, tenant_id: UUID | None = None) -> int:
    """Give the posts this competitor's runs gave up on another
    MAX_MEDIA_FETCH_ATTEMPTS tries. Returns how many rows that was.

    The counter has no other way back to zero: a competitor whose profile
    was behind a login wall for three refreshes would otherwise stay
    abandoned after the wall came down."""
    async with acquire(tenant_id) as conn:
        tag = await conn.execute(
            "UPDATE competitor_posts SET media_fetch_attempts = 0 "
            "WHERE competitor_id = $1::uuid AND stored_media_url = '' "
            "  AND media_fetch_attempts > 0", competitor_id)
    return int(tag.rsplit(" ", 1)[-1] or 0)


async def fetch_media_for_competitor(
    competitor: dict, limit: int = 30, tenant_id: UUID | None = None,
    force: bool = False,
) -> dict:
    """Re-scrape one competitor through Apify and store the media we are
    missing. Only touches rows whose `stored_media_url` is still empty AND
    that fewer than MAX_MEDIA_FETCH_ATTEMPTS runs have failed to fill, so
    re-running is cheap, never re-downloads what we already hold, and stops
    paying for the posts it cannot get. `force` resets that counter first.

    Returns `items_fetched` (what the actor returned, i.e. what was paid for)
    and `rows_new` (how many of our rows that filled) for the spend ledger.
    """
    platform = (competitor.get("platform") or "").lower()
    handle = (competitor.get("handle") or "").lstrip("@")
    actor = _ACTORS.get(platform)
    if not actor:
        return {"handle": handle, "error": f"no Apify actor for {platform}",
                "stored": 0}
    if not configured():
        return {"handle": handle, "error": "Apify is not configured", "stored": 0}

    reset = 0
    if force:
        reset = await reset_media_attempts(str(competitor["id"]), tenant_id)
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, url, media_type FROM competitor_posts
                WHERE competitor_id = $1::uuid AND stored_media_url = ''
                  AND media_fetch_attempts < $3
             ORDER BY posted_at DESC NULLS LAST LIMIT $2""",
            competitor["id"], max(1, limit), MAX_MEDIA_FETCH_ATTEMPTS)
    missing = {shortcode(r["url"]): r for r in rows if shortcode(r["url"])}
    if not missing:
        return {"handle": handle, "stored": 0, "items_fetched": 0, "rows_new": 0,
                "note": "nothing missing media"}

    if platform == "instagram":
        run_input = {"directUrls": [f"https://www.instagram.com/{handle}/"],
                     "resultsType": "posts", "resultsLimit": max(limit, len(missing)),
                     "addParentData": False}
    else:
        run_input = {"profiles": [handle], "resultsPerPage": max(limit, len(missing)),
                     "shouldDownloadVideos": False, "shouldDownloadCovers": False}

    items, err = await _run_actor(actor, run_input)
    if err:
        # An actor that did not run is an outage, not an attempt: nothing is
        # counted against the posts, they are asked for again next time.
        return {"handle": handle, "error": err, "stored": 0,
                "items_fetched": 0, "rows_new": 0}

    # A usable item is a real post: it names itself and has a file to fetch.
    # Error items (login wall, rate limit, private profile) have neither.
    usable = [(code, media_url, cover_url)
              for code, media_url, cover_url in (_media_urls(platform, i) for i in items)
              if code and media_url]
    if not usable:
        # The actor ran but never saw the profile: an outage, not an attempt,
        # exactly like the `err` branch — nothing is counted against the
        # posts, and they are asked for again next time.
        logger.info("[competitor_media] %s/%s items_fetched=%d usable=0 — outage, "
                    "attempts untouched", platform, handle, len(items))
        return {"handle": handle, "stored": 0, "failed": 0,
                "was_missing": len(missing), "scraped": len(items),
                "items_fetched": len(items), "rows_new": 0, "reset": reset,
                "errors": [], "error": "actor returned no usable posts "
                "(login wall, rate limit or private profile)"}

    from .competitor_sync import _store_media
    tenant = str(tenant_id or "")
    stored = failed = 0
    errors: list[str] = []
    # The actor read the profile, so every row this run was asked for and did
    # not fill gets an attempt — a failed download AND a post the actor never
    # returned alike. It was asked for at least len(missing) recent posts, so
    # one it did not return is older than a profile scrape reaches and no
    # later run will find it either; without counting those, the rows that
    # can never fill kept buying runs.
    unfilled: set = {str(r["id"]) for r in missing.values()}
    for code, media_url, cover_url in usable:
        row = missing.get(code)
        if not row:
            continue
        kind = "video" if (row["media_type"] or "") == "video" else "image"
        # Download NOW — these URLs are fresh but go stale within hours.
        durable, e = await _store_media(media_url, tenant, kind, f"{handle}-{code}")
        if not durable:
            failed += 1
            if e and len(errors) < 5:
                errors.append(f"{code}: {e}")
            continue
        unfilled.discard(str(row["id"]))
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

    if unfilled:
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE competitor_posts SET media_fetch_attempts = media_fetch_attempts + 1 "
                "WHERE id = ANY($1::uuid[])", list(unfilled))
    logger.info("[competitor_media] %s/%s items_fetched=%d rows_new=%d unfilled=%d",
                platform, handle, len(items), stored, len(unfilled))

    return {"handle": handle, "stored": stored, "failed": failed,
            "was_missing": len(missing), "scraped": len(items),
            "items_fetched": len(items), "rows_new": stored, "reset": reset,
            "errors": errors, "error": None}


async def fetch_all_missing_media(
    limit: int = 30, concurrency: int = 2, tenant_id: UUID | None = None,
    force: bool = False,
) -> dict:
    """Fill in missing media across every tracked competitor.

    Concurrency is low on purpose: each call is a paid actor run, and a
    burst of them is the easy way to turn a bug into a bill. `force` gives
    every competitor's given-up posts another go first.
    """
    from .competitors import list_competitors
    tracked = await list_competitors(status="tracked", tenant_id=tenant_id)
    if not tracked:
        return {"stored": 0, "results": [], "note": "No tracked competitors."}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c: dict) -> dict:
        async with sem:
            try:
                return await fetch_media_for_competitor(c, limit, tenant_id, force=force)
            except Exception as e:  # noqa: BLE001 — one competitor ≠ the batch
                return {"handle": c.get("handle"), "stored": 0,
                        "error": f"{type(e).__name__}: {e}"[:160]}

    results = await asyncio.gather(*[_one(c) for c in tracked])
    return {
        "stored": sum(r.get("stored", 0) for r in results),
        "failed": sum(r.get("failed", 0) for r in results),
        "items_fetched": sum(r.get("items_fetched", 0) for r in results),
        "rows_new": sum(r.get("rows_new", 0) for r in results),
        "reset": sum(r.get("reset", 0) for r in results),
        "results": list(results),
    }


async def run_competitor_media(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point. Tenant-bound and explicit."""
    cfg = config or {}
    await fetch_all_missing_media(
        limit=int(cfg.get("limit") or 30), tenant_id=tenant_id,
        force=bool(cfg.get("force")))


__all__ = [
    "shortcode", "configured", "fetch_media_for_competitor", "reset_media_attempts",
    "fetch_all_missing_media", "run_competitor_media", "MAX_MEDIA_FETCH_ATTEMPTS",
]
