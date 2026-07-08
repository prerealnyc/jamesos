"""Live vendor implementations of the D8 provider interfaces.

Vendor routing per docs/build-decisions.md D8/D9. This module is the ONLY
place vendor SDKs/HTTP endpoints are touched; agents see Protocols from
providers.base. Missing keys fail at call time, never import time.
Comments marked VERIFY-ON-PILOT flag field/endpoint names to confirm against
the live vendor account before go-live.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from anthropic import AsyncAnthropic

from .base import (
    AggregatorGroup,
    ChannelOverview,
    DeepResearchResult,
    NewsItem,
    PageContent,
    PlaceInfo,
    Providers,
    PublishResult,
    SearchResult,
    SocialPost,
    SocialProfile,
    TranscriptResult,
    VideoRef,
    WikiPage,
)
from ...config import Settings, settings as _settings
from .. import runs


# ------------------------------------------------------------------ helpers


def _require_key(value: str, vendor: str) -> str:
    if not value:
        raise RuntimeError(f"{vendor} key not configured")
    return value


async def _http(
    vendor: str,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json_body: dict | None = None,
    secret: str = "",
    ok_404: bool = False,
) -> httpx.Response:
    """30s timeout, one retry on 5xx/timeout, 4xx raises vendor+status (key
    redacted). ok_404=True returns the 404 response instead of raising — for
    endpoints where absence is a finding (Wikipedia lookup), not a failure."""
    last_error = ""
    for attempt in (0, 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=json_body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = type(exc).__name__
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            raise RuntimeError(f"{vendor} request failed: {last_error}") from exc
        if resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}"
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            raise RuntimeError(f"{vendor} server error: HTTP {resp.status_code}")
        if resp.status_code == 404 and ok_404:
            return resp
        if resp.status_code >= 400:
            body = resp.text[:200]
            if secret:
                body = body.replace(secret, "***")
            raise RuntimeError(f"{vendor} request failed: HTTP {resp.status_code} — {body}")
        return resp
    raise RuntimeError(f"{vendor} request failed: {last_error}")


def _first(d: dict, *keys: str, default: object = None) -> object:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


# ------------------------------------------------------------------- search


class SerperSearch:
    """SearchProvider — Serper.dev (D8; native Anthropic web search forbidden)."""

    _URL = "https://google.serper.dev/search"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        key = _require_key(self._settings.serper_api_key, "serper")
        resp = await _http(
            "serper", "POST", self._URL,
            headers={"X-API-KEY": key},
            json_body={"q": query, "num": num},
            secret=key,
        )
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=str(item.get("snippet", "")),
                source="serper",
            )
            for item in resp.json().get("organic", [])[:num]
        ]


# ------------------------------------------------------------------- scrape


class FirecrawlScrape:
    """ScrapeProvider — Firecrawl v1, markdown format (D8)."""

    _URL = "https://api.firecrawl.dev/v1/scrape"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def scrape(self, url: str) -> PageContent:
        key = _require_key(self._settings.firecrawl_api_key, "firecrawl")
        resp = await _http(
            "firecrawl", "POST", self._URL,
            headers={"Authorization": f"Bearer {key}"},
            json_body={"url": url, "formats": ["markdown"]},
            secret=key,
        )
        payload = resp.json()
        if payload.get("success") is False:
            raise RuntimeError(f"firecrawl scrape failed: {str(payload.get('error', 'unknown'))[:200]}")
        data = payload.get("data") or {}
        meta = data.get("metadata") or {}
        return PageContent(
            url=str(meta.get("sourceURL") or url),
            title=str(meta.get("title", "")),
            text=str(data.get("markdown", "")),
            meta=meta,
        )


# --------------------------------------------------------------------- news


class GNewsProvider:
    """NewsProvider — gnews.io v4; days= maps to the 'from' param (D8)."""

    _URL = "https://gnews.io/api/v4/search"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def search(self, query: str, days: int = 30) -> list[NewsItem]:
        key = _require_key(self._settings.gnews_api_key, "gnews")
        frm = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await _http(
            "gnews", "GET", self._URL,
            params={"q": query, "from": frm, "lang": "en", "max": 10, "apikey": key},
            secret=key,
        )
        return [
            NewsItem(
                title=str(a.get("title", "")),
                url=str(a.get("url", "")),
                published_at=str(a.get("publishedAt", "")),
                source=str((a.get("source") or {}).get("name", "")),
                snippet=str(a.get("description", "")),
            )
            for a in resp.json().get("articles", [])
        ]


class SerperNewsProvider:
    """NewsProvider fallback riding the Serper key — google.serper.dev/news.
    Used when GNEWS_API_KEY is absent so one key covers search + news."""

    _URL = "https://google.serper.dev/news"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def search(self, query: str, days: int = 30) -> list[NewsItem]:
        key = _require_key(self._settings.serper_api_key, "serper")
        resp = await _http(
            "serper-news", "POST", self._URL,
            headers={"X-API-KEY": key},
            json_body={"q": query, "num": 10},
            secret=key,
        )
        return [
            NewsItem(
                title=str(a.get("title", "")),
                url=str(a.get("link", "")),
                published_at=str(a.get("date", "")),
                source=str(a.get("source", "")),
                snippet=str(a.get("snippet", "")),
            )
            for a in resp.json().get("news", [])
        ]


class PlainScrape:
    """Keyless ScrapeProvider fallback (D8): plain fetch + tag-strip.
    Weaker than Firecrawl (no JS rendering) but keeps citations real."""

    async def scrape(self, url: str) -> PageContent:
        resp = await _http("plain-scrape", "GET", url, headers={"User-Agent": "Mozilla/5.0 (BrandManager)"})
        html = resp.text
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return PageContent(
            url=url,
            title=title_m.group(1).strip() if title_m else "",
            text=text[:20000],
            meta={"scraper": "plain"},
        )


# ------------------------------------------------------------------ ayrshare


_AYR_BASE = "https://api.ayrshare.com/api"
# VERIFY-ON-PILOT: metric field names in /api/history items vary by platform.
_AYR_METRIC_KEYS = (
    "likeCount", "commentCount", "commentsCount", "shareCount",
    "viewCount", "impressions", "engagementCount",
)


class AyrshareConnector:
    """SocialConnector — Ayrshare (D8). Bearer = main API key; tenant isolation
    via Profile-Key header (one Ayrshare profile per brand)."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    def _headers(self, profile_key: str | None = None) -> dict:
        key = _require_key(self._settings.ayrshare_api_key, "ayrshare")
        headers = {"Authorization": f"Bearer {key}"}
        if profile_key:
            headers["Profile-Key"] = profile_key
        return headers

    async def create_profile(self, brand_name: str) -> str:
        resp = await _http(
            "ayrshare", "POST", f"{_AYR_BASE}/profiles",
            headers=self._headers(),
            json_body={"title": brand_name},
            secret=self._settings.ayrshare_api_key,
        )
        # VERIFY-ON-PILOT: response field is 'profileKey' per current docs.
        profile_key = resp.json().get("profileKey", "")
        if not profile_key:
            raise RuntimeError("ayrshare create_profile: no profileKey in response")
        return str(profile_key)

    async def list_groups(self) -> list[AggregatorGroup]:
        # Ayrshare's group listing differs from PostProxy's; the picker is a
        # PostProxy-plan accommodation, so return empty (each brand mints its
        # own Ayrshare profile). VERIFY-ON-PILOT if group reuse is ever needed.
        return []

    async def connect_url(self, profile_key: str, platform: str = "instagram") -> str:
        # platform ignored: Ayrshare's JWT connect page covers all networks.
        # VERIFY-ON-PILOT: white-label SSO accounts may also require 'domain' and
        # 'privateKey' in this body per current docs; add to Settings if so.
        resp = await _http(
            "ayrshare", "POST", f"{_AYR_BASE}/profiles/generateJWT",
            headers=self._headers(),
            json_body={"profileKey": profile_key},
            secret=self._settings.ayrshare_api_key,
        )
        url = resp.json().get("url", "")
        if not url:
            raise RuntimeError("ayrshare generateJWT: no url in response")
        return str(url)

    async def list_accounts(self, profile_key: str) -> list[SocialProfile]:
        resp = await _http(
            "ayrshare", "GET", f"{_AYR_BASE}/user",
            headers=self._headers(profile_key),
            secret=self._settings.ayrshare_api_key,
        )
        data = resp.json()
        # VERIFY-ON-PILOT: 'displayNames' item shape (platform/username/displayName)
        # against the live account; 'activeSocialAccounts' is the bare fallback.
        accounts = [
            SocialProfile(
                platform=str(item.get("platform", "")),
                handle=str(item.get("username") or item.get("displayName") or ""),
                display_name=str(item.get("displayName") or ""),
                meta=item,
            )
            for item in data.get("displayNames") or []
            if isinstance(item, dict)
        ]
        if not accounts:
            accounts = [SocialProfile(platform=str(p), handle="") for p in data.get("activeSocialAccounts") or []]
        return accounts

    async def account_analytics(self, profile_key: str, platform: str) -> dict:
        resp = await _http(
            "ayrshare", "POST", f"{_AYR_BASE}/analytics/social",
            headers=self._headers(profile_key),
            json_body={"platforms": [platform]},
            secret=self._settings.ayrshare_api_key,
        )
        data = resp.json()
        block = data.get(platform)
        return block if isinstance(block, dict) else data

    async def post_history(self, profile_key: str, platform: str, months: int = 12) -> list[SocialPost]:
        months = max(1, min(months, 12))  # D9: Ayrshare lookback ≈ 12 months
        # /api/history/{platform} = the account's platform-native feed (what the
        # Auditor needs); bare /api/history returns only Ayrshare-sent posts.
        # VERIFY-ON-PILOT: 'lastDays'/'limit' param support on this subpath.
        resp = await _http(
            "ayrshare", "GET", f"{_AYR_BASE}/history/{platform}",
            headers=self._headers(profile_key),
            params={"lastDays": months * 30, "limit": 100},
            secret=self._settings.ayrshare_api_key,
        )
        data = resp.json()
        items = data.get("posts") or data.get("history") or (data if isinstance(data, list) else [])
        posts: list[SocialPost] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            posts.append(
                SocialPost(
                    platform=platform,
                    post_id=str(_first(item, "id", "postId", "sourceId", default="")),
                    url=str(_first(item, "postUrl", "url", "permalink", default="")),
                    text=str(_first(item, "post", "caption", "description", default="")),
                    published_at=str(_first(item, "created", "createdAt", "postDate", "publishedAt", default="")),
                    metrics={k: item[k] for k in _AYR_METRIC_KEYS if k in item},
                )
            )
        return posts

    async def publish(self, profile_key: str, platforms: list[str], text: str, media_urls: list[str]) -> dict:
        body: dict = {"post": text, "platforms": platforms}
        if media_urls:
            body["mediaUrls"] = media_urls
        resp = await _http(
            "ayrshare", "POST", f"{_AYR_BASE}/post",
            headers=self._headers(profile_key),
            json_body=body,
            secret=self._settings.ayrshare_api_key,
        )
        return resp.json()


