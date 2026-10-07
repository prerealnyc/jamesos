"""Stage 2 of competitor intelligence — pull their posts and never lose them.

Two things this fixes about how the codebase pulled competitor data before:

  1. It uses the RIGHT endpoint. `xpoz_intel.trending_from_creators` searched
     for the handle as a text query, ranked the hits by likes, truncated to
     four, and only THEN checked authorship — so it kept the four most-liked
     posts that merely *mentioned* the creator and hoped one was theirs. Xpoz
     has per-author endpoints (`get_posts_by_user`, `get_posts_by_author`);
     this uses them.

  2. It PERSISTS. Every post lands in competitor_posts. Metrics APPEND to
     metrics_history rather than overwriting, so a post's trajectory is
     recoverable — the old trend events froze metrics at first scrape and
     could never tell "climbing" from "peaked a month ago".

Media is copied to our own durable storage at sync time, not later: Instagram
and TikTok media URLs expire within hours, so a copy taken at any other
moment is a copy that fails. Images are small and always stored; video is
capped per run (`video_cap`) because it is not, and the cap is reported
rather than silently applied.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx

from . import competitor_apify, spend
from .competitors import PLATFORMS, _g, _int, configured
from .config import settings
from .db import acquire

logger = logging.getLogger(__name__)

# Per-platform post fields — everything the vision pass and the rollup need.
# Deliberately richer than xpoz_intel._FIELDS, which never asked Instagram
# for a play count or a media type and so could not tell a reel from a photo.
_POST_FIELDS: dict[str, list[str]] = {
    "instagram": [
        "id", "caption", "code_url", "like_count", "comment_count",
        "reshare_count", "created_at_date", "media_type", "post_type",
        "image_url", "video_url", "video_play_count", "video_duration",
        "username",
    ],
    "tiktok": [
        # NO "duration". The vendor client types TiktokPost.duration as an int
        # and TikTok returns seconds as a float (65.713), so asking for it
        # raises a pydantic ValidationError for the WHOLE page — every TikTok
        # account synced zero posts, silently, for as long as this list has
        # existed. The field is worth one integer; the posts are worth the
        # shelf. `_normalize` already defaults it to 0 when it is absent.
        "id", "description", "like_count", "comment_count", "forward_count",
        "play_count", "created_at_date", "post_type",
        "video_thumbnail", "video_url", "hashtags", "username",
    ],
    "twitter": [
        "id", "text", "like_count", "reply_count", "retweet_count",
        "quote_count", "impression_count", "created_at_date", "media_urls",
        "author_username", "hashtags", "is_retweet",
    ],
}

_POST_URL = {
    "instagram": "https://www.instagram.com/p/{id}/",
    "tiktok": "https://www.tiktok.com/@{handle}/video/{id}",
    "twitter": "https://x.com/{handle}/status/{id}",
}

# One video download is worth capping; one image is not.
_MAX_VIDEO_BYTES = 80 * 1024 * 1024
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_FETCH_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
# Instagram's CDN 403s a bare HTTP client. Without these headers only about
# half of the media downloads succeeded, and a post whose media we failed to
# save can never be analysed later — its source URL expires within hours.
_FETCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,video/*,*/*;q=0.8",
}


def _first_url(v: Any) -> str:
    """media_urls comes back as a list on X, a string elsewhere."""
    if isinstance(v, (list, tuple)):
        for x in v:
            if isinstance(x, str) and x.startswith("http"):
                return x
            if isinstance(x, dict):
                u = x.get("url") or x.get("media_url") or ""
                if isinstance(u, str) and u.startswith("http"):
                    return u
        return ""
    return v if isinstance(v, str) and v.startswith("http") else ""


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    txt = str(v).strip()
    if not txt:
        return None
    if txt.isdigit():
        try:
            n = int(txt)
            if n > 10_000_000_000:
                n //= 1000
            return datetime.fromtimestamp(n, tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _media_type(platform: str, p: Any) -> str:
    """image | video | carousel | text — the thing the vision pass routes on."""
    if platform == "instagram":
        raw = str(_g(p, "media_type", "") or _g(p, "post_type", "") or "").lower()
        if "carousel" in raw or "sidecar" in raw or raw == "8":
            return "carousel"
        if "video" in raw or "reel" in raw or raw == "2":
            return "video"
        if _g(p, "video_url"):
            return "video"
        return "image" if _g(p, "image_url") else "text"
    if platform == "tiktok":
        return "video"
    # twitter
    return "image" if _first_url(_g(p, "media_urls")) else "text"


def _normalize(platform: str, handle: str, p: Any) -> dict | None:
    pid = str(_g(p, "id", "") or "").strip()
    if not pid:
        return None
    if platform == "instagram":
        caption = str(_g(p, "caption", "") or "")
        url = str(_g(p, "code_url", "") or "") or _POST_URL[platform].format(id=pid, handle=handle)
        mt = _media_type(platform, p)
        video, image = str(_g(p, "video_url", "") or ""), str(_g(p, "image_url", "") or "")
        return {
            "post_id": pid, "url": url, "caption": caption, "media_type": mt,
            "media_url": video or image, "thumbnail_url": image,
            "duration": _int(_g(p, "video_duration")),
            "likes": _int(_g(p, "like_count")), "comments": _int(_g(p, "comment_count")),
            "shares": _int(_g(p, "reshare_count")), "views": _int(_g(p, "video_play_count")),
            "posted_at": _parse_dt(_g(p, "created_at_date")),
        }
    if platform == "tiktok":
        return {
            "post_id": pid,
            "url": _POST_URL[platform].format(id=pid, handle=handle),
            "caption": str(_g(p, "description", "") or ""), "media_type": "video",
            "media_url": str(_g(p, "video_url", "") or ""),
            "thumbnail_url": str(_g(p, "video_thumbnail", "") or ""),
            "duration": _int(_g(p, "duration")),
            "likes": _int(_g(p, "like_count")), "comments": _int(_g(p, "comment_count")),
            "shares": _int(_g(p, "forward_count")), "views": _int(_g(p, "play_count")),
            "posted_at": _parse_dt(_g(p, "created_at_date")),
        }
    # twitter
    media = _first_url(_g(p, "media_urls"))
    return {
        "post_id": pid,
        "url": _POST_URL[platform].format(id=pid, handle=handle),
        "caption": str(_g(p, "text", "") or ""),
        "media_type": _media_type(platform, p),
        "media_url": media, "thumbnail_url": media, "duration": 0,
        "likes": _int(_g(p, "like_count")), "comments": _int(_g(p, "reply_count")),
        "shares": _int(_g(p, "retweet_count")) + _int(_g(p, "quote_count")),
        "views": _int(_g(p, "impression_count")),
        "posted_at": _parse_dt(_g(p, "created_at_date")),
    }


async def _page_items(result: Any, limit: int) -> list[Any]:
    try:
        page = result.get_page()
        if asyncio.iscoroutine(page):
            page = await page
    except Exception:  # noqa: BLE001
        page = result
    if isinstance(page, list):
        items = page
    else:
        items = (_g(page, "items") or _g(page, "data") or _g(page, "results") or [])
    return list(items)[:limit]


async def _fetch_posts(client: Any, platform: str, handle: str,
                       limit: int, days: int) -> list[dict]:
    """The creator's OWN posts, via the per-author endpoint."""
    start = (date.today() - timedelta(days=max(1, days))).isoformat()
    ns = getattr(client, platform)
    kwargs: dict[str, Any] = {"fields": _POST_FIELDS[platform],
                              "limit": limit, "start_date": start}
    if platform == "twitter":
        res = await asyncio.wait_for(ns.get_posts_by_author(handle, **kwargs), timeout=40)
    else:
        res = await asyncio.wait_for(
            ns.get_posts_by_user(handle, identifier_type="username", **kwargs), timeout=40)
    raw = await _page_items(res, limit)
    out = []
    for p in raw:
        n = _normalize(platform, handle, p)
        if n:
            out.append(n)
    return out


