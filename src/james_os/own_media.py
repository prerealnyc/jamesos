"""A brand's OWN posted images, taken in and put to two uses.

The owner, 2026-10-08: "let apify scrape images from the brand first, then store
those images as the brand's — then we can make better templates with their own
images as first source. When we do that, we can use that to identify brands
current templates as well — double use."

BM2 owns the scrape (stated handles only, the Apify gate, the BM-published
exclusion). This module owns what happens to ONE image once BM2 has it, through
POST /v1/own-media/import:

    validate -> SSRF-safe download -> sniff -> EXIF transpose -> sha256 + dHash
    -> duplicate? -> ONE vision read (extract_template_spec) -> split:

  retry       the read could not happen (no key, failed, timed out) or the
              database is not ready. HTTP 503. NOTHING is written or stored, so
              BM2 may simply try again.
  duplicate   this picture (exact sha256, or dHash within 6 bits) is already in
              the brand's library or its own templates. No paid read.
  template    a designed post we can draw: kept as the brand's own layout
              (design_templates source_kind 'own') — "the brand's current
              templates". If the same SHAPE was already held as a competitor's
              or niche row, save() relabels it own or reports the match.
  photo       a read that positively says PHOTOGRAPH (photo_forward, not a
              solid background, at most one photo region) with no layout to
              reuse: the ORIGINAL bytes are stored under the brand's own prefix
              as a hero_photo with its provenance (origin, rights 'own_account',
              size, hashes, quality) and captioned immediately.
  undrawable  a designed post the renderer cannot draw, or a read that is not
              a photograph and has no layout either (a text-only card whose
              roles were all dropped, a multi-photo collage). Not stored as a
              picture — but recorded in own_media_seen, so a re-send is never
              paid for again (nor is a template whose shape the brand already
              held).
  rejected    the request or the file itself is wrong (not an image, too small,
              a link that is gone). Final — retrying will not change it.

RIGHTS. Only rights='own_account' is accepted here: the brand's OWN account's
posts. An image the platform merely mentions the brand in is not the brand's to
post and never arrives through this door.

LIKENESS. A photo imported here is origin own_<platform>. hero_context keeps
those OUT of the hero-likeness paths (the hero description, the gpt-image-1
face references), which read origin='owner_upload' only.

TENANT. Every read and write names the request's tenant explicitly; the bytes
are stored under that tenant's prefix — never the default tenant's.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from pydantic import BaseModel, Field

from .db import acquire

logger = logging.getLogger(__name__)

OWN_ORIGINS = ("own_instagram", "own_facebook", "own_tiktok", "own_linkedin")
ORIGIN_PLATFORM = {"own_instagram": "instagram", "own_facebook": "facebook",
                   "own_tiktok": "tiktok", "own_linkedin": "linkedin"}

MAX_BYTES = 20 * 1024 * 1024        # a social post image is a few MB at most
MAX_PIXELS = 40_000_000             # never decode a pixel bomb (BM2 uses the same)
MIN_EDGE = 320                      # below this there is nothing to post or read
ROTATION_EDGE = 1080                # short side a photo needs to fill a card unscaled
SHARP_FLOOR = 45.0                  # photo_pick's floor, measured on full-res bytes
DHASH_NEAR = 6                      # Hamming bits: a re-encode / resize of one picture
FETCH_TIMEOUT_S = 30.0
MAX_REDIRECTS = 4
EXTRACT_TIMEOUT_S = 75.0
_EXTRACT_SEM = asyncio.Semaphore(3)
LEGACY_WAIT_S = 15.0                # an import waits this long for the legacy hashing, at most
LEGACY_ROUNDS_MAX = 50              # rounds of LEGACY_BACKFILL_MAX per background drain
READ_TTL_S = 3600.0                 # an abandoned (timed-out) read is reusable this long
READ_CACHE_MAX = 64
# The palette design_cloner._sanitize fills in when the model gave none: with
# no notes, elements, decorations, logo or photo box it marks a read that said
# nothing at all (an empty {} or a refusal), which is not a verdict.
_SANITIZE_DEFAULT_PALETTE = {"bg": "#111318", "accent": "#c9a24b", "ink": "#ffffff"}

PHOTO_ROLE = "hero_photo"


class OwnMediaImport(BaseModel):
    url: str = Field(..., min_length=8, max_length=4000)
    origin: str
    origin_url: str = Field("", max_length=2000)
    # Must identify the IMAGE, not only the post: a carousel's slides share one
    # permalink, so a slide sends its own media id (or '<shortcode>:<n>').
    origin_ref: str = Field("", max_length=200)
    # Which image of the post (0 = the cover; a carousel's later slides 1..n).
    # None = not sent (BM2's retry pass sends no slide): the post-level legacy
    # check runs only for an EXPLICIT 0, so a missing slide costs at most a
    # deduped re-read, never a later slide taken for its post's cover.
    slide: int | None = Field(None, ge=0, le=100)
    handle: str = Field(..., min_length=1, max_length=120)
    platform: str = Field("", max_length=40)
    taken_at: str = Field("", max_length=64)
    rights: str = "own_account"
    published_by_bm: bool = False
    engagement: float = 0.0


class Refused(Exception):
    """A request or file that is wrong in itself — final (4xx)."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Retryable(Exception):
    """Could not decide now — nothing written (503)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── hashes (ported from BM2 harvest_sources: same bits, same thresholds) ────


def dhash(data: bytes) -> str:
    """64-bit difference hash (Pillow) as 16 hex chars, '' when undecodable."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                return ""  # never decode a pixel bomb (the image gates refuse it first)
            im.draft("L", (144, 128))  # JPEG: decode at a reduced scale; others: no-op
            px = im.convert("L").resize((9, 8)).tobytes()
    except Exception:  # noqa: BLE001
        return ""
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def dhash_flat(h: str) -> bool:
    """A near-flat picture (a solid fill, a plain gradient) has almost no bits
    set either way: its hash names no picture, so it is never compared."""
    try:
        n = bin(int(h, 16)).count("1")
    except (TypeError, ValueError):
        return True
    return n < 4 or n > 60


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def near(a: str, b: str, bits: int = DHASH_NEAR) -> bool:
    """True when two non-flat hashes name the same picture."""
    if not a or not b or dhash_flat(a) or dhash_flat(b):
        return False
    try:
        return hamming(a, b) <= bits
    except (TypeError, ValueError):
        return False


