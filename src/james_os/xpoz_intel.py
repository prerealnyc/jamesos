"""Xpoz social-data adapter — cross-platform brand listening.

One key (Settings → API connections → "Xpoz API key") unlocks normalized
search across X / Instagram / TikTok / Reddit via Xpoz's official async SDK.

Degrade-safe by design: no key, or the `xpoz` package missing, returns a
structured ``{error: …}`` — it NEVER raises into a request handler, matching
how every other provider in this codebase fails honestly.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any

from .config import settings

PLATFORMS = ("twitter", "instagram", "tiktok", "reddit")
_PLATFORM_LABEL = {"twitter": "x", "instagram": "instagram", "tiktok": "tiktok", "reddit": "reddit"}

# Curated field sets per platform — only what we normalize below, to keep
# result payloads (and Xpoz quota usage) lean.
_FIELDS: dict[str, list[str]] = {
    "twitter": ["id", "text", "author_username", "like_count", "retweet_count",
                "reply_count", "impression_count", "created_at_date"],
    "instagram": ["id", "caption", "username", "like_count", "comment_count",
                  "reshare_count", "code_url", "created_at"],
    "tiktok": ["id", "description", "username", "like_count", "play_count",
               "comment_count", "forward_count", "video_url", "created_at_date"],
    "reddit": ["id", "title", "selftext", "author_username", "subreddit_name",
               "score", "comments_count", "permalink"],
}


def configured() -> bool:
    return bool((settings.xpoz_api_key or "").strip())


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def _page_items(result: Any, limit: int) -> list[Any]:
    """Pull the first page of an AsyncPaginatedResult into a plain list,
    tolerant of the SDK returning a list directly or a page object."""
    try:
        page = result.get_page()
        if asyncio.iscoroutine(page):
            page = await page
    except Exception:  # noqa: BLE001 — fall back to treating result as iterable
        page = result
    if isinstance(page, list):
        items = page
    else:
        items = (_g(page, "items") or _g(page, "data") or _g(page, "results") or [])
    return list(items)[:limit]


def _normalize(platform: str, p: Any) -> dict:
    if platform == "twitter":
        pid = _g(p, "id")
        return {
            "platform": "x", "id": pid, "text": _g(p, "text", "") or "",
            "author": _g(p, "author_username"),
            "likes": _g(p, "like_count"), "comments": _g(p, "reply_count"),
            "shares": _g(p, "retweet_count"), "views": _g(p, "impression_count"),
            "created": str(_g(p, "created_at_date") or ""),
            "url": f"https://x.com/i/web/status/{pid}" if pid else None,
        }
    if platform == "instagram":
        return {
            "platform": "instagram", "id": _g(p, "id"), "text": _g(p, "caption", "") or "",
            "author": _g(p, "username"),
            "likes": _g(p, "like_count"), "comments": _g(p, "comment_count"),
            "shares": _g(p, "reshare_count"),
            "created": str(_g(p, "created_at") or ""), "url": _g(p, "code_url"),
        }
    if platform == "tiktok":
        return {
            "platform": "tiktok", "id": _g(p, "id"), "text": _g(p, "description", "") or "",
            "author": _g(p, "username"),
            "likes": _g(p, "like_count"), "comments": _g(p, "comment_count"),
            "shares": _g(p, "forward_count"), "views": _g(p, "play_count"),
            "created": str(_g(p, "created_at_date") or ""), "url": _g(p, "video_url"),
        }
    # reddit
    title = _g(p, "title", "") or ""
    body = _g(p, "selftext", "") or ""
    perma = _g(p, "permalink")
    return {
        "platform": "reddit", "id": _g(p, "id"),
        "text": (f"{title} — {body}" if body else title)[:600],
        "author": _g(p, "author_username"), "subreddit": _g(p, "subreddit_name"),
        "likes": _g(p, "score"), "comments": _g(p, "comments_count"),
        "created": "", "url": f"https://reddit.com{perma}" if perma else None,
    }


async def account_info() -> dict:
    """Plan + usage for the connected Xpoz account — answers 'what can I pull
    and how much is left?' without spending a search."""
    if not configured():
        return {"configured": False,
                "error": "No Xpoz API key. Add it under Settings → API connections."}
    try:
        import xpoz
    except ImportError:
        return {"configured": True, "error": "The `xpoz` package isn't installed on the server."}
    try:
        async with xpoz.AsyncXpozClient(settings.xpoz_api_key.strip(), check_update=False) as c:
            d = await c.account.get_account_details()
        # Flatten the SDK objects to JSON-safe primitives — the client
        # expects plain strings/numbers, not nested plan/usage objects.
        plan = _g(d, "plan")
        feats = _g(plan, "features")
        usage = _g(d, "usage")
        billing = _g(d, "billing")
        return {
            "configured": True,
            "plan_name": _g(plan, "name"),
            "credits": _g(feats, "credits"),
            "tracked_items": _g(feats, "tracked_items"),
            "reset_frequency": _g(feats, "credit_reset_frequency"),
            "credits_remaining": _g(usage, "subscription_credits_remaining"),
            "extra_credits_remaining": _g(usage, "extra_credits_remaining"),
            "billing_period": _g(billing, "billing_period"),
            "next_renewal": str(_g(billing, "next_renewal_date") or "") or None,
        }
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "error": f"{type(e).__name__}: {e}"}


