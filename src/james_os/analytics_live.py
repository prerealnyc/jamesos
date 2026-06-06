"""Live analytics — top-line metrics aggregated across every CONNECTED
social account (Meta + PostProxy), for a Klipfolio/Sendible-style
dashboard.

This is the *connector-backed* counterpart to `analytics.py`. Where
`analytics.py` aggregates posts the brand has *scraped into the events
table* (Apify-sourced trend data scoped to brand-owned handles), this
module reads straight off the live integrations the user has connected
in /settings:

  * PostProxy  — `connections.list_all_connections()` already returns a
                 flat profile inventory with `post_count` per profile.
                 Follower counts, when PostProxy includes them, live in
                 the verbatim `raw` payload under one of several keys
                 (the API is inconsistent across the 11 platforms) — we
                 probe a known set defensively.
  * Meta Graph — IG Business accounts expose `follower_count` via
                 `ig_account_insights`. Facebook Pages don't surface
                 follower/fan counts on the unified connection shape, so
                 they contribute account+post structure but no followers
                 here.

Design rules (deliberate):
  * BEST-EFFORT. Every external field may be missing, None, or a string.
    We coerce to int and treat unreachable numbers as absent — never
    crash on one bad profile. A summary with partial data is the
    correct, honest result.
  * Followers that aren't reachable are OMITTED from the sum (not
    counted as 0 in a way that hides the gap) and surfaced via
    `followers_partial` + a per-platform `followers_known` flag so the
    UI can show "≥ N" or a tooltip instead of a confidently-wrong total.
  * NO module-level network calls. Everything network-touching is inside
    the async functions, and even there it's wrapped so a dead token on
    one provider never sinks the whole dashboard.

Honest scope of what's reachable with TODAY's connectors:
  * total_posts      — PostProxy `post_count` is real. Meta returns 0 on
                       the connection shape (not fetched per-account here
                       to keep the summary one cheap call set), so Meta
                       posts read as 0.
  * total_followers  — Reachable for IG Business accounts (Meta insights)
                       and for any PostProxy profile whose raw payload
                       carries a follower field. Facebook Pages + any
                       PostProxy profile without that field are omitted.
  * engagement       — Per-profile recent engagement is reachable in
                       `platform_breakdown` (best-effort: IG like/comment
                       counts via recent media; PostProxy needs explicit
                       post_ids for stats, so it's left null there). The
                       summary tile sums whatever the breakdown found.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import APIRouter


# ── coercion helpers ─────────────────────────────────────────────────


def _to_int(v: Any) -> int | None:
    """Coerce a maybe-number to int. Returns None for anything that
    isn't a clean number — the caller decides whether absent means 0
    (posts) or means 'omit from sum' (followers)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


# Keys PostProxy (and the platforms it proxies) have been seen to use for
# a follower/fan/subscriber count, in priority order. The API is not
# consistent across its 11 platforms, so we probe several. First hit on a
# clean integer wins.
_FOLLOWER_KEYS = (
    "followers_count", "follower_count", "followers",
    "fan_count", "fans", "subscribers", "subscriber_count",
    "audience", "audience_size", "followed_by", "followers_total",
)

# Some PostProxy payloads nest the latest snapshot under one of these.
_STATS_CONTAINERS = ("stats", "latest_stats", "metrics", "insights", "statistics")


def _extract_followers(raw: dict) -> int | None:
    """Best-effort follower count from a verbatim PostProxy `raw` payload.
    Looks at the top level first, then one level into common stats
    containers. Returns None when nothing usable is present — that's the
    signal to OMIT this profile from the follower sum rather than count
    it as zero."""
    if not isinstance(raw, dict):
        return None
    for k in _FOLLOWER_KEYS:
        n = _to_int(raw.get(k))
        if n is not None:
            return n
    for c in _STATS_CONTAINERS:
        sub = raw.get(c)
        if isinstance(sub, dict):
            for k in _FOLLOWER_KEYS:
                n = _to_int(sub.get(k))
                if n is not None:
                    return n
        # Some APIs return stats as a chronological list of snapshots;
        # the last element is the most recent.
        elif isinstance(sub, list) and sub and isinstance(sub[-1], dict):
            for k in _FOLLOWER_KEYS:
                n = _to_int(sub[-1].get(k))
                if n is not None:
                    return n
    return None


async def _ig_follower_count(ig_business_id: str) -> int | None:
    """Latest follower_count for one IG Business account via Meta
    insights. `follower_count` is a daily timeseries — we take the last
    non-null value. Fully guarded: any failure (bad token, missing
    scope, no data) returns None so the dashboard degrades, never
    crashes."""
    if not ig_business_id:
        return None
    try:
        from .meta_graph import ig_account_insights
        resp = await ig_account_insights(ig_business_id, period="day", days=30)
    except Exception:  # noqa: BLE001 — best-effort, swallow per-account
        return None
    latest: int | None = None
    for metric in (resp.get("data") or []):
        if metric.get("name") != "follower_count":
            continue
        for point in (metric.get("values") or []):
            n = _to_int(point.get("value"))
            if n is not None:
                latest = n  # values are chronological; keep the last
    return latest


