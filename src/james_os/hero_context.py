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

from dataclasses import dataclass
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
    cache_key = str(tenant_id or settings.default_tenant_id)
    if not force_refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    photos = await list_media(role="hero_photo", tenant_id=tenant_id)
    photo_urls = [
        (m.get("uri") or "").strip()
        for m in photos
        if (m.get("uri") or "").startswith("http")
    ]
    if not photo_urls:
        return None

    videos = await list_media(role="hero_video", tenant_id=tenant_id)
    video_urls = [
        (m.get("uri") or "").strip()
        for m in videos
        if (m.get("uri") or "").startswith("http")
    ]

    described = await describe_hero_from_photos(photo_urls)
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
    )
    _CACHE[cache_key] = ctx
    return ctx


def invalidate_cache(tenant_id: UUID | None = None) -> None:
    """Bust the cache after a hero upload so the next production
    re-describes. Called by media upload endpoints."""
    key = str(tenant_id or settings.default_tenant_id)
    _CACHE.pop(key, None)
    _BYTES_CACHE.pop(key, None)


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

# tenant → list of (filename, bytes) tuples
_BYTES_CACHE: dict[str, list[tuple[str, bytes]]] = {}


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


async def get_hero_photo_files(
    tenant_id: UUID | None = None,
) -> list[tuple[str, bytes]]:
    """Up to _REF_MAX_COUNT hero photos, downloaded + resized + cached.

    Returns [(filename, bytes), ...] suitable for OpenAI's files= kwarg
    on the images.edit endpoint. Empty list when no hero photos exist
    OR when every download / resize failed (the caller falls back to
    the no-reference image-generate path)."""
    key = str(tenant_id or settings.default_tenant_id)
    if key in _BYTES_CACHE:
        return _BYTES_CACHE[key]

    ctx = await get_hero_context(tenant_id)
    if ctx is None or not ctx.photo_urls:
        _BYTES_CACHE[key] = []
        return []

    out: list[tuple[str, bytes]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        for i, url in enumerate(ctx.photo_urls[:_REF_MAX_COUNT]):
            try:
                r = await c.get(url, follow_redirects=True)
                r.raise_for_status()
                shrunk = _shrink_image(r.content)
                if shrunk is None:
                    continue
                # Always emit as .png filename — gpt-image-1 reads bytes
                # not extensions, but a clean filename helps logs.
                out.append((f"hero-{i + 1}.png", shrunk))
            except Exception:  # noqa: BLE001
                continue

    _BYTES_CACHE[key] = out
    return out


__all__ = [
    "HeroContext", "get_hero_context", "describe_hero_from_photos",
    "get_hero_photo_files", "invalidate_cache",
]
