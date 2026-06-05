"""Meta Graph API client — read-side.

Wraps the small slice of the Graph API we need to pull Instagram +
Facebook insights into JAMES OS analytics. Uses the long-lived
`meta_access_token` from the encrypted credential store; no OAuth
flow yet — that's the next layer.

The `inspect()` function is the first call the user should make
after pasting their token: it runs `/debug_token`, `/me`, and
`/me/accounts` to confirm the token is valid, list the scopes it
carries, and surface the IG Business accounts it can see. The UI
shows the result so the user knows what insights will be available
before any real fetch happens.

Honest scope:
  * Read-only. No publishing / posting.
  * Graph API version pinned to v23.0 (current stable as of 2026).
  * Errors from Meta come back with the verbatim error message —
    Meta's errors are the most actionable signal, don't wrap them.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings


GRAPH_VERSION = "v23.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


class MetaNotConfigured(RuntimeError):
    """No access token in settings — caller surfaces a clear 400."""


class MetaApiError(RuntimeError):
    """Meta's Graph API returned a non-2xx. .message carries Meta's
    verbatim error so the UI can show it without us re-interpreting."""


def _token(which: str = "content") -> str:
    """Get the right token for the job. `which`:
       * 'content' — IG/FB content + insights (meta_access_token)
       * 'ads'     — Marketing API / Ads Manager (meta_ads_access_token)

    Falls back EITHER WAY when one of the two tokens isn't set. This
    matters because a Meta System User token with a wide scope set
    (read_insights, pages_show_list, instagram_basic, ads_read, ...)
    can serve both sides — so a single-token setup works everywhere.
    """
    primary = (settings.meta_access_token or "").strip()
    ads = (settings.meta_ads_access_token or "").strip()
    if which == "ads":
        t = ads or primary
    else:
        t = primary or ads
    if not t:
        raise MetaNotConfigured(
            "Set meta_access_token (or meta_ads_access_token) in "
            "/settings — paste a long-lived Graph API token from your "
            "Meta Developer App."
        )
    return t


async def _get(path: str, params: dict[str, Any] | None = None, which: str = "content") -> dict:
    """One GET against Graph API, with the token tacked on."""
    q = {"access_token": _token(which), **(params or {})}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
        r = await c.get(f"{GRAPH}{path}", params=q)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or r.text[:200]
            raise MetaApiError(f"Graph {path} → HTTP {r.status_code}: {msg}")
        except ValueError:
            raise MetaApiError(f"Graph {path} → HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


# ── token + identity ────────────────────────────────────────────────


async def debug_token(which: str = "content") -> dict:
    """`/debug_token` — what scopes this token has, when it expires,
    which app it belongs to, which user it represents. The
    authoritative answer to "what can we actually do.

    `which` picks which configured token to inspect — content vs ads.
    """
    token = _token(which)
    return await _get(
        "/debug_token",
        params={"input_token": token},
        which=which,
    )


async def me() -> dict:
    """`/me` — the user/page/system-user this token represents."""
    return await _get("/me", params={"fields": "id,name,email"})


async def my_pages() -> dict:
    """`/me/accounts` — every Page this user (or system user) admins.
    For each Page we also pull the linked Instagram Business account
    id, which is the gateway to every IG insight."""
    return await _get(
        "/me/accounts",
        params={"fields": "id,name,access_token,instagram_business_account{id,username}"},
    )


async def _probe_token(which: str) -> dict:
    """Helper — debug_token + summary for one of the configured tokens.
    Returns {is_configured, is_valid, type, scopes, expires_at, error?}."""
    try:
        dt = await debug_token(which)
        data = dt.get("data", {})
        return {
            "is_configured": True,
            "is_valid": bool(data.get("is_valid", False)),
            "type": data.get("type", ""),
            "app_id": data.get("app_id", ""),
            "application": data.get("application", ""),
            "user_id": data.get("user_id", ""),
            "expires_at": data.get("expires_at", 0),
            "scopes": data.get("scopes", []),
        }
    except MetaNotConfigured:
        return {"is_configured": False, "is_valid": False, "scopes": []}
    except MetaApiError as e:
        return {
            "is_configured": True, "is_valid": False, "scopes": [],
            "error": str(e),
        }


async def inspect() -> dict:
    """All-in-one probe. Returns a structured 'here's what works'
    report the UI can render verbatim. Probes BOTH the content token
    (meta_access_token) and the ads token (meta_ads_access_token)
    when configured, so you can see what each one unlocks.
    """
    out: dict[str, Any] = {"ok": False}

    # Probe both. With the recent fallback, if only one is set the
    # content/ads functions still work — they share the token under
    # the hood. But here we report each slot honestly.
    primary_set = bool((settings.meta_access_token or "").strip())
    ads_set = bool((settings.meta_ads_access_token or "").strip())

    if primary_set:
        out["content_token"] = await _probe_token("content")
    else:
        out["content_token"] = {"is_configured": False, "is_valid": False, "scopes": []}

    if ads_set:
        out["ads_token"] = await _probe_token("ads")
    else:
        out["ads_token"] = {"is_configured": False, "is_valid": False, "scopes": []}

    # The effective token used for content-side calls — falls back
    # ads→content per _token(). Use this to decide if we can proceed.
    effective = out["content_token"] if out["content_token"]["is_configured"] \
        else out["ads_token"]

    if not effective["is_configured"]:
        out["error"] = (
            "No Meta token configured — paste meta_access_token or "
            "meta_ads_access_token in /settings."
        )
        return out

    # Keep `token` for backward compat with the older response shape.
    out["token"] = effective

    if not effective["is_valid"]:
        out["error"] = (
            effective.get("error")
            or "Token is invalid (Meta reports is_valid=false). "
               "Regenerate from the Graph API Explorer or your app's settings."
        )
        return out

    # Step 2 — /me
    try:
        out["user"] = await me()
    except MetaApiError as e:
        out["error"] = f"/me failed: {e}"
        return out

    # Step 3 — /me/accounts
    try:
        pages_resp = await my_pages()
        pages = pages_resp.get("data", [])
        out["pages"] = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "instagram_business_account": p.get("instagram_business_account") or {},
                "has_page_token": bool(p.get("access_token")),
            }
            for p in pages
        ]
    except MetaApiError as e:
        out["error"] = f"/me/accounts failed: {e}"
        return out

    # Build an "actionable" summary — covers BOTH tokens' scopes
    content_scopes = set(out["content_token"]["scopes"])
    ads_scopes = set(out["ads_token"]["scopes"]) if out["ads_token"]["is_configured"] else set()
    all_scopes = content_scopes | ads_scopes

    actionable: list[str] = []
    needed = {
        "instagram_basic": "Read IG profile + media",
        "instagram_manage_insights": "Read IG post + account insights",
        "pages_show_list": "List your Facebook Pages",
        "pages_read_engagement": "Read FB Page engagement",
        "ads_read": "Read Ads Manager campaigns + insights (via ads token)",
        "business_management": "Read Business Manager assets",
    }
    for scope, what in needed.items():
        if scope in all_scopes:
            actionable.append(f"✓ {what} (has `{scope}`)")
        else:
            actionable.append(f"✗ Missing `{scope}` — needed to: {what}")

    ig_count = sum(
        1 for p in out["pages"]
        if (p.get("instagram_business_account") or {}).get("id")
    )
    actionable.append(
        f"→ {len(out['pages'])} Facebook Page(s), {ig_count} with linked Instagram Business account"
    )
    if out["ads_token"]["is_configured"]:
        actionable.append(
            f"→ Ads token configured (type: {out['ads_token']['type'] or 'unknown'})"
        )

    out["actionable"] = actionable
    out["ok"] = True
    return out


# ── Instagram fetches ───────────────────────────────────────────────


async def ig_recent_media(ig_business_id: str, limit: int = 50) -> dict:
    """Recent media for an IG Business account. Each item has the
    metrics we'd want for analytics (like_count, comments_count).
    For full insights (impressions, reach, saved) call
    `ig_media_insights(media_id)` per post."""
    return await _get(
        f"/{ig_business_id}/media",
        params={
            "fields": "id,caption,media_type,media_url,permalink,thumbnail_url,"
                      "timestamp,like_count,comments_count",
            "limit": int(limit),
        },
    )


async def ig_media_insights(media_id: str) -> dict:
    """Per-post insights — impressions, reach, saved, engagement,
    plus per-format metrics (video_views for REELS / VIDEO,
    profile_visits for IMAGE). Requires `instagram_manage_insights`."""
    return await _get(
        f"/{media_id}/insights",
        params={"metric": "impressions,reach,saved,engagement,video_views"},
    )


async def ig_account_insights(
    ig_business_id: str, period: str = "day", days: int = 30,
) -> dict:
    """Account-level insights: follower_count, impressions, reach,
    profile_views. `period` is day / week / days_28."""
    from datetime import datetime, timedelta, timezone
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    return await _get(
        f"/{ig_business_id}/insights",
        params={
            "metric": "impressions,reach,follower_count,profile_views",
            "period": period,
            "since": since,
            "until": until,
        },
    )


__all__ = [
    "MetaNotConfigured", "MetaApiError",
    "debug_token", "me", "my_pages", "inspect",
    "ig_recent_media", "ig_media_insights", "ig_account_insights",
]
