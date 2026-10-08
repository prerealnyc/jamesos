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
    '"tags": 3-8 short lowercase keywords, "people": how many people are '
    'clearly visible (0 if none)}.'
)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json"}


NO_ANSWER = "_error"   # key describe() sets when the model was never heard from
# A NO_ANSWER reason starting with this is about ONE picture (OpenAI could not
# download it), not the engine: backfill skips past it, the photo stays unread.
PICTURE_RETRY = "picture:"

# Account-wide answers, whatever the status code says: never about the picture.
_ENGINE_CODES = frozenset({
    "insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
    "model_not_found", "rate_limit_exceeded", "invalid_api_key",
    "account_deactivated", "unsupported_country_region_territory"})
# 400 codes / message words that name the IMAGE itself: a final answer.
_PICTURE_CODES = frozenset({"invalid_image_format", "image_parse_error", "invalid_image",
                            "image_too_large", "unsupported_image"})
_PICTURE_WORDS = ("unsupported image", "invalid image", "image_too_large", "image too large")
# 400 that says OpenAI's own fetch of the image failed: often transient.
_DOWNLOAD_CODES = frozenset({"invalid_image_url"})
_DOWNLOAD_WORDS = ("timeout while downloading", "error while downloading", "invalid image url")


def _error_of(response) -> tuple[str, str, str]:
    """(error.code, error.type, error.message) of an OpenAI error body, lowercased."""
    try:
        err = response.json().get("error")
    except Exception:  # noqa: BLE001 — a body that is not JSON names nothing
        return "", "", ""
    if isinstance(err, str):
        return "", "", err.lower()
    if not isinstance(err, dict):
        return "", "", ""
    return tuple(str(err.get(k) or "").strip().lower() for k in ("code", "type", "message"))


def _http_verdict(status: int, response) -> dict:
    """What one HTTP error from chat/completions says, as describe() answers it.

    {} (final, stamps the photo) ONLY when the answer is about this picture: 413,
    415, or a 400 that names the image. A 400 saying OpenAI could not DOWNLOAD
    the picture is a picture-scoped retry. Everything else (401/403, 404
    model_not_found, 408/409/429, quota and billing, any 5xx) is account-wide:
    NO_ANSWER, and backfill stops. A 400 on neither list is a picture-scoped retry."""
    code, etype, msg = _error_of(response)
    tag = f"http_{status}" + (f":{code}" if code else "")
    if code in _ENGINE_CODES or etype in _ENGINE_CODES:
        return {NO_ANSWER: tag}
    if status in (413, 415):
        return {}
    if status == 400:
        if code in _DOWNLOAD_CODES or any(w in msg for w in _DOWNLOAD_WORDS):
            return {NO_ANSWER: PICTURE_RETRY + tag}
        if code in _PICTURE_CODES or any(w in msg for w in _PICTURE_WORDS):
            return {}
        # A 400 not on either list (a content-policy refusal of this image, a
        # new code) most likely concerns the picture: left unread, but never a
        # reason to stop the backfill at the head of the queue.
        return {NO_ANSWER: PICTURE_RETRY + tag}
    return {NO_ANSWER: tag}


async def describe(url: str) -> dict:
    """A factual description of one photo.

    {} means the model answered but said nothing usable (a refusal, a blank or
    unparseable reply) — a final answer for this picture. {NO_ANSWER: reason}
    means no answer was obtained at all (no key, HTTP 429/5xx, a timeout, a
    network fault, any account-wide 4xx): the photo must stay unread so a later
    pass retries it. Only an answer about THIS picture (413, 415, a 400 naming
    the image) is final: {}. A 400 saying the image could not be downloaded is
    {NO_ANSWER: 'picture:...'}: unread, but not a reason to stop a backfill."""
    if not url:
        return {}
    if not (settings.openai_api_key or "").strip():
        return {NO_ANSWER: "no_key"}
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
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        got = _http_verdict(code, exc.response)
        if not got:
            # The API refused THIS picture (a format it cannot read): a final
            # answer, or one bad photo blocks the queue.
            logger.info("vision refused %s: HTTP %s", url[:120], code)
        else:
            logger.warning("could not describe %s: %s", url[:120], got[NO_ANSWER])
        return got
    except Exception as exc:  # noqa: BLE001 — an unread photo is not an outage
        logger.warning("could not describe %s", url[:120], exc_info=True)
        return {NO_ANSWER: type(exc).__name__}
    try:
        out = json.loads((data["choices"][0]["message"]["content"] or "").strip())
    except Exception:  # noqa: BLE001 — the model answered, just not usefully
        logger.info("unusable description for %s", url[:120])
        return {}
    return out if isinstance(out, dict) else {}


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