# ----------------------------------------------------------------- postproxy


_PP_BASE = "https://api.postproxy.dev/api"
# PostProxy platform ids (reference/profiles): 'twitter', never 'x'.
_PP_PLATFORM_ALIASES = {"x": "twitter"}
# Account-level stats keys come straight from each platform's API (docs:
# 'Stats fields by network' — NOT normalized): instagram/twitter/telegram
# 'followers_count', tiktok/pinterest 'follower_count', linkedin
# 'followerCount', bluesky 'followersCount', youtube 'subscriberCount',
# facebook 'fan_count'/'followers_count'.
_PP_FOLLOWER_KEYS = (
    "followers_count", "follower_count", "followerCount", "followersCount",
    "subscriberCount", "fan_count",
)
# Per-post stats keys (docs: 'Stats fields by platform') — tolerant union.
_PP_POST_METRIC_KEYS = (
    "impressions", "likes", "comments", "shares", "saved", "retweets",
    "replies", "reposts", "quotes", "clicks", "outbound_clicks",
    "profile_visits", "follows",
)
# Placement networks: account stats live per-placement; the profile detail's
# summary_stats rolls them up (docs: reference/profiles#get-profile).
_PP_PLACEMENT_NETWORKS = ("facebook", "linkedin", "telegram", "google_business")


def _pp_platform(platform: str) -> str:
    p = platform.lower()
    return _PP_PLATFORM_ALIASES.get(p, p)


def _pp_post_in_window(item: dict, wanted: str, cutoff: datetime) -> bool:
    """Whether a /api/posts item's publish time falls inside the lookback
    window (unparseable timestamps count as in-window — never early-stop on
    bad data)."""
    block = next(
        (
            b for b in item.get("platforms") or []
            if isinstance(b, dict) and str(b.get("platform", "")).lower() == wanted
        ),
        {},
    )
    dt = _parse_iso(str(block.get("attempted_at") or item.get("created_at") or ""))
    return dt is None or dt >= cutoff


