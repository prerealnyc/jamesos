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


_GENERIC_ANCHOR = "modern professional"


def _build_query(raw: str, fallback: str = "") -> str:
    """A short concrete-noun query from a topic/brief. Strips hashtags, mentions,
    URLs, emoji and punctuation, keeps the first few real words; if nothing
    concrete remains, anchors to `fallback` — THIS brand's industry/place (see
    tenant_anchor) — so results stay on-theme. Never the global
    settings.brand_industry: that is one brand's text (Tenant Zero's real estate), and
    a golf course whose topic was all hashtags got suburban houses."""
    q = _STRIP.sub(" ", raw or "")
    q = _NON_WORD.sub(" ", q)
    words = [w for w in q.split() if len(w) > 2][:6]
    out = " ".join(words).strip()
    if len(out) < 3:
        out = (fallback or _GENERIC_ANCHOR).split(" — ")[0][:80]
    return out[:100]


async def tenant_anchor(tenant_id=None) -> str:
    """The words a thin query falls back to, for THIS tenant.

    The brand's confirmed niche, else its identity's industry, plus its location
    when the profile names one. The operator's own tenant (the default) keeps
    the configured industry text, which is genuinely its own. Anything else with
    no profile gets a neutral anchor — never another brand's industry."""
    from .db import _request_tenant
    # Resolved exactly as db.acquire() resolves it (explicit, else the request's,
    # else the default tenant), so a background render with no request tenant
    # anchors to the tenant its rows are read as — not to nothing.
    tid = tenant_id or _request_tenant.get() or settings.default_tenant_id
    try:
        from .brands import get_brand_profile
        prof = await get_brand_profile(tid) if tid else None
    except Exception:  # noqa: BLE001 — a profile miss must never stop a render
        prof = None
    ident = (prof or {}).get("identity") or {}
    if not isinstance(ident, dict):
        ident = {}
    subject = ""
    if str(ident.get("niche") or "").strip() and ident.get("niche_confirmed_at"):
        subject = str(ident["niche"]).strip()
    subject = subject or str(ident.get("industry") or "").strip()
    place = str(ident.get("location") or ident.get("city") or ident.get("region") or "").strip()
    if not subject and tid and str(tid) == str(settings.default_tenant_id):
        subject = (settings.brand_industry or "").split(" — ")[0]
    out = " ".join(p for p in (subject, place) if p).strip()
    return out[:80] or _GENERIC_ANCHOR


# image URL -> the attribution Unsplash requires for it. Filled by every fetch,
# so a caller that only kept the 2-tuple (the URL is its hero key) can still
# write the credit onto the payload with credit_for(url). Bounded: render
# processes are long-lived.
_CREDITS: dict[str, dict] = {}
_CREDITS_MAX = 512


def credit_for(url: str) -> dict:
    """The Unsplash credit for a photo this process fetched, or {}."""
    return dict(_CREDITS.get(str(url or ""), {}))


def _remember_credit(url: str, credit: dict) -> None:
    if len(_CREDITS) >= _CREDITS_MAX:
        _CREDITS.pop(next(iter(_CREDITS)))
    _CREDITS[url] = credit


async def fetch_unsplash_hero_credited(
    query: str, *, exclude: Sequence[str] = (), orientation: str = "portrait",
    tenant_id=None,
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
        if q == _GENERIC_ANCHOR:
            # The topic left nothing concrete — anchor to THIS brand.
            q = _build_query(query, await tenant_anchor(tenant_id))
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
        _remember_credit(img_url, credit)
        return img_url, data, credit
    except Exception:  # noqa: BLE001 — a stock lookup must never crash a render
        _log.info("unsplash hero fetch failed", exc_info=True)
        return None


async def fetch_unsplash_hero(
    query: str, *, exclude: Sequence[str] = (), orientation: str = "portrait",
    tenant_id=None,
) -> tuple[str, bytes] | None:
    """(image_url, jpeg_bytes) or None — the 2-tuple form for callers that don't
    carry attribution in their return. The credit is NOT dropped: it is kept
    against the URL, and credit_for(url) hands it back. Thin wrapper so the
    license logic lives in one place."""
    got = await fetch_unsplash_hero_credited(query, exclude=exclude, orientation=orientation,
                                             tenant_id=tenant_id)
    if got is None:
        return None
    return got[0], got[1]


__all__ = ["fetch_unsplash_hero", "fetch_unsplash_hero_credited", "credit_for",
           "tenant_anchor"]