async def _ig_recent_engagement(ig_business_id: str, limit: int = 25) -> int | None:
    """Sum of like_count + comments_count over the most recent IG media.
    Best-effort; None on any failure so a single bad account doesn't
    blank the whole table."""
    if not ig_business_id:
        return None
    try:
        from .meta_graph import ig_recent_media
        resp = await ig_recent_media(ig_business_id, limit=limit)
    except Exception:  # noqa: BLE001
        return None
    total = 0
    seen = False
    for m in (resp.get("data") or []):
        likes = _to_int(m.get("like_count"))
        comments = _to_int(m.get("comments_count"))
        if likes is not None:
            total += likes
            seen = True
        if comments is not None:
            total += comments
            seen = True
    return total if seen else None


# ── 1) dashboard summary (top-line tiles) ────────────────────────────


async def dashboard_summary(tenant_id: Any = None) -> dict:
    """Aggregate top-line metrics across ALL connected profiles for the
    metric tiles.

    `tenant_id` is accepted for interface symmetry with `analytics.py`
    and future per-tenant credential scoping; the underlying connectors
    read process-level (per-tenant) credentials today, so it's currently
    informational only.

    Returns::

        {
          "total_followers": int,         # sum where reachable
          "followers_partial": bool,      # True if any account's
                                          #   followers couldn't be read
          "followers_known_accounts": int,# how many accounts contributed
          "total_posts": int,             # sum of post_count
          "account_count": int,           # connected profiles
          "platform_count": int,          # distinct platforms
          "per_platform": [
             {"platform": "instagram",
              "accounts": 2, "posts": 140,
              "followers": 50321, "followers_known": true}
          ],
          "providers": {...},             # raw provider health passthrough
          "notes": ["..."],               # honest caveats for the UI
        }
    """
    try:
        from .connections import list_all_connections
        conn = await list_all_connections()
    except Exception as e:  # noqa: BLE001 — never crash the dashboard
        return {
            "total_followers": 0,
            "followers_partial": True,
            "followers_known_accounts": 0,
            "total_posts": 0,
            "account_count": 0,
            "platform_count": 0,
            "per_platform": [],
            "providers": {},
            "notes": [f"Could not load connections: {e}"],
        }

    profiles: list[dict] = conn.get("profiles") or []

    # Group per platform, accumulating posts + followers as we go.
    per_pf: dict[str, dict] = defaultdict(
        lambda: {
            "accounts": 0,
            "posts": 0,
            "followers": 0,
            "followers_known": False,  # flips true once any account in
                                       # this platform yields a real number
        }
    )

    total_posts = 0
    total_followers = 0
    followers_known_accounts = 0
    followers_partial = False
    notes: list[str] = []

    # Kick off the IG follower fetches concurrently — they're the only
    # network calls and there are usually only a handful of IG accounts.
    ig_ids = [
        p.get("id")
        for p in profiles
        if p.get("provider") == "meta"
        and (p.get("platform") or "").lower() == "instagram"
        and p.get("id")
    ]
    ig_followers: dict[str, int | None] = {}
    if ig_ids:
        results = await asyncio.gather(
            *[_ig_follower_count(i) for i in ig_ids],
            return_exceptions=True,
        )
        for i, r in zip(ig_ids, results, strict=True):
            ig_followers[i] = r if isinstance(r, int) else None

    for p in profiles:
        platform = (p.get("platform") or "unknown").lower()
        provider = p.get("provider") or ""
        slot = per_pf[platform]
        slot["accounts"] += 1

        # ── posts
        posts = _to_int(p.get("post_count")) or 0
        slot["posts"] += posts
        total_posts += posts

        # ── followers (best-effort, source depends on provider)
        followers: int | None = None
        if provider == "meta" and platform == "instagram":
            followers = ig_followers.get(p.get("id"))
        elif provider == "postproxy":
            followers = _extract_followers(p.get("raw") or {})
        # Facebook Pages (meta) + anything else: no reachable follower
        # count on this shape → leave None.

        if followers is not None:
            slot["followers"] += followers
            slot["followers_known"] = True
            total_followers += followers
            followers_known_accounts += 1
        else:
            followers_partial = True

    account_count = len(profiles)
    platform_count = len(per_pf)

    per_platform = [
        {
            "platform": pf,
            "accounts": v["accounts"],
            "posts": v["posts"],
            "followers": v["followers"],
            "followers_known": v["followers_known"],
        }
        for pf, v in sorted(per_pf.items(), key=lambda kv: -kv[1]["accounts"])
    ]

    # ── honest caveats
    if followers_partial:
        unknown = account_count - followers_known_accounts
        notes.append(
            f"Follower count unavailable for {unknown} of {account_count} "
            "account(s) — Facebook Pages and any PostProxy profile whose "
            "payload omits a follower field are excluded from the total."
        )
    if any(
        (p.get("provider") == "meta") for p in profiles
    ):
        notes.append(
            "Meta post counts read as 0 here — the unified connection "
            "shape doesn't include them; fetch per-account for exact totals."
        )

    return {
        "total_followers": total_followers,
        "followers_partial": followers_partial,
        "followers_known_accounts": followers_known_accounts,
        "total_posts": total_posts,
        "account_count": account_count,
        "platform_count": platform_count,
        "per_platform": per_platform,
        "providers": conn.get("providers") or {},
        "notes": notes,
    }