class PostProxyConnector:
    """SocialConnector — PostProxy (D8 primary; per-post-volume pricing).
    Bearer = API key; tenant isolation via profile groups (one group per
    brand — the group id is what the platform stores as
    aggregator_profile_key). Endpoints verified against postproxy.dev
    reference docs (profile-groups / profiles / posts), July 2026."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    def _headers(self) -> dict:
        key = _require_key(self._settings.postproxy_api_key, "postproxy")
        return {"Authorization": f"Bearer {key}"}

    async def _list_groups(self) -> list[dict]:
        resp = await _http(
            "postproxy", "GET", f"{_PP_BASE}/profile_groups",
            headers=self._headers(),
            secret=self._settings.postproxy_api_key,
        )
        payload = resp.json()
        rows = payload if isinstance(payload, list) else (payload.get("data") or [])
        return [g for g in rows if isinstance(g, dict)]

    async def create_profile(self, brand_name: str) -> str:
        try:
            resp = await _http(
                "postproxy", "POST", f"{_PP_BASE}/profile_groups",
                headers=self._headers(),
                json_body={"profile_group": {"name": brand_name}},
                secret=self._settings.postproxy_api_key,
            )
        except RuntimeError as exc:
            # Plan group-limit reached (free plan caps at 2): reuse an existing
            # group instead of failing. Prefer a same-named group, else the
            # first available. On a paid plan the create succeeds and each
            # brand keeps its own group.
            if "limit" not in str(exc).lower():
                raise
            groups = await self._list_groups()
            if not groups:
                raise RuntimeError(
                    "postproxy: profile group limit reached and no existing group to reuse — "
                    "upgrade the PostProxy plan or free a group"
                ) from exc
            match = next((g for g in groups if str(g.get("name", "")) == brand_name), None)
            chosen = match or groups[0]
            return str(chosen.get("id", ""))
        group_id = resp.json().get("id", "")
        if not group_id:
            raise RuntimeError("postproxy create_profile: no id in response")
        return str(group_id)

    async def connect_url(self, profile_key: str, platform: str = "instagram") -> str:
        # PostProxy's connect-link flow is per-platform (initialize_connection
        # requires 'platform'); the SocialConnector Protocol is platform-less
        # (Ayrshare's page covers all networks), so default to instagram —
        # the first onboarding conversion step (D9). Callers that know the
        # platform can pass it explicitly.
        # bluesky/telegram need credential payloads instead of a redirect and
        # are not wired here (VERIFY-ON-PILOT).
        resp = await _http(
            "postproxy", "POST",
            f"{_PP_BASE}/profile_groups/{profile_key}/initialize_connection",
            headers=self._headers(),
            json_body={
                "platform": _pp_platform(platform),
                "redirect_url": self._settings.postproxy_redirect_url,
            },
            secret=self._settings.postproxy_api_key,
        )
        url = resp.json().get("url", "")
        if not url:
            raise RuntimeError("postproxy initialize_connection: no url in response")
        return str(url)

    async def _profiles(self, profile_key: str) -> list[dict]:
        resp = await _http(
            "postproxy", "GET", f"{_PP_BASE}/profiles",
            headers=self._headers(),
            params={"profile_group_id": profile_key},
            secret=self._settings.postproxy_api_key,
        )
        return [item for item in resp.json().get("data") or [] if isinstance(item, dict)]

    async def list_accounts(self, profile_key: str) -> list[SocialProfile]:
        return [
            SocialProfile(
                platform=str(item.get("platform", "")),
                handle=str(item.get("name", "")).lstrip("@"),
                display_name=str(item.get("name", "")),
                meta=item,
            )
            for item in await self._profiles(profile_key)
        ]

    async def list_groups(self) -> list[AggregatorGroup]:
        out: list[AggregatorGroup] = []
        for g in await self._list_groups():
            gid = str(g.get("id", ""))
            if not gid:
                continue
            try:
                accts = await self.list_accounts(gid)
            except Exception:  # a group we can't read still shows (0 accounts)
                accts = []
            out.append(AggregatorGroup(group_id=gid, name=str(g.get("name", "")), accounts=accts))
        return out

    async def _profile_for(self, profile_key: str, platform: str) -> dict:
        wanted = _pp_platform(platform)
        for item in await self._profiles(profile_key):
            if str(item.get("platform", "")).lower() == wanted:
                return item
        raise RuntimeError(f"postproxy: no connected {platform} profile in group {profile_key}")

    async def account_analytics(self, profile_key: str, platform: str) -> dict:
        prof = await self._profile_for(profile_key, platform)
        resp = await _http(
            "postproxy", "GET", f"{_PP_BASE}/profiles/{prof.get('id', '')}",
            headers=self._headers(),
            params={"profile_group_id": profile_key},
            secret=self._settings.postproxy_api_key,
        )
        data = resp.json()
        # Snapshots poll ~every 23h; a freshly connected account has none yet.
        # Raise (not empty dict) so the Auditor degrades to its public
        # fallback instead of writing an audited-but-empty baseline.
        # VERIFY-ON-PILOT: confirm snapshot latency on the live account.
        summary = data.get("summary_stats") or {}
        latest = [e for e in data.get("latest_stats") or [] if isinstance(e, dict)]
        if _pp_platform(platform) in _PP_PLACEMENT_NETWORKS and isinstance(summary.get("stats"), dict):
            stats = dict(summary["stats"])
        elif latest and isinstance(latest[0].get("stats"), dict):
            stats = dict(latest[0]["stats"])
        else:
            raise NotImplementedError(
                f"postproxy: account analytics unavailable for {platform} "
                "(no stats snapshot yet — polled ~every 23h) — VERIFY-ON-PILOT"
            )
        followers = _first(stats, *_PP_FOLLOWER_KEYS)
        if followers is not None and "followers" not in stats:
            stats["followers"] = int(followers)  # normalize for the Auditor
        return stats

    async def post_history(self, profile_key: str, platform: str, months: int = 12) -> list[SocialPost]:
        months = max(1, min(months, 12))  # D9: aggregator lookback cap
        cutoff = datetime.now(timezone.utc) - timedelta(days=round(months * 30.44))
        wanted = _pp_platform(platform)
        items: list[dict] = []
        # GET /api/posts covers app/API posts AND source='imported' posts
        # pulled from the connected account (changelog 2026-04-27). No date
        # filter exists and listing order is undocumented, so page until a
        # full page falls outside the window AFTER we've collected something
        # (order-tolerant early stop), bounded by a hard cap.
        # VERIFY-ON-PILOT: whether native-post import runs automatically on
        # connect (or needs a dashboard action) and how far back it reaches.
        in_window_seen = False
        for page in range(20):  # hard cap: 20 * 50 = 1000 posts
            resp = await _http(
                "postproxy", "GET", f"{_PP_BASE}/posts",
                headers=self._headers(),
                params={
                    "profile_group_id": profile_key,
                    "platforms[]": wanted,
                    "page": page,
                    "per_page": 50,
                },
                secret=self._settings.postproxy_api_key,
            )
            data = resp.json()
            batch = [p for p in data.get("data") or [] if isinstance(p, dict)]
            if not batch:
                break
            items.extend(batch)
            page_in_window = any(_pp_post_in_window(p, wanted, cutoff) for p in batch)
            if in_window_seen and not page_in_window:
                break  # past the window (newest-first listing) — stop paging
            in_window_seen = in_window_seen or page_in_window
            if len(items) >= int(data.get("total") or 0):
                break

        posts: list[SocialPost] = []
        for item in items:
            block = next(
                (
                    b for b in item.get("platforms") or []
                    if isinstance(b, dict) and str(b.get("platform", "")).lower() == wanted
                ),
                {},
            )
            if block and block.get("status") not in (None, "published"):
                continue  # drafts / failed attempts are not history
            published = str(block.get("attempted_at") or item.get("created_at") or "")
            dt = _parse_iso(published)
            if dt is not None and dt < cutoff:
                continue
            insights = block.get("insights") if isinstance(block.get("insights"), dict) else {}
            posts.append(
                SocialPost(
                    platform=platform,
                    post_id=str(item.get("id", "")),
                    url=str(block.get("permalink") or ""),
                    text=str(item.get("body", "")),
                    published_at=published,
                    metrics={
                        k: int(v)
                        for k, v in insights.items()
                        if k in _PP_POST_METRIC_KEYS and isinstance(v, (int, float))
                    },
                )
            )
        if not posts:
            raise NotImplementedError(
                f"postproxy: no {platform} post history returned — native-post "
                "import unconfirmed for this account — VERIFY-ON-PILOT"
            )
        await self._enrich_metrics(profile_key, wanted, posts)
        return posts

    async def _enrich_metrics(self, profile_key: str, wanted: str, posts: list[SocialPost]) -> None:
        """Merge the latest GET /api/posts/stats snapshot per post (likes,
        comments, shares… — list-posts insights carry impressions only).
        Best-effort: history stays usable when the stats call fails."""
        for start in range(0, len(posts), 50):  # post_ids max 50 per call
            chunk = posts[start:start + 50]
            try:
                resp = await _http(
                    "postproxy", "GET", f"{_PP_BASE}/posts/stats",
                    headers=self._headers(),
                    params={
                        "post_ids": ",".join(p.post_id for p in chunk if p.post_id),
                        "profiles": wanted,
                    },
                    secret=self._settings.postproxy_api_key,
                )
            except RuntimeError as exc:
                logging.getLogger("providers").warning(
                    "postproxy post stats unavailable (%s); returning history with insights only",
                    str(exc)[:160],
                )
                return
            data = resp.json().get("data") or {}
            for post in chunk:
                blocks = (data.get(post.post_id) or {}).get("platforms") or []
                records = next(
                    (
                        b.get("records") or [] for b in blocks
                        if isinstance(b, dict) and str(b.get("platform", "")).lower() == wanted
                    ),
                    [],
                )
                # snapshot ordering is undocumented — pick newest by recorded_at
                newest = max(
                    (r for r in records if isinstance(r, dict)),
                    key=lambda r: str(r.get("recorded_at") or ""),
                    default=None,
                )
                if newest is not None:
                    stats = newest.get("stats") or {}
                    post.metrics.update(
                        {
                            k: int(v)
                            for k, v in stats.items()
                            if k in _PP_POST_METRIC_KEYS and isinstance(v, (int, float))
                        }
                    )

    async def publish(self, profile_key: str, platforms: list[str], text: str, media_urls: list[str]) -> dict:
        body: dict = {
            "post": {"body": text},
            "profiles": [_pp_platform(p) for p in platforms],
            "profile_group_id": profile_key,
        }
        if media_urls:
            body["media"] = media_urls
        resp = await _http(
            "postproxy", "POST", f"{_PP_BASE}/posts",
            headers=self._headers(),
            json_body=body,
            secret=self._settings.postproxy_api_key,
        )
        return resp.json()


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- peer data


_YT_BASE = "https://www.googleapis.com/youtube/v3"
_APIFY_BASE = "https://api.apify.com/v2"
# Apify actors per platform (the '/' in an actor id is '~' in the URL).
# VERIFY-ON-PILOT: actor ids + their input/output field names change across
# actor versions — confirm against the account on first real run.
_APIFY_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "tiktok": "clockworks~tiktok-scraper",
    "facebook": "apify~facebook-pages-scraper",
}
# per-platform actor input builder (keyed by handle/username or profile url)
def _apify_input(platform: str, handle: str, limit: int) -> dict:
    h = handle.lstrip("@")
    if platform == "instagram":
        return {"directUrls": [f"https://www.instagram.com/{h}/"], "resultsType": "posts", "resultsLimit": limit}
    if platform == "tiktok":
        return {"profiles": [h], "resultsPerPage": limit, "shouldDownloadVideos": False}
    if platform == "facebook":
        return {"startUrls": [{"url": f"https://www.facebook.com/{h}"}], "resultsLimit": limit}
    return {"usernames": [h], "resultsLimit": limit}


_APIFY_FOLLOWER_KEYS = ("followersCount", "followers", "fans", "pageLikes", "followerCount")
_APIFY_NAME_KEYS = ("fullName", "ownerFullName", "name", "nickName", "title", "pageName")
_APIFY_TEXT_KEYS = ("caption", "text", "title", "message", "desc")
# post engagement — tolerant union across IG / TikTok / FB actor outputs
_APIFY_METRIC_KEYS = {
    "likes": ("likesCount", "diggCount", "likes"),
    "comments": ("commentsCount", "comments"),
    "shares": ("sharesCount", "shareCount", "shares", "reshareCount"),
    "views": ("videoViewCount", "playCount", "views", "videoPlayCount"),
    "saves": ("savesCount", "collectCount"),
}


_SC_BASE = "https://api.scrapecreators.com"
# VERIFY-ON-PILOT: ScrapeCreators endpoint paths and 'handle' param name are
# assumed from their public docs; confirm per-platform before go-live.
_SC_PROFILE_PATHS = {
    "instagram": "/v1/instagram/profile",
    "tiktok": "/v1/tiktok/profile",
    "x": "/v1/twitter/profile",
}
_SC_POSTS_PATHS = {
    "instagram": "/v1/instagram/user/posts",
    "tiktok": "/v1/tiktok/user/videos",
    "x": "/v1/twitter/user-tweets",
}
# VERIFY-ON-PILOT: ScrapeCreators response shapes differ per platform; the
# key lists below are a tolerant union (IG / TikTok / X naming conventions).
_SC_METRIC_KEYS = {
    "likes": ("likeCount", "like_count", "diggCount", "digg_count", "favorite_count", "likes"),
    "comments": ("commentCount", "comment_count", "commentsCount", "reply_count", "comments"),
    "views": ("playCount", "play_count", "viewCount", "view_count", "views"),
    "shares": ("shareCount", "share_count", "retweet_count", "shares"),
}


def _norm_platform(platform: str) -> str:
    p = platform.lower()
    return "x" if p in ("x", "twitter") else p


class CompositePeerData:
    """PeerDataProvider — per-platform routing (D8/D9):
    youtube -> YouTube Data API v3 (channels.list/playlistItems.list/videos.list
    ONLY — search.list is ~100/day and forbidden per D9);
    instagram/tiktok/facebook/x -> Apify actors when APIFY_API_KEY is set,
    else ScrapeCreators REST."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings
        self._apify_cache: dict[tuple[str, str], list] = {}

    def _use_apify(self, platform: str) -> bool:
        return bool(self._settings.apify_api_key) and platform in _APIFY_ACTORS

    def _scraper_providers(self, platform: str) -> list[str]:
        """Preference-ordered scraper backends available for this platform:
        the resilient chain. Apify first when its token is set, ScrapeCreators
        second (or vice versa) — a snapshot only fails when BOTH are down, so
        the growth trajectory rarely has a gap. Both agreeing is high
        confidence; only the primary is called on success (no double cost)."""
        chain: list[str] = []
        if self._use_apify(platform):
            chain.append("apify")
        if self._settings.scrapecreators_api_key and platform in _SC_PROFILE_PATHS:
            chain.append("scrapecreators")
        return chain

    async def _chain(self, kind: str, platform: str, handle: str, limit: int):
        """Try each available scraper backend in order; fall through on failure
        or empty result. Raises only when every backend fails."""
        errors: list[str] = []
        for backend in self._scraper_providers(platform):
            try:
                if backend == "apify":
                    result = (
                        await self._apify_profile(platform, handle) if kind == "profile"
                        else await self._apify_recent_posts(platform, handle, limit)
                    )
                else:
                    result = (
                        await self._sc_profile(platform, handle) if kind == "profile"
                        else await self._sc_recent_posts(platform, handle, limit)
                    )
                # empty posts / follower-less profile -> try the next backend
                if kind == "posts" and not result:
                    errors.append(f"{backend}: empty")
                    continue
                if kind == "profile" and result.followers is None and len(self._scraper_providers(platform)) > 1:
                    errors.append(f"{backend}: no followers")
                    continue
                return result
            except Exception as exc:  # noqa: BLE001 — fall through to the next backend
                errors.append(f"{backend}: {str(exc)[:80]}")
        raise RuntimeError(
            f"peer {kind} unavailable for {platform} @{handle}: " + "; ".join(errors)
            if errors else f"peer data not supported for platform: {platform}"
        )

    async def profile(self, platform: str, handle: str) -> SocialProfile:
        p = _norm_platform(platform)
        if p == "youtube":
            return await self._yt_profile(handle)
        return await self._chain("profile", p, handle, 20)

    async def recent_posts(self, platform: str, handle: str, limit: int = 20) -> list[SocialPost]:
        p = _norm_platform(platform)
        if p == "youtube":
            return await self._yt_recent_posts(handle, limit)
        return await self._chain("posts", p, handle, limit)

    # -- apify (instagram / tiktok / facebook), token-gated, run-cached

    async def _apify_run(self, platform: str, handle: str, limit: int) -> list:
        cache_key = (platform, handle.lstrip("@").lower())
        if cache_key in self._apify_cache:
            return self._apify_cache[cache_key]
        token = _require_key(self._settings.apify_api_key, "apify")
        actor = _APIFY_ACTORS[platform]
        resp = await _http(
            "apify", "POST",
            f"{_APIFY_BASE}/acts/{actor}/run-sync-get-dataset-items",
            params={"token": token},
            json_body=_apify_input(platform, handle, limit),
            secret=token,
        )
        items = resp.json()
        items = items if isinstance(items, list) else []
        self._apify_cache[cache_key] = items
        return items

    async def _apify_profile(self, platform: str, handle: str) -> SocialProfile:
        items = await self._apify_run(platform, handle, 20)
        first = items[0] if items and isinstance(items[0], dict) else {}
        # follower/name may live on the item or a nested author object
        nests = [first] + [first[k] for k in ("owner", "author", "authorMeta", "user") if isinstance(first.get(k), dict)]
        followers = next((v for n in nests for v in [_first(n, *_APIFY_FOLLOWER_KEYS)] if v is not None), None)
        name = next((v for n in nests for v in [_first(n, *_APIFY_NAME_KEYS)] if v), "")
        return SocialProfile(
            platform=platform,
            handle=handle,
            display_name=str(name),
            followers=int(followers) if followers is not None else None,  # VERIFY-ON-PILOT: actor follower field
            bio=str(_first(first, "biography", "bio", "description", default="")),
            meta={"apify_actor": _APIFY_ACTORS[platform]},
        )

    async def _apify_recent_posts(self, platform: str, handle: str, limit: int) -> list[SocialPost]:
        items = await self._apify_run(platform, handle, limit)
        posts: list[SocialPost] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            text = _first(item, *_APIFY_TEXT_KEYS, default="")
            metrics: dict = {}
            for name, keys in _APIFY_METRIC_KEYS.items():
                v = _first(item, *keys)
                if v is not None:
                    try:
                        metrics[name] = int(v)
                    except (TypeError, ValueError):
                        pass
            posts.append(
                SocialPost(
                    platform=platform,
                    post_id=str(_first(item, "id", "postId", "shortCode", "pk", default="")),
                    url=str(_first(item, "url", "postUrl", "webVideoUrl", default="")),
                    text=str(text),
                    published_at=str(_first(item, "timestamp", "createTimeISO", "date", "time", default="")),
                    metrics=metrics,
                )
            )
        return posts

    # -- youtube (1-unit endpoints only, D9)

    async def _yt_channel(self, handle: str) -> dict:
        key = _require_key(self._settings.youtube_api_key, "youtube")
        params = {"part": "snippet,statistics,contentDetails", "key": key}
        if handle.startswith("UC") and len(handle) == 24:
            params["id"] = handle
        else:
            params["forHandle"] = handle.lstrip("@")
        resp = await _http("youtube", "GET", f"{_YT_BASE}/channels", params=params, secret=key)
        items = resp.json().get("items") or []
        if not items:
            raise RuntimeError(f"youtube channel not found: {handle}")
        return items[0]

    async def _yt_profile(self, handle: str) -> SocialProfile:
        ch = await self._yt_channel(handle)
        snippet, stats = ch.get("snippet", {}), ch.get("statistics", {})
        followers = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))
        return SocialProfile(
            platform="youtube",
            handle=handle,
            display_name=str(snippet.get("title", "")),
            followers=followers,
            bio=str(snippet.get("description", "")),
            meta={
                "channel_id": ch.get("id", ""),
                "video_count": int(stats.get("videoCount", 0)),
                "view_count": int(stats.get("viewCount", 0)),
            },
        )

    async def _yt_recent_posts(self, handle: str, limit: int) -> list[SocialPost]:
        key = _require_key(self._settings.youtube_api_key, "youtube")
        ch = await self._yt_channel(handle)
        uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        if not uploads:
            return []
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/playlistItems",
            params={"part": "contentDetails", "playlistId": uploads, "maxResults": min(limit, 50), "key": key},
            secret=key,
        )
        ids = [
            item["contentDetails"]["videoId"]
            for item in resp.json().get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not ids:
            return []
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/videos",
            params={"part": "snippet,statistics", "id": ",".join(ids[:50]), "key": key},
            secret=key,
        )
        posts = []
        for video in resp.json().get("items", []):
            snippet, stats = video.get("snippet", {}), video.get("statistics", {})
            vid = video.get("id", "")
            posts.append(
                SocialPost(
                    platform="youtube",
                    post_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    text=str(snippet.get("title", "")),
                    published_at=str(snippet.get("publishedAt", "")),
                    metrics={
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                    },
                )
            )
        return posts[:limit]

    # -- scrapecreators (instagram / tiktok / x)

    async def _sc_get(self, path: str, handle: str) -> dict | list:
        key = _require_key(self._settings.scrapecreators_api_key, "scrapecreators")
        resp = await _http(
            "scrapecreators", "GET", f"{_SC_BASE}{path}",
            headers={"x-api-key": key},
            params={"handle": handle.lstrip("@")},
            secret=key,
        )
        data = resp.json()
        # the API can 200 with an error envelope (even success=True alongside
        # errorStatus=404, e.g. "Profile is restricted") — raise so the chain
        # falls through / reports honestly instead of returning hollow data
        if isinstance(data, dict):
            err_status = data.get("errorStatus")
            if (isinstance(err_status, int) and err_status >= 400) or data.get("error"):
                msg = str(data.get("message") or data.get("error") or "unknown error")[:120]
                raise RuntimeError(f"scrapecreators {path} @{handle}: {msg}")
        return data

    async def _sc_profile(self, platform: str, handle: str) -> SocialProfile:
        raw = await self._sc_get(_SC_PROFILE_PATHS[platform], handle)
        data = raw if isinstance(raw, dict) else {}
        for _ in range(3):  # unwrap nested envelopes (e.g. instagram is data.user)
            inner = next(
                (data[k] for k in ("data", "user", "profile", "userInfo") if isinstance(data.get(k), dict)),
                None,
            )
            if inner is None:
                break
            data = inner
        followers = _first(data, "followerCount", "followersCount", "follower_count", "followers", "fans")
        if followers is None:  # instagram GraphQL shape: edge_followed_by.count
            edge = data.get("edge_followed_by")
            if isinstance(edge, dict):
                followers = edge.get("count")
        return SocialProfile(
            platform=platform,
            handle=handle,
            display_name=str(_first(data, "fullName", "full_name", "name", "nickname", "displayName", default="")),
            followers=int(followers) if followers is not None else None,
            bio=str(_first(data, "biography", "bio", "description", "signature", default="")),
            meta=data,
        )

    async def _sc_recent_posts(self, platform: str, handle: str, limit: int) -> list[SocialPost]:
        raw = await self._sc_get(_SC_POSTS_PATHS[platform], handle)
        if isinstance(raw, list):
            items = raw
        else:
            items = next(
                (raw[k] for k in ("posts", "items", "videos", "tweets", "data") if isinstance(raw.get(k), list)),
                [],
            )
        posts: list[SocialPost] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            text = _first(item, "caption", "text", "desc", "description", "full_text", "title", default="")
            if isinstance(text, dict):  # IG nests caption as {"text": ...}
                text = text.get("text", "")
            metrics: dict = {}
            for name, keys in _SC_METRIC_KEYS.items():
                value = _first(item, *keys)
                if value is not None:
                    try:
                        metrics[name] = int(value)
                    except (TypeError, ValueError):
                        pass
            posts.append(
                SocialPost(
                    platform=platform,
                    post_id=str(_first(item, "id", "pk", "post_id", "aweme_id", "rest_id", default="")),
                    url=str(_first(item, "url", "permalink", "webVideoUrl", "share_url", "link", default="")),
                    text=str(text),
                    published_at=str(
                        _first(item, "timestamp", "taken_at", "createTime", "create_time", "created_at",
                               "publishedAt", default="")
                    ),
                    metrics=metrics,
                )
            )
        return posts


