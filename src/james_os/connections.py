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


async def list_profile_posts(provider: str, profile_id: str, limit: int = 20) -> dict:
    """Recent posts for one profile. Routes to the right backend
    based on `provider`."""
    if provider == "postproxy":
        from .postproxy import list_posts, PostProxyError
        # PostProxy doesn't filter posts by profile_id directly via
        # list_posts; use post_stats with profile_ids instead.
        from .postproxy import post_stats
        try:
            return await post_stats(profile_ids=[profile_id])
        except PostProxyError as e:
            return {"error": str(e)}
    if provider == "meta":
        # `profile_id` here is the IG Business account id. For Pages
        # we'd hit a different endpoint — wire that later.
        from .meta_graph import ig_recent_media, MetaApiError
        try:
            return await ig_recent_media(profile_id, limit=limit)
        except MetaApiError as e:
            return {"error": str(e)}
    return {"error": f"unknown provider: {provider}"}


__all__ = ["list_all_connections", "list_profile_posts"]