async def search_social(
    query: str,
    platforms: list[str] | None = None,
    limit: int = 10,
    start_date: str | None = None,
    min_likes: int = 10000,
) -> dict:
    """Normalized cross-platform post search. Per-platform failures are
    collected in `errors` and never abort the whole query.

    Quality floor: `min_likes` (default 10,000) drops low-engagement posts.
    Because 10k+ likes is genuinely rare, we fetch a LARGER pool per
    platform and keep only the qualifiers — so the result is "proven viral",
    not just recent. `filtered_out` reports how many were below the bar.
    """
    if not configured():
        return {"error": "No Xpoz API key configured. Add it under Settings → API connections."}
    plats = [p for p in (platforms or list(PLATFORMS)) if p in PLATFORMS]
    if not query.strip() or not plats:
        return {"query": query, "results": [], "count": 0, "errors": {}, "min_likes": min_likes}
    try:
        import xpoz
    except ImportError:
        return {"error": "The `xpoz` package isn't installed on the server."}

    # When a quality floor is on, pull a deeper pool so enough posts clear
    # the bar; capped to keep credit spend bounded.
    fetch_n = min(50, max(limit * 4, 30)) if min_likes > 0 else limit

    out: list[dict] = []
    errors: dict[str, str] = {}
    try:
        # Lower client timeout than the default 300s — we're behind a ~30s
        # edge proxy, so a slow provider must fail fast, not hang the request.
        async with xpoz.AsyncXpozClient(
            settings.xpoz_api_key.strip(), check_update=False, timeout=26
        ) as c:
            async def _one(plat: str) -> tuple[str, list[dict], str | None]:
                try:
                    ns = getattr(c, plat)
                    kwargs: dict[str, Any] = {"fields": _FIELDS[plat], "limit": fetch_n}
                    if start_date:
                        kwargs["start_date"] = start_date
                    # Per-platform ceiling so one slow network can't blow the
                    # whole request past the edge timeout.
                    res = await asyncio.wait_for(ns.search_posts(query, **kwargs), timeout=24)
                    items = await _page_items(res, fetch_n)
                    return plat, [_normalize(plat, p) for p in items], None
                except asyncio.TimeoutError:
                    return plat, [], "timed out"
                except Exception as e:  # noqa: BLE001 — one platform can't kill the rest
                    return plat, [], f"{type(e).__name__}: {e}"

            # Run all platforms CONCURRENTLY — total time ≈ slowest single
            # platform, not the sum (the sequential loop blew the 30s edge
            # timeout on a 4-platform search).
            for plat, posts, err in await asyncio.gather(*[_one(p) for p in plats]):
                out.extend(posts)
                if err:
                    errors[plat] = err
    except Exception as e:  # noqa: BLE001 — connect/auth failure
        return {"error": f"{type(e).__name__}: {e}"}

    pool = len(out)
    if min_likes > 0:
        out = [r for r in out if (r.get("likes") or 0) >= min_likes]
    out.sort(key=lambda r: (r.get("likes") or 0), reverse=True)
    out = out[:limit]
    return {
        "query": query, "results": out, "count": len(out), "errors": errors,
        "min_likes": min_likes, "filtered_out": max(0, pool - len(out)),
    }


async def trending_in_niche(
    niche: str,
    platforms: list[str] | None = None,
    limit: int = 8,
    days: int = 7,
    min_likes: int = 10000,
) -> dict:
    """Top recent posts in a niche, ranked by engagement — 'what's working
    right now' for content ideation. Thin wrapper over search_social with a
    recency window + the 10k-likes quality floor."""
    from datetime import date, timedelta

    start = (date.today() - timedelta(days=max(1, days))).isoformat()
    return await search_social(
        niche, platforms=platforms, limit=limit, start_date=start, min_likes=min_likes
    )


