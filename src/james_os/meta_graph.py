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


def _token() -> str:
    t = (settings.meta_access_token or "").strip()
    if not t:
        raise MetaNotConfigured(
            "Set meta_access_token in /settings — paste a long-lived "
            "Graph API token from your Meta Developer App."
        )
    return t


async def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """One GET against Graph API, with the token tacked on."""
    q = {"access_token": _token(), **(params or {})}
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


async def debug_token() -> dict:
    """`/debug_token` — what scopes this token has, when it expires,
    which app it belongs to, which user it represents. The
    authoritative answer to "what can we actually do."""
    token = _token()
    return await _get(
        "/debug_token",
        params={"input_token": token},
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


async def inspect() -> dict:
    """All-in-one probe. Returns a structured 'here's what works'
    report the UI can render verbatim:

        {
          ok: true,
          token: { app_id, type, expires_at, scopes: [...], is_valid },
          user: { id, name },
          pages: [{ id, name, instagram_business_account: { id, username } } ...],
          actionable: [ "Can fetch IG media for @x", "Missing scope: ..." ],
        }

    Any error surfaces with the verbatim Meta message — usually the
    fastest path to fixing it (expired token, missing scope, etc).
    """
    out: dict[str, Any] = {"ok": False}

    # Step 1 — debug_token
    try:
        dt = await debug_token()
        data = dt.get("data", {})
        out["token"] = {
            "is_valid": bool(data.get("is_valid", False)),
            "type": data.get("type", ""),
            "app_id": data.get("app_id", ""),
            "application": data.get("application", ""),
            "user_id": data.get("user_id", ""),
            "expires_at": data.get("expires_at", 0),
            "data_access_expires_at": data.get("data_access_expires_at", 0),
            "scopes": data.get("scopes", []),
        }
        if not out["token"]["is_valid"]:
            out["error"] = "Token is invalid (Meta reports is_valid=false). " \
                "Regenerate from the Graph API Explorer or your app's settings."
            return out
    except MetaApiError as e:
        out["error"] = f"debug_token failed: {e}"
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

    # Build an "actionable" summary
    scopes = set(out["token"]["scopes"])
    actionable: list[str] = []
    needed = {
        "instagram_basic": "Read IG profile + media",
        "instagram_manage_insights": "Read IG post + account insights",
        "pages_show_list": "List your Facebook Pages",
        "pages_read_engagement": "Read FB Page engagement",
    }
    for scope, what in needed.items():
        if scope in scopes:
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
