"""Connected accounts — a unified view across Meta + PostProxy.

The user has profiles in two integrations:
  * Meta Graph (Pages + IG Business accounts) — via meta_access_token
    or meta_ads_access_token
  * PostProxy (any of 11 platforms) — via postproxy_api_key

This module merges them into one shape the Analytics UI can render
without caring which integration owns a given profile:

    {
      "provider": "meta" | "postproxy",
      "platform": "instagram" | "facebook" | "tiktok" | ...,
      "id": "<provider-scoped id>",
      "handle": "<display handle / username>",
      "name": "<display name>",
      "status": "active" | "inactive",
      "post_count": int,
      "avatar_url": str,
      "expires_at": iso8601 | null,
      "raw": {...}   // verbatim provider payload, for debugging
    }

When the same handle shows up in both providers (e.g. Facebook page
linked to both a Meta token AND PostProxy), we keep both entries —
the user picks which one to sync from. This is deliberate; deduping
hides whichever integration is actually working.
"""

from __future__ import annotations

from typing import Any


async def list_all_connections() -> dict:
    """Probe both integrations, return a flat list of every connected
    profile + a per-provider summary. Best-effort — if one integration
    is misconfigured the other still surfaces."""
    profiles: list[dict] = []
    providers: dict[str, Any] = {}

    # ── PostProxy
    try:
        from .postproxy import inspect as pp_inspect, PostProxyNotConfigured
        pp = await pp_inspect()
        providers["postproxy"] = {
            "configured": True,
            "ok": pp.get("ok", False),
            "error": pp.get("error"),
            "profile_count": len(pp.get("profiles") or []),
        }
        for p in pp.get("profiles", []):
            raw = p.get("raw") or {}
            profiles.append({
                "provider": "postproxy",
                "platform": (p.get("platform") or "").lower(),
                "id": p.get("id") or raw.get("id") or "",
                "handle": p.get("handle") or raw.get("name") or "",
                "name": raw.get("name") or p.get("handle") or "",
                "status": raw.get("status") or "active",
                "post_count": int(raw.get("post_count") or 0),
                "avatar_url": raw.get("avatar_url") or "",
                "expires_at": raw.get("expires_at"),
                "raw": raw,
            })
    except PostProxyNotConfigured:
        providers["postproxy"] = {"configured": False}
    except Exception as e:  # noqa: BLE001
        providers["postproxy"] = {"configured": True, "ok": False, "error": str(e)}

    # ── Meta (IG + FB Pages)
    try:
        from .meta_graph import inspect as meta_inspect, MetaNotConfigured
        meta = await meta_inspect()
        providers["meta"] = {
            "configured": True,
            "ok": meta.get("ok", False),
            "error": meta.get("error"),
            "scopes": meta.get("token", {}).get("scopes", []),
            "type": meta.get("token", {}).get("type"),
        }
        for page in meta.get("pages", []):
            # Each Page surfaces once as a Facebook entry. If it has
            # a linked Instagram Business account, that's a second
            # entry under instagram.
            profiles.append({
                "provider": "meta",
                "platform": "facebook",
                "id": page.get("id"),
                "handle": page.get("name") or "",
                "name": page.get("name") or "",
                "status": "active",
                "post_count": 0,  # Meta doesn't return this on /me/accounts; fetched on demand
                "avatar_url": "",
                "expires_at": None,
                "raw": page,
            })
            iba = page.get("instagram_business_account") or {}
            if iba.get("id"):
                profiles.append({
                    "provider": "meta",
                    "platform": "instagram",
                    "id": iba.get("id"),
                    "handle": iba.get("username") or "",
                    "name": iba.get("username") or "",
                    "status": "active",
                    "post_count": 0,
                    "avatar_url": "",
                    "expires_at": None,
                    "raw": {"parent_page_id": page.get("id"), **iba},
                })
    except MetaNotConfigured:
        providers["meta"] = {"configured": False}
    except Exception as e:  # noqa: BLE001
        providers["meta"] = {"configured": True, "ok": False, "error": str(e)}

    # ── Summary
    by_platform: dict[str, int] = {}
    for p in profiles:
        pl = p["platform"] or "unknown"
        by_platform[pl] = by_platform.get(pl, 0) + 1
    return {
        "profiles": profiles,
        "providers": providers,
        "by_platform": by_platform,
        "total": len(profiles),
    }


def _normalize_postproxy_posts(raw: dict, platform: str) -> list[dict]:
    """Flatten PostProxy's nested post shape into the normalized card
    shape the UI renders. Each PostProxy post can target multiple
    platforms; we pull the matching placement's permalink + cover."""
    out: list[dict] = []
    for p in (raw.get("data") or []):
        # Find the placement matching the requested platform (or the
        # first one if no platform filter).
        placements = p.get("platforms") or []
        match = None
        for pl in placements:
            if not platform or (pl.get("platform") or "").lower() == platform.lower():
                match = pl
                break
        match = match or (placements[0] if placements else {})
        params = match.get("params") or {}
        out.append({
            "id": p.get("id"),
            "caption": (p.get("body") or "")[:400],
            "title": params.get("title") or "",
            "permalink": match.get("permalink") or "",
            "thumbnail": params.get("cover_url") or params.get("thumbnail_url") or "",
            "platform": (match.get("platform") or platform or "").lower(),
            "status": match.get("status") or p.get("status") or "",
            "posted_at": p.get("scheduled_at") or p.get("created_at") or "",
            # PostProxy's list endpoint doesn't carry per-post metrics;
            # those need /api/posts/stats with explicit post_ids. The
            # UI can request them on demand.
            "views": None, "likes": None, "comments": None,
        })
    return out


def _normalize_ig_media(raw: dict) -> list[dict]:
    """Flatten Meta IG /media into the same normalized card shape."""
    out: list[dict] = []
    for m in (raw.get("data") or []):
        out.append({
            "id": m.get("id"),
            "caption": (m.get("caption") or "")[:400],
            "title": "",
            "permalink": m.get("permalink") or "",
            "thumbnail": m.get("thumbnail_url") or m.get("media_url") or "",
            "platform": "instagram",
            "status": "published",
            "posted_at": m.get("timestamp") or "",
            "views": None,
            "likes": m.get("like_count"),
            "comments": m.get("comments_count"),
        })
    return out


async def list_profile_posts(
    provider: str, profile_id: str, platform: str = "", limit: int = 20,
) -> dict:
    """Recent posts for one profile, NORMALIZED to a common card shape:
        { posts: [{id, caption, title, permalink, thumbnail, platform,
                   status, posted_at, views, likes, comments}], error? }
    Routes to the right backend based on `provider`."""
    if provider == "postproxy":
        from .postproxy import list_posts, PostProxyError
        try:
            raw = await list_posts(
                platforms=[platform] if platform else None,
                per_page=limit,
            )
            return {"posts": _normalize_postproxy_posts(raw, platform)}
        except PostProxyError as e:
            return {"error": str(e), "posts": []}
    if provider == "meta":
        # `profile_id` here is the IG Business account id.
        from .meta_graph import ig_recent_media, MetaApiError
        try:
            raw = await ig_recent_media(profile_id, limit=limit)
            return {"posts": _normalize_ig_media(raw)}
        except MetaApiError as e:
            return {"error": str(e), "posts": []}
    return {"error": f"unknown provider: {provider}", "posts": []}


__all__ = ["list_all_connections", "list_profile_posts"]