# ── durable media ─────────────────────────────────────────────────────

def media_choice(row: dict, videos_done: int, video_cap: int) -> tuple[str, str, str]:
    """(url to fetch, what it IS, skip reason) for one post's media.

    Two rules learned the hard way:

      * what we actually DOWNLOAD decides the kind. A video post with no file
        URL (YouTube gives none) falls back to its cover, and storing a JPEG
        under .mp4 leaves it unreadable to the design eye and unrenderable in
        the grid.
      * past the video cap we take the COVER rather than skipping the post.
        Downloading every video is expensive, so the cap is right — but a post
        with no picture can never be analysed, and video is most of some
        niches: skelon held 100 posts and 28 pictures, so the eye had nothing
        to read for the other 72.
    """
    file_url = str(row.get("media_url") or "")
    cover = str(row.get("thumbnail_url") or "")
    is_video = (row.get("media_type") == "video") and bool(file_url)
    if not is_video:
        src = file_url or cover
        return (src, "image", "") if src else ("", "image", "no media url")
    if videos_done >= video_cap:
        return (cover, "image", "") if cover else ("", "video", "video cap, no cover")
    return file_url, "video", ""


async def _store_media(url: str, tenant: str, kind: str, label: str) -> tuple[str, str]:
    """Copy one media URL into our own storage. Returns (durable_url, error).

    Never raises: a competitor post is still worth keeping without its media,
    and one dead CDN link must not sink a whole sync.
    """
    if not url or not url.startswith("http"):
        return "", "no media url"
    cap = _MAX_VIDEO_BYTES if kind == "video" else _MAX_IMAGE_BYTES
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT,
                                     headers=_FETCH_HEADERS) as c:
            r = await c.get(url, follow_redirects=True)
            r.raise_for_status()
            data = r.content
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}"
    if not data:
        return "", "empty body"
    if len(data) > cap:
        return "", f"over cap ({len(data) // 1024 // 1024}MB)"
    ext = "mp4" if kind == "video" else "jpg"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:60] or "post"
    try:
        from .media import storage as media_storage
        durable, _ = await asyncio.to_thread(
            media_storage().save, tenant, data, f"{safe}.{ext}")
        return durable, ""
    except Exception as e:  # noqa: BLE001
        return "", f"store failed: {type(e).__name__}"


# ── persistence ───────────────────────────────────────────────────────

def _row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id", "competitor_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("posted_at", "first_seen_at", "last_synced_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    mh = d.get("metrics_history")
    if isinstance(mh, str):
        d["metrics_history"] = json.loads(mh)
    return d


async def _upsert_post(conn, competitor_id: str, platform: str,
                       post: dict, followers: int) -> dict:
    """Insert or refresh one post. metrics_history gains a snapshot ONLY when
    the numbers actually moved, so the trajectory stays readable instead of
    filling with identical rows on every sync."""
    now = datetime.now(UTC).isoformat()
    likes, comments = post["likes"], post["comments"]
    views = post["views"]
    eng_rate = round((likes + comments) / followers, 6) if followers > 0 else 0.0
    snapshot = json.dumps([{"at": now, "likes": likes,
                            "comments": comments, "views": views}])
    row = await conn.fetchrow(
        """
        INSERT INTO competitor_posts (
            competitor_id, platform, post_id, url, caption, media_type,
            media_url, thumbnail_url, duration, likes, comments, shares,
            views, engagement_rate, posted_at, metrics_history)
        VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
        ON CONFLICT (tenant_id, platform, post_id) WHERE post_id <> ''
        DO UPDATE SET
            caption         = COALESCE(NULLIF(EXCLUDED.caption, ''), competitor_posts.caption),
            media_url       = COALESCE(NULLIF(EXCLUDED.media_url, ''), competitor_posts.media_url),
            thumbnail_url   = COALESCE(NULLIF(EXCLUDED.thumbnail_url, ''), competitor_posts.thumbnail_url),
            media_type      = COALESCE(NULLIF(EXCLUDED.media_type, ''), competitor_posts.media_type),
            duration        = GREATEST(EXCLUDED.duration, competitor_posts.duration),
            likes           = EXCLUDED.likes,
            comments        = EXCLUDED.comments,
            shares          = EXCLUDED.shares,
            views           = GREATEST(EXCLUDED.views, competitor_posts.views),
            engagement_rate = EXCLUDED.engagement_rate,
            posted_at       = COALESCE(EXCLUDED.posted_at, competitor_posts.posted_at),
            last_synced_at  = now(),
            metrics_history = CASE
                WHEN EXCLUDED.likes    IS DISTINCT FROM competitor_posts.likes
                  OR EXCLUDED.comments IS DISTINCT FROM competitor_posts.comments
                  OR EXCLUDED.views    IS DISTINCT FROM competitor_posts.views
                THEN (competitor_posts.metrics_history || EXCLUDED.metrics_history)
                ELSE competitor_posts.metrics_history END
        RETURNING *, (xmax = 0) AS is_new
        """,
        competitor_id, platform, post["post_id"], post["url"], post["caption"],
        post["media_type"], post["media_url"], post["thumbnail_url"],
        post["duration"], likes, comments, post["shares"], views, eng_rate,
        post["posted_at"], snapshot,
    )
    return _row(row)



# ── the spend ledger ─────────────────────────────────────────────────
#
# Every sync is paid — Xpoz credits for Instagram/TikTok/X, a pay-per-result
# Apify run for YouTube/LinkedIn — and none of it was recorded, so "what did
# the competitor shelf cost this week" had no answer and a brand over its cap
# kept buying. One row per provider call, written here because only here is
# it known how many of the posts fetched were new (rows_new): the ratio of
# items_fetched to rows_new is what re-pulling an unchanged shelf costs.

async def _meter_xpoz(model: str, platform: str, handle: str,
                      tenant_id: UUID | None, site: str, **meta) -> None:
    """One Xpoz call. The SDK reports no per-call credit cost (only the
    account's remaining balance), so the unit is the request and the row is
    unpriced (meta.priced=false) rather than priced by a guess."""
    await spend.record("xpoz", model, 1, "requests", None, site,
                       {"platform": platform, "handle": handle,
                        "credits_reported": False, **meta},
                       tenant_id=tenant_id)


async def _meter_apify_runs(runs: list[dict], platform: str, handle: str,
                            competitor_id: str, rows_new: int,
                            tenant_id: UUID | None) -> None:
    """One row per Apify actor run a sync made. LinkedIn may run two (company,
    then profile); the posts came from the last one that returned any, so the
    new rows are booked against that run and the others show 0."""
    producer = max((i for i, r in enumerate(runs) if r.get("items")), default=-1)
    for i, r in enumerate(runs):
        n = int(r.get("items") or 0)
        meta = {"items_fetched": n, "rows_new": rows_new if i == producer else 0,
                "platform": platform, "handle": handle, "competitor_id": competitor_id,
                "price_basis": "per_result_estimate"}
        if r.get("error"):
            meta["error"] = str(r["error"])[:200]
        await spend.record("apify", r.get("actor") or "", n, "results",
                           spend.estimate_results_usd(r.get("actor") or "", n),
                           "competitor_sync.sync", meta, tenant_id=tenant_id)


async def refresh_profile(competitor: dict, tenant_id: UUID | None = None) -> dict:
    """Re-read the competitor's profile so follower count is current.

    Runs before every sync for two reasons. Engagement RATE is
    (likes+comments)/followers — a stale or zero follower count silently
    turns every rate into 0.00%, which is exactly what a hand-added
    competitor produced. And each read appends to follower_history, which is
    the only way follower GROWTH ever becomes computable.

    Returns the competitor dict with a fresh `followers`; on any failure it
    returns the input unchanged rather than raising, because a sync with a
    stale follower count still beats no sync.
    """
    from .competitors import verify_handles
    platform = (competitor.get("platform") or "").lower()
    handle = (competitor.get("handle") or "").lstrip("@")
    if platform not in PLATFORMS or not handle:
        return competitor
    found, _ = await verify_handles([{"platform": platform, "handle": handle}])
    if configured():
        # One Xpoz get_user per sync, before the posts are even asked for; it
        # was the half of every sync's Xpoz spend nobody counted.
        await _meter_xpoz(f"{platform}.get_user", platform, handle, tenant_id,
                          "competitor_sync.refresh_profile", found=bool(found))
    if not found:
        return competitor
    fresh = found[0]
    followers = _int(fresh.get("followers"))
    if followers <= 0:
        return competitor
    now = datetime.now(UTC).isoformat()
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """UPDATE competitors SET
                 followers   = $2,
                 following   = $3,
                 posts_total = $4,
                 verified    = $5,
                 name        = COALESCE(NULLIF($6, ''), name),
                 bio         = COALESCE(NULLIF($7, ''), bio),
                 avatar_url  = COALESCE(NULLIF($8, ''), avatar_url),
                 follower_history = CASE
                     WHEN $2 IS DISTINCT FROM followers
                     THEN follower_history || $9::jsonb
                     ELSE follower_history END
               WHERE id = $1::uuid""",
            str(competitor["id"]), followers, _int(fresh.get("following")),
            _int(fresh.get("posts_total")), bool(fresh.get("verified")),
            fresh.get("name") or "", (fresh.get("bio") or "")[:500],
            fresh.get("avatar_url") or "",
            json.dumps([{"at": now, "followers": followers}]))
    return {**competitor, "followers": followers,
            "name": competitor.get("name") or fresh.get("name") or ""}


