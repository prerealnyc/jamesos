"""Analytics — aggregate reads over the BRAND's social-media data.

Scoped to handles the brand actually owns — see `brand_accounts.py`.
Peer / competitor data from `trends.get_watchlist()` is intentionally
excluded so the dashboard answers "how is OUR content doing," not
"how is the cohort doing." Cohort comparison happens elsewhere.

Note on asyncpg + jsonb: the pool returned by `acquire()` does NOT
register a JSON codec, so `row["payload"]` comes back as a string of
JSON text. We `json.loads` on every read; the cost is negligible
relative to the network round-trip.

Source of truth: the `events` table where every scraped post is stored
with `payload->>'category' = 'trend'` (see `apify.TREND_CATEGORY`).
Each row carries per-post metrics as JSONB:

    {
      "platform": "instagram" | "tiktok" | "youtube",
      "handle":   "<creator handle>",
      "url":      "<post URL>",
      "caption":  "<text>",
      "views":    int,
      "likes":    int,
      "comments": int,
      "shares":   int,
      "outlier_score": float,   # views ÷ creator-median (research-backed)
      "velocity":     float,    # views per hour since posted_at
      "posted_at":    "<iso>",
      "thumbnail":    "<url>"
    }

This module is READ-ONLY. Scraping / ingest live in `apify.py` +
`trends.py`. Anything that mutates state belongs over there.

Honest scope: aggregates are computed on every call (no materialized
view). With ~40 posts that's cheap; if the events table grows past a
few thousand trend rows per tenant, push these down to SQL `GROUP BY`
or a refreshed view.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .apify import TREND_CATEGORY
from .brand_accounts import brand_handle_set
from .db import acquire


def _payload_to_dict(raw: Any) -> dict:
    """Coerce an asyncpg jsonb field to a dict whether it came back as a
    dict (codec registered) or a JSON string (no codec). Returns {} on
    anything we can't parse — analytics should never crash on one bad
    row.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _engagement(p: dict) -> int:
    """Likes + comments + shares — the engagement-actions count.
    Views are tracked separately (impressions vs. interaction)."""
    return int(p.get("likes") or 0) + int(p.get("comments") or 0) + int(p.get("shares") or 0)


def _engagement_rate(p: dict) -> float:
    """Engagement / views — Instagram's headline metric. Returns 0 when
    views are zero so the UI can show a dash instead of NaN."""
    views = int(p.get("views") or 0)
    if views <= 0:
        return 0.0
    return round(_engagement(p) / views, 4)