# ── the file ────────────────────────────────────────────────────────────────


def sniff(data: bytes) -> str:
    from .house_harvest import sniff as _sniff
    return _sniff(bytes(data[:16]))


def orientation_of(w: int, h: int) -> str:
    if h * 100 > w * 105:
        return "portrait"
    if w * 100 > h * 105:
        return "landscape"
    return "square"


def _finite(x: float) -> float | None:
    return round(float(x), 2) if isinstance(x, (int, float)) and math.isfinite(x) else None


def measure(data: bytes) -> dict:
    """Everything stored about a picture, read ONCE from its bytes.

    Returns {data, mime, width, height, orientation, sha256, dhash, quality}.
    `data` is what gets stored: the ORIGINAL bytes, or — only when the file
    carries an EXIF rotation — the same pixels turned upright once, at full
    resolution, so nothing downstream ever has to remember to. Raises Refused
    for a file that is not a usable image."""
    from PIL import Image, ImageOps

    from .photo_pick import looks_like_screenshot, sharpness_score

    mime = sniff(data)
    if not mime:
        raise Refused(415, "not_an_image")
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:  # noqa: BLE001
        raise Refused(415, "undecodable") from None
    if w * h > MAX_PIXELS:
        raise Refused(413, "too_many_pixels")
    try:
        exif = img.getexif()
        orient = int(exif.get(0x0112, 1) or 1)
        camera = bool(exif.get(0x010F) or exif.get(0x0110))   # Make / Model
    except Exception:  # noqa: BLE001
        orient, camera = 1, False
    stored = data
    if orient != 1:
        try:
            img.load()
            up = ImageOps.exif_transpose(img)
            buf = io.BytesIO()
            if mime == "image/png" or up.mode in ("RGBA", "LA"):
                up.save(buf, "PNG", optimize=True)
                mime = "image/png"
            else:
                up.convert("RGB").save(buf, "JPEG", quality=95, optimize=True)
                mime = "image/jpeg"
            stored = buf.getvalue()
            w, h = up.size
        except Exception:  # noqa: BLE001
            raise Refused(415, "undecodable") from None
    short = min(w, h)
    if short < MIN_EDGE:
        raise Refused(422, "too_small")
    sharp = sharpness_score(stored)
    screenshot = looks_like_screenshot(stored) or (
        mime == "image/png" and not camera and max(w, h) / max(1, short) >= 1.9)
    reasons = []
    if short < ROTATION_EDGE:
        reasons.append("below_1080")
    if math.isfinite(sharp) and sharp < SHARP_FLOOR:
        reasons.append("soft")
    if screenshot:
        reasons.append("screenshot")
    return {
        "data": stored, "mime": mime, "width": int(w), "height": int(h),
        "orientation": orientation_of(w, h),
        "sha256": hashlib.sha256(stored).hexdigest(), "dhash": dhash(stored),
        "quality": {"sharpness": _finite(sharp), "short_edge": int(short),
                    "screenshot": bool(screenshot), "camera_exif": camera,
                    "in_rotation": not reasons, "reasons": reasons},
    }


def image_key(url: str) -> str:
    """The file name a CDN gives one image (the last path segment), which stays
    the same when the signed query string around it changes."""
    try:
        return urlparse(str(url or "")).path.rsplit("/", 1)[-1].lower()[:200]
    except ValueError:
        return ""


_IG_POST = re.compile(r"instagram\.com/(?:[^/?#]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]{3,64})")


_FB_HOSTS = ("facebook.com", "fb.com", "fb.watch")
# Facebook path segments that name an ENDPOINT, not a post: the post itself is
# in the query (permalink.php?story_fbid=..&id=.., photo.php?fbid=..,
# photo/?fbid=.., watch/?v=.., media/set/?set=..). Any '*.php' segment too.
_FB_GENERIC_SEGMENTS = frozenset({"photo", "watch", "media"})


def _query_identified(canon: str) -> bool:
    """A Facebook link whose post identity lives in the query string, so the
    query-less canon would be one string shared by every such post."""
    try:
        parsed = urlparse(canon)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in _FB_HOSTS):
        return False
    segs = [s.lower() for s in parsed.path.split("/") if s]
    if not segs:
        return True
    return any(s.endswith(".php") or s in _FB_GENERIC_SEGMENTS for s in segs)


