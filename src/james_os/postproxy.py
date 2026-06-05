"""PostProxy client — unified social-API SaaS (postproxy.dev).

PostProxy abstracts the OAuth + API surface for 11 platforms (X/Twitter,
Instagram, LinkedIn, TikTok, YouTube, Facebook, Threads, Pinterest,
Bluesky, Telegram, Google Business). We pay them, they handle every
platform's auth, we get a single Bearer-token API.

For X/Twitter specifically this is the cheapest path — X's own API
costs $100+/month for the Basic tier; PostProxy is bundled.

This module wraps the read-side endpoints we care about for analytics:
  * /api/profiles          — connected accounts
  * /api/profiles/:id/stats — account-level timeseries
  * /api/posts             — recent posts (filterable by platform)
  * /api/posts/stats       — per-post metrics snapshots

Auth: `Authorization: Bearer <postproxy_api_key>` (from the encrypted
credential store). Base URL: https://api.postproxy.dev.

Honest scope:
  * Read-only here. Posting via /api/posts POST is a separate concern
    (the publish path, not the analytics path).
  * No caching layer — fresh fetch every call. PostProxy's free/paid
    tiers handle rate limiting; we just propagate their HTTP errors
    upstream so the user sees the verbatim message.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings


BASE = "https://api.postproxy.dev"


class PostProxyNotConfigured(RuntimeError):
    """No API key set — caller turns this into a clean 400."""


class PostProxyError(RuntimeError):
    """PostProxy returned a non-2xx. .args[0] is their verbatim error."""


def _token() -> str:
    t = (settings.postproxy_api_key or "").strip()
    if not t:
        raise PostProxyNotConfigured(
            "Set postproxy_api_key in /settings — get it from your "
            "PostProxy dashboard at https://app.postproxy.dev."
        )
    return t


async def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """One GET against PostProxy. Returns the parsed JSON body. Raises
    PostProxyError with the verbatim error message on non-2xx."""
    headers = {"Authorization": f"Bearer {_token()}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
        r = await c.get(f"{BASE}{path}", headers=headers, params=params or {})
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("message") or body.get("error") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise PostProxyError(f"PostProxy {path} → HTTP {r.status_code}: {msg}")
    try:
        return r.json()
    except ValueError:
        return {}


# ── profiles (connected accounts) ───────────────────────────────────


async def list_profiles(profile_group_id: str = "") -> dict:
    """Every social account connected to this PostProxy workspace.
    Each entry: id, platform (twitter / instagram / linkedin / ...),
    handle / username, latest stats snapshot."""
    params = {"profile_group_id": profile_group_id} if profile_group_id else {}
    return await _get("/api/profiles", params=params)


async def get_profile(profile_id: str) -> dict:
    """One profile with placements + latest stats per placement."""
    return await _get(f"/api/profiles/{profile_id}")


async def profile_stats(
    profile_id: str, placement_id: str,
    since_iso: str = "", until_iso: str = "",
) -> dict:
    """Account-level timeseries. PostProxy stores periodic snapshots
    of follower count, post count, engagement aggregates — this
    endpoint returns them ordered chronologically. Place-specific
    (each platform like X / IG / etc is a separate 'placement')."""
    params: dict[str, Any] = {"placement_id": placement_id}
    if since_iso:
        params["from"] = since_iso
    if until_iso:
        params["to"] = until_iso
    return await _get(f"/api/profiles/{profile_id}/stats", params=params)


# ── posts (cross-platform) ──────────────────────────────────────────


async def list_posts(
    platforms: list[str] | None = None,
    page: int = 0, per_page: int = 30,
    status: str = "published",
) -> dict:
    """Recent posts. `platforms` is a list filter — e.g.
    `['twitter']` for X-only, or omit for all platforms. `status`
    defaults to 'published' so we only see what actually shipped."""
    params: dict[str, Any] = {
        "page": int(page),
        "per_page": int(per_page),
        "status": status,
    }
    if platforms:
        # PostProxy uses repeated query params: platforms[]=x&platforms[]=y
        params["platforms"] = list(platforms)
    return await _get("/api/posts", params=params)


async def post_stats(
    post_ids: list[str] | None = None,
    profile_ids: list[str] | None = None,
    since_iso: str = "", until_iso: str = "",
) -> dict:
    """Per-post metrics snapshots. Either filter by specific post_ids
    (up to 50) or by profile to get every post for that profile."""
    params: dict[str, Any] = {}
    if post_ids:
        params["post_ids"] = ",".join(post_ids[:50])
    if profile_ids:
        params["profiles"] = ",".join(profile_ids)
    if since_iso:
        params["from"] = since_iso
    if until_iso:
        params["to"] = until_iso
    return await _get("/api/posts/stats", params=params)


# ── one-shot inspect ────────────────────────────────────────────────


async def inspect() -> dict:
    """Probe — confirms the key works AND lists every connected
    account so the user can see what platforms / handles are
    reachable through their PostProxy workspace.

    Returns:
      {
        ok: true,
        profiles: [{ id, platform, handle, placement_count }, ...],
        by_platform: { twitter: 1, instagram: 2, ... },
        actionable: [ "Can fetch 47 X posts via PostProxy", ... ]
      }
    """
    try:
        resp = await list_profiles()
    except PostProxyNotConfigured:
        raise
    except PostProxyError as e:
        return {"ok": False, "error": str(e)}

    # PostProxy's response shape: {"data": [...]} most likely.
    rows = resp.get("data") if isinstance(resp, dict) else resp
    if not isinstance(rows, list):
        rows = []

    profiles = []
    by_platform: dict[str, int] = {}
    for p in rows:
        if not isinstance(p, dict):
            continue
        # PostProxy may surface platform on the profile or via
        # placements; cover both shapes.
        platform = (
            p.get("platform")
            or p.get("network")
            or (p.get("placements", [{}])[0] if p.get("placements") else {}).get("platform", "")
        )
        platform = (platform or "").lower()
        profiles.append({
            "id": p.get("id"),
            "platform": platform,
            "handle": p.get("handle") or p.get("username") or p.get("name", ""),
            "placement_count": len(p.get("placements") or []),
            "raw": p,
        })
        if platform:
            by_platform[platform] = by_platform.get(platform, 0) + 1

    actionable: list[str] = []
    for pl, n in sorted(by_platform.items(), key=lambda kv: -kv[1]):
        actionable.append(f"✓ {n} {pl} profile{'s' if n > 1 else ''} connected")
    if not profiles:
        actionable.append(
            "⚠ No profiles connected yet — log into app.postproxy.dev and "
            "connect at least one social account."
        )

    return {
        "ok": True,
        "profiles": profiles,
        "by_platform": by_platform,
        "actionable": actionable,
    }


__all__ = [
    "PostProxyNotConfigured", "PostProxyError",
    "list_profiles", "get_profile", "profile_stats",
    "list_posts", "post_stats", "inspect",
]