# ------------------------------------ parallel research lanes (D11) — wiki


class WikipediaKnowledge:
    """KnowledgeProvider — Wikipedia REST API, keyless (D11 wikipedia lane).
    opensearch resolves the best title, then the summary endpoint fills the
    page. None = no page — itself a finding (positioning.wikipedia_presence
    -> the 'create a Wikipedia page' recommendation)."""

    _SEARCH_URL = "https://en.wikipedia.org/w/api.php"
    _SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
    # Wikimedia UA policy: descriptive agent string with contact.
    _HEADERS = {"User-Agent": "BrandManager/0.1 (brand research pipeline; contact: roy@prerealinvestments.com)"}

    async def lookup(self, name: str) -> WikiPage | None:
        resp = await _http(
            "wikipedia", "GET", self._SEARCH_URL,
            headers=self._HEADERS,
            params={"action": "opensearch", "search": name, "limit": 1, "namespace": 0, "format": "json"},
        )
        payload = resp.json()
        # opensearch shape: [query, [titles], [descriptions], [urls]]
        titles = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        if not titles:
            return None
        title = str(titles[0])
        resp = await _http(
            "wikipedia", "GET", self._SUMMARY_URL + quote(title.replace(" ", "_"), safe=""),
            headers=self._HEADERS,
            ok_404=True,
        )
        if resp.status_code == 404:
            return None
        data = resp.json()
        if data.get("type") == "disambiguation":
            return None  # a name collision page, not a dedicated brand page
        facts: dict = {}
        if data.get("description"):
            facts["description"] = str(data["description"])
        coords = data.get("coordinates")
        if isinstance(coords, dict) and coords.get("lat") is not None and coords.get("lon") is not None:
            facts["coordinates"] = {"lat": coords["lat"], "lon": coords["lon"]}
        resolved_title = str(data.get("title") or title)
        if not _wiki_title_matches(name, resolved_title):
            return None  # prefix collision (opensearch is prefix-based) — absence beats a wrong entity
        url = str(
            ((data.get("content_urls") or {}).get("desktop") or {}).get("page")
            or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}"
        )
        return WikiPage(
            title=resolved_title,
            url=url,
            summary=str(data.get("extract") or ""),
            facts=facts,
        )