def post_key(origin_url: str) -> tuple[str, str]:
    """(the post link without query, fragment or trailing slash, its Instagram
    shortcode or ''). The older reference route (/v1/design-templates/reference)
    kept the post link as design_templates.source_url and nothing else.

    ('', '') for a Facebook link identified only by its query (permalink.php,
    story.php, photo.php, photo/, watch/, video.php): the canon would collapse
    every such post into one, so the legacy post match is skipped for them.
    The canon is a function of the query-less link alone, the same string the
    SQL in _legacy_own_template computes from source_url, so a stored generic
    row can never equal a path-identified canon either."""
    u = str(origin_url or "").strip()
    if not u.startswith("http"):
        return "", ""
    canon = u.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if _query_identified(canon):
        return "", ""
    m = _IG_POST.search(canon)
    return canon, (m.group(1) if m else "")


async def fetch_image(url: str) -> bytes:
    """Download a public image with every hop SSRF-checked and a size cap.

    Redirects are followed by hand so each Location is validated too (netguard
    checks only the URL it is given). Raises Refused for a link that is gone or
    not public, Retryable for a network fault or a server error."""
    import httpx

    from .netguard import url_public_status

    cur = url
    async with httpx.AsyncClient(timeout=httpx.Timeout(FETCH_TIMEOUT_S, connect=10.0),
                                 follow_redirects=False) as c:
        for _hop in range(MAX_REDIRECTS + 1):
            verdict = await url_public_status(cur)
            if verdict == "unresolved":
                # A resolver blip is not a verdict on the picture: retry later.
                raise Retryable("fetch_dns_error")
            if verdict != "public":
                raise Refused(422, "url_not_public")
            try:
                async with c.stream("GET", cur) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get("location") or ""
                        if not loc:
                            raise Refused(422, "bad_redirect")
                        cur = urljoin(cur, loc)
                        continue
                    if r.status_code >= 500 or r.status_code == 429:
                        raise Retryable(f"fetch_{r.status_code}")
                    if r.status_code >= 400:
                        # An expired CDN signature is 403/404: final, re-scrape for a fresh link.
                        raise Refused(422, f"fetch_{r.status_code}")
                    try:
                        if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                            raise Refused(413, "too_large")
                    except ValueError:
                        pass
                    buf = bytearray()
                    async for chunk in r.aiter_bytes(1 << 16):
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise Refused(413, "too_large")
                    if not buf:
                        raise Refused(422, "empty")
                    return bytes(buf)
            except (Refused, Retryable):
                raise
            except Exception as exc:  # noqa: BLE001 — a network fault is not a verdict
                raise Retryable(f"fetch_error:{type(exc).__name__}") from None
    raise Refused(422, "too_many_redirects")


# ── the library (each helper names the tenant) ──────────────────────────────


def _is_missing_column(exc: BaseException) -> bool:
    return type(exc).__name__ in ("UndefinedColumnError", "UndefinedTableError")


async def find_by_origin_ref(tenant_id, origin: str, origin_ref: str, key: str) -> dict | None:
    """A re-scrape of an image already taken in, before downloading it again."""
    if not origin_ref or not key:
        return None
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT id::text, width, height FROM media_assets WHERE role = $1 "
            "AND origin = $2 AND origin_ref = $3 AND quality->>'image_key' = $4 LIMIT 1",
            PHOTO_ROLE, origin, origin_ref, key)
        if r:
            return {"media_id": r["id"], "width": r["width"], "height": r["height"]}
        t = await conn.fetchrow(
            "SELECT id::text FROM design_templates WHERE source_kind = 'own' "
            "AND spec->'source_image'->>'origin_ref' = $1 "
            "AND spec->'source_image'->>'image_key' = $2 LIMIT 1", origin_ref, key)
        if t:
            return {"template_id": t["id"]}
        s = await conn.fetchrow(
            "SELECT body FROM own_media_seen WHERE origin = $1 AND origin_ref = $2 "
            "AND image_key = $3 LIMIT 1", origin, origin_ref, key)
    return _seen_answer(s)


async def _legacy_own_template(tenant_id, origin_url: str) -> dict | None:
    """An own template the older reference route already minted from this POST.

    That route read ONE still per post (its cover) and kept only the post link
    (rows this module mints carry spec.source_image instead), so a fresh
    scrape of the same post is a byte-different file that no hash matches; a
    second read could mint the same layout twice under a slightly different
    fingerprint, and the own lane is allocated by count. Answered before the
    download, so it also saves the paid read."""
    canon, shortcode = post_key(origin_url)
    if not canon:
        return None
    async with acquire(tenant_id) as conn:
        t = await conn.fetchrow(
            "SELECT id::text FROM design_templates WHERE source_kind = 'own' AND source_url <> '' "
            "AND coalesce(spec->'source_image'->>'image_key', '') = '' "
            "AND (rtrim(split_part(split_part(source_url, '#', 1), '?', 1), '/') = $1::text "
            "     OR ($2::text <> '' AND source_url ~ ('instagram\\.com/([^/?#]+/)?(p|reels?|tv)/' || $2::text "
            "                                     || '([/?#]|$)'))) LIMIT 1",
            canon, shortcode)
    return {"template_id": t["id"], "legacy": True} if t else None


