"""Hero-photo selection gates — James's rejections, verbatim:
"photo has been used before." / "blurry image" / "repettitive image".

Gates applied at PICK time:
  * reuse memory  — the payload of every queued post records which hero
    photo it used (hero_photo_key); the picker prefers the least-recently
    used photo instead of random/hash choice (zero new DDL — the actions
    table is the memory).
  * sharpness     — a Laplacian-variance gate drops visibly blurry photos
    from the pool (unless EVERY photo fails, then the sharpest wins).
  * screenshots   — a phone screenshot (status bar, app chrome) is not a photo;
    refused unless it is all the library has.
  * resolution    — a photo under 1080px on its short side is upscaled onto a
    1080x1350 card, so a big-enough photo wins a TIE among the least used. Only
    a tie-break: as a pre-filter it let one large photo (blurry or not) beat
    every sharp smaller one and win every pick.
"""

from __future__ import annotations

import hashlib
import io
import random
from collections.abc import Collection, Sequence
from uuid import UUID

import numpy as np
from PIL import Image

from .db import acquire

_SHARP_FLOOR = 45.0        # Laplacian variance below this ≈ visibly soft
_USE_WINDOW_DAYS = 21      # how far back "used before" looks
MIN_RENDER_EDGE = 1080     # short side a photo needs to fill a card unscaled

# Native sizes of common phone screenshots (portrait; either orientation counts).
# A camera never produces these exact pixel counts; a screen grab always does.
# 1080x1920 is deliberately absent: it is also every exported 9:16 story image.
_SCREEN_SIZES = {
    (750, 1334), (828, 1792), (1080, 2340), (1080, 2400),
    (1125, 2436), (1170, 2532), (1179, 2556), (1242, 2208), (1242, 2688),
    (1284, 2778), (1290, 2796), (1440, 3040), (1440, 3200),
}
_SCREEN_ASPECT = 1.9       # long/short at or past this is a screen, not a print