def _wiki_title_matches(name: str, title: str) -> bool:
    """Guard against opensearch prefix collisions ('Turtleback' -> 'Turtleback
    Mountain'). Strict: after stripping disambiguation qualifiers
    ('(businessman)', ', Arizona'), token sets must match exactly — a missed
    page only costs a spurious 'create a page' recommendation, while a wrong
    entity injects a false summary AND suppresses that recommendation.
    VERIFY-ON-PILOT: single-token names colliding with an identically-titled
    notable entity remain undetectable at title level; extract cross-check
    against the seed website is the pilot follow-up if it bites."""

    def norm(s: str) -> set[str]:
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    stripped = re.sub(r"\([^)]*\)", " ", title).split(",")[0]
    name_tokens, title_tokens = norm(name), norm(stripped)
    return bool(name_tokens) and name_tokens == title_tokens


# ----------------------------------- parallel research lanes (D11) — video


def _yt_channel_lookup(query: str) -> dict | None:
    """channels.list filter params from a LOCATED channel URL (D9: never
    search.list). youtube.com URLs: /channel/UC… -> id, /@handle -> forHandle,
    /user/name -> forUsername, /c/name -> forHandle (best effort — legacy
    vanity names may not resolve). Other youtube.com URLs (watch/shorts/…)
    carry no channel identity -> None. Bare (non-URL) queries -> None, never
    a slug-guessed forHandle: @<slugified-name> can belong to a STRANGER, and
    writing their stats into the profile is worse than no lane output (D11:
    the researcher locates the channel url via web search, then passes it)."""
    if "youtube.com/" not in query:
        return None
    m = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})", query)
    if m:
        return {"id": m.group(1)}
    m = re.search(r"youtube\.com/@([A-Za-z0-9._-]+)", query)
    if m:
        return {"forHandle": m.group(1)}
    m = re.search(r"youtube\.com/user/([A-Za-z0-9._-]+)", query)
    if m:
        return {"forUsername": m.group(1)}
    m = re.search(r"youtube\.com/c/([A-Za-z0-9._-]+)", query)
    if m:
        return {"forHandle": m.group(1)}
    return None