async def trending_from_creators(
    creators: list[dict],
    limit: int = 10,
    days: int = 14,
    min_likes: int = 10000,
    max_creators: int = 6,
) -> dict:
    """Latest high-engagement posts BY the tenant's tracked creators (the
    Research watchlist) via Xpoz. For each creator we search their platform
    for the handle and keep only posts whose author matches that handle,
    ≥min_likes, within `days`. Bounded to `max_creators` per run to cap
    credit spend (this runs inside the autopilot batch, not a web request).
    Returns the search_social shape; each post is tagged `from_creator`."""
    if not configured():
        return {"results": [], "count": 0, "creators_queried": 0,
                "error": "No Xpoz API key configured."}
    # Dedup by (platform, handle); curated creators (those with interests)
    # first so a bounded run favours the hand-picked, on-topic names.
    seen: set = set()
    picks: list[dict] = []
    ordered = sorted(creators or [], key=lambda c: 0 if c.get("interests") else 1)
    for c in ordered:
        plat = (c.get("platform") or "").lower()
        handle = (c.get("handle") or "").strip().lstrip("@")
        if plat not in PLATFORMS or not handle:
            continue
        key = (plat, handle.lower())
        if key in seen:
            continue
        seen.add(key)
        picks.append({"platform": plat, "handle": handle, "name": c.get("name") or handle})
        if len(picks) >= max_creators:
            break
    if not picks:
        return {"results": [], "count": 0, "creators_queried": 0, "error": None}

    from datetime import date, timedelta
    start = (date.today() - timedelta(days=max(1, days))).isoformat()

    async def _one(p: dict) -> list[dict]:
        r = await search_social(
            p["handle"], platforms=[p["platform"]], limit=4,
            start_date=start, min_likes=min_likes,
        )
        h = p["handle"].lower()
        mine = [
            x for x in (r.get("results") or [])
            if (x.get("author") or "").lower().lstrip("@") == h
        ]
        for x in mine:
            x["from_creator"] = p["name"]
        return mine

    batches = await asyncio.gather(*[_one(p) for p in picks], return_exceptions=True)
    flat: list[dict] = []
    for b in batches:
        if isinstance(b, list):
            flat.extend(b)
    # Dedup by url, rank by likes, cap.
    seen_urls: set = set()
    uniq: list[dict] = []
    for x in sorted(flat, key=lambda r: (r.get("likes") or 0), reverse=True):
        u = x.get("url") or ""
        if u and u in seen_urls:
            continue
        seen_urls.add(u)
        uniq.append(x)
    uniq = uniq[:limit]
    return {"results": uniq, "count": len(uniq), "creators_queried": len(picks), "error": None}


def trending_lines(posts: list[dict], cap: int = 10) -> list[str]:
    """Compact one-line summaries of trending posts for LLM prompt grounding.
    e.g. '[x @jp ♥12000 💬300] We just closed the biggest deal…'"""
    out: list[str] = []
    for p in (posts or [])[:cap]:
        txt = (p.get("text") or "").replace("\n", " ").strip()[:180]
        likes = p.get("likes")
        comments = p.get("comments")
        tag = f" ·tracked:{p['from_creator']}" if p.get("from_creator") else ""
        out.append(
            f"[{p.get('platform','?')} @{p.get('author') or 'unknown'} "
            f"♥{likes if likes is not None else '?'} 💬{comments if comments is not None else '?'}{tag}] {txt}"
        )
    return out


_KW_STOP = {
    "this", "that", "with", "from", "your", "have", "what", "when", "they",
    "will", "about", "just", "like", "than", "then", "them", "were", "into",
    "over", "more", "youre", "dont", "cant", "weve", "heres", "https", "http",
    "because", "really", "could", "would", "should", "their", "there", "these",
    "those", "being", "after", "make", "made", "want", "need", "know", "people",
    "every", "still", "much", "very", "here", "thing", "things", "going",
}


def trending_keywords(posts: list[dict], cap: int = 12) -> list[str]:
    """Top hashtags + frequent salient terms across the trending posts — the
    literal 'what words are working right now' signal for the script writer.
    Hashtags rank first (highest intent), then frequent content words."""
    tags: Counter = Counter()
    words: Counter = Counter()
    for p in posts or []:
        txt = (p.get("text") or "")
        for tag in re.findall(r"#(\w{2,30})", txt):
            tags[tag.lower()] += 1
        for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", txt.lower()):
            w = w.strip("'")
            if len(w) >= 4 and w not in _KW_STOP:
                words[w] += 1
    out: list[str] = [f"#{t}" for t, _ in tags.most_common(cap)]
    for w, _ in words.most_common(cap * 3):
        if len(out) >= cap:
            break
        out.append(w)
    return out[:cap]


__all__ = [
    "PLATFORMS", "configured", "account_info", "search_social",
    "trending_in_niche", "trending_from_creators", "trending_lines",
    "trending_keywords",
]