def has_person(desc: dict) -> bool | None:
    """Does the description say a person is in the picture? None = it did not say."""
    if not isinstance(desc, dict) or "people" not in desc:
        return None
    try:
        return int(desc.get("people") or 0) > 0
    except (TypeError, ValueError):
        return None


async def read_one(media_id, uri: str, tenant_id: UUID | str | None, *, emb=None) -> dict:
    """Describe and embed ONE hero photo, and write the result back.

    Factored out of backfill so a photo is captioned the moment it arrives (an
    upload's analysis job, an own-post import) instead of waiting for a batch
    nobody calls. Returns {"status": "read"|"unreadable"|"retry"|"no_embedder"|
    "embed_failed", "caption": str, "has_person": bool|None}. Never raises.
    Only "read" and "unreadable" (the model answered) stamp subject_read_at.
    """
    emb = emb if emb is not None else _real_embedder()
    if emb is None:
        return {"status": "no_embedder", "caption": "", "has_person": None}
    try:
        desc = await describe(uri)
        if isinstance(desc, dict) and desc.get(NO_ANSWER):
            # No answer (no key, 429/5xx, timeout): leave subject_read_at NULL
            # so the next backfill reads it — stamping it would bury the photo
            # uncaptioned for good.
            return {"status": "retry", "caption": "", "has_person": None,
                    "reason": str(desc.get(NO_ANSWER))}
        text = searchable(desc)
        person = has_person(desc)
        if not text:
            # Stamp it anyway: an unreadable photo re-read on every backfill
            # would pay the same vision call forever for the same non-answer.
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE media_assets SET subject_read_at = now() WHERE id = $1", media_id)
            return {"status": "unreadable", "caption": "", "has_person": None}
        try:
            vec = (await emb.embed([text]))[0]
        except Exception:  # noqa: BLE001
            logger.warning("could not embed a photo description", exc_info=True)
            return {"status": "embed_failed", "caption": text, "has_person": person}
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE media_assets SET subject_caption = $2, "
                "subject_embedding = $3::vector, subject_read_at = now(), "
                "updated_at = now() WHERE id = $1",
                # A plain list of floats: db.acquire registers pgvector's
                # asyncpg codec on every connection (db.py:48), so formatting
                # this as a string makes asyncpg try to parse it as a float and
                # fail — the codec wants the values, not their textual form.
                media_id, text, vec)
        if person is not None:
            # Its own statement: has_person arrives with migration 072, and a
            # library that has not had it yet must still get its captions.
            try:
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE media_assets SET has_person = $2 WHERE id = $1",
                        media_id, person)
            except Exception:  # noqa: BLE001
                logger.info("has_person not stored (migration 072 not applied?)")
        return {"status": "read", "caption": text, "has_person": person}
    except Exception:  # noqa: BLE001 — a caption miss must never cost the photo
        logger.warning("could not caption photo %s", media_id, exc_info=True)
        return {"status": "embed_failed", "caption": "", "has_person": None}


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
    read = failed = later = 0
    deferred = ""
    for row in rows:
        got = await read_one(row["id"], row["uri"], tenant_id, emb=emb)
        if got["status"] == "read":
            read += 1
        elif got["status"] == "unreadable":
            failed += 1
        elif got["status"] == "retry":
            if str(got.get("reason") or "").startswith(PICTURE_RETRY):
                # OpenAI could not fetch THIS picture: it stays unread for a
                # later pass, and the photos behind it are still read now.
                later += 1
                continue
            # The vision model is not answering (no key, rate limit, outage):
            # stop here — the rest stay unread and the next pass picks them up.
            deferred = got.get("reason") or "no_answer"
            break
    out = {"read": read, "unreadable": failed, "queued": len(rows)}
    if later:
        out["picture_retry"] = later
    if deferred:
        out["deferred"] = deferred
    return out


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


__all__ = ["describe", "searchable", "backfill", "read_one", "has_person", "subject_ranked"]