class YouTubeVideo:
    """VideoProvider — YouTube Data API v3, 1-unit endpoints only (D9/D11:
    search.list forbidden). channels.list resolves the channel, the uploads
    playlist supplies recent titles. BY DESIGN the query must be a channel
    URL: bare brand names return None rather than being slug-guessed into a
    handle that may belong to a different creator. The researcher's youtube
    lane locates the channel URL via web search (a user-confirmed url wins,
    else site:youtube.com search) and passes it here."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def channel_overview(self, query: str) -> ChannelOverview | None:
        key = _require_key(self._settings.youtube_api_key, "youtube")
        lookup = _yt_channel_lookup(query)
        if not lookup:
            return None
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/channels",
            params={"part": "snippet,statistics,contentDetails", "key": key, **lookup},
            secret=key,
        )
        items = resp.json().get("items") or []
        if not items:
            return None  # no such channel — a finding, not an error
        ch = items[0]
        snippet, stats = ch.get("snippet", {}), ch.get("statistics", {})
        channel_id = str(ch.get("id", ""))
        custom = str(snippet.get("customUrl") or "")
        return ChannelOverview(
            platform="youtube",
            channel_id=channel_id,
            title=str(snippet.get("title", "")),
            url=(
                f"https://www.youtube.com/{custom}" if custom
                else f"https://www.youtube.com/channel/{channel_id}"
            ),
            subscribers=None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
            video_count=int(stats.get("videoCount", 0)),
            recent_titles=await self._recent_titles(ch, key),
        )

    async def _recent_titles(self, ch: dict, key: str) -> list[str]:
        uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        if not uploads:
            return []
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/playlistItems",
            params={"part": "snippet", "playlistId": uploads, "maxResults": 5, "key": key},
            secret=key,
            ok_404=True,  # channels with zero uploads 404 on their uploads playlist
        )
        if resp.status_code == 404:
            return []
        return [
            str(item["snippet"]["title"])
            for item in resp.json().get("items", [])
            if item.get("snippet", {}).get("title")
        ]

    async def recent_uploads(self, channel_query: str, limit: int = 6) -> list[VideoRef]:
        """The brand's own recent uploads (video_id + url + title) from the
        uploads playlist — 1-unit playlistItems, D9-safe. The voice harvester
        transcribes these; own-channel provenance is the speaker verification."""
        key = _require_key(self._settings.youtube_api_key, "youtube")
        lookup = _yt_channel_lookup(channel_query)
        if not lookup:
            return []
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/channels",
            params={"part": "contentDetails", "key": key, **lookup}, secret=key,
        )
        items = resp.json().get("items") or []
        if not items:
            return []
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        if not uploads:
            return []
        resp = await _http(
            "youtube", "GET", f"{_YT_BASE}/playlistItems",
            params={"part": "snippet,contentDetails", "playlistId": uploads,
                    "maxResults": min(max(limit, 1), 50), "key": key},
            secret=key, ok_404=True,
        )
        if resp.status_code == 404:
            return []
        refs: list[VideoRef] = []
        for it in resp.json().get("items", []):
            sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
            vid = str(cd.get("videoId") or sn.get("resourceId", {}).get("videoId") or "")
            if not vid:
                continue
            refs.append(VideoRef(
                video_id=vid,
                url=f"https://www.youtube.com/watch?v={vid}",
                title=str(sn.get("title", "")),
                published_at=str(cd.get("videoPublishedAt") or sn.get("publishedAt") or ""),
            ))
        return refs[:limit]


# ---------------------------------- parallel research lanes (D11) — places


class SerperPlaces:
    """PlacesProvider — Serper.dev /places riding the search key (D11 places
    lane; physical_asset + institution entity types only). Top result only."""

    _URL = "https://google.serper.dev/places"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def place(self, query: str) -> PlaceInfo | None:
        key = _require_key(self._settings.serper_api_key, "serper")
        resp = await _http(
            "serper-places", "POST", self._URL,
            headers={"X-API-KEY": key},
            json_body={"q": query},
            secret=key,
        )
        places = resp.json().get("places") or []
        if not places:
            return None
        top = places[0]
        # VERIFY-ON-PILOT: Serper places item fields assumed 'title', 'address',
        # 'rating', 'ratingCount', 'category' (single string), 'website', 'cid'.
        rating = top.get("rating")
        reviews = _first(top, "ratingCount", "reviewsCount", "reviews")
        category = str(_first(top, "category", "type", default=""))
        cid = str(top.get("cid") or "")
        return PlaceInfo(
            name=str(top.get("title", "")),
            address=str(top.get("address", "")),
            rating=float(rating) if rating is not None else None,
            reviews_count=int(reviews) if reviews is not None else None,
            categories=[category] if category else [],
            website=str(top.get("website", "")),
            url=f"https://maps.google.com/?cid={cid}" if cid else "",
        )


# ------------------------------------ parallel research lanes (D11) — deep


class PerplexityDeepResearch:
    """DeepResearchProvider — Perplexity 'sonar' (D11 deep lane). Output is
    supplementary synthesis: callers write it source=researched SECONDARY
    confidence with the returned citations, never primary (base Protocol)."""

    _URL = "https://api.perplexity.ai/chat/completions"
    _SYSTEM = (
        "You are a factual brand-research assistant. Report only verifiable, "
        "sourced public facts about the brand or entity in the question — "
        "positioning, offerings, audience, competitors, press coverage. "
        "No speculation, no advice, no invented details; state plainly when "
        "something is unknown. Be concise and cite sources."
    )

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def research(self, question: str) -> DeepResearchResult:
        key = _require_key(self._settings.perplexity_api_key, "perplexity")
        resp = await _http(
            "perplexity", "POST", self._URL,
            headers={"Authorization": f"Bearer {key}"},
            json_body={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": self._SYSTEM},
                    {"role": "user", "content": question},
                ],
            },
            secret=key,
        )
        data = resp.json()
        choices = data.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        synthesis = str(message.get("content") or "")
        if not synthesis:
            raise RuntimeError("perplexity returned no message content")
        # VERIFY-ON-PILOT: citation shape varies — older responses carry a
        # top-level 'citations' list[str]; newer ones 'search_results'
        # list[{title,url,...}]. Read both defensively.
        citations: list[str] = []
        for item in data.get("citations") or []:
            if isinstance(item, str) and item:
                citations.append(item)
            elif isinstance(item, dict) and item.get("url"):
                citations.append(str(item["url"]))
        for item in data.get("search_results") or []:
            if isinstance(item, dict) and item.get("url"):
                citations.append(str(item["url"]))
        seen: set[str] = set()
        citations = [u for u in citations if not (u in seen or seen.add(u))]
        return DeepResearchResult(question=question, synthesis=synthesis, citations=citations)


# ------------------------------------------------------------- transcription


class AssemblyAITranscription:
    """TranscriptionProvider — AssemblyAI submit + poll (max ~10 min)."""

    _BASE = "https://api.assemblyai.com/v2"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    async def transcribe(self, media_url: str) -> str:
        key = _require_key(self._settings.assemblyai_api_key, "assemblyai")
        headers = {"authorization": key}
        resp = await _http(
            "assemblyai", "POST", f"{self._BASE}/transcript",
            headers=headers,
            json_body={"audio_url": media_url},
            secret=key,
        )
        transcript_id = resp.json().get("id", "")
        if not transcript_id:
            raise RuntimeError("assemblyai transcript submit: no id in response")
        delay, waited = 3.0, 0.0
        while waited < 600.0:
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.5, 30.0)
            resp = await _http(
                "assemblyai", "GET", f"{self._BASE}/transcript/{transcript_id}",
                headers=headers, secret=key,
            )
            data = resp.json()
            status = data.get("status", "")
            if status == "completed":
                return str(data.get("text") or "")
            if status == "error":
                raise RuntimeError(f"assemblyai transcription failed: {str(data.get('error', ''))[:200]}")
        raise RuntimeError("assemblyai transcription timed out after ~10 minutes")

    async def transcribe_source(self, url: str, *, title: str = "") -> TranscriptResult:
        """Any spoken source -> text. YouTube: captions first (free, fast), then
        audio extraction -> AssemblyAI. Direct media: AssemblyAI. Best-effort:
        a source we can't transcribe returns empty text + a note, never raises."""
        vid = _youtube_id(url)
        if vid:
            text = await asyncio.to_thread(_youtube_captions, vid)
            if text:
                return TranscriptResult(url=url, text=text, method="captions", title=title)
            if not self._settings.assemblyai_api_key:
                return TranscriptResult(
                    url=url, text="", method="none", title=title,
                    note="no captions and no AssemblyAI key for audio transcription",
                )
            try:
                path = await asyncio.to_thread(_yt_extract_audio, url)
            except Exception as exc:
                return TranscriptResult(url=url, text="", method="none", title=title,
                                        note=f"audio extraction failed: {str(exc)[:120]}")
            if not path:
                return TranscriptResult(url=url, text="", method="none", title=title,
                                        note="no captions; audio extraction produced nothing")
            try:
                text = await self._transcribe_file(path)
            except Exception as exc:
                return TranscriptResult(url=url, text="", method="none", title=title,
                                        note=f"transcription failed: {str(exc)[:120]}")
            finally:
                await asyncio.to_thread(_safe_unlink, path)
            return TranscriptResult(url=url, text=text, method="assemblyai", title=title,
                                    note="" if text else "empty transcript")
        # direct media URL (podcast mp3, etc.)
        if not self._settings.assemblyai_api_key:
            return TranscriptResult(url=url, text="", method="none", title=title,
                                    note="AssemblyAI key not configured")
        try:
            text = await self.transcribe(url)
        except Exception as exc:
            return TranscriptResult(url=url, text="", method="none", title=title,
                                    note=f"transcription failed: {str(exc)[:120]}")
        return TranscriptResult(url=url, text=text, method="direct", title=title)

    async def _transcribe_file(self, path: str) -> str:
        """AssemblyAI file upload (raw bytes -> upload_url) then transcribe it."""
        key = _require_key(self._settings.assemblyai_api_key, "assemblyai")
        with open(path, "rb") as fh:
            payload = fh.read()
        async with httpx.AsyncClient(timeout=180.0) as client:
            up = await client.post(f"{self._BASE}/upload", headers={"authorization": key}, content=payload)
            up.raise_for_status()
            upload_url = up.json().get("upload_url", "")
        if not upload_url:
            raise RuntimeError("assemblyai upload returned no upload_url")
        return await self.transcribe(upload_url)