# VIDEO SCRAPING IS PAUSED, deliberately and reversibly.
#
# Downloading a competitor's video files is the most expensive thing this
# pipeline does, and nothing downstream can use one yet: the reel builder is
# not wired to competitor video, so every megabyte bought a file that sits
# there. Meanwhile the still cloner runs short of material — 34 candidates,
# one survivor — because half a shelf was video.
#
# A video post is still KEPT, and its cover still becomes an image: a cover is
# a designed still (big headline, face, brand colours) and in some niches it is
# the only designed still anyone posts. What stops is fetching the video file.
#
# Set this False when competitor video has somewhere to go.
VIDEO_SCRAPING_PAUSED = True


def effective_video_cap(video_cap: int) -> int:
    """0 while video scraping is paused — covers only, no video files."""
    return 0 if VIDEO_SCRAPING_PAUSED else video_cap


# A competitor synced inside this window is not synced again unless the caller
# says `force`. Every sync is paid — Xpoz credits for Instagram/TikTok/X, a
# pay-per-result Apify run for YouTube/LinkedIn, and the profile re-read before
# it — and the scheduler, BM2's Monday refresh and the operator's button all
# reach the same function, so without this the three clocks stacked into a
# daily (or more) re-pull of posts already on the shelf. Six days, not seven,
# so a weekly caller never lands a few minutes short of the window and skips
# the whole week; mirrors BM2's peer_snapshot_cache_days.
SYNC_FRESH_DAYS = 6


def is_fresh(last_synced_at: Any, days: int = SYNC_FRESH_DAYS,
             now: datetime | None = None) -> bool:
    """True when `last_synced_at` (datetime or the ISO string competitors._row
    emits, or None) is within `days` of now. Anything unparseable is NOT fresh
    — a competitor we cannot date must be synced, never silently skipped."""
    if not last_synced_at:
        return False
    at = last_synced_at if isinstance(last_synced_at, datetime) else _parse_dt(last_synced_at)
    if at is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - at < timedelta(days=max(0, days))


async def sync_competitor(
    competitor: dict, limit: int = 24, days: int = 90,
    video_cap: int = 3, store_media: bool = True,
    tenant_id: UUID | None = None, force: bool = False,
) -> dict:
    """Pull one competitor's recent posts, persist them, and copy their media.

    Returns {handle, platform, fetched, stored, media_stored, media_skipped,
    error}. Never raises — a broken handle reports and the batch continues.

    A competitor synced within SYNC_FRESH_DAYS is skipped (`skipped` says
    why, `error` is None) unless `force` — see the constant for the bill that
    paid for. `items_fetched` / `rows_new` are what the provider returned and
    how many of those were posts we did not already hold; the spend ledger
    reads them to show the waste ratio.
    """
    platform = (competitor.get("platform") or "").lower()
    handle = (competitor.get("handle") or "").lstrip("@")
    base = {"competitor_id": competitor.get("id"), "handle": handle,
            "platform": platform, "fetched": 0, "stored": 0,
            "items_fetched": 0, "rows_new": 0,
            "media_stored": 0, "media_skipped": []}
    if not handle:
        return {**base, "error": "no handle"}
    # YouTube and LinkedIn have no Xpoz search; they come in by name from the
    # brand's approved peers and are fetched through Apify instead. Everything
    # after this point is identical — the two fetchers return the same rows.
    via_apify = platform in competitor_apify.PLATFORMS
    if not via_apify and platform not in PLATFORMS:
        return {**base, "error": f"unsupported platform '{platform}'"}
    # Before any provider is touched: the profile re-read below costs credits too.
    if not force and is_fresh(competitor.get("last_synced_at")):
        return {**base, "error": None,
                "skipped": f"synced within {SYNC_FRESH_DAYS} days"}
    if via_apify and not competitor_apify.configured():
        return {**base, "error": "No Apify token configured (APIFY_API_KEY)."}
    if not via_apify:
        if not configured():
            return {**base, "error": "No Xpoz API key configured."}
        try:
            import xpoz
        except ImportError:
            return {**base, "error": "The `xpoz` package isn't installed on the server."}

    # Follower count first — engagement rate is meaningless without it.
    # (A no-op on the Apify platforms: there is no profile search to read.)
    competitor = await refresh_profile(competitor, tenant_id=tenant_id)

    cid = str(competitor.get("id") or "")
    runs: list[dict] = []
    xpoz_model = f"{platform}.get_posts_by_{'author' if platform == 'twitter' else 'user'}"
    if via_apify:
        with competitor_apify.collect_runs() as runs:
            posts, err = await competitor_apify.fetch_posts(platform, handle, limit, days)
        if err and not posts:
            await _meter_apify_runs(runs, platform, handle, cid, 0, tenant_id)
            return {**base, "error": err}
    else:
        try:
            async with xpoz.AsyncXpozClient(
                settings.xpoz_api_key.strip(), check_update=False, timeout=45
            ) as c:
                posts = await _fetch_posts(c, platform, handle, limit, days)
        except TimeoutError:
            # The request went out; whether it was charged is Xpoz's call, so
            # it is recorded rather than assumed free.
            await _meter_xpoz(xpoz_model, platform, handle, tenant_id,
                              "competitor_sync.sync", items_fetched=0, rows_new=0,
                              competitor_id=cid, error="timed out")
            return {**base, "error": "timed out"}
        except Exception as e:  # noqa: BLE001
            await _meter_xpoz(xpoz_model, platform, handle, tenant_id,
                              "competitor_sync.sync", items_fetched=0, rows_new=0,
                              competitor_id=cid, error=f"{type(e).__name__}"[:200])
            return {**base, "error": f"{type(e).__name__}: {e}"}

    followers = _int(competitor.get("followers"))
    stored: list[dict] = []
    rows_new = 0
    async with acquire(tenant_id) as conn:
        for p in posts:
            try:
                row = await _upsert_post(conn, str(competitor["id"]),
                                         platform, p, followers)
            except Exception as e:  # noqa: BLE001 — one bad row ≠ no sync
                print(f"[competitor_sync] {handle} post {p.get('post_id')}: {e}")
                continue
            # (xmax = 0) is true only for a row this statement INSERTED; an
            # upsert that merely refreshed metrics is a post we already paid
            # for once. The spend ledger records this split per run.
            if row.pop("is_new", False):
                rows_new += 1
            stored.append(row)
    logger.info("[competitor_sync] %s/%s items_fetched=%d rows_new=%d",
                platform, handle, len(posts), rows_new)
    if via_apify:
        await _meter_apify_runs(runs, platform, handle, cid, rows_new, tenant_id)
    else:
        await _meter_xpoz(xpoz_model, platform, handle, tenant_id, "competitor_sync.sync",
                          items_fetched=len(posts), rows_new=rows_new, competitor_id=cid)
    if rows_new and int(competitor.get("media_outage_runs") or 0) > 0:
        # New posts: the profile is readable again, so the media fetcher that
        # gave up on it after MAX_MEDIA_OUTAGE_RUNS empty runs may try again.
        from .competitor_media import clear_media_outages
        await clear_media_outages(str(competitor["id"]), tenant_id)

    media_stored, skipped = 0, []
    if store_media and stored:
        # Videos are expensive; take the highest-engagement ones first so the
        # cap spends the budget on the posts worth watching.
        ranked = sorted(stored, key=lambda r: (r.get("likes", 0) + r.get("comments", 0)),
                        reverse=True)
        videos_done = 0
        cap = effective_video_cap(video_cap)
        for row in ranked:
            if row.get("stored_media_url"):
                continue
            src, kind, reason = media_choice(row, videos_done, cap)
            if reason:
                skipped.append({"url": row.get("url"), "reason": reason})
                continue
            if kind == "video":
                videos_done += 1
            durable, err = await _store_media(
                src, str(row["tenant_id"]), kind, f"{platform}-{handle}-{row['post_id']}")
            if durable:
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE competitor_posts SET stored_media_url = $2 "
                        "WHERE id = $1::uuid", row["id"], durable)
                row["stored_media_url"] = durable
                media_stored += 1
            elif err:
                skipped.append({"url": row.get("url"), "reason": err})

    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE competitors SET last_synced_at = now() WHERE id = $1::uuid",
            str(competitor["id"]))
        # Posts stored during an earlier sync that ran without a follower
        # count are sitting at rate 0. Now that we have one, fix them.
        if followers > 0:
            await conn.execute(
                "UPDATE competitor_posts SET engagement_rate = "
                "  round(((likes + comments)::numeric / $2)::numeric, 6)::float8 "
                "WHERE competitor_id = $1::uuid AND engagement_rate = 0 "
                "  AND (likes + comments) > 0",
                str(competitor["id"]), followers)

    return {**base, "fetched": len(posts), "stored": len(stored),
            "items_fetched": len(posts), "rows_new": rows_new,
            "media_stored": media_stored, "media_skipped": skipped[:10],
            "error": None}