def _seen_answer(row) -> dict | None:
    """A verdict own_media_seen holds for a picture already PAID for, repeated
    as it was first given (with seen=True) instead of reading it again."""
    if not row:
        return None
    body = row["body"]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {}
    body = dict(body) if isinstance(body, dict) else {}
    if not body.get("verdict"):
        return None
    return {**body, "seen": True}


async def record_seen(tenant_id, req: OwnMediaImport, m: dict, key: str, body: dict) -> None:
    """Remember a PAID verdict that left no row naming this picture (undrawable,
    or a template whose shape the brand or the catalogue already held), so a
    re-send — a BM2 retry after its timeout, a ledger loss — is answered from
    here and never pays the vision read again. Never raises: a missed trace
    costs one more read later, not the verdict now."""
    try:
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "INSERT INTO own_media_seen (origin, origin_ref, image_key, sha256, dhash, "
                "verdict, body) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) "
                "ON CONFLICT (tenant_id, sha256) DO NOTHING",
                req.origin, req.origin_ref or "", key or "", m["sha256"], m["dhash"] or "",
                str(body.get("verdict") or ""), json.dumps(body, default=str))
    except Exception:  # noqa: BLE001
        logger.warning("own media: could not record a seen verdict for %s", tenant_id,
                       exc_info=True)


async def find_duplicate(tenant_id, sha: str, dh: str) -> dict | None:
    """Is this picture already the brand's? Exact sha256 (the full column, or a
    legacy upload's 32-hex tag), else a dHash within DHASH_NEAR bits — across
    the brand's hero photos AND the source images of its own templates."""
    tag = f"sha256:{sha[:32]}"
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT id::text, width, height FROM media_assets WHERE role = $1 "
            "AND (sha256 = $2 OR $3 = ANY(tags)) LIMIT 1", PHOTO_ROLE, sha, tag)
        if r:
            return {"media_id": r["id"], "width": r["width"], "height": r["height"],
                    "match": "sha256"}
        t = await conn.fetchval(
            "SELECT id::text FROM design_templates WHERE source_kind = 'own' "
            "AND spec->'source_image'->>'sha256' = $1 LIMIT 1", sha)
        if t:
            return {"template_id": t, "match": "sha256"}
        s = _seen_answer(await conn.fetchrow(
            "SELECT body FROM own_media_seen WHERE sha256 = $1 LIMIT 1", sha))
        if s:
            return {**s, "match": "sha256"}
    if not dh or dhash_flat(dh):
        return None
    # Photos uploaded before migration 072 carry no dHash, and the platform's
    # re-encode of a picture never matches its sha — they are hashed first, or
    # every photo the owner both uploaded and posted is bought twice. That runs
    # as ONE background task per tenant (not on this request: 60 downloads in
    # line would outlast BM2's timeout); this import waits for it briefly.
    await _await_legacy_hashes(tenant_id)
    async with acquire(tenant_id) as conn:
        photos = await conn.fetch(
            "SELECT id::text, dhash, width, height FROM media_assets "
            "WHERE role = $1 AND dhash IS NOT NULL AND dhash <> ''", PHOTO_ROLE)
        own = await conn.fetch(
            "SELECT id::text, spec->'source_image'->>'dhash' AS dhash FROM design_templates "
            "WHERE source_kind = 'own' AND spec->'source_image' ? 'dhash'")
        seen = await conn.fetch(
            "SELECT dhash AS seen_dhash, body FROM own_media_seen WHERE dhash <> ''")
    for p in photos:
        if near(dh, p["dhash"]):
            return {"media_id": p["id"], "width": p["width"], "height": p["height"],
                    "match": "dhash"}
    for t in own:
        if near(dh, t["dhash"]):
            return {"template_id": t["id"], "match": "dhash"}
    for r in seen:
        if near(dh, r["seen_dhash"]):
            got = _seen_answer(r)
            if got:
                return {**got, "match": "dhash"}
    return None


LEGACY_BACKFILL_MAX = 60           # legacy photos hashed per import call, at most
_LEGACY_LOCKS: dict[str, asyncio.Lock] = {}     # per (tenant, event loop)
_LEGACY_SKIP: set[tuple[str, str]] = set()   # (tenant, media id) unreachable this process


def _legacy_measure(raw: bytes) -> dict:
    """What a legacy row lacks, from its stored bytes. The dHash is taken on
    the uprighted picture (as measure() does for an import), so a phone upload
    carrying an EXIF rotation still matches the platform's upright copy."""
    try:
        m = measure(raw)
    except Refused:
        return {"dhash": "", "sha256": hashlib.sha256(raw).hexdigest()}
    return {"dhash": m["dhash"], "sha256": hashlib.sha256(raw).hexdigest(),
            "width": m["width"], "height": m["height"], "orientation": m["orientation"],
            "quality": m["quality"]}


