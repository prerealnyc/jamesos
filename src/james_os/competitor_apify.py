"""Competitor posts from the platforms Xpoz does not cover — via Apify.

Xpoz answers Instagram, TikTok and X. Plenty of competitors live somewhere
else: a B2B agency's real presence is LinkedIn, a creator's is YouTube. Those
peers used to be dropped at the door — "platform not scrapable" — so their
content never reached the shelf, the visual eye never saw it, and no layout was
ever learned from it. For an AI-agency brand that is not an edge case, it is
the whole niche.

One actor per platform, each pay-per-result (no rental), each returning the
same normalised row `competitor_sync._upsert_post` already speaks:

  * YouTube  — `streamers/youtube-scraper`, ~$0.003-0.004 a video. Channel URL
    in, videos out with title, thumbnail, views and likes.
  * LinkedIn — `apimaestro/linkedin-company-posts` for a company page and
    `apimaestro/linkedin-profile-posts` for a person, $0.005 a post. Both want
    a full URL: the bare slug answers "No posts found or wrong input", so the
    URL is built here rather than hoped for.

A handle does not say whether it is a company or a person, so LinkedIn tries
the company page first and falls back to the profile. A run that finds nothing
costs nothing (pay per RESULT), which is what makes the fallback affordable.

Never raises. Every failure comes back as (rows, error) so one dead account
cannot sink a sync — the same contract as the Xpoz path.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# What this module can fetch. Xpoz owns instagram/tiktok/twitter; these are
# the ones it cannot reach.
PLATFORMS = ("youtube", "linkedin")

_BASE = "https://api.apify.com/v2"
_ACTORS = {
    "youtube": "streamers~youtube-scraper",
    "linkedin_company": "apimaestro~linkedin-company-posts",
    "linkedin_profile": "apimaestro~linkedin-profile-posts",
}
# An actor run is a scrape, not a request: minutes, not seconds.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_RUN_SECONDS = 240


def configured() -> bool:
    return bool((settings.apify_api_key or "").strip())


def _int(v: Any) -> int:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _when(v: Any) -> datetime | None:
    """Whatever the actor sent, as an aware datetime — or None."""
    if isinstance(v, dict):  # LinkedIn: {"date": "...", "timestamp": ms}
        if v.get("timestamp"):
            try:
                return datetime.fromtimestamp(float(v["timestamp"]) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
        v = v.get("date")
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    s = str(v or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# The actor runs made inside a collect_runs() block. Every run here is paid
# per result, and none of it reached the spend ledger: the only trace of a
# LinkedIn sync that ran two actors was the posts it stored. The caller
# (competitor_sync) is the one that knows how many of those posts were NEW, so
# the runs are handed back to it to record rather than recorded here.
_RUNS: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "competitor_apify_runs", default=None)


@contextmanager
def collect_runs():
    """Yield a list that fills with {actor, items, error} — one entry per
    actor run attempted inside the block (fetch_posts may run two)."""
    runs: list[dict] = []
    token = _RUNS.set(runs)
    try:
        yield runs
    finally:
        _RUNS.reset(token)


def _note_run(actor: str, items: int, error: str) -> None:
    runs = _RUNS.get()
    if runs is not None:
        runs.append({"actor": actor, "items": int(items), "error": error or ""})


async def _run(actor: str, payload: dict) -> tuple[list[dict], str]:
    """Run one actor to completion and return its dataset items."""
    key = (settings.apify_api_key or "").strip()
    if not key:
        return [], "no Apify token configured"
    items, err = await _run_call(actor, key, payload)
    _note_run(actor, len(items), err)
    return items, err


async def _run_call(actor: str, key: str, payload: dict) -> tuple[list[dict], str]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{_BASE}/acts/{actor}/run-sync-get-dataset-items",
                             params={"token": key, "timeout": _RUN_SECONDS}, json=payload)
    except Exception as e:  # noqa: BLE001 — a dead actor is not an outage
        return [], f"{type(e).__name__}"
    if r.status_code >= 400:
        return [], f"actor HTTP {r.status_code}: {r.text[:140]}"
    try:
        items = r.json()
    except ValueError:
        return [], "actor returned no JSON"
    return ([i for i in items if isinstance(i, dict)], "") if isinstance(items, list) else ([], "")


def youtube_url(handle: str) -> str:
    """A channel URL from whatever we hold: a URL, a channel id, or a handle."""
    h = (handle or "").strip().lstrip("@")
    if h.startswith("http"):
        return h
    if len(h) == 24 and h.startswith("UC"):
        return f"https://www.youtube.com/channel/{h}"
    return f"https://www.youtube.com/@{h}"


def linkedin_urls(handle: str) -> tuple[str, str]:
    """(company url, profile url) for a stored LinkedIn handle.

    The actors refuse a bare slug, and a handle never says which kind it is."""
    h = (handle or "").strip().lstrip("@")
    if h.startswith("http"):
        return h, h
    return (f"https://www.linkedin.com/company/{h}/",
            f"https://www.linkedin.com/in/{h}/")


def _youtube_row(handle: str, r: dict) -> dict | None:
    vid = str(r.get("id") or "").strip()
    url = str(r.get("url") or "").strip() or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
    if not vid and not url:
        return None
    return {
        "post_id": vid or url,
        "url": url,
        "caption": str(r.get("title") or ""),
        "media_type": "video",
        # YouTube gives no downloadable file; the thumbnail IS the still the
        # visual eye reads, so it is what we keep.
        "media_url": "",
        "thumbnail_url": str(r.get("thumbnailUrl") or r.get("thumbnail") or ""),
        "duration": _int(r.get("duration")),
        "likes": _int(r.get("likes") or r.get("likeCount")),
        "comments": _int(r.get("commentsCount") or r.get("commentCount")),
        "shares": 0,
        "views": _int(r.get("viewCount") or r.get("views")),
        "posted_at": _when(r.get("date") or r.get("uploadDate") or r.get("publishedAt")),
    }


def _linkedin_row(handle: str, r: dict) -> dict | None:
    urn = str(r.get("full_urn") or r.get("activity_urn") or "").strip()
    url = str(r.get("post_url") or "").strip()
    if not urn and not url:
        return None
    media = r.get("media") if isinstance(r.get("media"), dict) else {}
    kind = str(media.get("type") or "").lower()
    first = ""
    for item in (media.get("items") or []):
        if isinstance(item, dict) and item.get("url"):
            first = str(item["url"])
            break
    stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
    return {
        "post_id": urn or url,
        "url": url,
        "caption": str(r.get("text") or ""),
        # An image post is a still we can read a layout from; a video post is
        # a video; a text-only post is neither and says so.
        "media_type": "image" if kind == "image" else ("video" if kind == "video" else "text"),
        "media_url": first if kind == "image" else "",
        "thumbnail_url": first,
        "duration": 0,
        "likes": _int(stats.get("total_reactions") or stats.get("like")),
        "comments": _int(stats.get("comments")),
        "shares": _int(stats.get("reposts")),
        "views": 0,
        "posted_at": _when(r.get("posted_at")),
    }


def _recent(rows: list[dict], days: int) -> list[dict]:
    """Keep undated posts — an actor that omits a date is not evidence the
    post is old, and dropping them would silently empty a shelf."""
    if days <= 0:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [r for r in rows if r.get("posted_at") is None or r["posted_at"] >= cutoff]


def _window(rows: list[dict], days: int) -> tuple[list[dict], str]:
    """Apply the date window, and SAY SO when it is what emptied the result.

    A company page that posts monthly has nothing inside 90 days, and
    "fetched 0, no error" reads as a broken scraper rather than a quiet
    account — which is exactly how it read the first time."""
    kept = _recent(rows, days)
    if rows and not kept:
        return [], f"found {len(rows)} posts, all older than {days} days"
    return kept, ""


async def fetch_posts(platform: str, handle: str, limit: int = 24,
                      days: int = 120) -> tuple[list[dict], str]:
    """A competitor's own recent posts. Returns (rows, error); never raises."""
    platform = (platform or "").strip().lower()
    handle = (handle or "").strip().lstrip("@")
    if platform not in PLATFORMS:
        return [], f"{platform or 'unknown'} is not an Apify platform"
    if not handle:
        return [], "no handle"
    if not configured():
        return [], "no Apify token configured"
    limit = max(1, min(int(limit or 24), 50))

    if platform == "youtube":
        items, err = await _run(_ACTORS["youtube"], {
            "startUrls": [{"url": youtube_url(handle)}],
            "maxResults": limit,
            # Subtitles are a separate (and slower) product; the shelf wants posts.
            "transcriptionAndSubtitle": "NONE",
        })
        if err:
            return [], err
        rows = [x for x in (_youtube_row(handle, i) for i in items) if x]
        return _window(rows, days)

    # LinkedIn: company page first, then the person. A run that finds nothing
    # costs nothing, which is what makes trying twice reasonable.
    company_url, profile_url = linkedin_urls(handle)
    items, err = await _run(_ACTORS["linkedin_company"],
                            {"company_name": company_url, "limit": limit, "page_number": 1})
    rows = [x for x in (_linkedin_row(handle, i) for i in items) if x]
    if not rows:
        items2, err2 = await _run(_ACTORS["linkedin_profile"],
                                  {"username": profile_url, "limit": limit, "page_number": 1})
        rows = [x for x in (_linkedin_row(handle, i) for i in items2) if x]
        err = err or err2
    if not rows and not err:
        err = "no posts found for that LinkedIn handle (company or profile)"
    if not rows:
        return [], err
    return _window(rows, days)


__all__ = ["PLATFORMS", "configured", "fetch_posts", "youtube_url", "linkedin_urls",
           "collect_runs"]