async def _load_posts(
    *, platform: str = "", handle: str = "",
    since: datetime | None = None,
    tenant_id: UUID | None = None,
    allowed_handles: set[str] | None = None,
) -> list[dict]:
    """Pull post payloads from the events table, filtered to trends and
    (optionally) a platform / handle / posted-since window.

    `allowed_handles` is the brand-account whitelist: when non-None and
    non-empty, only posts whose handle is in this set are returned —
    that's how analytics stays scoped to the brand's own content. When
    None, no whitelist filter is applied (used by internal helpers
    that have already scoped the read). When empty set, returns zero
    posts (correctly — no accounts configured means no data).
    """
    clauses = [
        "payload ->> 'category' = $1",
        "superseded_by IS NULL",
    ]
    args: list[Any] = [TREND_CATEGORY]
    if platform:
        args.append(platform)
        clauses.append(f"payload ->> 'platform' = ${len(args)}")
    if handle:
        args.append(handle)
        # Lowercase comparison to be robust against @-prefix and case drift.
        clauses.append(
            f"lower(replace(payload ->> 'handle', '@', '')) = "
            f"lower(replace(${len(args)}, '@', ''))"
        )
    if since is not None:
        args.append(since)
        # posted_at lives inside the JSONB payload; parse + compare.
        clauses.append(
            f"nullif(payload ->> 'posted_at', '')::timestamptz >= ${len(args)}"
        )
    if allowed_handles is not None:
        if not allowed_handles:
            # No accounts configured → no posts. Don't run a query
            # that would silently leak peer content.
            return []
        args.append(list(allowed_handles))
        clauses.append(
            f"lower(replace(payload ->> 'handle', '@', '')) "
            f"= ANY(${len(args)}::text[])"
        )
    sql = f"""
        SELECT payload, effective_at
          FROM events
         WHERE {' AND '.join(clauses)}
         ORDER BY effective_at DESC
         LIMIT 2000
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *args)
    out: list[dict] = []
    for r in rows:
        p = _payload_to_dict(r["payload"])
        # effective_at is the row's ingest timestamp; useful when posted_at
        # is missing or the API returned a stale date.
        if "_ingested_at" not in p and r["effective_at"]:
            p["_ingested_at"] = r["effective_at"].isoformat()
        out.append(p)
    return out


# ── handle inventory ─────────────────────────────────────────────────


async def list_tracked_handles(
    tenant_id: UUID | None = None,
) -> list[dict]:
    """The brand's configured accounts, joined with post counts from
    the events table. Returns every configured handle even when no
    posts exist yet — so the empty state on the UI ("scrape your
    accounts to populate this") is honest.
    """
    from .brand_accounts import get_brand_accounts
    accounts = await get_brand_accounts(tenant_id)
    if not accounts:
        return []
    # One query, group counts by handle.
    sql = """
        SELECT payload ->> 'platform' AS platform,
               lower(replace(payload ->> 'handle', '@', '')) AS handle,
               count(*) AS posts,
               max(nullif(payload ->> 'posted_at', '')::timestamptz) AS last_post_at
          FROM events
         WHERE payload ->> 'category' = $1
           AND superseded_by IS NULL
           AND payload ->> 'handle' IS NOT NULL
         GROUP BY 1, 2
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, TREND_CATEGORY)
    by_key = {
        ((r["platform"] or "").lower(), (r["handle"] or "").lower()): r
        for r in rows
    }
    out: list[dict] = []
    for a in accounts:
        platform = (a.get("platform") or "").lower()
        handle = (a.get("handle") or "").lower()
        r = by_key.get((platform, handle))
        out.append({
            "platform": platform,
            "handle": handle,
            "name": a.get("name") or "",
            "posts": int(r["posts"]) if r else 0,
            "last_post_at": (
                r["last_post_at"].isoformat()
                if r and r["last_post_at"] else None
            ),
        })
    # Configured-with-data first, then unscraped (zero posts) at the
    # bottom so the user can see what's pending.
    out.sort(key=lambda x: (-x["posts"], x["handle"]))
    return out


# ── per-handle summary ───────────────────────────────────────────────