async def backfill_legacy_hashes(tenant_id, *, limit: int = LEGACY_BACKFILL_MAX) -> int:
    """Hash the brand's photos that predate migration 072 (dhash NULL), so the
    near-duplicate check sees the WHOLE library. App-side and per tenant
    because the migration cannot (FORCE RLS, no tenant set). Writes the dHash
    ('' for a file that is not a usable image, so it is not fetched again), the
    full sha256, and size/orientation/quality only where they are still empty.
    Serialised per tenant: concurrent imports wait, then find the work done.
    Returns how many rows were hashed. Never raises."""
    tkey = str(tenant_id)
    lock = _LEGACY_LOCKS.setdefault(f"{tkey}:{id(asyncio.get_running_loop())}", asyncio.Lock())
    done = 0
    try:
        async with lock:
            async with acquire(tenant_id) as conn:
                rows = await conn.fetch(
                    "SELECT id::text, uri FROM media_assets WHERE role = $1 AND dhash IS NULL "
                    "AND source_type <> 'generated' AND uri LIKE 'http%' "
                    "ORDER BY created_at DESC LIMIT $2",
                    PHOTO_ROLE, int(limit) + len(_LEGACY_SKIP))
            rows = [r for r in rows if (tkey, r["id"]) not in _LEGACY_SKIP][:limit]
            for r in rows:
                try:
                    raw = await fetch_image(r["uri"])
                    got = await asyncio.to_thread(_legacy_measure, raw)
                except Refused:
                    got = {"dhash": ""}            # gone / not public: final
                except Exception:  # noqa: BLE001 — a network fault: try again next process
                    _LEGACY_SKIP.add((tkey, r["id"]))
                    continue
                q = got.get("quality")
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE media_assets SET dhash = $2, sha256 = COALESCE(sha256, $3), "
                        "width = COALESCE(width, $4), height = COALESCE(height, $5), "
                        "orientation = COALESCE(orientation, $6), "
                        "quality = CASE WHEN quality = '{}'::jsonb AND $7::jsonb IS NOT NULL "
                        "THEN $7::jsonb ELSE quality END "
                        "WHERE id = $1::uuid AND dhash IS NULL",
                        r["id"], got.get("dhash") or "", got.get("sha256"), got.get("width"),
                        got.get("height"), got.get("orientation"),
                        json.dumps(q) if q is not None else None)
                done += 1
    except Exception:  # noqa: BLE001 — a backfill miss only weakens the dedupe
        logger.warning("own media: legacy hash backfill failed for %s", tenant_id, exc_info=True)
    return done


_LEGACY_TASKS: dict[str, asyncio.Task] = {}   # per (tenant, event loop)


async def _drain_legacy_hashes(tenant_id) -> int:
    total = 0
    for _ in range(LEGACY_ROUNDS_MAX):
        n = await backfill_legacy_hashes(tenant_id)
        total += n
        if n < LEGACY_BACKFILL_MAX:
            break
    return total


async def _await_legacy_hashes(tenant_id, *, wait_s: float | None = None) -> None:
    """Start (or join) the tenant's background legacy hashing and wait for it
    at most `wait_s`. The task is shielded: a timeout here, or this request
    being cancelled, leaves it running for the next import."""
    key = f"{tenant_id}:{id(asyncio.get_running_loop())}"
    task = _LEGACY_TASKS.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_drain_legacy_hashes(tenant_id))
        _LEGACY_TASKS[key] = task
    try:
        await asyncio.wait_for(asyncio.shield(task),
                               LEGACY_WAIT_S if wait_s is None else wait_s)
    except TimeoutError:
        logger.info("own media: legacy hashing for %s still running; checking what is hashed",
                    tenant_id)
    except Exception:  # noqa: BLE001 — a backfill miss only weakens the dedupe
        logger.warning("own media: legacy hashing failed for %s", tenant_id, exc_info=True)


# ── the one paid read, never paid twice for one picture ────────────────────

_READS: dict[str, tuple[float, asyncio.Task]] = {}


def _empty_read(spec: dict) -> bool:
    """A read that said nothing at all: design_cloner sanitises an empty {}
    (a refusal's content is None) into status 'ok' with defaults only."""
    bg = spec.get("background") or {}
    return (not spec.get("elements") and not spec.get("decorations")
            and not spec.get("logo_box") and not bg.get("photo_box")
            and not bg.get("photo_boxes")
            and not str(spec.get("design_notes") or "").strip()
            and spec.get("palette") == _SANITIZE_DEFAULT_PALETTE)


def _reads_as_photograph(spec: dict) -> bool:
    """Only a read that positively says PHOTOGRAPH may become a hero photo: a
    full picture, not a solid card, and one photo region (a collage is not a
    background a new card can sit on)."""
    bg = spec.get("background") or {}
    boxes = bg.get("photo_boxes") or []
    return (spec.get("kind") == "photo_forward"
            and bg.get("treatment") != "solid"
            and (not isinstance(boxes, list) or len(boxes) <= 1))


