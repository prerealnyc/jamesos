"""Influencer / trend scraping via Apify.

Finds what's performing on Instagram, TikTok and YouTube — by topic
(discovery) or for specific creators (watchlist) — and normalizes wildly
different actor outputs into one `TrendItem` shape. The trend layer
(`trends.py`) then turns those into memory events and scores virality.

Design rules (same honesty contract as research.py / video.py):

  * Provider-abstracted. `StubTrendProvider` proves the discover → ingest →
    rank → script loop with NO key and NO network — it returns an
    obviously-labelled placeholder, never invented "viral" posts dressed up
    as real data.
  * `ApifyTrendProvider` is the real engine. One Apify token drives all
    three platforms via their best-in-class actors, including YouTube
    transcripts (the raw material for replicating a format in-voice).
  * Field extraction is defensive: actor outputs differ and drift, so every
    field is pulled with fallbacks and missing data stays empty rather than
    guessed.

Apify is called through the synchronous run-and-get-dataset-items endpoint
so a discovery returns inline. Result limits are kept modest to stay inside
the sync window; large back-catalog scrapes are a later async-job concern.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from .config import settings

TREND_CATEGORY = "trend"
PLATFORMS = ("instagram", "tiktok", "youtube")

_APIFY_BASE = "https://api.apify.com/v2"
# Apify actor ids use ~ in the path (org~name).
_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "tiktok": "clockworks~tiktok-scraper",
    "youtube": "streamers~youtube-scraper",
}
# Discovery can run a real scrape across platforms; give it room but cap it
# so a single call can't hang the request indefinitely.
_TIMEOUT = httpx.Timeout(280.0, connect=10.0)


@dataclass
class TrendItem:
    platform: str
    handle: str            # creator username/channel
    url: str               # canonical post/video url
    caption: str = ""      # caption / title — the hook + framing
    transcript: str = ""   # YouTube subtitles when available (replication fuel)
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    posted_at: str = ""    # ISO8601 if the actor provided it
    thumbnail: str = ""
    duration: int = 0      # seconds
    extra: dict = field(default_factory=dict)

    def dedupe_basis(self) -> str:
        return self.url or f"{self.platform}:{self.handle}:{self.caption[:40]}"


class TrendProvider(ABC):
    name: str

    @abstractmethod
    async def discover(
        self, topic: str, platforms: list[str], limit: int
    ) -> list[TrendItem]:
        """Find top-performing posts on a topic across platforms."""
        ...

    @abstractmethod
    async def scrape_handles(
        self, handles: dict[str, list[str]], limit: int
    ) -> list[TrendItem]:
        """Scrape recent posts for specific creators, keyed by platform."""
        ...


class StubTrendProvider(TrendProvider):
    """No key, no network. Proves the loop without inventing viral data."""

    name = "stub"

    async def discover(
        self, topic: str, platforms: list[str], limit: int
    ) -> list[TrendItem]:
        return []

    async def scrape_handles(
        self, handles: dict[str, list[str]], limit: int
    ) -> list[TrendItem]:
        return []


class ApifyTrendProvider(TrendProvider):
    """Real scraping via Apify's IG/TikTok/YouTube actors."""

    name = "apify"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("APIFY_API_KEY is required for Apify trend scraping")
        self.api_key = api_key

    async def _run_actor(self, actor: str, run_input: dict) -> list[dict]:
        """Run an actor synchronously and return its dataset items."""
        url = f"{_APIFY_BASE}/acts/{actor}/run-sync-get-dataset-items"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                url,
                params={"token": self.api_key},
                json=run_input,
            )
        if r.status_code in (401, 403):
            raise RuntimeError(f"Apify auth failed (HTTP {r.status_code})")
        if r.status_code == 402:
            raise RuntimeError("Apify usage/credit limit reached (HTTP 402)")
        if r.status_code >= 400:
            raise RuntimeError(f"Apify actor {actor} HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return data if isinstance(data, list) else data.get("items", []) or []

    # ── discovery: top posts on a topic ──

    async def discover(
        self, topic: str, platforms: list[str], limit: int
    ) -> list[TrendItem]:
        items: list[TrendItem] = []
        tag = topic.replace("#", "").strip()
        for p in platforms:
            if p not in _ACTORS:
                continue
            try:
                raw = await self._run_actor(_ACTORS[p], self._discover_input(p, tag, limit))
            except Exception:  # noqa: BLE001 — one platform failing must not sink the rest
                continue
            items.extend(self._normalize(p, r) for r in raw)
        return [it for it in items if it.url]

    async def scrape_handles(
        self, handles: dict[str, list[str]], limit: int
    ) -> list[TrendItem]:
        items: list[TrendItem] = []
        for p, hs in handles.items():
            if p not in _ACTORS or not hs:
                continue
            try:
                raw = await self._run_actor(_ACTORS[p], self._handles_input(p, hs, limit))
            except Exception:  # noqa: BLE001
                continue
            items.extend(self._normalize(p, r) for r in raw)
        return [it for it in items if it.url]

    # ── per-platform actor inputs ──

    @staticmethod
    def _discover_input(platform: str, tag: str, limit: int) -> dict:
        if platform == "instagram":
            return {
                "search": tag,
                "searchType": "hashtag",
                "searchLimit": 1,
                "resultsType": "posts",
                "resultsLimit": limit,
                "addParentData": False,
            }
        if platform == "tiktok":
            return {
                "searchQueries": [tag],
                "resultsPerPage": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            }
        # youtube
        return {
            "searchKeywords": tag,
            "maxResults": limit,
            "maxResultsShorts": limit,
            "downloadSubtitles": True,
            "saveSubsToKVS": False,
            "subtitlesLanguage": "en",
            "subtitlesFormat": "plaintext",
        }

    @staticmethod
    def _handles_input(platform: str, handles: list[str], limit: int) -> dict:
        clean = [h.lstrip("@").strip() for h in handles if h.strip()]
        if platform == "instagram":
            return {
                "directUrls": [f"https://www.instagram.com/{h}/" for h in clean],
                "resultsType": "posts",
                "resultsLimit": limit,
                "addParentData": False,
            }
        if platform == "tiktok":
            return {
                "profiles": clean,
                "resultsPerPage": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            }
        # youtube — accept @handles, raw channel IDs (UC…22 chars), or
        # full URLs. Channel IDs need /channel/<id>; everything else uses @.
        def _yt_url(h: str) -> str:
            if h.startswith("http"):
                return h
            # YouTube channel IDs are exactly 24 chars starting with "UC".
            if len(h) == 24 and h.startswith("UC"):
                return f"https://www.youtube.com/channel/{h}"
            return f"https://www.youtube.com/@{h}"

        urls = [_yt_url(h) for h in clean]
        return {
            "startUrls": [{"url": u} for u in urls],
            "maxResults": limit,
            "downloadSubtitles": True,
            "saveSubsToKVS": False,
            "subtitlesLanguage": "en",
            "subtitlesFormat": "plaintext",
        }

    # ── normalize: many actor shapes → one TrendItem ──

    @staticmethod
    def _normalize(platform: str, r: dict) -> TrendItem:
        g = r.get  # local alias

        def first(*keys, default=None):
            for k in keys:
                v = g(k)
                if v not in (None, "", []):
                    return v
            return default

        if platform == "instagram":
            return TrendItem(
                platform=platform,
                handle=str(first("ownerUsername", "ownerFullName", default="")),
                url=str(first("url", "postUrl", default="")),
                caption=str(first("caption", default="")),
                views=_int(first("videoViewCount", "videoPlayCount", "viewsCount", default=0)),
                likes=_int(first("likesCount", "likes", default=0)),
                comments=_int(first("commentsCount", "comments", default=0)),
                posted_at=str(first("timestamp", "takenAtTimestamp", default="")),
                thumbnail=str(first("displayUrl", "thumbnailUrl", default="")),
                duration=_int(first("videoDuration", "duration", default=0)),
                extra={"hashtags": g("hashtags") or [], "type": g("type")},
            )
        if platform == "tiktok":
            author = g("authorMeta") or {}
            video = g("videoMeta") or {}
            return TrendItem(
                platform=platform,
                handle=str(first("authorMeta.name", default="") or author.get("name", "")),
                url=str(first("webVideoUrl", "url", default="")),
                caption=str(first("text", "description", default="")),
                views=_int(first("playCount", "viewCount", default=0)),
                likes=_int(first("diggCount", "likes", default=0)),
                comments=_int(first("commentCount", default=0)),
                shares=_int(first("shareCount", default=0)),
                posted_at=str(first("createTimeISO", "createTime", default="")),
                thumbnail=str(video.get("coverUrl", "") or first("covers", default="")),
                duration=_int(video.get("duration", 0)),
                extra={"hashtags": [h.get("name") for h in (g("hashtags") or []) if isinstance(h, dict)]},
            )
        # youtube
        return TrendItem(
            platform=platform,
            handle=str(first("channelName", "channelUsername", default="")),
            url=str(first("url", "videoUrl", default="")),
            caption=str(first("title", default="")),
            transcript=_yt_transcript(r),
            views=_int(first("viewCount", "views", default=0)),
            likes=_int(first("likes", "likeCount", default=0)),
            comments=_int(first("commentsCount", "commentCount", default=0)),
            posted_at=str(first("date", "uploadDate", "publishedAt", default="")),
            thumbnail=str(first("thumbnailUrl", "thumbnail", default="")),
            duration=_int(first("duration", default=0)),
            extra={"channelUrl": g("channelUrl"), "channelId": g("channelId")},
        )


def _int(v) -> int:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _yt_transcript(r: dict) -> str:
    """YouTube actors expose subtitles in several shapes — flatten to text."""
    subs = r.get("subtitles") or r.get("captions") or r.get("text")
    if isinstance(subs, str):
        return subs[:8000]
    if isinstance(subs, list):
        parts: list[str] = []
        for s in subs:
            if isinstance(s, str):
                parts.append(s)
            elif isinstance(s, dict):
                parts.append(str(s.get("text") or s.get("plaintext") or ""))
        return " ".join(p for p in parts if p)[:8000]
    return ""


def make_trend_provider() -> TrendProvider:
    key = (settings.apify_api_key or "").strip()
    return ApifyTrendProvider(api_key=key) if key else StubTrendProvider()


_provider: TrendProvider | None = None


def get_trend_provider() -> TrendProvider:
    global _provider
    if _provider is None:
        _provider = make_trend_provider()
    return _provider


__all__ = [
    "TrendItem", "TrendProvider", "get_trend_provider", "make_trend_provider",
    "TREND_CATEGORY", "PLATFORMS",
]