async def sync_all(
    limit: int = 24, days: int = 90, video_cap: int = 3,
    concurrency: int = 3, tenant_id: UUID | None = None,
    force: bool = False,
) -> dict:
    """Sync every TRACKED competitor. Bounded concurrency keeps Xpoz credit
    burn and memory predictable when a tenant tracks dozens of accounts.

    `force` re-pulls competitors synced within SYNC_FRESH_DAYS; by default
    they are reported under `skipped` and cost nothing.

    Skipped — logged, never raised — when spend says the brand is over its
    daily cap or PAUSE_SPEND is on; `spend_blocked` carries the reason."""
    from .competitor_media import spend_blocked
    gate = await spend_blocked(tenant_id, "competitor sync")
    if gate:
        return {"synced": 0, "skipped": 0, "posts_stored": 0, "items_fetched": 0,
                "rows_new": 0, "media_stored": 0, "ranked": [], "results": [],
                "spend_blocked": gate.get("reason") or "blocked",
                "note": f"skipped: {gate.get('reason') or 'spend blocked'}"}
    from .competitors import list_competitors
    tracked = await list_competitors(status="tracked", tenant_id=tenant_id)
    if not tracked:
        return {"synced": 0, "results": [],
                "note": "No tracked competitors yet — discover a niche or "
                        "import the watchlist first."}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c: dict) -> dict:
        async with sem:
            return await sync_competitor(
                c, limit=limit, days=days, video_cap=video_cap,
                tenant_id=tenant_id, force=force)

    results = await asyncio.gather(*[_one(c) for c in tracked],
                                   return_exceptions=True)
    clean: list[dict] = []
    for c, r in zip(tracked, results, strict=False):
        if isinstance(r, dict):
            clean.append(r)
        else:
            clean.append({"handle": c.get("handle"), "platform": c.get("platform"),
                          "fetched": 0, "stored": 0, "error": str(r)[:200]})
    # Now that real posts are on the shelf, re-rank everyone on measured
    # numbers instead of provisional discovery signals.
    from .competitors import recompute_ranks
    try:
        ranked = await recompute_ranks(tenant_id=tenant_id)
    except Exception as e:  # noqa: BLE001 — a ranking failure must not lose a sync
        ranked = []
        print(f"[competitor_sync] rank recompute failed: {e}")

    return {
        # A skipped competitor is neither synced nor failed: counting it as
        # synced would make "synced 12" true of a run that touched nobody.
        "synced": sum(1 for r in clean if not r.get("error") and not r.get("skipped")),
        "skipped": sum(1 for r in clean if r.get("skipped")),
        "posts_stored": sum(r.get("stored", 0) for r in clean),
        "items_fetched": sum(r.get("items_fetched", 0) for r in clean),
        "rows_new": sum(r.get("rows_new", 0) for r in clean),
        "media_stored": sum(r.get("media_stored", 0) for r in clean),
        "ranked": [r for r in ranked if r.get("measured_posts")],
        "results": clean,
    }


# ── reads ─────────────────────────────────────────────────────────────

async def list_posts(
    competitor_id: str = "", platform: str = "", media_type: str = "",
    sort: str = "engagement", limit: int = 60, tenant_id: UUID | None = None,
) -> list[dict]:
    """The saved shelf — every competitor post we hold, newest or best first."""
    clauses, args = [], []
    if competitor_id:
        args.append(competitor_id)
        clauses.append(f"competitor_id = ${len(args)}::uuid")
    if platform:
        args.append(platform)
        clauses.append(f"platform = ${len(args)}")
    if media_type:
        args.append(media_type)
        clauses.append(f"media_type = ${len(args)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = {
        "engagement": "engagement_rate DESC NULLS LAST, likes DESC",
        "likes": "likes DESC",
        "recent": "posted_at DESC NULLS LAST",
    }.get(sort, "engagement_rate DESC NULLS LAST, likes DESC")
    # Must match the route cap; a lower clamp here would silently return
    # 200 of the 240 asked for, which reads as "some posts vanished".
    args.append(max(1, min(limit, 500)))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM competitor_posts {where} "
            f"ORDER BY {order} LIMIT ${len(args)}", *args)
    return [_row(r) for r in rows]