async def _read_spec(tenant_id, m: dict, extract) -> dict:
    """The vision read for this picture. Shielded and remembered per (tenant,
    sha256): when it outlasts EXTRACT_TIMEOUT_S the request answers retry but
    the read is NOT cancelled (OpenAI bills it either way) — the retry joins
    the same read instead of paying for a second one."""
    now = asyncio.get_running_loop().time()
    for k, (t0, t) in list(_READS.items()):
        if t.done() and now - t0 > READ_TTL_S:
            _READS.pop(k, None)
    key = f"{tenant_id}:{m['sha256']}:{id(asyncio.get_running_loop())}"
    ent = _READS.get(key)
    if ent is not None and ent[1].done() and (ent[1].cancelled() or ent[1].exception()):
        _READS.pop(key, None)
        ent = None
    if ent is None:
        data, mime = m["data"], m["mime"]

        async def run():
            async with _EXTRACT_SEM:
                return await extract(data, mime=mime)
        ent = (now, asyncio.create_task(run()))
        _READS[key] = ent
        while len(_READS) > READ_CACHE_MAX:
            oldest = next((k for k, (_t0, t) in _READS.items() if t.done()), None)
            if oldest is None:
                break
            _READS.pop(oldest, None)
    task = ent[1]
    try:
        spec = await asyncio.wait_for(asyncio.shield(task), timeout=EXTRACT_TIMEOUT_S)
    except TimeoutError:
        raise Retryable("vision_timeout") from None      # still running; a retry joins it
    except Exception:  # noqa: BLE001 — a failed read is retryable, never final
        _READS.pop(key, None)
        logger.warning("own media: vision read raised", exc_info=True)
        raise Retryable("vision_failed") from None
    # Answered: this request turns it into a verdict, so nothing is kept.
    _READS.pop(key, None)
    return spec


async def _template_by_fingerprint(tenant_id, fp: str) -> dict | None:
    """The row already holding this shape: {id, source_kind}, or None."""
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT id::text, source_kind FROM design_templates WHERE fingerprint = $1 LIMIT 1", fp)
    return {"id": r["id"], "source_kind": r["source_kind"]} if r else None


async def _store(tenant_id, data: bytes, mime: str) -> tuple[str, str]:
    from .media import storage

    ext = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    # to_thread: the Supabase storage client is sync HTTP.
    return await asyncio.to_thread(storage().save, str(tenant_id), data, f"own-post.{ext}")


async def insert_photo(tenant_id, *, uri: str, file_path: str, m: dict, req: OwnMediaImport,
                       platform: str, taken_at: datetime | None) -> tuple[str, bool]:
    """Write the hero_photo row; (id, created). The duplicate check is re-run
    under a per-(tenant, picture) advisory lock, so two imports of one picture
    racing each other leave one row, not two."""
    tag = f"sha256:{m['sha256'][:32]}"
    quality = {**m["quality"], "image_key": image_key(req.url)}
    async with acquire(tenant_id) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           f"own_media:{tenant_id}:{m['sha256']}")
        dup = await conn.fetchval(
            "SELECT id::text FROM media_assets WHERE role = $1 "
            "AND (sha256 = $2 OR $3 = ANY(tags)) LIMIT 1", PHOTO_ROLE, m["sha256"], tag)
        if dup:
            return dup, False
        new_id = await conn.fetchval(
            """INSERT INTO media_assets
                   (role, source_type, uri, file_path, title, platform, mime, tags, notes,
                    width, height, orientation, origin, origin_url, origin_ref, rights,
                    taken_at, sha256, dhash, quality)
               VALUES ($1, 'upload', $2, $3, $4, $5, $6, $7::text[], $8,
                       $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19::jsonb)
               RETURNING id::text""",
            PHOTO_ROLE, uri, file_path, f"@{req.handle} · {platform}"[:200], platform,
            m["mime"], ["own-post", tag, f"origin:{req.origin}"], req.origin_url,
            m["width"], m["height"], m["orientation"], req.origin, req.origin_url,
            req.origin_ref, req.rights, taken_at, m["sha256"], m["dhash"] or None,
            json.dumps(quality))
    return new_id, True


# ── the one entry point ─────────────────────────────────────────────────────


def _validate(req: OwnMediaImport) -> tuple[str, datetime | None]:
    if req.published_by_bm:
        # Rule: never learn our own renders back as the brand's (rejected ones included).
        raise Refused(422, "published_by_bm")
    if req.rights != "own_account":
        raise Refused(422, "rights_not_own_account")
    if req.origin not in OWN_ORIGINS:
        raise Refused(422, "origin_not_own")
    u = urlparse(req.url)
    if u.scheme != "https" or not u.hostname:
        raise Refused(422, "url_must_be_https")
    if req.origin_url and urlparse(req.origin_url).scheme not in ("http", "https"):
        raise Refused(422, "bad_origin_url")
    platform = (req.platform or ORIGIN_PLATFORM[req.origin]).strip().lower()
    if platform != ORIGIN_PLATFORM[req.origin]:
        raise Refused(422, "platform_origin_mismatch")
    req.handle = req.handle.strip().lstrip("@")
    if not req.handle:
        raise Refused(422, "no_handle")
    taken = None
    if req.taken_at:
        try:
            taken = datetime.fromisoformat(req.taken_at.strip().replace("Z", "+00:00"))
        except ValueError:
            taken = None
    return platform, taken


def _dims(m: dict | None) -> dict:
    m = m or {}
    return {"width": m.get("width"), "height": m.get("height")}