async def handle_summary(
    *, handle: str = "", platform: str = "",
    days: int = 30, tenant_id: UUID | None = None,
) -> dict:
    """Aggregate stats for one handle (or all handles when blank).

    Window is the last `days` days based on posted_at. The returned
    shape is intentionally flat so the frontend renders a few cards
    without unwrapping nested objects.
    """
    since = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ) if days <= 0 else None
    if days > 0:
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(days=days)
    allowed = await brand_handle_set(tenant_id)
    posts = await _load_posts(
        platform=platform, handle=handle, since=since, tenant_id=tenant_id,
        allowed_handles=allowed,
    )
    n = len(posts)
    views = sum(int(p.get("views") or 0) for p in posts)
    likes = sum(int(p.get("likes") or 0) for p in posts)
    comments = sum(int(p.get("comments") or 0) for p in posts)
    shares = sum(int(p.get("shares") or 0) for p in posts)
    eng = likes + comments + shares
    er = (eng / views) if views > 0 else 0.0
    outliers = [float(p.get("outlier_score") or 0.0) for p in posts]
    median_outlier = (
        sorted(outliers)[len(outliers) // 2] if outliers else 0.0
    )
    avg_outlier = (sum(outliers) / len(outliers)) if outliers else 0.0
    # Best post by outlier score (the breakout) so the UI can show one
    # high-signal card without re-sorting.
    best = max(
        posts, key=lambda p: float(p.get("outlier_score") or 0.0),
        default=None,
    )
    by_platform: dict[str, int] = defaultdict(int)
    for p in posts:
        by_platform[str(p.get("platform") or "")] += 1
    return {
        "handle": handle,
        "platform": platform,
        "days": days,
        "post_count": n,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement": eng,
        "engagement_rate": round(er, 4),
        "median_outlier": round(median_outlier, 2),
        "avg_outlier": round(avg_outlier, 2),
        "by_platform": dict(by_platform),
        "best_post": (
            {
                "url": best.get("url") or "",
                "caption": (best.get("caption") or "")[:200],
                "views": int(best.get("views") or 0),
                "outlier_score": float(best.get("outlier_score") or 0.0),
                "platform": best.get("platform") or "",
                "thumbnail": best.get("thumbnail") or "",
                "posted_at": best.get("posted_at") or "",
            }
            if best
            else None
        ),
    }


# ── post list (sortable) ─────────────────────────────────────────────


_SORT_KEYS = {
    "views": lambda p: int(p.get("views") or 0),
    "likes": lambda p: int(p.get("likes") or 0),
    "comments": lambda p: int(p.get("comments") or 0),
    "engagement": _engagement,
    "engagement_rate": _engagement_rate,
    "outlier": lambda p: float(p.get("outlier_score") or 0.0),
    "velocity": lambda p: float(p.get("velocity") or 0.0),
    "recent": lambda p: (
        _parse_dt(p.get("posted_at")) or datetime.min.replace(tzinfo=timezone.utc)
    ),
}


async def list_posts(
    *, handle: str = "", platform: str = "",
    days: int = 30, sort: str = "outlier",
    limit: int = 30, tenant_id: UUID | None = None,
) -> list[dict]:
    """Sortable list of posts for the analytics table. `sort` ∈ keys of
    _SORT_KEYS — unknown sorts fall back to outlier so the UI never
    breaks on a stale option."""
    from datetime import timedelta
    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days > 0 else None
    )
    allowed = await brand_handle_set(tenant_id)
    posts = await _load_posts(
        platform=platform, handle=handle, since=since, tenant_id=tenant_id,
        allowed_handles=allowed,
    )
    key = _SORT_KEYS.get(sort, _SORT_KEYS["outlier"])
    posts.sort(key=key, reverse=True)
    out = []
    for p in posts[:limit]:
        out.append({
            "platform": p.get("platform") or "",
            "handle": p.get("handle") or "",
            "url": p.get("url") or "",
            "caption": (p.get("caption") or "")[:240],
            "thumbnail": p.get("thumbnail") or "",
            "views": int(p.get("views") or 0),
            "likes": int(p.get("likes") or 0),
            "comments": int(p.get("comments") or 0),
            "shares": int(p.get("shares") or 0),
            "engagement_rate": _engagement_rate(p),
            "outlier_score": float(p.get("outlier_score") or 0.0),
            "velocity": float(p.get("velocity") or 0.0),
            "posted_at": p.get("posted_at") or "",
        })
    return out


# ── time-series for the chart ────────────────────────────────────────


async def daily_timeline(
    *, handle: str = "", platform: str = "",
    days: int = 30, tenant_id: UUID | None = None,
) -> list[dict]:
    """One row per day in the window: {date, views, posts, engagement}.

    Posts with no posted_at fall back to the row's effective_at so a
    daily count is never silently truncated by missing-data rows.
    """
    from datetime import timedelta
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    since = today - timedelta(days=days)
    allowed = await brand_handle_set(tenant_id)
    posts = await _load_posts(
        platform=platform, handle=handle, since=since, tenant_id=tenant_id,
        allowed_handles=allowed,
    )
    by_day: dict[str, dict] = {}
    for p in posts:
        d = _parse_dt(p.get("posted_at")) or _parse_dt(p.get("_ingested_at"))
        if d is None:
            continue
        key = d.strftime("%Y-%m-%d")
        slot = by_day.setdefault(
            key, {"date": key, "views": 0, "posts": 0, "engagement": 0},
        )
        slot["views"] += int(p.get("views") or 0)
        slot["engagement"] += _engagement(p)
        slot["posts"] += 1
    # Fill empty days so the chart line doesn't have visual gaps.
    out: list[dict] = []
    for i in range(days + 1):
        key = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(by_day.get(
            key, {"date": key, "views": 0, "posts": 0, "engagement": 0},
        ))
    return out


