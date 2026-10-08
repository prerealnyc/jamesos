"""Hero context — describe the brand's hero from uploaded photos.

When the user uploads photos of the brand's hero (role='hero_photo'),
this module:

  1. Pulls up to 5 photo URLs from the media library.
  2. Calls GPT-4o vision once to write a 2-3 sentence character
     description (face, build, dress, era, signature look).
  3. Caches the description in-process per tenant so the next 50
     productions don't burn vision-API tokens redescribing him.

The cinematic image-prompt LLM uses this description to refer to "the
hero" as a recurring visual character across beats. This is what the
user means by "we need to put more efforts in prompting so we get
better outputs" — every prompt that mentions a person gets the same
visual anchor, so the audience sees a consistent recurring character
across the slideshow.

Honest scope:
  * gpt-image-1 doesn't have a face-LoRA — descriptions guide it but
    won't produce a perfect likeness. For a faithful likeness we'd
    chain through Runway's character feature or a FLUX IP-Adapter.
    Flagged, not silently faked.
  * Cache is per-process. A multi-worker deployment would re-describe
    once per worker. Acceptable for the current single-uvicorn setup.
  * "Hero" generalises beyond James — any tenant's hero_photo uploads
    seed their own context.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from .config import settings
from .media import list_media

_HEADERS = lambda: {
    "Authorization": f"Bearer {settings.openai_api_key}",
    "Content-Type": "application/json",
}

_MAX_PHOTOS = 5
_VISION_MODEL = "gpt-4o"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# In-process cache: tenant_id (str) → HeroContext. Cleared by hand if
# the user re-uploads — see invalidate_cache().
_CACHE: dict[str, "HeroContext"] = {}


@dataclass
class HeroContext:
    description: str             # 2-3 sentences for the prompt LLM
    photo_count: int             # how many photos were sampled
    photo_urls: list[str]        # the actual photos (so we can pass
                                 #   them through to image-edit later)
    video_urls: list[str]        # available hero videos (unused today,
                                 #   reserved for a future avatar swap)
    # The photos that may stand for the HERO'S LIKENESS: the ones the owner
    # uploaded. A scraped own-post photo (a venue, a plate of food, a golf hole)
    # is the brand's to post, but describing it as "the hero" or handing it to
    # gpt-image-1 as a face reference poisons every likeness prompt.
    likeness_urls: list[str] = field(default_factory=list)
    # The photos a HERO-LED card (hero_quote, statement) should show first: the
    # owner's uploads only. A scraped own post is the brand's to post — even
    # one with a person in it — but it is not the hero.
    hero_urls: list[str] = field(default_factory=list)
    # Kept in the library but out of the render pool while anything else is
    # there: a photo with text already printed on it (new copy would land on
    # top of the old) and a screenshot.
    held_back_urls: list[str] = field(default_factory=list)


# Card formats that place THE HERO (the owner's real photo) rather than any photo.
HERO_FORMATS = frozenset({"hero_quote", "statement"})


def _tenant_key(tenant_id: UUID | str | None) -> str:
    """The tenant a cache entry belongs to, resolved the way acquire() resolves
    it: explicit arg, then the request's tenant, then the default. Keying on the
    arg alone filed brand B's photos under Tenant Zero whenever a caller inside B's
    request passed no tenant (story_video, the /hero routes)."""
    from .db import _request_tenant
    return str(tenant_id or _request_tenant.get() or settings.default_tenant_id)


def _tags(m: dict) -> list[str]:
    return [str(t) for t in (m.get("tags") or [])]


def origin_of(m: dict) -> str:
    """Where a media row came from. The migration-072 column when it is there;
    otherwise the import's own tags ('origin:<x>' / 'own-post'), so a row read
    before the column exists is never mistaken for an owner upload."""
    if (m.get("source_type") or "") == "generated":
        return "generated"
    for t in _tags(m):
        if t.startswith("origin:") and len(t) > 7:
            return t[7:]
    if "own-post" in _tags(m):
        return "own_post"
    return str(m.get("origin") or "owner_upload")


def held_back(m: dict) -> bool:
    """Text already on the picture, or a screenshot: not a background to draw on."""
    q = _quality(m)
    return bool(q.get("has_text") or q.get("screenshot"))


def _quality(m: dict) -> dict:
    q = m.get("quality")
    if isinstance(q, str):
        try:
            q = json.loads(q)
        except ValueError:
            q = {}
    return q if isinstance(q, dict) else {}


_ROTATION_EDGE = 1080      # a photo smaller than this on its short side is upscaled on a card


def _rank_key(m: dict) -> tuple:
    """Library order for the render pool: usable photos first, the owner's own
    uploads before scraped ones on a tie, then newest (taken, else added)."""
    q = _quality(m)
    w, h = m.get("width"), m.get("height")
    small = bool(w and h and min(int(w), int(h)) < _ROTATION_EDGE)
    return (bool(q.get("screenshot")), small or q.get("in_rotation") is False,
            origin_of(m) != "owner_upload", -_ts(m.get("taken_at") or m.get("created_at")))


def _ts(v) -> float:
    """Seconds since the epoch for a datetime or an ISO string; 0 when unknown."""
    from datetime import datetime
    try:
        if isinstance(v, datetime):
            return v.timestamp()
        if v:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return 0.0


_VISION_SYSTEM = (
    "You describe a recurring visual character from reference photos. "
    "The description will be injected into prompts for an AI image "
    "generator that paints cinematic stills of this person across "
    "many scenes. Your description must let the model produce a "
    "RECOGNIZABLE, CONSISTENT character — same age range, same build, "
    "same dress, same hair, same beard, same era — every time it's "
    "used. Be concrete, visual, brief.\n\n"
    "Return STRICT JSON:\n"
    "{\"description\": \"...\", \"signature_dress\": \"...\", "
    "\"signature_setting\": \"...\"}\n\n"
    "Rules:\n"
    "  * description: 2-3 sentences. Age range. Build. Face / hair / "
    "    beard. Dress style. Era (modern / vintage / etc).\n"
    "  * signature_dress: ONE phrase the image gen can paste in (e.g. "
    "    'navy blazer over open-collar shirt' or 'leather jacket, "
    "    salt-and-pepper beard').\n"
    "  * signature_setting: ONE phrase for his world (e.g. 'NYC "
    "    rooftops at golden hour' or 'wood-paneled brokerage office').\n"
    "  * Do not name the person. Use 'the hero' or 'a man in his 40s'.\n"
    "  * No make-believe — only what you can see in the photos."
)


async def describe_hero_from_photos(photo_urls: list[str]) -> dict:
    """Call GPT-4o vision once with all photos and return its structured
    description. Honest fallback: returns an empty dict on any failure
    so the caller defaults to a generic character context."""
    if not photo_urls or not (settings.openai_api_key or "").strip():
        return {}
    user_content: list[dict] = [
        {"type": "text",
         "text": (
             f"Describe the recurring person across these "
             f"{len(photo_urls)} reference photos."
         )},
    ]
    for u in photo_urls[:_MAX_PHOTOS]:
        user_content.append({"type": "image_url", "image_url": {"url": u}})
    body = {
        "model": _VISION_MODEL,
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 400,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers=_HEADERS(),
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        import json as _json
        return _json.loads(text)
    except Exception:  # noqa: BLE001
        return {}


async def get_hero_context(
    tenant_id: UUID | None = None, force_refresh: bool = False
) -> HeroContext | None:
    """Return the hero context for this tenant, computing it the first
    time and caching it after. None when no hero photos are uploaded
    (the caller treats this as "no hero context — proceed as before")."""
    cache_key = _tenant_key(tenant_id)
    if not force_refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    photos = await list_media(role="hero_photo", tenant_id=tenant_id)
    # ONLY the brand's REAL photos — never AI-generated ones
    # (source_type='generated'). Every template + reference that pulls a hero
    # image goes through here, so this guarantees the authentic likeness is used
    # (per owner: "all images on all templates use hero's original images").
    # The WHOLE library, ranked (quality, then the owner's uploads, then newest)
    # — not the newest few — so an import of 300 own posts cannot push the
    # owner's uploads out, and a screenshot or thumbnail sorts last.
    real = sorted(
        (m for m in photos
         if (m.get("uri") or "").startswith("http")
         and (m.get("source_type") or "") != "generated"),
        key=_rank_key)
    photo_urls = [(m.get("uri") or "").strip() for m in real]
    if not photo_urls:
        return None
    likeness_urls = [(m.get("uri") or "").strip() for m in real
                     if origin_of(m) == "owner_upload"]
    # Hero-led cards place THE HERO, so only the owner's own uploads count.
    # A scraped own post with a person in it (a customer, staff, a guest) is
    # not the owner's likeness — has_person never promotes a scrape (rule 7).
    hero_urls = list(likeness_urls)
    held_back_urls = [(m.get("uri") or "").strip() for m in real if held_back(m)]

    videos = await list_media(role="hero_video", tenant_id=tenant_id)
    video_urls = [
        (m.get("uri") or "").strip()
        for m in videos
        if (m.get("uri") or "").startswith("http")
    ]

    # Describe the hero from the OWNER'S photos only (empty → no description,
    # which every consumer already treats as "generic character"). The paid
    # description is kept per likeness SET: an own-post import (or any other
    # invalidation that leaves the owner's uploads as they were) refreshes the
    # library without paying gpt-4o to describe the same photos again.
    likeness_set = tuple(sorted(likeness_urls))
    held = _DESCRIBED.get(cache_key)
    if not likeness_urls:
        described = {}
    elif not force_refresh and held and held[0] == likeness_set:
        described = held[1]
    else:
        described = await describe_hero_from_photos(likeness_urls)
        if described:            # a failed read is not kept — the next build retries
            _DESCRIBED[cache_key] = (likeness_set, described)
    description = described.get("description") or ""
    if described.get("signature_dress"):
        description += f" Signature dress: {described['signature_dress']}."
    if described.get("signature_setting"):
        description += f" Signature setting: {described['signature_setting']}."
    description = description.strip()

    # No description = couldn't see anything useful. Still cache so we
    # don't keep retrying; consumers will see the empty description and
    # fall back to generic prompts.
    ctx = HeroContext(
        description=description,
        photo_count=len(photo_urls),
        photo_urls=photo_urls,
        video_urls=video_urls,
        likeness_urls=likeness_urls,
        hero_urls=hero_urls,
        held_back_urls=held_back_urls,
    )
    _CACHE[cache_key] = ctx
    return ctx


# tenant → (the sorted likeness URLs described, the description). Survives
# invalidate_cache on purpose; recomputed only when that set changes.
_DESCRIBED: dict[str, tuple[tuple[str, ...], dict]] = {}


def invalidate_cache(tenant_id: UUID | None = None) -> None:
    """Bust the cache after a hero upload so the next production
    re-describes. Called by media upload endpoints."""
    key = _tenant_key(tenant_id)
    _CACHE.pop(key, None)
    # The bytes cache is keyed '<tenant>:<pool>' — popping the bare tenant key
    # (as this did) never matched, so a new photo stayed invisible to designed
    # cards and AI references until the process restarted.
    for k in [k for k in _BYTES_CACHE if k == key or k.startswith(key + ":")]:
        _BYTES_CACHE.pop(k, None)
        _BYTES_AT.pop(k, None)


# ── reference image bytes cache ───────────────────────────────────────
#
# When the story pipeline generates a hero-tagged image via gpt-image-1's
# edit endpoint, it needs the actual PNG/JPEG bytes of the hero photos
# (not just their URLs). Downloading them per beat would be wasteful —
# one render with 3 hero beats would re-fetch the same 5 photos 3 times.
# Cache the bytes per-process per-tenant; bust on hero upload.

# Per gpt-image-1's edit endpoint, each reference image must be <4 MB
# and roughly 1024x1024 quality. We resize on download with PIL so
# Supabase-original photos don't blow the limit and so the model isn't
# wasting compute on full-res inputs that don't help identity capture.
_REF_MAX_SIDE = 1024
_REF_MAX_BYTES = 3 * 1024 * 1024     # 3 MB — leave headroom under the
                                     #          4 MB API limit per file
_REF_MAX_COUNT = 3                    # 1-3 refs is the sweet spot — more
                                     # tends to confuse gpt-image-1
# The rotation pool for DESIGNED cards / carousels (NOT the AI-ref cap): how
# many photos are held as bytes at once. Which ones is chosen from the WHOLE
# library, least-used first (get_hero_photo_files), and re-chosen every
# _POOL_TTL_S — the cap bounds memory, it no longer means "the newest 24".
_DESIGNED_POOL_MAX = 24

# tenant → list of (filename, bytes) tuples
_BYTES_CACHE: dict[str, list[tuple[str, bytes]]] = {}
# When each render-pool entry was filled. The render pool is chosen least-used
# first, so it must be re-chosen now and then or the rotation would freeze on
# the first 24 photos picked; AI-reference entries never expire (the owner's
# uploads change only on upload, which invalidates).
_BYTES_AT: dict[str, float] = {}
_POOL_TTL_S = 15 * 60

# Render-pool photos are kept at NATIVE resolution: the canvas is 1080x1350
# (1080x1920 for reels), and the old 1024 cap upscaled every photo onto it.
# Only a photo far larger than any canvas is reduced, and never below
# _RENDER_MIN_SHORT on its short side.
_RENDER_MAX_LONG = 2160
_RENDER_MIN_SHORT = 1350
# Above this a photo is decoded at a reduced scale first (JPEG's DCT draft), so a
# 48-50 MP phone shot costs what a 12 MP one does. Only a file past Pillow's own
# decompression-bomb limit is refused outright.
_RENDER_MAX_PIXELS = 40_000_000


def _render_scale(w: int, h: int) -> float:
    if max(w, h) <= _RENDER_MAX_LONG:
        return 1.0
    return min(1.0, max(_RENDER_MAX_LONG / max(w, h), _RENDER_MIN_SHORT / max(1, min(w, h))))


def _render_image(data: bytes) -> bytes | None:
    """A render-pool copy: the ORIGINAL bytes when they are already a sane size
    and upright, else EXIF-transposed and reduced only as far as the caps say
    (JPEG q90; PNG when there is alpha).

    A large photo (a 48 MP iPhone Pro / 50 MP Samsung shot) is REDUCED, never
    dropped: a JPEG decodes at a reduced scale, anything else is decoded and
    resized while it is under Pillow's bomb limit. Past that limit — or when
    this path cannot read the file — the 1024 reference copy (_shrink_image) is
    returned rather than nothing; None only when no path can decode it."""
    try:
        from io import BytesIO

        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
        if w * h > _RENDER_MAX_PIXELS and (img.format or "") != "JPEG" and \
                w * h > (Image.MAX_IMAGE_PIXELS or w * h):
            return _shrink_image(data)
        orient = 1
        try:
            orient = int(img.getexif().get(0x0112, 1) or 1)
        except Exception:  # noqa: BLE001
            orient = 1
        scale = _render_scale(w, h)
        if scale >= 1.0 and orient == 1 and (img.format or "") in ("JPEG", "PNG", "WEBP"):
            return data          # native, upright — hand it over untouched
        if scale < 1.0 and (img.format or "") == "JPEG":
            # Decode at the smallest DCT scale still at least the target size.
            img.draft("RGB", (max(1, int(w * scale)), max(1, int(h * scale))))
        img.load()
        img = ImageOps.exif_transpose(img)
        scale = _render_scale(*img.size)
        if scale < 1.0:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                             Image.LANCZOS)
        buf = BytesIO()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.convert("RGBA").save(buf, "PNG", optimize=True)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — the reference copy beats no photo at all
        return _shrink_image(data)


def _shrink_image(data: bytes) -> bytes | None:
    """Resize the longer edge to <= _REF_MAX_SIDE and recompress to PNG
    until it fits under _REF_MAX_BYTES. Returns None if Pillow can't
    decode the file (caller skips it)."""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        # Pillow missing means we can't shrink — return as-is and let
        # gpt-image-1 reject if too large. Caller flags the failure.
        return data
    try:
        img = Image.open(BytesIO(data))
        img.load()
        # Phone photos carry their rotation as an EXIF tag, and the PNG re-encode
        # below drops it — upright the pixels first or the reference goes in
        # sideways.
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        return None
    # Convert to RGB for PNG output; PIL keeps alpha for RGBA→PNG which
    # works fine, but we strip exotic modes that gpt-image-1 might choke on.
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    longer = max(w, h)
    if longer > _REF_MAX_SIDE:
        scale = _REF_MAX_SIDE / longer
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    # Try PNG first; if still over the cap, drop to JPEG quality 85.
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    if buf.getbuffer().nbytes <= _REF_MAX_BYTES:
        return buf.getvalue()
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue() if buf.getbuffer().nbytes <= _REF_MAX_BYTES else None


# (url, 'render'|'ref') → processed bytes, across pool re-choices and
# invalidations. A library URL names one stored object, so its processed copy
# never goes stale; without this every 15-minute re-choice (and every import)
# re-downloaded and re-encoded photos the process already held. Bounded by
# bytes, least-recently used out first.
_URL_BYTES: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_URL_BYTES_BUDGET = 256 * 1024 * 1024


def _url_bytes_put(k: tuple[str, str], data: bytes) -> None:
    _URL_BYTES[k] = data
    _URL_BYTES.move_to_end(k)
    total = sum(len(v) for v in _URL_BYTES.values())
    while total > _URL_BYTES_BUDGET and len(_URL_BYTES) > 1:
        _old, v = _URL_BYTES.popitem(last=False)
        total -= len(v)


async def _fetch_processed(c: httpx.AsyncClient, url: str, variant: str) -> bytes | None:
    """One library photo as render ('render', native) or reference ('ref',
    1024) bytes; None when blocked, unreachable or undecodable. The decode /
    resize / encode runs in a thread — on the loop it stalled every other
    request for the length of a pool fill."""
    k = (url, variant)
    if k in _URL_BYTES:
        _URL_BYTES.move_to_end(k)
        return _URL_BYTES[k]
    try:
        import asyncio

        from .netguard import url_is_public
        if not await url_is_public(url, allow_http=True):
            return None  # SSRF guard: skip internal/private-IP URLs
        r = await c.get(url, follow_redirects=True)
        r.raise_for_status()
        fn = _render_image if variant == "render" else _shrink_image
        out = await asyncio.to_thread(fn, r.content)
    except Exception:  # noqa: BLE001
        return None
    if out is not None:
        _url_bytes_put(k, out)
    return out


def rotation_urls(ctx: HeroContext | None) -> list[str]:
    """The library photos that may be POSTED or drawn on, in library order:
    text-on-photo (a flyer) and screenshots stay out while anything else is
    there. Every rotation over ctx.photo_urls goes through here — the render
    pool and the photo-mode batches alike."""
    if ctx is None:
        return []
    held = set(ctx.held_back_urls)
    return [u for u in ctx.photo_urls if u not in held] or list(ctx.photo_urls)


def _render_choice(ctx: HeroContext, pool: int, counts: dict[str, int] | None) -> list[str]:
    """Which library photos fill the render pool (counts=None: every candidate,
    library order — the caller only needs to know whether it overflows).

    Text-on-photo and screenshots stay out while anything else is there. Then
    HALF the pool is reserved for the hero's photos (the owner's uploads),
    least-used first among themselves, and the rest is filled
    least-used first from everything left. Least-used across the whole library
    alone let an import of 150 never-used own posts take every slot from
    owner uploads used once in the window, for weeks."""
    urls = rotation_urls(ctx)
    if counts is None:
        return urls
    order = {u: i for i, u in enumerate(urls)}

    def least(u: str) -> tuple:
        return (counts.get(u, 0), order[u])

    hero = set(ctx.hero_urls)
    reserved = sorted((u for u in urls if u in hero), key=least)[:pool // 2]
    taken = set(reserved)
    rest = sorted((u for u in urls if u not in taken), key=least)
    return reserved + rest[:max(0, pool - len(reserved))]


async def hero_led_keys(tenant_id: UUID | None = None) -> frozenset[str]:
    """The photo keys a hero-led card (HERO_FORMATS) should pick from first —
    empty when the brand has none, and the caller then picks from everything."""
    try:
        ctx = await get_hero_context(tenant_id)
    except Exception:  # noqa: BLE001 — no context: no preference
        return frozenset()
    return frozenset(ctx.hero_urls) if ctx is not None else frozenset()


async def get_hero_photo_files(
    tenant_id: UUID | None = None,
    limit: int | None = _REF_MAX_COUNT,
    *,
    likeness: bool = False,
) -> list[tuple[str, bytes]]:
    """Hero photos as bytes, downloaded + cached. Three jobs:

    * limit=None — the RENDER pool for designed cards, carousels and photo
      posts. Drawn from the WHOLE library (see _render_choice): half the slots
      go to the hero's own photos, the rest least-used first (the same ledger
      photo_pick rotates on), then by quality and freshness; text-on-photo and
      screenshots only when nothing else is there. The top _DESIGNED_POOL_MAX
      are fetched at native resolution (see _render_image). The pool is re-chosen every _POOL_TTL_S,
      so over a few batches the rotation reaches photos the newest-24 cap never
      showed. Scraped own-post photos are in it — they are the brand's to post.
      A photo that dropped out of the pool is still reachable by key:
      library_photo().
    * limit=N — up to N of the brand's real photos (the ranked library, own
      posts included), shrunk to 1024: "does this brand have photography, and
      what does it look like" (palette, theme proposal, first-post format).
    * limit=N, likeness=True — up to N AI-LIKENESS references for gpt-image-1,
      from the photos the OWNER uploaded only. A scraped venue or food photo
      must never be handed over as "the hero's face".

    Returns [(url, bytes), ...] — the NAME is the source URL, the stable
    identity the photo-reuse ledger keys on (same photo must share ONE key
    whether used as bytes or by URL). Empty list when there is nothing usable
    (the caller falls back). Cached per (tenant, job, pool)."""
    render = limit is None
    pool = _DESIGNED_POOL_MAX if render else limit
    job = "render" if render else ("like" if likeness else "ref")
    key = f"{_tenant_key(tenant_id)}:{job}:{pool}"
    if key in _BYTES_CACHE and (
            not render or time.monotonic() - _BYTES_AT.get(key, 0.0) < _POOL_TTL_S):
        return _BYTES_CACHE[key]

    ctx = await get_hero_context(tenant_id)
    urls: list[str] = []
    if ctx is not None:
        if render:
            urls = _render_choice(ctx, pool, None)
            if len(urls) > pool:
                try:
                    from .photo_pick import _use_counts
                    counts = await _use_counts(tenant_id)
                except Exception:  # noqa: BLE001 — no ledger: library order stands
                    counts = {}
                urls = _render_choice(ctx, pool, counts)
            urls = urls[:pool]
        elif likeness:
            urls = list(ctx.likeness_urls)[:pool]
        else:
            urls = list(ctx.photo_urls)[:pool]
    if not urls:
        _BYTES_CACHE[key] = []
        _BYTES_AT[key] = time.monotonic()
        return []

    out: list[tuple[str, bytes]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        for url in urls:
            got = await _fetch_processed(c, url, "render" if render else "ref")
            if got is not None:
                out.append((url, got))

    _BYTES_CACHE[key] = out
    _BYTES_AT[key] = time.monotonic()
    return out


async def library_photo(tenant_id: UUID | None, photo_key: str) -> tuple[str, bytes] | None:
    """The exact library photo a card recorded (its hero_photo_key, a URL), at
    render resolution — whether or not it is in the current rotation pool.

    The pool is re-chosen least-used first, so the photo a card JUST used is
    the one most likely to have left it; "keep the image" must not depend on
    the pool. Only a URL in THIS tenant's own library is fetched, never an
    arbitrary key. None when it is not (any more) in the library."""
    if not photo_key or not photo_key.startswith("http"):
        return None
    try:
        ctx = await get_hero_context(tenant_id)
        if ctx is None or photo_key not in ctx.photo_urls:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            got = await _fetch_processed(c, photo_key, "render")
    except Exception:  # noqa: BLE001 — a miss is "not found"; the caller picks
        return None
    return (photo_key, got) if got is not None else None


__all__ = [
    "HeroContext", "get_hero_context", "describe_hero_from_photos",
    "get_hero_photo_files", "invalidate_cache", "origin_of", "library_photo",
    "HERO_FORMATS", "hero_led_keys", "held_back", "rotation_urls",
]