async def import_own_media(tenant_id: UUID, req: OwnMediaImport, *, extract=None) -> tuple[int, dict]:
    """Take one of the brand's own posted images. Returns (http_status, body).

    `extract` replaces design_cloner.extract_template_spec (tests)."""
    from . import design_templates as dt
    from .house_harvest import _is_plain_photo
    from .house_layouts import family_key
    from .layout_types import classify

    try:
        platform, taken = _validate(req)
        key = image_key(req.url)
        try:
            seen = await find_by_origin_ref(tenant_id, req.origin, req.origin_ref, key)
            # Only an EXPLICIT cover: a retry that omits the slide may be slide n.
            if not seen and req.slide == 0:
                seen = await _legacy_own_template(tenant_id, req.origin_url)
        except Exception as exc:  # noqa: BLE001
            if _is_missing_column(exc):
                raise Retryable("migration_072_missing") from None
            raise
        if seen:
            return 200, {"verdict": "duplicate",
                         "match": "post" if seen.get("legacy") else "origin_ref", **seen}

        raw = await fetch_image(req.url)
        # Decoding, hashing and the blur measure are CPU work: off the event loop.
        m = await asyncio.to_thread(measure, raw)
        try:
            dup = await find_duplicate(tenant_id, m["sha256"], m["dhash"])
        except Exception as exc:  # noqa: BLE001
            if _is_missing_column(exc):
                raise Retryable("migration_072_missing") from None
            raise
        if dup:
            return 200, {"verdict": "duplicate", **_dims(m), **dup}

        # The one paid step.
        if extract is None:
            from .design_cloner import extract_template_spec as extract
        spec = await _read_spec(tenant_id, m, extract)
        status = spec.get("status") if isinstance(spec, dict) else "failed"
        if status == "no_key":
            raise Retryable("no_key")
        if status == "failed" or not isinstance(spec, dict):
            raise Retryable("vision_failed")
        if _empty_read(spec):
            # An empty reply or a refusal is no answer about the picture.
            raise Retryable("vision_empty")

        layout = dt.usable(spec) and not _is_plain_photo(spec)
        if layout and dt.drawable(spec):
            out = await _keep_template(tenant_id, req, spec, m, platform, key,
                                       dt=dt, classify=classify, family_key=family_key)
            if not (out.get("created") or out.get("relabelled")):
                # The picture is named by no row (the shape was already held):
                # remember the verdict so a re-send is not read again.
                await record_seen(tenant_id, req, m, key,
                                  {k: v for k, v in out.items() if k != "stored_image_uri"})
            return 200, out
        if not layout and _reads_as_photograph(spec):
            return 200, await _keep_photo(tenant_id, req, _mark_printed(m, spec), platform, taken)
        # A layout we cannot draw — or no layout and not a photograph either
        # (a text card whose roles were all dropped, a solid card, a collage).
        named = classify(spec)
        out = {"verdict": "undrawable", "layout_type": named["type"],
               "reason": "undrawable_layout" if layout else "not_a_photo", **_dims(m)}
        await record_seen(tenant_id, req, m, key, out)
        return 200, out
    except Refused as exc:
        return exc.status, {"verdict": "rejected", "reason": exc.reason}
    except Retryable as exc:
        return 503, {"verdict": "retry", "reason": exc.reason}
    except Exception as exc:  # noqa: BLE001 — an infrastructure fault is never a verdict
        logger.exception("own media: import failed for tenant %s", tenant_id)
        return 503, {"verdict": "retry", "reason": f"internal:{type(exc).__name__}"}


def _mark_printed(m: dict, spec: dict) -> dict:
    """What the read found PRINTED on a photo, recorded on its quality.

    A photo with a headline or CTA burned in ('GRAND OPENING SAT') is still the
    brand's post and is kept, but it is no background: a designed card would
    draw new copy over the old. It is flagged has_text and taken out of
    rotation. A logo alone is recorded (has_logo) and stays in rotation."""
    els = [e for e in (spec.get("elements") or []) if isinstance(e, dict)]
    decos = [d for d in (spec.get("decorations") or []) if isinstance(d, dict)]
    q = dict(m["quality"])
    reasons = list(q.get("reasons") or [])
    if els or decos:
        q["has_text"] = True
        q["text_elements"] = len(els)
        if "has_text" not in reasons:
            reasons.append("has_text")
    if spec.get("logo_box"):
        q["has_logo"] = True
    q["reasons"] = reasons
    q["in_rotation"] = not reasons
    return {**m, "quality": q}


async def _keep_template(tenant_id, req, spec, m, platform, key, *, dt, classify, family_key) -> dict:
    fp = dt.fingerprint(spec)
    to_save = {**spec, "source_image": {
        "sha256": m["sha256"], "dhash": m["dhash"], "origin": req.origin,
        "origin_ref": req.origin_ref, "image_key": key, "handle": req.handle,
        "taken_at": req.taken_at, "width": m["width"], "height": m["height"]}}
    uri, path = "", ""
    held = await _template_by_fingerprint(tenant_id, fp)
    if not held or str(held.get("source_kind") or "") != "own":
        # A new shape — or one held as another account's, which save() may
        # relabel as the brand's: keep our own copy of THE BRAND'S picture (the
        # CDN link expires, and a relabelled row must not keep showing the
        # other account's). A shape the brand already holds needs no copy.
        uri, path = await _store(tenant_id, m["data"], m["mime"])
    outcome: dict = {}
    try:
        tid = await dt.save(tenant_id, to_save, source_kind="own", source_url=req.origin_url,
                            source_image_uri=uri, source_handle=req.handle,
                            source_platform=platform,
                            source_engagement=float(req.engagement or 0), outcome=outcome)
    except Exception:
        if path:
            _discard(path)
        raise
    if path and not (outcome.get("created") or outcome.get("relabelled")):
        # The shape stayed someone else's (adopted / in the catalogue) or the
        # brand's own row won a race: our copy is referenced by nothing.
        _discard(path)
        uri = ""
    named = classify(spec)
    return {"verdict": "template", "template_id": tid,
            "layout_type": named["type"], "label": named["label"],
            "family_key": family_key(spec),
            "matched_kind": outcome.get("matched_kind", ""),
            "relabelled": bool(outcome.get("relabelled")),
            "created": bool(outcome.get("created")),
            "stored_image_uri": uri, **_dims(m)}


