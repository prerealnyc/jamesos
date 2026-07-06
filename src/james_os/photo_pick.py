"""Hero-photo selection gates — James's rejections, verbatim:
"photo has been used before." / "blurry image" / "repettitive image".

Two gates applied at PICK time:
  * reuse memory  — the payload of every queued post records which hero
    photo it used (hero_photo_key); the picker prefers the least-recently
    used photo instead of random/hash choice (zero new DDL — the actions
    table is the memory).
  * sharpness     — a Laplacian-variance gate drops visibly blurry photos
    from the pool (unless EVERY photo fails, then the sharpest wins).
"""

from __future__ import annotations

import hashlib
import io
import random
from uuid import UUID

import numpy as np
from PIL import Image

from .db import acquire

_SHARP_FLOOR = 45.0        # Laplacian variance below this ≈ visibly soft
_USE_WINDOW_DAYS = 21      # how far back "used before" looks


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


def _least_used(keys: list[str], counts: dict[str, int]) -> str:
    lo = min(counts.get(k, 0) for k in keys)
    pool = [k for k in keys if counts.get(k, 0) == lo]
    return random.choice(pool)


async def pick_hero_bytes(
    refs: list[tuple[str, bytes]], tenant_id: UUID | None = None,
) -> tuple[str, bytes] | None:
    """Pick from in-memory photo refs [(name, bytes)] with both gates.
    Returns (hero_photo_key, bytes). The ref NAME (the source URL from
    hero_context) is the reuse key, so bytes- and URL-path uses of the
    same photo share ONE ledger entry; non-URL names fall back to a
    content hash."""
    if not refs:
        return None
    scored = [
        (name if name.startswith("http") else photo_key(b), b,
         sharpness_score(b))
        for name, b in refs
    ]
    sharp = [s for s in scored if s[2] >= _SHARP_FLOOR]
    if not sharp:
        # Everything is soft — use the least-bad rather than nothing.
        sharp = [max(scored, key=lambda s: s[2])]
    counts = await _use_counts(tenant_id)
    key = _least_used([s[0] for s in sharp], counts)
    chosen = next(s for s in sharp if s[0] == key)
    return chosen[0], chosen[1]


_URL_SHARPNESS: dict[str, float] = {}   # hero libraries are small + stable


async def _url_sharpness(url: str) -> float:
    if url not in _URL_SHARPNESS:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get(url, follow_redirects=True)
                r.raise_for_status()
            _URL_SHARPNESS[url] = sharpness_score(r.content)
        except Exception:  # noqa: BLE001 — unreachable photo: don't gate on it
            _URL_SHARPNESS[url] = float("inf")
    return _URL_SHARPNESS[url]


async def pick_hero_url(
    urls: list[str], tenant_id: UUID | None = None,
) -> str | None:
    """Pick from photo URLs with BOTH gates (bytes fetched once per URL and
    the sharpness cached — the 'blurry image' rejections came through this
    photo-post path too)."""
    if not urls:
        return None
    scores = {u: await _url_sharpness(u) for u in urls}
    sharp = [u for u in urls if scores[u] >= _SHARP_FLOOR]
    if not sharp:
        sharp = [max(urls, key=lambda u: scores[u])]
    counts = await _use_counts(tenant_id)
    return _least_used(sharp, counts)


__all__ = ["pick_hero_bytes", "pick_hero_url", "photo_key", "sharpness_score"]