# ── per-brand-account leaderboard ────────────────────────────────────


async def accounts_leaderboard(
    *, platform: str = "", days: int = 30,
    tenant_id: UUID | None = None,
) -> list[dict]:
    """One row per brand account, ranked by views in the window. When
    the brand owns 3 accounts (e.g. personal IG + brand IG + TikTok),
    this is the side-by-side comparison view.

    Configured-but-not-yet-scraped accounts appear with zero stats —
    that's deliberate so the user sees "this account needs a refresh"
    rather than the account silently disappearing.
    """
    from datetime import timedelta
    from .brand_accounts import get_brand_accounts
    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days > 0 else None
    )
    accounts = await get_brand_accounts(tenant_id)
    if not accounts:
        return []
    allowed = {(a.get("handle") or "").lower() for a in accounts if a.get("handle")}
    posts = await _load_posts(
        platform=platform, since=since, tenant_id=tenant_id,
        allowed_handles=allowed,
    )
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in posts:
        h = (p.get("handle") or "").replace("@", "").lower()
        pl = (p.get("platform") or "").lower()
        if not h:
            continue
        grouped[(pl, h)].append(p)
    rows: list[dict] = []
    # Iterate over the CONFIGURED accounts (not the grouped data) so
    # accounts with no posts yet still appear.
    for a in accounts:
        pl = (a.get("platform") or "").lower()
        h = (a.get("handle") or "").lower()
        if not h:
            continue
        if platform and platform != pl:
            continue
        bucket = grouped.get((pl, h), [])
        views = sum(int(p.get("views") or 0) for p in bucket)
        outliers = [float(p.get("outlier_score") or 0.0) for p in bucket]
        med = sorted(outliers)[len(outliers) // 2] if outliers else 0.0
        eng = sum(_engagement(p) for p in bucket)
        rows.append({
            "platform": pl,
            "handle": h,
            "name": a.get("name") or "",
            "posts": len(bucket),
            "views": views,
            "engagement": eng,
            "median_outlier": round(med, 2),
        })
    rows.sort(key=lambda r: r["views"], reverse=True)
    return rows


async def platform_performance(
    *, days: int = 30, tenant_id: UUID | None = None,
) -> dict:
    """Per-platform performance breakdown across all the brand's own posts.

    Lets a caller see which CHANNEL is winning ("IG 2.1% ER vs TikTok 5.3%")
    rather than which individual post. Sorted by post_count desc (stable).
    """
    posts = await list_posts(
        handle="", platform="", days=days, sort="recent",
        limit=5000, tenant_id=tenant_id,
    )
    by: dict[str, list] = defaultdict(list)
    for p in posts:
        by[str(p.get("platform") or "")].append(p)
    out: list[dict] = []
    for pf, bucket in by.items():
        if not pf:
            continue
        ers = [_engagement_rate(p) for p in bucket]
        avg_er = sum(ers) / len(ers) if ers else 0.0
        med_er = sorted(ers)[len(ers) // 2] if ers else 0.0
        best = max(bucket, key=_engagement_rate, default={})
        out.append({
            "platform": pf,
            "post_count": len(bucket),
            "total_views": sum(int(p.get("views") or 0) for p in bucket),
            "total_engagement": sum(_engagement(p) for p in bucket),
            "avg_engagement_rate": round(avg_er, 4),
            "median_engagement_rate": round(med_er, 4),
            "best_post_url": best.get("url") or "",
        })
    out.sort(key=lambda r: r["post_count"], reverse=True)
    return {"days": days, "platforms": out}


__all__ = [
    "list_tracked_handles", "handle_summary", "list_posts",
    "daily_timeline", "accounts_leaderboard", "platform_performance",
]