async def _keep_photo(tenant_id, req, m, platform, taken) -> dict:
    uri, path = await _store(tenant_id, m["data"], m["mime"])
    try:
        media_id, created = await insert_photo(tenant_id, uri=uri, file_path=path, m=m, req=req,
                                               platform=platform, taken_at=taken)
    except Exception as exc:  # noqa: BLE001
        _discard(path)
        if _is_missing_column(exc):
            raise Retryable("migration_072_missing") from None
        raise
    if not created:
        _discard(path)
        return {"verdict": "duplicate", "match": "sha256", "media_id": media_id, **_dims(m)}
    caption: dict = {}
    try:
        from .photo_subject import read_one
        caption = await read_one(UUID(media_id), uri, tenant_id)
    except Exception:  # noqa: BLE001 — an uncaptioned photo is still the brand's photo
        logger.warning("own media: caption failed for %s", media_id, exc_info=True)
    try:
        from .hero_context import invalidate_cache
        invalidate_cache(tenant_id)
    except Exception:  # noqa: BLE001
        pass
    return {"verdict": "photo", "media_id": media_id, "uri": uri,
            "orientation": m["orientation"],
            "in_rotation": bool(m["quality"]["in_rotation"]),
            "quality_reasons": list(m["quality"]["reasons"]),
            "caption": caption.get("caption", ""), "has_person": caption.get("has_person"),
            **_dims(m)}


def _discard(path: str) -> None:
    try:
        from .media import storage
        storage().delete(path)
    except Exception:  # noqa: BLE001 — an orphan object is a storage cost, not an error
        logger.info("own media: could not discard %s", path)


# ── the "current templates" view ────────────────────────────────────────────


def _iso(v: Any) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


async def summary(tenant_id: UUID, *, days: int = 0, examples: int = 3) -> dict:
    """What the brand's own posts have given it: photos by origin, orientation
    and whether a person is in them; own templates by layout type, with counts
    and the newest examples — the brand's CURRENT templates."""
    from .layout_types import classify, display_name

    days = max(0, min(int(days or 0), 3650))
    since = " AND created_at >= now() - make_interval(days => $1)" if days else ""
    args: list = [days] if days else []
    out: dict = {"days": days or None}

    try:
        async with acquire(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT origin, orientation, has_person, count(*) AS n, max(created_at) AS last "
                "FROM media_assets WHERE role = 'hero_photo' AND source_type <> 'generated'"
                + since + " GROUP BY 1, 2, 3", *args)
        by_origin: dict[str, int] = {}
        by_orient: dict[str, int] = {}
        by_person = {"yes": 0, "no": 0, "unknown": 0}
        for r in rows:
            n = int(r["n"])
            by_origin[str(r["origin"])] = by_origin.get(str(r["origin"]), 0) + n
            o = str(r["orientation"] or "unknown")
            by_orient[o] = by_orient.get(o, 0) + n
            hp = r["has_person"]
            by_person["unknown" if hp is None else "yes" if hp else "no"] += n
        out["photos"] = {"total": sum(by_origin.values()), "by_origin": by_origin,
                         "by_orientation": by_orient, "by_has_person": by_person,
                         "own_posts": sum(v for k, v in by_origin.items() if k in OWN_ORIGINS)}
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_column(exc):
            raise
        out["photos"] = {"error": "migration_072_missing"}

    async with acquire(tenant_id) as conn:
        trows = await conn.fetch(
            "SELECT id::text, spec, source_url, source_image_uri, source_handle, "
            "source_platform, source_engagement, status, times_used, created_at "
            "FROM design_templates WHERE source_kind = 'own'" + since
            + " ORDER BY created_at DESC LIMIT 1000", *args)
    groups: dict[str, dict] = {}
    for r in trows:
        spec = r["spec"]
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                spec = {}
        named = classify(spec if isinstance(spec, dict) else {})
        g = groups.setdefault(named["type"], {
            "layout_type": named["type"], "name": display_name(named["type"]),
            "count": 0, "active": 0, "last_seen": "", "examples": []})
        g["count"] += 1
        g["active"] += 1 if r["status"] == "active" else 0
        if not g["last_seen"]:
            g["last_seen"] = _iso(r["created_at"])
        if len(g["examples"]) < examples:
            g["examples"].append({
                "template_id": r["id"], "label": named["label"],
                "image_uri": r["source_image_uri"], "source_url": r["source_url"],
                "handle": r["source_handle"], "platform": r["source_platform"],
                "engagement": float(r["source_engagement"] or 0),
                "times_used": int(r["times_used"] or 0), "status": r["status"],
                "created_at": _iso(r["created_at"])})
    out["templates"] = {"total": len(trows),
                        "by_layout_type": sorted(groups.values(),
                                                 key=lambda g: (-g["count"], g["layout_type"]))}
    return out


__all__ = ["OwnMediaImport", "import_own_media", "summary", "measure", "dhash",
           "dhash_flat", "hamming", "near", "fetch_image", "image_key", "OWN_ORIGINS"]