async def gallery(
    competitor_id: str = "", replicate: str = "", media_only: bool = True,
    sort: str = "engagement", limit: int = 60, tenant_id: UUID | None = None,
) -> list[dict]:
    """Everything the review screen needs for one post, in one query.

    The post, our durable media copy, the competitor it belongs to, and what
    the eyes made of it — the brand is deciding "do I want one of these", and
    that decision is much easier with the format and the hook next to the
    picture than with the picture alone.

    `media_only` defaults to True and means we hold a DURABLE copy — not
    merely that the post arrived with a (long-dead) source thumbnail. Pass
    False to audit what is still missing media.
    """
    clauses = ["1=1"]
    args: list = []
    if competitor_id:
        args.append(competitor_id)
        clauses.append(f"p.competitor_id = ${len(args)}::uuid")
    if replicate:
        args.append(replicate)
        clauses.append(f"p.replicate_status = ${len(args)}")
    if media_only:
        # "Has media" means WE HOLD A COPY. thumbnail_url is usually still the
        # Instagram CDN link the post arrived with, and browsers send a Referer
        # that Instagram refuses for hotlinking — so counting it here filled the
        # gallery with cards that could never render. A post is worth showing
        # when we can actually show it.
        clauses.append("p.stored_media_url <> ''")
    if not replicate:
        # 'skipped' means the brand said this does not belong. Keep showing it
        # and the rejection accomplishes nothing; they re-read it every visit.
        clauses.append("p.replicate_status <> 'skipped'")
    order = {
        "engagement": "p.engagement_rate DESC NULLS LAST, p.likes DESC",
        "likes": "p.likes DESC",
        "recent": "p.posted_at DESC NULLS LAST",
        "picked": "p.replicate_at DESC NULLS LAST",
    }.get(sort, "p.engagement_rate DESC NULLS LAST, p.likes DESC")
    # Must match the route cap; a lower clamp here would silently return
    # 200 of the 240 asked for, which reads as "some posts vanished".
    args.append(max(1, min(limit, 500)))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.url, p.caption, p.media_type, p.stored_media_url,
                       p.thumbnail_url, p.likes, p.comments, p.views,
                       p.engagement_rate, p.posted_at, p.duration,
                       p.replicate_status, p.replicate_note,
                       -- competitor_id is what the gallery GROUPS BY. Omitting
                       -- it did not error anywhere: the field simply arrived
                       -- undefined, every post fell into one bucket, and the
                       -- shelf rendered as a single account's header with
                       -- everybody's posts under it.
                       p.competitor_id,
                       c.handle, c.platform, c.followers,
                       a.format, a.hook, a.hook_pattern, a.topic, a.cta,
                       a.eye_score, a.why_it_works, a.transferable_pattern,
                       a.classification
                  FROM competitor_posts p
                  JOIN competitors c ON c.id = p.competitor_id
             LEFT JOIN competitor_post_analysis a
                       ON a.post_id = p.id AND a.status = 'ok'
                 WHERE {' AND '.join(clauses)}
              ORDER BY {order} LIMIT ${len(args)}""", *args)
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        if d.get("competitor_id") is not None:
            d["competitor_id"] = str(d["competitor_id"])
        if d.get("posted_at") is not None:
            d["posted_at"] = d["posted_at"].isoformat()
        if isinstance(d.get("classification"), str):
            d["classification"] = json.loads(d["classification"])
        out.append(d)
    return out


# The brand's verdicts. Free text in the column (no CHECK), so this tuple is
# the only gate — keep it as the single source of truth.
_REPLICATE_STATUSES = ("", "saved", "template", "idea", "skipped", "queued")


async def set_replicate(
    post_id: str, status: str, note: str = "", tenant_id: UUID | None = None,
) -> dict:
    """Record the brand's verdict on one post.

    The verdicts are deliberately distinct, because they route differently:

      saved     replicate this piece — make our version of it
      template  the STRUCTURE is worth reusing, not this one post; it becomes
                a reusable format rather than a one-off
      idea      not the execution, the concept — goes to the strategiser as a
                thing to create, and the post it came from is the reference
                (the link back IS this row: the verdict lives on the post)
      skipped   does not belong in our set — hidden from the shelf so the
                brand stops re-reading a post it already rejected
      queued    a draft has been generated from it

    '' clears it back to untouched, which matters: a brand changing its mind
    should leave no trace of the earlier pick.
    """
    status = (status or "").strip().lower()
    if status not in _REPLICATE_STATUSES:
        raise ValueError(
            "status must be one of: '' (clear), "
            + ", ".join(repr(x) for x in sorted(_REPLICATE_STATUSES) if x))
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """UPDATE competitor_posts
                  SET replicate_status = $2,
                      replicate_note = $3,
                      replicate_at = CASE WHEN $2 = '' THEN NULL ELSE now() END
                WHERE id = $1::uuid
            RETURNING id, replicate_status, replicate_note, replicate_at""",
            post_id, status, note[:500])
    if not row:
        raise ValueError("post not found")
    d = dict(row)
    d["id"] = str(d["id"])
    if d.get("replicate_at") is not None:
        d["replicate_at"] = d["replicate_at"].isoformat()
    return d


async def replicate_counts(tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE replicate_status = 'saved')    AS saved,
                      count(*) FILTER (WHERE replicate_status = 'template') AS template,
                      count(*) FILTER (WHERE replicate_status = 'idea')     AS idea,
                      count(*) FILTER (WHERE replicate_status = 'skipped')  AS skipped,
                      count(*) FILTER (WHERE replicate_status = 'queued')   AS queued,
                      count(*) FILTER (WHERE stored_media_url <> '')       AS with_media,
                      count(*) AS total
                 FROM competitor_posts""")
    return dict(row)


