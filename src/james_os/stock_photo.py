"""Stock-photo hero fallback — a real Unsplash photo when a brand's own library
is empty, tried BEFORE the slow gpt-image-1 draw.

Honesty contract: with no UNSPLASH_ACCESS_KEY, or on ANY failure (no results,
HTTP/rate-limit error, unexpected JSON shape, fetch error), this returns None and
the caller falls through to its existing path (a text card, or gpt-image-1). It
never fabricates, and it never raises into a render.

Unsplash API Terms compliance lives HERE so no caller has to remember it:
  - the mandatory download-registration trigger fires for every photo we use, and
  - photographer + Unsplash attribution (UTM-tagged) is returned for the caller
    to persist / surface.

Auth gotcha: Unsplash wants `Authorization: Client-ID <key>`, NOT `Bearer` (the
header shape every other BM1 call uses) — a Bearer header 401s every request.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

import httpx

from .config import settings
from .netguard import url_is_public

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.unsplash.com/search/photos"
_TIMEOUT = 20.0
_ORIENTATIONS = {"landscape", "portrait", "squarish"}

# Unsplash search is keyword/visual, not semantic — strip the parts that turn a
# post caption into junk queries.
_STRIP = re.compile(r"(#\w+|@\w+|https?://\S+)")
_NON_WORD = re.compile(r"[^\w\s-]", re.UNICODE)


def _build_query(raw: str) -> str:
    """A short concrete-noun query from a topic/brief. Strips hashtags, mentions,
    URLs, emoji and punctuation, keeps the first few real words; if nothing
    concrete remains, anchors to the brand's industry so results stay on-theme."""
    q = _STRIP.sub(" ", raw or "")
    q = _NON_WORD.sub(" ", q)
    words = [w for w in q.split() if len(w) > 2][:6]
    out = " ".join(words).strip()
    if len(out) < 3:
        out = (settings.brand_industry or "modern professional").split(" — ")[0][:80]
    return out[:100]


async def fetch_unsplash_hero_credited(
    query: str, *, exclude: Sequence[str] = (), orientation: str = "portrait",
) -> tuple[str, bytes, dict] | None:
    """(image_url, jpeg_bytes, credit) or None. `exclude` holds hero keys already
    used (Unsplash URLs or ids) so a design-QA retry won't re-serve the same photo.
    See the module docstring for the failure/compliance contract."""
    key = (settings.unsplash_access_key or "").strip()
    if not key:
        return None  # key-gated: no key, no network call
    if orientation not in _ORIENTATIONS:
        orientation = "portrait"
    excl = {str(x) for x in (exclude or ()) if x}
    headers = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    try:
        q = _build_query(query)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(
                _SEARCH_URL,
                params={"query": q, "per_page": 10, "orientation": orientation,
                        "content_filter": "high", "page": 1},
                headers=headers,
            )
            if r.status_code in (401, 403, 429):
                _log.warning("unsplash search returned %s for query %r", r.status_code, q)
                return None
            r.raise_for_status()
            results = (r.json() or {}).get("results") or []

        chosen = None
        for res in results:
            if not isinstance(res, dict):
                continue
            url = str((res.get("urls") or {}).get("regular") or "")
            rid = str(res.get("id") or "")
            if not url or url in excl or (rid and rid in excl):
                continue
            chosen = res
            break
        if chosen is None:
            return None

        img_url = str((chosen.get("urls") or {}).get("regular") or "")
        # SSRF defence-in-depth (images.unsplash.com is public → passes).
        if not img_url or not await url_is_public(img_url):
            return None

        # The CDN image fetch does NOT count against the api.unsplash.com rate limit.
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
            ir = await c.get(img_url)
            ir.raise_for_status()
            data = ir.content
        if not data:
            return None

        # MANDATORY (Unsplash API Terms): register the download for every photo we
        # use. Best-effort — a trigger failure must never block or crash the render.
        dl = str((chosen.get("links") or {}).get("download_location") or "")
        if dl:
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                    await c.get(dl, headers=headers)
            except Exception:  # noqa: BLE001 — best-effort trigger
                pass

        user = chosen.get("user") or {}
        uhtml = str((user.get("links") or {}).get("html") or "")
        credit = {
            "source": "unsplash",
            "photographer": str(user.get("name") or "").strip(),
            "photographer_url": (uhtml + "?utm_source=james_os&utm_medium=referral") if uhtml else "",
            "photo_url": str((chosen.get("links") or {}).get("html") or ""),
            "download_triggered": bool(dl),
        }
        return img_url, data, credit
    except Exception:  # noqa: BLE001 — a stock lookup must never crash a render
        _log.info("unsplash hero fetch failed", exc_info=True)
        return None


async def fetch_unsplash_hero(
    query: str, *, exclude: Sequence[str] = (), orientation: str = "portrait",
) -> tuple[str, bytes] | None:
    """(image_url, jpeg_bytes) or None — the 2-tuple form for callers that don't
    persist attribution. Thin wrapper so the license logic lives in one place."""
    got = await fetch_unsplash_hero_credited(query, exclude=exclude, orientation=orientation)
    if got is None:
        return None
    return got[0], got[1]


__all__ = ["fetch_unsplash_hero", "fetch_unsplash_hero_credited"]