# ------------------------------------------- voice-harvest transcription helpers


_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def _youtube_id(url: str) -> str:
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return ""
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else ""


def _youtube_captions(video_id: str) -> str:
    """English captions (auto or manual). '' on any failure — caption-less
    videos fall through to audio transcription. Handles both the <1.0 and >=1.0
    youtube-transcript-api APIs."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return ""
    langs = ["en", "en-US", "en-GB"]
    try:
        try:  # youtube-transcript-api < 1.0
            segs = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            return " ".join(s.get("text", "") for s in segs if s.get("text")).strip()
        except AttributeError:  # >= 1.0 instance API
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=langs)
            return " ".join(getattr(s, "text", "") for s in fetched).strip()
    except Exception:
        return ""


def _yt_extract_audio(url: str) -> str:
    """Download bestaudio to a temp dir (no ffmpeg needed) for transcription.
    Returns the file path, or '' on failure."""
    import os
    import tempfile

    try:
        import yt_dlp
    except Exception:
        return ""
    tmpdir = tempfile.mkdtemp(prefix="voice-")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True, "cachedir": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    return path if os.path.exists(path) else ""


def _safe_unlink(path: str) -> None:
    import os
    import shutil

    try:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------- llm


# LLMRouter contract: token usage accrues to the active AgentRun. The run is
# bound automatically by runs.start_run and unbound by finish_run,
# so every agent gets accounting for free; unbound calls are legal but
# untracked.


def _extract_json(text: str) -> dict | None:
    """First JSON object in text — raw, fenced, or embedded in prose."""
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    return None


class AnthropicRouter:
    """LLMRouter — tier routing per D8 (extract/content/strategy -> settings
    models). Never passes tools: web access is SearchProvider-only (D8)."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings
        self._cached_client: AsyncAnthropic | None = None

    def _client(self) -> AsyncAnthropic:
        _require_key(self._settings.anthropic_api_key, "anthropic")
        if self._cached_client is None:
            self._cached_client = AsyncAnthropic(api_key=self._settings.anthropic_api_key, max_retries=1)
        return self._cached_client

    def _model_for(self, tier: str) -> str:
        models = {
            "extract": self._settings.llm_extract_model,
            "content": self._settings.llm_content_model,
            "strategy": self._settings.llm_strategy_model,
        }
        if tier not in models:
            raise ValueError(f"unknown llm tier: {tier!r}")
        return models[tier]

    async def complete(self, tier: str, system: str, prompt: str, max_tokens: int = 2000) -> str:
        resp = await self._client().messages.create(
            model=self._model_for(tier),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            runs.add_usage(tokens_in=usage.input_tokens, tokens_out=usage.output_tokens)
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")

    async def complete_json(self, tier: str, system: str, prompt: str, max_tokens: int = 4000) -> dict:
        text = await self.complete(tier, system, prompt, max_tokens)
        parsed = _extract_json(text)
        if parsed is None:
            nudge = f"{prompt}\n\nReturn ONLY a single valid JSON object — no prose, no markdown fences."
            text = await self.complete(tier, system, nudge, max_tokens)
            parsed = _extract_json(text)
        if parsed is None:
            raise RuntimeError(f"anthropic returned non-JSON output for tier {tier!r} after one retry")
        return parsed


class PerplexityRouter:
    """LLMRouter over Perplexity's chat completions — the fallback LLM when
    Anthropic is unavailable (missing key, exhausted credits). Sonar models
    are search-grounded; the system suffix pins them to the provided material
    so extraction stays provenance-faithful. Tiers: extract/content -> sonar,
    strategy -> sonar-pro. VERIFY-ON-PILOT: per-request search fees make this
    pricier per call than Haiku — a bridge, not the destination."""

    _URL = "https://api.perplexity.ai/chat/completions"
    _NO_SEARCH_SUFFIX = (
        " Work ONLY from the material provided in the prompt. Do not add "
        "facts from the web or your own knowledge, even if you know them."
    )

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or _settings

    def _model_for(self, tier: str) -> str:
        if tier not in ("extract", "content", "strategy"):
            raise ValueError(f"unknown llm tier: {tier!r}")
        return "sonar-pro" if tier == "strategy" else "sonar"

    async def complete(self, tier: str, system: str, prompt: str, max_tokens: int = 2000) -> str:
        key = _require_key(self._settings.perplexity_api_key, "perplexity")
        resp = await _http(
            "perplexity", "POST", self._URL,
            headers={"Authorization": f"Bearer {key}"},
            json_body={
                "model": self._model_for(tier),
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system + self._NO_SEARCH_SUFFIX},
                    {"role": "user", "content": prompt},
                ],
            },
            secret=key,
        )
        data = resp.json()
        usage = data.get("usage") or {}
        runs.add_usage(
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
        )
        choices = data.get("choices") or []
        content = str(((choices[0] or {}).get("message") or {}).get("content", "")) if choices else ""
        # sonar may inline citation markers like [1] — harmless in prose,
        # noise inside JSON; stripped before parsing in complete_json.
        return content

    async def complete_json(self, tier: str, system: str, prompt: str, max_tokens: int = 4000) -> dict:
        text = await self.complete(tier, system, prompt, max_tokens)
        parsed = _extract_json(re.sub(r"\[\d+\]", "", text))
        if parsed is None:
            nudge = f"{prompt}\n\nReturn ONLY a single valid JSON object — no prose, no markdown fences."
            text = await self.complete(tier, system, nudge, max_tokens)
            parsed = _extract_json(re.sub(r"\[\d+\]", "", text))
        if parsed is None:
            raise RuntimeError(f"perplexity returned non-JSON output for tier {tier!r} after one retry")
        return parsed