async def studio_status(tenant_id: UUID | None = None) -> dict:
    """Where the shelf actually stands, read from the DATA.

    The background jobs keep their state in an in-memory dict, which is fine
    for "did my click land" and useless for anything else: it dies with the
    process, a second worker has never heard of the job, and a poll that
    outlives the request loses the thread entirely. A ten-minute analysis
    polled for six minutes looked like a failure while it was quietly
    succeeding.

    The database always knows. This is the number the UI should trust.
    """
    from .competitor_media import MAX_MEDIA_FETCH_ATTEMPTS
    try:
        row = await _status_row(tenant_id, _GIVEN_UP_SQL, MAX_MEDIA_FETCH_ATTEMPTS)
    except asyncpg.exceptions.UndefinedColumnError:
        # Migration 070 is applied by hand (migrate.py), not by the deploy. Code
        # that lands before it would 42703 every status poll BM2 makes; until the
        # column exists nothing has been given up on, so 0 is the truth.
        logger.warning("[competitor_sync] competitor_posts.media_fetch_attempts "
                       "missing — apply migration 070")
        row = await _status_row(tenant_id, "0::bigint")
    d = dict(row)
    for k in ("last_sync", "last_analysis"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    # What is left to do, so the UI can say "12 still to analyse" rather than
    # spinning with no idea whether anything is happening. A post the media
    # fetcher has given up on is not pending — it would read as "12 still to
    # fetch" on every visit, forever, for files no run will ask for again.
    d["media_pending"] = max(0, d["posts"] - d["with_media"] - d["media_given_up"])
    d["analysis_pending"] = max(0, d["with_media"] - d["analysed"])
    return d


_GIVEN_UP_SQL = """(SELECT count(*) FROM competitor_posts
                   WHERE stored_media_url = ''
                     AND media_fetch_attempts >= $1)"""


async def _status_row(tenant_id: UUID | None, given_up_sql: str, *args):
    async with acquire(tenant_id) as conn:
        return await conn.fetchrow(
            f"""SELECT
                 (SELECT count(*) FROM competitors
                   WHERE status = 'tracked')                        AS competitors,
                 (SELECT count(*) FROM competitor_posts)            AS posts,
                 (SELECT count(*) FROM competitor_posts
                   WHERE stored_media_url <> '')                    AS with_media,
                 {given_up_sql}                                     AS media_given_up,
                 (SELECT count(*) FROM competitor_post_analysis a
                    JOIN competitor_posts p ON p.id = a.post_id)     AS analysed,
                 (SELECT count(*) FROM competitor_post_analysis a
                    JOIN competitor_posts p ON p.id = a.post_id
                   WHERE a.status = 'ok')                           AS analysed_ok,
                 (SELECT count(*) FROM competitor_posts
                   WHERE replicate_status IN ('saved','template','idea')) AS picked,
                 (SELECT max(last_synced_at) FROM competitor_posts) AS last_sync,
                 (SELECT max(a.analyzed_at) FROM competitor_post_analysis a
                    JOIN competitor_posts p ON p.id = a.post_id)     AS last_analysis
            """, *args)


# ── the refresh clock ───────────────────────────────────────────────
# One full refresh per tenant at a time, and one per REFRESH_COOLDOWN. The
# chain is paid at every stage — a pull of every tracked competitor, an Apify
# re-scrape per competitor with missing media, a batch of vision calls, an LLM
# rollup — and three callers reach it on three clocks: the scheduler's
# competitor_refresh tick, BM2's Monday refresh_shelves (POST
# /competitors/refresh) and the operator's button. The first cut of this gate
# lived in the HTTP route, which the scheduler never passes through, so a tick
# and a Monday call in the same half hour still ran two chains for one tenant.
# It lives here, at full_refresh itself, so every caller reads one clock. Same
# rule and window as BM2's competitor_chain.COOLDOWN_MINUTES. Held in memory
# like the route's job stores: a restart forgets it and costs one extra run,
# not a stacked chain.
REFRESH_COOLDOWN = timedelta(minutes=30)
# A claim nobody released — a worker killed mid-chain — must not hold the
# tenant's slot forever. Measured from the chain's last sign of life (every
# stage boundary touches it), not from its start: a real chain over twenty
# peers, with Apify polling up to six minutes an actor, can run past two hours
# and must not have a second chain stacked on it for being slow.
REFRESH_MAX_RUNTIME = timedelta(hours=2)
_REFRESH_STATE: dict[str, dict] = {}
# tenant → {job_id, started_at, last_progress_at, finished_at, scope, prev, rerun}
#
# The cooldown exists to stop a second chain re-buying what the first just
# bought; it must never turn away work nobody has bought yet. Weekly cadence
# made that matter: onboarding's first tick runs with nothing tracked and
# started the cooldown, BM2's chain then auto-tracked five peers and called
# refresh inside the window, got reused="cooldown" (which it records as
# "refreshed"), and the peers waited a week for the next tick. So:
#   * a chain that pulled nothing — no competitor synced, no file stored, no
#     post analysed — does not START the cooldown (one already running from an
#     earlier chain that did pull is left exactly as it was);
#   * a tracked competitor that has never been synced always gets past the
#     cooldown, but as a NARROW run (scope "unsynced"): the chain for those
#     competitors only. The rest of the roster was just bought, and a handle
#     that always errors then costs one failed pull per call, not a roster;
#   * a call that lands while a chain is in flight sets `rerun`; the holder
#     pulls whoever was tracked after its own sync listed the roster, because
#     the caller took the in_flight reply as done and will not call again.


def _tenant_key(tenant_id: UUID | None) -> str:
    # The same resolution as acquire(): explicit → the request's tenant → the
    # default. Keyed on a raw None, the route in single-tenant mode (no tenant
    # on the request) and the scheduler (the default tenant's uuid from
    # scheduled_jobs) read two different slots for one shelf, and the gate
    # never saw the one from the other.
    from .db import _request_tenant
    return str(tenant_id or _request_tenant.get() or settings.default_tenant_id)


def refresh_state(tenant_id: UUID | None) -> dict | None:
    """What the clock holds for this tenant (a copy), or None."""
    st = _REFRESH_STATE.get(_tenant_key(tenant_id))
    return dict(st) if st else None


def _running(st: dict, now: datetime) -> bool:
    alive = st.get("last_progress_at") or st["started_at"]
    return st.get("finished_at") is None and now - alive < REFRESH_MAX_RUNTIME


def claim_refresh(
    tenant_id: UUID | None, job_id: str, force: bool = False,
    now: datetime | None = None, unsynced: set[str] | None = None,
) -> dict | None:
    """Take the tenant's refresh slot for `job_id`.

    None means the slot is yours: run the chain and call release_refresh.
    Otherwise it is the reply to hand back INSTEAD of running —
    {job_id, reused: "in_flight" | "cooldown", note} — naming the run that
    already covers this tenant.

    A running claim is never overridden, `force` included: it cannot stack a
    second chain on a first. A claim that finished inside REFRESH_COOLDOWN is
    reused unless `force`, which is the operator saying "I know, do it again".
    Claiming again with the SAME job_id is a no-op for the holder, so the route
    can claim before it answers (two clicks a second apart get one job_id
    back) and full_refresh re-enters that claim when the background task
    starts. There is no await in here: inside one process the check and the
    stamp are one step, which is what makes the gate a gate.

    `unsynced` is the tenant's tracked-but-never-synced competitor ids (see
    try_claim_refresh, which reads them): when there are any, the cooldown
    lets the claim through with scope "unsynced" — full_refresh then runs
    the chain for those competitors only. An in_flight reply marks the
    holder `rerun`.
    """
    key = _tenant_key(tenant_id)
    now = now or datetime.now(UTC)
    st = _REFRESH_STATE.get(key)
    scope, prev = "full", None
    if st:
        if _running(st, now):
            if st.get("job_id") == job_id:
                return None
            st["rerun"] = True
            return {"job_id": st["job_id"], "reused": "in_flight",
                    "note": "a refresh is already running for this tenant; "
                            "competitors tracked since it began are pulled "
                            "when it finishes"}
        at = st.get("finished_at")
        if at:
            # Kept so a run that pulls nothing can put this cooldown back.
            prev = {"job_id": st["job_id"], "finished_at": at}
            if not force and now - at < REFRESH_COOLDOWN:
                if not unsynced:
                    mins = int(REFRESH_COOLDOWN.total_seconds() // 60)
                    return {"job_id": st["job_id"], "reused": "cooldown",
                            "note": f"a refresh finished within {mins}m; "
                                    "pass force=true to run another"}
                scope = "unsynced"
    _REFRESH_STATE[key] = {"job_id": job_id, "started_at": now,
                           "last_progress_at": now, "finished_at": None,
                           "scope": scope, "prev": prev}
    return None


def touch_refresh(tenant_id: UUID | None, job_id: str,
                  now: datetime | None = None) -> None:
    """The holder's sign of life; REFRESH_MAX_RUNTIME counts from here."""
    st = _REFRESH_STATE.get(_tenant_key(tenant_id))
    if st and st.get("job_id") == job_id and st.get("finished_at") is None:
        st["last_progress_at"] = now or datetime.now(UTC)


def release_refresh(tenant_id: UUID | None, job_id: str,
                    now: datetime | None = None, pulled: bool = True) -> None:
    """End the holder's claim — on failure too, which is why full_refresh
    calls this from a finally. Only the holder may release: a late release
    from a job that lost its slot to a newer one is ignored.

    `pulled` says whether the chain got anything (see _chain_pulled). If it
    did, the cooldown starts now. If it did not, nothing was bought for a
    cooldown to protect: the cooldown an earlier chain started is put back
    as it was, or the slot is simply freed."""
    key = _tenant_key(tenant_id)
    st = _REFRESH_STATE.get(key)
    if not (st and st.get("job_id") == job_id and st.get("finished_at") is None):
        return
    now = now or datetime.now(UTC)
    if pulled:
        _REFRESH_STATE[key] = {"job_id": job_id, "started_at": st["started_at"],
                               "finished_at": now}
        return
    prev = st.get("prev")
    if prev:
        _REFRESH_STATE[key] = {"job_id": prev["job_id"],
                               "started_at": prev["finished_at"],
                               "finished_at": prev["finished_at"]}
    else:
        _REFRESH_STATE.pop(key, None)


async def unsynced_tracked_ids(tenant_id: UUID | None) -> set[str]:
    """Tracked competitors that have never been synced — work no chain has
    bought yet. last_synced_at is stamped only by a sync that reached the
    provider, so a handle that errored stays in here."""
    # The tenant predicate is explicit, not left to RLS: a superuser or
    # BYPASSRLS role (the docker test DB's is one) skips policies, and then
    # another tenant's never-synced peer would reopen THIS tenant's cooldown
    # and buy a run for a roster that has nothing new.
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id FROM competitors "
            "WHERE status = 'tracked' AND last_synced_at IS NULL "
            "AND tenant_id = current_setting('app.current_tenant', true)::uuid")
    return {str(r["id"]) for r in rows}


async def _unsynced_or_empty(tenant_id: UUID | None) -> set[str]:
    # Best-effort: the gate falls back to the plain cooldown rather than
    # failing a refresh because this read did.
    try:
        return await unsynced_tracked_ids(tenant_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[competitor_sync] unsynced read failed: %s", e)
        return set()


async def try_claim_refresh(
    tenant_id: UUID | None, job_id: str, force: bool = False,
) -> dict | None:
    """claim_refresh, plus the one fact it cannot read itself: whether a
    tracked competitor is still waiting for its first pull. Read only when the
    cooldown is what is refusing, so the ordinary claim costs no query. The
    await sits between two claims, not inside one, so the gate stays a gate:
    a rival that claims in between is simply in flight to the second claim."""
    reused = claim_refresh(tenant_id, job_id, force=force)
    if not reused or reused["reused"] != "cooldown":
        return reused
    unsynced = await _unsynced_or_empty(tenant_id)
    if not unsynced:
        return reused
    return claim_refresh(tenant_id, job_id, force=force, unsynced=unsynced)


async def _catch_up(
    ids: set[str], tenant_id: UUID | None, limit: int, touch=None,
) -> dict:
    """The chain for these competitors only: pull, fill missing media, look,
    write up. Used for the never-synced competitors a cooldown let through,
    and for the ones tracked while a chain ran. The rest of the roster was
    just done; re-running the whole chain would re-buy it."""
    from . import competitor_media, competitor_profile, competitor_vision
    from .competitors import list_competitors
    tracked = [c for c in await list_competitors(status="tracked", tenant_id=tenant_id)
               if str(c.get("id")) in ids]
    results: list[dict] = []
    for c in tracked:
        if touch:
            touch()
        r = await sync_competitor(c, limit=limit, days=365, video_cap=10,
                                  tenant_id=tenant_id)
        results.append(r)
        if r.get("error") or r.get("skipped"):
            continue
        for stage, call in (
            ("media", lambda c=c: competitor_media.fetch_media_for_competitor(
                c, max(limit, 30), tenant_id)),
            ("vision", lambda c=c: competitor_vision.analyze_competitor(
                str(c["id"]), post_cap=14, video_cap=5, tenant_id=tenant_id)),
            ("profile", lambda c=c: competitor_profile.build_profile(
                str(c["id"]), tenant_id=tenant_id)),
        ):
            try:
                await call()
            except Exception as e:  # noqa: BLE001 — best-effort, like the chain
                r.setdefault("catch_up_errors", {})[stage] = str(e)[:200]
    return {
        "competitors": len(tracked),
        "synced": sum(1 for r in results if not r.get("error") and not r.get("skipped")),
        "items_fetched": sum(r.get("items_fetched", 0) for r in results),
        "rows_new": sum(r.get("rows_new", 0) for r in results),
        "results": results,
    }


def _chain_pulled(out: dict) -> bool:
    """Whether a finished chain got anything: a competitor synced, a file
    stored, a post analysed. That is what the cooldown protects from being
    bought twice. A chain that pulled nothing — nothing tracked yet, everyone
    fresh, every provider call refused — leaves nothing to protect, and
    starting the cooldown on it is what kept onboarding's first real pull a
    week away. Media runs that stored nothing are not counted: they are capped
    per post (MAX_MEDIA_FETCH_ATTEMPTS) and per competitor
    (MAX_MEDIA_OUTAGE_RUNS), so a caller repeating them buys a bounded few.
    A stage that raised is counted as having pulled: it may have spent, and
    the cooldown is the safe side of not knowing."""
    stages = out.get("stages") or {}
    if any((stages.get(s) or {}).get("error") for s in ("sync", "media", "vision")):
        return True
    if ((stages.get("sync") or {}).get("synced")
            or (stages.get("media") or {}).get("stored")
            or (stages.get("vision") or {}).get("analysed")):
        return True
    return any((out.get(k) or {}).get("synced") or (out.get(k) or {}).get("error")
               for k in ("catch_up", "late_catch_up"))


async def full_refresh(
    tenant_id: UUID | None = None, limit: int = 30, progress=None,
    force: bool = False, job_id: str = "",
) -> dict:
    """The whole chain, in the only order that works.

    pull posts → download the media → analyse it → roll up profiles → measure
    the gap.

    Each step reads what the previous one wrote. Run separately they raced or
    were skipped, and the studio sat on posts it never looked at, analyses
    never rolled up, and a gap table nobody filled. `progress` is called with
    a dict at each stage so the UI can show where a long run is.

    Every stage is best-effort: one failing (a capped provider, a dead actor)
    must not cost the stages that already succeeded, so each is caught and
    reported rather than raised.

    `force` re-pulls competitors synced within SYNC_FRESH_DAYS, gives the
    media fetcher's given-up posts another go, and overrides the cooldown;
    the default leaves them alone, which is what makes three callers on
    three clocks (scheduler, BM2's Monday, the operator's button) cost one
    pull a week. It never overrides a chain in flight. Without `force`, a run
    in which no competitor synced, no file stored and no post was analysed
    does not re-run the profile rollup (an LLM call per competitor) or the
    gap: nothing on their side moved, and BM2's Monday call landing after the
    BM1 tick re-bought both every week.

    `job_id` is the claim the caller already holds (the route claims before
    it answers); without one this claims for itself, which is the scheduler
    path. Either way the slot is released here, failure included. A call
    the clock turns away returns {skipped: "in_flight" | "cooldown"} and
    runs nothing. A never-synced tracked competitor gets past the cooldown,
    and that run is only for them (`scope: "unsynced"`, `catch_up`); one
    tracked while this chain ran is pulled before it ends if anyone asked
    meanwhile (`catch_up` / `late_catch_up`). See _REFRESH_STATE.

    Over the brand's daily spend cap, or with PAUSE_SPEND on, nothing runs
    and the reply is {skipped: "spend", reason}: logged, never raised. It is
    asked before the claim, so a refusal neither holds the slot nor starts
    the cooldown (a route that claimed first releases in its own finally).
    sync_all and the media fetcher ask again, so a cap reached mid-chain
    stops the next paid stage too.
    """
    from . import competitor_gap, competitor_media, competitor_profile, competitor_vision

    gate = await competitor_media.spend_blocked(tenant_id, "competitor refresh")
    if gate:
        reason = gate.get("reason") or "spend blocked"
        return {"skipped": "spend", "reason": reason,
                "note": f"not run: {reason}", "stages": {}}

    job_id = job_id or f"refresh-{uuid4()}"
    reused = await try_claim_refresh(tenant_id, job_id, force=force)
    if reused:
        logger.info("[competitor_sync] refresh for %s not run: %s (%s)",
                    _tenant_key(tenant_id), reused["reused"], reused["job_id"])
        return {"skipped": reused["reused"], "reused_job_id": reused["job_id"],
                "note": reused["note"], "stages": {}}
    narrow = (refresh_state(tenant_id) or {}).get("scope") == "unsynced"
    # Who was waiting for a first pull when this chain began — sync_all lists
    # the roster a moment later. Whoever is unsynced at the end and is NOT in
    # here was tracked mid-chain.
    before = await _unsynced_or_empty(tenant_id)

    def _touch() -> None:
        touch_refresh(tenant_id, job_id)

    def _say(stage: str, n: int) -> None:
        _touch()
        if progress:
            progress({"stage": stage, "step": n, "steps": 5})

    out: dict = {"stages": {}}
    completed = False
    try:
        if narrow:
            out["scope"] = "unsynced"
            _say("pulling the competitors nobody has pulled yet", 1)
            try:
                out["catch_up"] = await _catch_up(before, tenant_id, limit, _touch)
            except Exception as e:  # noqa: BLE001
                out["catch_up"] = {"error": str(e)[:200]}
        else:
            stages = out["stages"]
            _say("pulling their posts", 1)
            try:
                r = await sync_all(limit=limit, days=365, video_cap=10,
                                   concurrency=2, tenant_id=tenant_id, force=force)
                stages["sync"] = {"synced": r.get("synced", 0),
                                  "posts": r.get("posts_stored", 0),
                                  "media": r.get("media_stored", 0),
                                  "skipped": r.get("skipped", 0),
                                  "items_fetched": r.get("items_fetched", 0),
                                  "rows_new": r.get("rows_new", 0)}
                if r.get("spend_blocked"):
                    stages["sync"]["spend_blocked"] = r["spend_blocked"]
            except Exception as e:  # noqa: BLE001
                stages["sync"] = {"error": str(e)[:200]}

            _say("downloading the media", 2)
            try:
                r = await competitor_media.fetch_all_missing_media(
                    limit=max(limit, 30), tenant_id=tenant_id, force=force)
                stages["media"] = {"stored": r.get("stored", 0),
                                   "actor_runs": r.get("actor_runs", 0),
                                   "items_fetched": r.get("items_fetched", 0),
                                   "rows_new": r.get("rows_new", 0)}
                if r.get("spend_blocked"):
                    stages["media"]["spend_blocked"] = r["spend_blocked"]
            except Exception as e:  # noqa: BLE001
                stages["media"] = {"error": str(e)[:200]}

            _say("looking at what they post", 3)
            try:
                r = await competitor_vision.analyze_all(
                    post_cap=14, video_cap=5, tenant_id=tenant_id)
                stages["vision"] = {"analysed": r.get("analyzed", 0),
                                    "ok": r.get("ok", 0)}
            except Exception as e:  # noqa: BLE001
                stages["vision"] = {"error": str(e)[:200]}

            # Two weekly clocks reach this chain (the BM1 tick and BM2's Monday
            # call) and the freshness skip makes the second one's pull free —
            # but the rollup re-synthesises every competitor with an LLM call
            # and the gap is recomputed, on a shelf that did not change. Skipped
            # only when nothing moved AND nothing failed (an errored stage
            # might have left work for them).
            if not (force or _chain_pulled(out)):
                stages["profiles"] = {"skipped": "nothing new on their side"}
                stages["gap"] = {"skipped": "nothing new on their side"}
            else:
                _say("writing up what they do", 4)
                try:
                    r = await competitor_profile.build_all_profiles(tenant_id=tenant_id)
                    stages["profiles"] = {"built": r.get("built", 0)}
                except Exception as e:  # noqa: BLE001
                    stages["profiles"] = {"error": str(e)[:200]}

                # The subtraction — what they post that we don't. It was computed
                # only when somebody opened the gap view, so the table was empty
                # for every brand nobody had opened it for: two of three, measured
                # 2026-09-19. It belongs at the end of the chain because it needs
                # BOTH sides, and the analyses it reads were only just written.
                _say("measuring the gap", 5)
                try:
                    r = await competitor_gap.content_gap(tenant_id=tenant_id)
                    stages["gap"] = {
                        "gaps": len(((r or {}).get("facts") or {})
                                    .get("measured_format_shortfall") or []),
                        "insufficient": list((r or {}).get("insufficient") or []),
                    }
                except Exception as e:  # noqa: BLE001
                    stages["gap"] = {"error": str(e)[:200]}

        # Someone asked while this ran and was told "in flight"; they will not
        # ask again. Pull whoever was tracked after the roster was read. Last
        # before the release so the window for a request to miss both this
        # check and the cooldown's reopening is the release itself.
        st = _REFRESH_STATE.get(_tenant_key(tenant_id))
        if st and st.get("job_id") == job_id and st.pop("rerun", False):
            new = await _unsynced_or_empty(tenant_id) - before
            if new:
                key = "late_catch_up" if "catch_up" in out else "catch_up"
                try:
                    out[key] = await _catch_up(new, tenant_id, limit, _touch)
                except Exception as e:  # noqa: BLE001
                    out[key] = {"error": str(e)[:200]}

        out["status"] = await studio_status(tenant_id)
        completed = True
    finally:
        # However the chain ended: one that raised is assumed to have spent.
        pulled = _chain_pulled(out) if completed else True
        out["cooldown_started"] = pulled
        release_refresh(tenant_id, job_id, pulled=pulled)
    return out


async def run_competitor_refresh(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point for the whole chain. Tenant-bound and explicit."""
    cfg = config or {}
    await full_refresh(tenant_id=tenant_id, limit=int(cfg.get("limit") or 30),
                       force=bool(cfg.get("force")))


async def shelf_stats(tenant_id: UUID | None = None) -> dict:
    """How much competitor material we actually hold — the honest counter
    behind 'we save everything'."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT count(*) AS posts,
                      count(DISTINCT competitor_id) AS competitors,
                      count(*) FILTER (WHERE stored_media_url <> '') AS media_saved,
                      count(*) FILTER (WHERE media_type = 'video') AS videos,
                      max(last_synced_at) AS last_sync
                 FROM competitor_posts""")
    d = dict(row)
    if d.get("last_sync") is not None:
        d["last_sync"] = d["last_sync"].isoformat()
    return d


async def run_competitor_sync(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point — keeps every tracked competitor's shelf current.

    Tenant-bound and explicit, per the scheduler contract: there is no request
    contextvar here, so the tenant is threaded through every call. This is what
    makes "we save everything automatically" true rather than aspirational —
    without it the shelf only grows when someone clicks a button.
    """
    cfg = config or {}
    await sync_all(
        limit=int(cfg.get("limit") or 24),
        days=int(cfg.get("days") or 90),
        video_cap=int(cfg.get("video_cap") or 3),
        tenant_id=tenant_id,
        force=bool(cfg.get("force")),
    )


__all__ = [
    "gallery", "set_replicate", "replicate_counts",
    "refresh_profile", "sync_competitor", "run_competitor_sync", "sync_all", "list_posts", "shelf_stats",
    "SYNC_FRESH_DAYS", "is_fresh", "full_refresh", "run_competitor_refresh",
    "REFRESH_COOLDOWN", "REFRESH_MAX_RUNTIME", "claim_refresh", "release_refresh",
    "refresh_state", "try_claim_refresh", "touch_refresh", "unsynced_tracked_ids",
]
