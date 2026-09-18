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
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from . import competitor_apify
from .competitors import PLATFORMS, _g, _int, configured
from .config import settings
from .db import acquire

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
        RETURNING *
        """,
        competitor_id, platform, post["post_id"], post["url"], post["caption"],
        post["media_type"], post["media_url"], post["thumbnail_url"],
        post["duration"], likes, comments, post["shares"], views, eng_rate,
        post["posted_at"], snapshot,
    )
    return _row(row)



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


async def sync_competitor(
    competitor: dict, limit: int = 24, days: int = 90,
    video_cap: int = 3, store_media: bool = True,
    tenant_id: UUID | None = None,
) -> dict:
    """Pull one competitor's recent posts, persist them, and copy their media.

    Returns {handle, platform, fetched, stored, media_stored, media_skipped,
    error}. Never raises — a broken handle reports and the batch continues.
    """
    platform = (competitor.get("platform") or "").lower()
    handle = (competitor.get("handle") or "").lstrip("@")
    base = {"competitor_id": competitor.get("id"), "handle": handle,
            "platform": platform, "fetched": 0, "stored": 0,
            "media_stored": 0, "media_skipped": []}
    if not handle:
        return {**base, "error": "no handle"}
    # YouTube and LinkedIn have no Xpoz search; they come in by name from the
    # brand's approved peers and are fetched through Apify instead. Everything
    # after this point is identical — the two fetchers return the same rows.
    via_apify = platform in competitor_apify.PLATFORMS
    if not via_apify and platform not in PLATFORMS:
        return {**base, "error": f"unsupported platform '{platform}'"}
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

    if via_apify:
        posts, err = await competitor_apify.fetch_posts(platform, handle, limit, days)
        if err and not posts:
            return {**base, "error": err}
    else:
        try:
            async with xpoz.AsyncXpozClient(
                settings.xpoz_api_key.strip(), check_update=False, timeout=45
            ) as c:
                posts = await _fetch_posts(c, platform, handle, limit, days)
        except TimeoutError:
            return {**base, "error": "timed out"}
        except Exception as e:  # noqa: BLE001
            return {**base, "error": f"{type(e).__name__}: {e}"}

    followers = _int(competitor.get("followers"))
    stored: list[dict] = []
    async with acquire(tenant_id) as conn:
        for p in posts:
            try:
                stored.append(await _upsert_post(conn, str(competitor["id"]),
                                                 platform, p, followers))
            except Exception as e:  # noqa: BLE001 — one bad row ≠ no sync
                print(f"[competitor_sync] {handle} post {p.get('post_id')}: {e}")

    media_stored, skipped = 0, []
    if store_media and stored:
        # Videos are expensive; take the highest-engagement ones first so the
        # cap spends the budget on the posts worth watching.
        ranked = sorted(stored, key=lambda r: (r.get("likes", 0) + r.get("comments", 0)),
                        reverse=True)
        videos_done = 0
        for row in ranked:
            if row.get("stored_media_url"):
                continue
            # What we actually DOWNLOAD decides the kind. A video post with no
            # file URL (YouTube gives none) falls back to its thumbnail, and
            # storing a JPEG under .mp4 makes it unreadable to the design eye
            # and unrenderable in the grid.
            src = row.get("media_url") or ""
            kind = "video" if (row.get("media_type") == "video" and src) else "image"
            if not src:
                src = row.get("thumbnail_url") or ""
            if kind == "video":
                if videos_done >= video_cap:
                    skipped.append({"url": row.get("url"), "reason": "video cap"})
                    continue
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
            "media_stored": media_stored, "media_skipped": skipped[:10],
            "error": None}


async def sync_all(
    limit: int = 24, days: int = 90, video_cap: int = 3,
    concurrency: int = 3, tenant_id: UUID | None = None,
) -> dict:
    """Sync every TRACKED competitor. Bounded concurrency keeps Xpoz credit
    burn and memory predictable when a tenant tracks dozens of accounts."""
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
                tenant_id=tenant_id)

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
        "synced": sum(1 for r in clean if not r.get("error")),
        "posts_stored": sum(r.get("stored", 0) for r in clean),
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
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT
                 (SELECT count(*) FROM competitors
                   WHERE status = 'tracked')                        AS competitors,
                 (SELECT count(*) FROM competitor_posts)            AS posts,
                 (SELECT count(*) FROM competitor_posts
                   WHERE stored_media_url <> '')                    AS with_media,
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
            """)
    d = dict(row)
    for k in ("last_sync", "last_analysis"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    # What is left to do, so the UI can say "12 still to analyse" rather than
    # spinning with no idea whether anything is happening.
    d["media_pending"] = max(0, d["posts"] - d["with_media"])
    d["analysis_pending"] = max(0, d["with_media"] - d["analysed"])
    return d


async def full_refresh(
    tenant_id: UUID | None = None, limit: int = 30, progress=None,
) -> dict:
    """The whole chain, in the only order that works.

        pull posts → download the files → look at them → roll up profiles

    Each stage depends on the one before: you cannot analyse a post whose
    media never downloaded, and you cannot profile a competitor whose posts
    were never pulled. Splitting these into four buttons made that ordering
    the operator's problem and left the last two mostly unclicked — which is
    why a shelf could sit there with media and no analysis.

    `progress` is called between stages so a watcher can say WHICH stage is
    running rather than spinning. Every stage is best-effort: one failing
    (a capped provider, a dead actor) must not cost the stages that already
    succeeded, so each is caught and reported rather than raised.
    """
    from . import competitor_media, competitor_profile, competitor_vision

    def _say(stage: str, n: int) -> None:
        if progress:
            progress({"stage": stage, "step": n, "steps": 4})

    out: dict = {"stages": {}}

    _say("pulling their posts", 1)
    try:
        r = await sync_all(limit=limit, days=365, video_cap=10,
                           concurrency=2, tenant_id=tenant_id)
        out["stages"]["sync"] = {"posts": r.get("posts_stored", 0),
                                 "media": r.get("media_stored", 0)}
    except Exception as e:  # noqa: BLE001
        out["stages"]["sync"] = {"error": str(e)[:200]}

    _say("downloading the media", 2)
    try:
        r = await competitor_media.fetch_all_missing_media(
            limit=max(limit, 30), tenant_id=tenant_id)
        out["stages"]["media"] = {"stored": r.get("stored", 0)}
    except Exception as e:  # noqa: BLE001
        out["stages"]["media"] = {"error": str(e)[:200]}

    _say("looking at what they post", 3)
    try:
        r = await competitor_vision.analyze_all(
            post_cap=14, video_cap=5, tenant_id=tenant_id)
        out["stages"]["vision"] = {"analysed": r.get("analyzed", 0),
                                   "ok": r.get("ok", 0)}
    except Exception as e:  # noqa: BLE001
        out["stages"]["vision"] = {"error": str(e)[:200]}

    _say("writing up what they do", 4)
    try:
        r = await competitor_profile.build_all_profiles(tenant_id=tenant_id)
        out["stages"]["profiles"] = {"built": r.get("built", 0)}
    except Exception as e:  # noqa: BLE001
        out["stages"]["profiles"] = {"error": str(e)[:200]}

    out["status"] = await studio_status(tenant_id)
    return out


async def run_competitor_refresh(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point for the whole chain. Tenant-bound and explicit."""
    cfg = config or {}
    await full_refresh(tenant_id=tenant_id, limit=int(cfg.get("limit") or 30))


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
    )


__all__ = [
    "gallery", "set_replicate", "replicate_counts",
    "refresh_profile", "sync_competitor", "run_competitor_sync", "sync_all", "list_posts", "shelf_stats",
]