class ChainLLMRouter:
    """Tries routers in order; on failure of one, falls through to the next.
    Makes 'Anthropic out of credits' degrade to Perplexity instead of killing
    every LLM lane. Never retries on tier-name errors (caller bugs must
    surface), and never includes the mock in live envs (see live_providers)."""

    def __init__(self, routers: list, names: list[str]):
        self._routers = routers
        self._names = names
        self._log = logging.getLogger("providers")

    async def _try(self, method: str, *args, **kwargs):
        last_exc: Exception | None = None
        for router, name in zip(self._routers, self._names):
            try:
                return await getattr(router, method)(*args, **kwargs)
            except ValueError:
                raise  # unknown tier — a bug, not an availability problem
            except Exception as exc:  # noqa: BLE001 — availability fallback by design
                self._log.warning("llm router %s failed (%s); falling through", name, str(exc)[:160])
                last_exc = exc
        raise RuntimeError(f"all llm routers failed; last: {last_exc}") from last_exc

    async def complete(self, tier: str, system: str, prompt: str, max_tokens: int = 2000) -> str:
        return await self._try("complete", tier, system, prompt, max_tokens=max_tokens)

    async def complete_json(self, tier: str, system: str, prompt: str, max_tokens: int = 4000) -> dict:
        return await self._try("complete_json", tier, system, prompt, max_tokens=max_tokens)


# ------------------------------------------------------------------- wiring


# -------------------------------------------------- execution 'hands' (live)


class ResendEmailProvider:
    """Live email 'hand' via Resend. Guarded by resend_api_key; a failure comes
    back as PublishResult(ok=False) so the owner sees an honest miss, never a
    fake 'sent'. VERIFY-ON-PILOT: confirm the from-domain is verified in Resend."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or _settings

    async def send(
        self, *, to: list[str], subject: str, html: str, from_name: str = "", preheader: str = ""
    ) -> PublishResult:
        key = _require_key(self.settings.resend_api_key, "resend")
        recipients = [t for t in (to or []) if t]
        if not recipients:
            return PublishResult(ok=False, provider="resend", detail={"error": "no recipients"})
        sender = from_name or self.settings.email_from
        try:
            resp = await _http(
                "resend", "POST", "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json_body={"from": sender, "to": recipients, "subject": subject, "html": html},
                secret=key,
            )
            data = resp.json() if resp.content else {}
        except Exception as exc:  # honest failure, not a fabricated send
            return PublishResult(ok=False, provider="resend", detail={"error": str(exc)[:200]})
        return PublishResult(
            ok=True, provider="resend", ref=str(data.get("id") or ""),
            detail={"to": recipients, "subject": subject},
        )


class WebhookBlogPublisher:
    """Live blog 'hand': POSTs the post to a configured CMS/webhook
    (blog_publish_url). Surface-agnostic (own hosted blog, Substack proxy,
    Ghost, WP) — swapping targets is a config change (PRD R8.1)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or _settings

    async def publish(
        self, *, title: str, body_markdown: str, slug: str = "", meta: dict | None = None
    ) -> PublishResult:
        url = self.settings.blog_publish_url
        if not url:
            return PublishResult(ok=False, provider="webhook", detail={"error": "blog_publish_url not set"})
        headers = {"Content-Type": "application/json"}
        if self.settings.blog_api_key:
            headers["Authorization"] = f"Bearer {self.settings.blog_api_key}"
        try:
            resp = await _http(
                "blog", "POST", url, headers=headers,
                json_body={"title": title, "body_markdown": body_markdown, "slug": slug, "meta": meta or {}},
                secret=self.settings.blog_api_key,
            )
            data = resp.json() if resp.content else {}
        except Exception as exc:
            return PublishResult(ok=False, provider="webhook", detail={"error": str(exc)[:200]})
        public = str(data.get("url") or "")
        if not public and self.settings.blog_public_base and slug:
            public = f"{self.settings.blog_public_base.rstrip('/')}/{slug}"
        return PublishResult(
            ok=True, provider="webhook", ref=str(data.get("id") or slug), url=public,
            detail={"title": title, "slug": slug},
        )


def live_providers(fallback: Providers | None = None) -> Providers:
    """Per-provider go-live: each provider is live iff its key is configured,
    otherwise it falls back to the supplied (mock) provider. This makes
    incremental go-live a matter of adding keys to .env — SERPER_API_KEY +
    ANTHROPIC_API_KEY alone are enough for real brand research."""
    settings = _settings
    log = logging.getLogger("providers")

    def pick(name: str, live_obj, has_key: bool):
        if has_key:
            log.info("provider %s: LIVE (%s)", name, type(live_obj).__name__)
            return live_obj
        if fallback is None:
            log.info("provider %s: LIVE (%s) — no key, will fail at call time", name, type(live_obj).__name__)
            return live_obj
        fb = getattr(fallback, name)
        log.info("provider %s: fallback (%s) — key not configured", name, type(fb).__name__)
        return fb

    if settings.gnews_api_key:
        news = GNewsProvider(settings)
        log.info("provider news: LIVE (GNewsProvider)")
    elif settings.serper_api_key:
        news = SerperNewsProvider(settings)
        log.info("provider news: LIVE (SerperNewsProvider via Serper key)")
    else:
        news = fallback.news if fallback else GNewsProvider(settings)
        log.info("provider news: fallback — no gnews/serper key")

    if settings.firecrawl_api_key:
        scrape = FirecrawlScrape(settings)
        log.info("provider scrape: LIVE (FirecrawlScrape)")
    else:
        scrape = PlainScrape()
        log.info("provider scrape: LIVE (PlainScrape, keyless fallback)")

    # D11 wikipedia lane is keyless — in a live env it is always live.
    wiki = WikipediaKnowledge()
    log.info("provider wiki: LIVE (WikipediaKnowledge, keyless)")

    # Social publish + analytics (D8): PostProxy primary (per-post-volume
    # pricing), Ayrshare fallback vendor — switching stays a config change.
    if settings.postproxy_api_key:
        social = PostProxyConnector(settings)
        log.info("provider social: LIVE (PostProxyConnector)")
    elif settings.ayrshare_api_key:
        social = AyrshareConnector(settings)
        log.info("provider social: LIVE (AyrshareConnector, fallback vendor)")
    elif fallback is not None:
        social = fallback.social
        log.info("provider social: fallback (%s) — no postproxy/ayrshare key", type(social).__name__)
    else:
        social = PostProxyConnector(settings)
        log.info("provider social: LIVE (PostProxyConnector) — no key, will fail at call time")

    # LLM: availability chain, not a single vendor (Anthropic-out-of-credits
    # degrades to Perplexity). Deliberately NO mock link in live env: a lane
    # failing honestly beats fixture data masquerading as researched fields.
    routers: list = []
    router_names: list[str] = []
    if settings.anthropic_api_key:
        routers.append(AnthropicRouter(settings))
        router_names.append("anthropic")
    if settings.perplexity_api_key:
        routers.append(PerplexityRouter(settings))
        router_names.append("perplexity")
    if not routers:
        llm = fallback.llm if fallback else AnthropicRouter(settings)
        log.info("provider llm: fallback (no anthropic/perplexity key)")
    elif len(routers) == 1:
        llm = routers[0]
        log.info("provider llm: LIVE (%s)", router_names[0])
    else:
        llm = ChainLLMRouter(routers, router_names)
        log.info("provider llm: chain [%s]", " -> ".join(router_names))

    return Providers(
        search=pick("search", SerperSearch(settings), bool(settings.serper_api_key)),
        scrape=scrape,
        news=news,
        peers=pick(
            "peers", CompositePeerData(settings),
            bool(settings.scrapecreators_api_key or settings.youtube_api_key),
        ),
        social=social,
        transcription=pick(
            "transcription", AssemblyAITranscription(settings), bool(settings.assemblyai_api_key)
        ),
        llm=llm,
        wiki=wiki,
        video=pick("video", YouTubeVideo(settings), bool(settings.youtube_api_key)),
        places=pick("places", SerperPlaces(settings), bool(settings.serper_api_key)),
        deep=pick("deep", PerplexityDeepResearch(settings), bool(settings.perplexity_api_key)),
        email=pick("email", ResendEmailProvider(settings), bool(settings.resend_api_key)),
        blog=pick("blog", WebhookBlogPublisher(settings), bool(settings.blog_publish_url)),
    )