def _dims(data: bytes) -> tuple[int, int] | None:
    """(width, height) as displayed (EXIF rotation applied), header-only read."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        try:
            if int(img.getexif().get(0x0112, 1) or 1) in (5, 6, 7, 8):
                w, h = h, w
        except Exception:  # noqa: BLE001
            pass
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return None


def _flat_band(a: np.ndarray) -> bool:
    """A uniform strip — a status bar or app header, not a sky (a sky has
    gradient and noise; a UI bar is one colour to the pixel)."""
    return a.size > 0 and float(a.std()) < 2.0


def looks_like_screenshot(data: bytes) -> bool:
    """A phone screenshot rather than a photograph, judged on what survives a
    re-encode (pixel size and content), never on format or EXIF alone.

    Exact phone-screen pixel sizes, or a screen-tall aspect (>= 1.9) together
    with a flat band across the top (status bar) or bottom (home bar / tab
    bar). Conservative on purpose: a false positive only moves a photo down the
    pecking order, and the gate falls back when nothing else is left."""
    d = _dims(data)
    if d is None:
        return False
    w, h = d
    if (min(w, h), max(w, h)) in _SCREEN_SIZES:
        return True
    if max(w, h) / max(1, min(w, h)) < _SCREEN_ASPECT:
        return False
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
        img.thumbnail((256, 512))
        a = np.asarray(img, dtype=np.float32)
        band = max(2, a.shape[0] // 25)
        return _flat_band(a[:band]) or _flat_band(a[-band:])
    except Exception:  # noqa: BLE001
        return False


def _not_screenshots(items: list, is_shot) -> list:
    """Screenshots out — unless that would leave nothing."""
    photos = [it for it in items if not is_shot(it)]
    return photos or items


def _big_enough(short_edge: int | None) -> bool:
    return short_edge is None or short_edge >= MIN_RENDER_EDGE


# name -> (sharpness, is_screenshot, short_edge) for URL-named refs. A library
# URL names one stored object, so its measure never changes; measuring every
# ref on every pick decoded the whole pool each time, on the event loop.
_REF_MEASURE: dict[tuple[str, int], tuple[float, bool, int | None]] = {}


def _measure_ref(data: bytes) -> tuple[float, bool, int | None]:
    d = _dims(data)
    return sharpness_score(data), looks_like_screenshot(data), (min(d) if d else None)


async def _ref_measures(refs: list[tuple[str, bytes]]) -> list[tuple[float, bool, int | None]]:
    import asyncio

    out = []
    for name, b in refs:
        k = (name, len(b))
        got = _REF_MEASURE.get(k) if name.startswith("http") else None
        if got is None:
            got = await asyncio.to_thread(_measure_ref, b)
            if name.startswith("http"):
                _REF_MEASURE[k] = got
        out.append(got)
    return out


def photo_key(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def sharpness_score(data: bytes) -> float:
    """Variance of the Laplacian on a downscaled grayscale copy — the
    standard cheap blur metric. Returns +inf on decode failure so an
    unreadable file is never *preferred* for being 'sharp = unknown'."""
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
        img.thumbnail((512, 512))
        a = np.asarray(img, dtype=np.float32)
        if a.shape[0] < 3 or a.shape[1] < 3:
            return float("inf")
        lap = (a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
               - 4.0 * a[1:-1, 1:-1])
        return float(lap.var())
    except Exception:  # noqa: BLE001 — never let the gate break a post
        return float("inf")


async def _use_counts(tenant_id: UUID | None) -> dict[str, int]:
    try:
        async with acquire(tenant_id) as conn:
            rows = await conn.fetch(
                f"""SELECT payload->>'hero_photo_key' AS k, count(*) AS n
                      FROM actions
                     WHERE payload ? 'hero_photo_key'
                       AND created_at > now() - interval '{_USE_WINDOW_DAYS} days'
                     GROUP BY 1"""
            )
        return {r["k"]: int(r["n"]) for r in rows if r["k"]}
    except Exception:  # noqa: BLE001
        return {}


def _least_used(keys: list[str], counts: dict[str, int],
                prefer: set[str] | None = None) -> str:
    """The least-used key. `prefer` only breaks ties among the least used — a
    photo big enough for the card wins a tie, but never jumps the rotation (a
    pre-filter on size let one large photo, blurry or not, win every pick)."""
    lo = min(counts.get(k, 0) for k in keys)
    pool = [k for k in keys if counts.get(k, 0) == lo]
    if prefer:
        pool = [k for k in pool if k in prefer] or pool
    return random.choice(pool)


async def pick_hero_bytes(
    refs: list[tuple[str, bytes]], tenant_id: UUID | None = None,
    exclude: Sequence[str] = (),
    prefer: Collection[str] = (),
) -> tuple[str, bytes] | None:
    """Pick from in-memory photo refs [(name, bytes)] with both gates.

    `prefer` (photo keys) narrows the rotation to those keys when any of them
    survives the gates — a hero-led card shows the hero before a scraped venue
    shot, however less used the venue shot is — and is ignored otherwise.
    Returns (hero_photo_key, bytes). The ref NAME (the source URL from
    hero_context) is the reuse key, so bytes- and URL-path uses of the
    same photo share ONE ledger entry; non-URL names fall back to a
    content hash.

    `exclude` drops photo keys from the running BEFORE either gate — a
    regeneration passes the key the owner just rejected so it cannot come
    back. Returns None when excluding empties the pool, which is the honest
    answer ("this library has nothing else"): the caller then changes the
    layout instead of silently re-serving the rejected photo."""
    if not refs:
        return None
    if exclude:
        blocked = set(exclude)
        refs = [(n, b) for n, b in refs
                if (n if n.startswith("http") else photo_key(b)) not in blocked]
        if not refs:
            return None
    # (key, bytes, sharpness, is_screenshot, short_edge)
    scored = [
        (name if name.startswith("http") else photo_key(b), b, *meas)
        for (name, b), meas in zip(refs, await _ref_measures(refs), strict=True)
    ]
    # Order matters: a screenshot is refused outright; then the sharpness
    # floor; then the rotation. Size is only a tie-break inside the rotation.
    scored = _not_screenshots(scored, lambda s: s[3])
    sharp = [s for s in scored if s[2] >= _SHARP_FLOOR]
    if not sharp:
        # Everything is soft — use the least-bad rather than nothing.
        sharp = [max(scored, key=lambda s: s[2])]
    if prefer:
        wanted = set(prefer)
        sharp = [s for s in sharp if s[0] in wanted] or sharp
    counts = await _use_counts(tenant_id)
    key = _least_used([s[0] for s in sharp], counts,
                      prefer={s[0] for s in sharp if _big_enough(s[4])})
    chosen = next(s for s in sharp if s[0] == key)
    return chosen[0], chosen[1]


async def pick_hero_set(
    refs: list[tuple[str, bytes]], tenant_id: UUID | None = None,
    n: int = 1, exclude: Sequence[str] = (),
) -> list[tuple[str, bytes]]:
    """Up to `n` DISTINCT photos, for a layout with several photo regions.

    Repeated application of pick_hero_bytes rather than a second picker: every
    gate that makes a single pick good — the sharpness floor, the
    least-recently-used rotation, the exclusion list — should apply to the
    second photo and the fourth exactly as it does to the first. Feeding each
    chosen key back in as an exclusion is the whole implementation.

    Returns FEWER than `n` when the library has fewer usable photos, and that is
    the honest answer. The renderer cycles what it is given rather than leaving a
    frame empty, so a brand with two good photos gets a four-up collage with two
    repeats instead of two holes.
    """
    out: list[tuple[str, bytes]] = []
    seen = list(exclude)
    for _ in range(max(1, int(n))):
        got = await pick_hero_bytes(refs, tenant_id, exclude=seen)
        if not got:
            break                      # the library is exhausted; say so by stopping
        out.append(got)
        seen.append(got[0])
    return out


_URL_SHARPNESS: dict[str, float] = {}   # hero libraries are small + stable
# url -> (is_screenshot, short_edge or None) — what the render gates need,
# measured on the same single fetch as the sharpness.
_URL_SHAPE: dict[str, tuple[bool, int | None]] = {}


async def _url_sharpness(url: str) -> float:
    if url not in _URL_SHARPNESS:
        try:
            from .netguard import url_is_public
            if not await url_is_public(url, allow_http=True):
                raise ValueError("blocked non-public url")  # SSRF guard
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get(url, follow_redirects=True)
                r.raise_for_status()
            import asyncio
            sharp, shot, short = await asyncio.to_thread(_measure_ref, r.content)
            _URL_SHARPNESS[url] = sharp
            _URL_SHAPE[url] = (shot, short)
        except Exception:  # noqa: BLE001 — unreachable photo: don't gate on it
            _URL_SHARPNESS[url] = float("inf")
    return _URL_SHARPNESS[url]


def _url_shape(u: str) -> tuple[bool, int | None]:
    return _URL_SHAPE.get(u, (False, None))


async def pick_hero_url(
    urls: list[str], tenant_id: UUID | None = None,
    exclude: Sequence[str] = (),
) -> str | None:
    """Pick from photo URLs with BOTH gates (bytes fetched once per URL and
    the sharpness cached — the 'blurry image' rejections came through this
    photo-post path too).

    `exclude` drops URLs before the gates so a regeneration cannot re-serve
    the photo the owner just rejected. On ties `_least_used` picks at random,
    so re-running alone is NOT enough to get a different photo — excluding is
    what makes "it must not come back the same" a guarantee rather than a
    coin flip. Returns None when the exclusion empties the pool."""
    if not urls:
        return None
    if exclude:
        blocked = set(exclude)
        urls = [u for u in urls if u not in blocked]
        if not urls:
            return None
    scores = {u: await _url_sharpness(u) for u in urls}
    urls = _not_screenshots(urls, lambda u: _url_shape(u)[0])
    sharp = [u for u in urls if scores[u] >= _SHARP_FLOOR]
    if not sharp:
        sharp = [max(urls, key=lambda u: scores[u])]
    counts = await _use_counts(tenant_id)
    return _least_used(sharp, counts, prefer={u for u in sharp if _big_enough(_url_shape(u)[1])})


__all__ = ["pick_hero_bytes", "pick_hero_set", "pick_hero_url", "photo_key",
           "sharpness_score", "looks_like_screenshot", "MIN_RENDER_EDGE"]
