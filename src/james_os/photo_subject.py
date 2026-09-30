"""What a hero photo is OF, and choosing one that matches the headline.

The gap this closes: nothing in the system knew what its own photos showed. The
hero picker ranked by layout fit (edge energy under the copy) and then rotated
by least-recently-used — both questions about SHAPE. Measured 2026-09-28,
"Unforgettable Dubai Awaits" rendered over Phang Nga limestone karst, and no
component was misbehaving; none of them had any idea what the picture was.

TWO STAGES, and the split is the point.
  * PRECOMPUTE, once per photo: a vision model writes a factual caption, which
    is embedded and stored. Costs a fraction of a cent per photo, forever.
  * RENDER: embed the headline, compare against the stored vectors, and NARROW
    the pool. Sub-millisecond, no model call on the image.

NARROW, NEVER CHOOSE. Collapsing to a single best photo would silently end the
rotation that stops a brand posting the same picture every week — a regression
`suited_photos` already recorded once and this must not repeat. The subject pass
cuts the obviously-wrong photos and hands the rest on; layout fit and the
least-recently-used rotation still decide.

REFUSES TO GUESS. Two gates, because a cosine threshold alone is not meaningful:
an absolute floor, and a SEPARATION test — if every photo scores about the same,
the library cannot answer this query and the honest move is to change nothing.
And it will not rank on stub embeddings at all: StubEmbedder returns a seeded
fake vector, which would produce confident, meaningless orderings that look
exactly like working code.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import httpx

from .config import settings
from .db import acquire

logger = logging.getLogger(__name__)

_VISION_MODEL = "gpt-4o-mini"
_TIMEOUT = 45.0

# Keep at least this many photos in the pool, however decisive the ranking. The
# rotation downstream needs somewhere to rotate.
_KEEP_MIN = 4

# CALIBRATED, not chosen. Measured 2026-09-29 against Turtleback's real 40-photo
# library with voyage-3-large: ten on-niche queries and ten deliberately
# off-niche ones.
#
#   MEAN similarity   on-niche 0.602-0.775   off-niche 0.516-0.641
#   BEST similarity   on-niche 0.618-0.847   off-niche 0.537-0.672
#
# Two things that measurement killed:
#
#  * A standout test (best >= mean + k*sigma) is BACKWARDS here. An irrelevant
#    query scores low but very tightly (sd ~0.01), so any small deviation looks
#    like a huge standout: off-niche queries averaged z=2.07 against on-niche
#    z=1.55, and "a downtown loft apartment" scored the highest z of all twenty.
#    It was why "a Dubai city break" narrowed a library of golf courses.
#  * BEST alone cannot separate either — the ranges overlap (0.618 vs 0.672).
#
# The MEAN separates best of the signals available, so that is the gate, set
# just under the on-niche floor. It is not clean: two off-niche queries clear it.
# That is tolerable precisely because the failure is mild — when a library holds
# nothing relevant, EVERY photo in it is equally wrong, so narrowing changes
# which wrong photo, not whether the photo is wrong. The gate exists to stop
# pointless loss of rotation, not to prevent a bad match it cannot prevent.
#
# Model-specific: voyage-3-large runs a high baseline (unrelated text still
# scores ~0.55). Re-measure before changing embedding model.
_MIN_MEAN_SIM = 0.60
# How far above the pack a photo must be to stay in the narrowed pool.
_KEEP_ABOVE_SD = 0.5

_SYSTEM = (
    "You describe a photograph factually for a media library. Never flatter, "
    "never sell, never guess. If you cannot recognise the place, say so with an "
    "empty string rather than naming somewhere plausible — a confident wrong "
    "place is far worse here than an honest blank."
)
_PROMPT = (
    'Return JSON: {"caption": one factual sentence, "place": the named city or '
    'region if you genuinely recognise it else "", "subject": what is in the '
    'foreground, "setting": indoor/outdoor and the kind of location, '
    '"tags": 3-8 short lowercase keywords}.'
)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json"}


async def describe(url: str) -> dict:
    """A factual description of one photo, or {} on any failure."""
    if not url or not (settings.openai_api_key or "").strip():
        return {}
    body = {
        "model": _VISION_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                # detail:"low" is a flat token cost and plenty to say what a
                # picture is of; "high" tiles the image and buys nothing here.
                {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
            ]},
        ],
        "max_tokens": 250,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post("https://api.openai.com/v1/chat/completions",
                             headers=_headers(), json=body)
            r.raise_for_status()
            data = r.json()
        return json.loads((data["choices"][0]["message"]["content"] or "").strip())
    except Exception:  # noqa: BLE001 — an unread photo is not an outage
        logger.warning("could not describe %s", url[:120], exc_info=True)
        return {}


def searchable(desc: dict) -> str:
    """The one string that gets embedded. Place first because it is the field
    that was actually wrong — a caption mentioning "turquoise water" matches a
    Dubai headline about as well as a Thailand one, and the place name is what
    breaks the tie."""
    if not isinstance(desc, dict):
        return ""
    parts = [str(desc.get("place") or ""), str(desc.get("subject") or ""),
             str(desc.get("setting") or ""), str(desc.get("caption") or "")]
    tags = desc.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags[:8])
    return " · ".join(p.strip() for p in parts if str(p).strip())[:800]


def _real_embedder():
    """The embedder, or None when it would return fakes.

    StubEmbedder yields a seeded vector per string: same text gives the same
    vector, different text a different one, so every plumbing check passes and
    the rankings are noise. Ranking on that is worse than not ranking, because
    it looks like it is working."""
    try:
        from .embedder import get_embedder

        emb = get_embedder()
        if getattr(emb, "model_name", "") == "stub":
            return None
        return emb
    except Exception:  # noqa: BLE001
        return None


async def backfill(tenant_id: UUID | str | None, *, limit: int = 50) -> dict:
    """Describe and embed hero photos that have never been read. Idempotent."""
    emb = _real_embedder()
    if emb is None:
        return {"read": 0, "skipped": 0, "reason": "no real embedder configured"}
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, uri FROM media_assets "
            " WHERE role = 'hero_photo' AND uri <> '' AND subject_read_at IS NULL "
            " ORDER BY created_at DESC LIMIT $1", max(1, min(int(limit), 500)))
    read = failed = 0
    for row in rows:
        desc = await describe(row["uri"])
        text = searchable(desc)
        if not text:
            # Stamp it anyway: an unreadable photo re-read on every backfill
            # would pay the same vision call forever for the same non-answer.
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE media_assets SET subject_read_at = now() WHERE id = $1", row["id"])
            failed += 1
            continue
        try:
            vec = (await emb.embed([text]))[0]
        except Exception:  # noqa: BLE001
            logger.warning("could not embed a photo description", exc_info=True)
            continue
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE media_assets SET subject_caption = $2, "
                "subject_embedding = $3::vector, subject_read_at = now(), "
                "updated_at = now() WHERE id = $1",
                # A plain list of floats: db.acquire registers pgvector's
                # asyncpg codec on every connection (db.py:48), so formatting
                # this as a string makes asyncpg try to parse it as a float and
                # fail — the codec wants the values, not their textual form.
                row["id"], text, vec)
        read += 1
    return {"read": read, "unreadable": failed, "queued": len(rows)}


def _narrow(scored: list[tuple[float, str]]) -> set[str] | None:
    """Which URIs survive, or None when the library cannot answer the query.

    See the constants above for why this gates on the MEAN and not on a
    standout score — the standout test was measured to be backwards.
    """
    if len(scored) < 2:
        return None
    sims = [s for s, _ in scored]
    mean = sum(sims) / len(sims)
    if mean < _MIN_MEAN_SIM:
        return None                      # nothing here is about this subject
    var = sum((s - mean) ** 2 for s in sims) / len(sims)
    sigma = var ** 0.5
    if sigma <= 1e-6:
        return None                      # the library genuinely has no preference
    cut = mean + _KEEP_ABOVE_SD * sigma
    keep = {uri for s, uri in scored if s >= cut}
    if len(keep) < _KEEP_MIN:
        keep = {uri for _s, uri in sorted(scored, reverse=True)[:_KEEP_MIN]}
    return keep or None


async def subject_ranked(
    tenant_id: UUID | str | None, topic: str,
    refs: list[tuple[str, bytes]],
) -> list[tuple[str, bytes]]:
    """`refs` narrowed to the photos that are actually about `topic`.

    Returns refs UNCHANGED whenever it cannot improve on them — no embedder, no
    descriptions stored, a topic too thin to match on, or a library with no
    opinion. Never raises, never empties the pool.
    """
    if not refs or len(refs) <= _KEEP_MIN or not (topic or "").strip():
        return refs
    emb = _real_embedder()
    if emb is None:
        return refs
    try:
        async with acquire(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT uri, subject_embedding FROM media_assets "
                " WHERE role = 'hero_photo' AND subject_embedding IS NOT NULL")
        if len(rows) < 2:
            return refs
        # The codec hands these back as a sequence of floats already.
        known = {r["uri"]: list(r["subject_embedding"]) for r in rows
                 if r["subject_embedding"] is not None}
        have = [(name, b) for name, b in refs if name in known]
        if len(have) < 2:
            return refs

        qvec = (await emb.embed([topic.strip()[:400]]))[0]
        qn = sum(v * v for v in qvec) ** 0.5 or 1.0
        scored = []
        for name, _b in have:
            v = known[name]
            dot = sum(a * b for a, b in zip(qvec, v))
            vn = sum(x * x for x in v) ** 0.5 or 1.0
            scored.append((dot / (qn * vn), name))
        keep = _narrow(scored)
        if keep is None:
            return refs
        out = [(n, b) for n, b in refs if n in keep]
        return out or refs
    except Exception:  # noqa: BLE001 — a ranking miss must never cost the post
        logger.warning("subject ranking failed; using the library as-is", exc_info=True)
        return refs


__all__ = ["describe", "searchable", "backfill", "subject_ranked"]