# ── 2) platform breakdown (per-profile table) ────────────────────────


async def platform_breakdown(tenant_id: Any = None) -> dict:
    """One row per connected profile, with whatever metrics are
    reachable — for a sortable table view under the tiles.

    Returns::

        {
          "rows": [
            {
              "provider": "meta" | "postproxy",
              "platform": "instagram",
              "id": "...",
              "handle": "@brand",
              "name": "Brand",
              "status": "active",
              "posts": 140,            # post_count (0 for Meta)
              "followers": 50321,      # int or null when unreachable
              "recent_engagement": 812,# int or null (IG only today)
              "metrics_partial": true, # any field on this row is null
            }
          ],
          "providers": {...},
          "notes": ["..."],
        }
    """
    try:
        from .connections import list_all_connections
        conn = await list_all_connections()
    except Exception as e:  # noqa: BLE001
        return {"rows": [], "providers": {}, "notes": [f"Could not load connections: {e}"]}

    profiles: list[dict] = conn.get("profiles") or []
    notes: list[str] = []

    # Concurrent IG enrichments (followers + recent engagement) keyed by id.
    ig_ids = [
        p.get("id")
        for p in profiles
        if p.get("provider") == "meta"
        and (p.get("platform") or "").lower() == "instagram"
        and p.get("id")
    ]
    ig_followers: dict[str, int | None] = {}
    ig_engagement: dict[str, int | None] = {}
    if ig_ids:
        f_res, e_res = await asyncio.gather(
            asyncio.gather(*[_ig_follower_count(i) for i in ig_ids], return_exceptions=True),
            asyncio.gather(*[_ig_recent_engagement(i) for i in ig_ids], return_exceptions=True),
        )
        for i, r in zip(ig_ids, f_res, strict=True):
            ig_followers[i] = r if isinstance(r, int) else None
        for i, r in zip(ig_ids, e_res, strict=True):
            ig_engagement[i] = r if isinstance(r, int) else None

    rows: list[dict] = []
    any_partial = False
    for p in profiles:
        provider = p.get("provider") or ""
        platform = (p.get("platform") or "unknown").lower()
        pid = p.get("id") or ""

        posts = _to_int(p.get("post_count")) or 0

        followers: int | None = None
        recent_engagement: int | None = None
        if provider == "meta" and platform == "instagram":
            followers = ig_followers.get(pid)
            recent_engagement = ig_engagement.get(pid)
        elif provider == "postproxy":
            followers = _extract_followers(p.get("raw") or {})
            # PostProxy per-post metrics need explicit post_ids via
            # /api/posts/stats — not fetched here to keep the table cheap.
            recent_engagement = None

        partial = followers is None or recent_engagement is None
        any_partial = any_partial or partial

        rows.append({
            "provider": provider,
            "platform": platform,
            "id": pid,
            "handle": p.get("handle") or "",
            "name": p.get("name") or p.get("handle") or "",
            "status": p.get("status") or "active",
            "posts": posts,
            "followers": followers,
            "recent_engagement": recent_engagement,
            "metrics_partial": partial,
        })

    # Stable, useful ordering: followers desc (None last), then posts.
    rows.sort(key=lambda r: (-(r["followers"] or 0), -(r["posts"] or 0)))

    if any_partial:
        notes.append(
            "Some metrics are null: recent engagement is reachable for "
            "Instagram only (Meta media insights); PostProxy per-post "
            "metrics require explicit post_ids and are fetched on demand. "
            "Follower counts depend on what each connector exposes."
        )

    return {"rows": rows, "providers": conn.get("providers") or {}, "notes": notes}


# ── FastAPI router ───────────────────────────────────────────────────
# Mounted in main.py via:  app.include_router(analytics_live_router)

router = APIRouter(prefix="/analytics/live", tags=["analytics-live"])


@router.get("/summary")
async def analytics_live_summary() -> dict:
    """Top-line metric tiles aggregated across every connected social
    account (Meta + PostProxy). See `dashboard_summary` for the shape."""
    return await dashboard_summary()


@router.get("/breakdown")
async def analytics_live_breakdown() -> dict:
    """Per-connected-profile rows for the breakdown table. See
    `platform_breakdown` for the shape."""
    return await platform_breakdown()


__all__ = [
    "dashboard_summary",
    "platform_breakdown",
    "router",
]
