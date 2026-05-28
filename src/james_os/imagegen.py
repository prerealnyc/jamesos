"""Image generation — two flavours, one OpenAI client.

1. `generate_seed_image` — B-roll seed stills, returned as a data URI so
   Runway can consume them directly (no public hosting required).
2. `generate_post_image` — hero images for LinkedIn/Twitter/IG posts.
   Returns the decoded PNG bytes + the prompt, so the caller can persist
   to storage and create a media_asset row. Tuned for an editorial,
   uncrowded, single-focal-point aesthetic — the kind of image you'd
   actually post alongside a text post, not a busy collage.

Stub-honest: with no OpenAI key both return (None, reason) — never a
fake image.
"""

import base64

from openai import AsyncOpenAI

from .config import settings

# gpt-image-1 sizes: 1024x1024 | 1024x1536 (portrait) | 1536x1024 (landscape)
_SIZE_FOR_ASPECT = {
    "9:16": "1024x1536",
    "16:9": "1536x1024",
    "1:1": "1024x1024",
}

# Per-platform default aspect for post hero images. Twitter and LinkedIn
# render best at 16:9 (1.91:1 cropping aside); IG feed at 1:1; vertical
# 9:16 only for IG/TT/YT Shorts (not a "post image" surface).
_POST_ASPECT = {
    "twitter":  "16:9",
    "x":        "16:9",
    "linkedin": "16:9",
    "facebook": "16:9",
    "instagram": "1:1",
    "ig":       "1:1",
}

# Style prefix for post hero images. Tuned for clarity over flash:
#   * editorial illustration, not photoreal — avoids uncanny-faces issues
#     and produces a cleaner feed scroll-stopper.
#   * uncluttered composition, single focal point, plenty of negative
#     space — direct counter to "image not too crowded".
#   * no text overlays, no logos, no faces unless integral — the post
#     copy carries the message; the image is the hook.
_POST_STYLE = (
    "Clean editorial illustration, modern flat-vector aesthetic. "
    "Single clear focal point, simple uncluttered composition with plenty "
    "of negative space. Restrained color palette (2–4 tones). "
    "No text overlays, no logos, no busy background detail, no faces "
    "unless integral to the topic. Professional but inviting; suitable "
    "as a LinkedIn/Twitter/Instagram post hero image."
)


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


async def generate_seed_image(prompt: str, aspect: str = "9:16") -> tuple[str | None, str]:
    """Text → still image. Returns (data_uri, error). data_uri is
    'data:image/png;base64,...' suitable as a Runway promptImage."""
    client = _client()
    if client is None:
        return None, "No OpenAI key — add it in Settings to generate B-roll seed images."
    size = _SIZE_FOR_ASPECT.get(aspect, "1024x1536")
    style = (settings.image_style or "").strip()
    full_prompt = f"{style} {prompt}".strip() if style else prompt
    try:
        res = await client.images.generate(
            model=settings.image_model,
            prompt=full_prompt[:1000],
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"image generation failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        url = getattr(item, "url", None) if item else None
        if url:
            return url, ""  # some models return a URL instead of b64
        return None, "image model returned no image"
    return f"data:image/png;base64,{b64}", ""


def _build_post_prompt(topic: str, brief: str = "") -> str:
    """Compose the final text prompt for a post hero image. Topic is the
    subject line; brief is optional extra context (e.g. the draft body or
    a specific angle). Style prefix forces the editorial look."""
    parts = [_POST_STYLE, f"Subject: {topic.strip()}."]
    if brief.strip():
        parts.append(f"Context: {brief.strip()[:240]}")
    return " ".join(parts)[:1000]


async def generate_post_image(
    topic: str,
    platform: str = "linkedin",
    brief: str = "",
    aspect: str = "",
) -> tuple[bytes | None, dict, str]:
    """Topic → PNG bytes for a shareable post hero image.

    Returns (png_bytes, meta, error). meta carries the final prompt,
    chosen size, model and platform so the caller can persist it for
    later reproducibility.

    aspect override beats per-platform default; falls back to 16:9 when
    neither is recognised — the most universal social ratio.
    """
    client = _client()
    if client is None:
        return None, {}, (
            "No OpenAI key — add OPENAI_API_KEY in Settings to enable "
            "post-image generation."
        )
    topic = (topic or "").strip()
    if not topic:
        return None, {}, "topic is required"
    chosen_aspect = (aspect or "").strip() or _POST_ASPECT.get(
        platform.lower(), "16:9"
    )
    size = _SIZE_FOR_ASPECT.get(chosen_aspect, "1536x1024")
    full_prompt = _build_post_prompt(topic, brief)
    try:
        res = await client.images.generate(
            model=settings.image_model,
            prompt=full_prompt,
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, {}, f"image generation failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        # gpt-image-1 always returns b64_json. A URL path would mean the
        # caller has to fetch separately — we refuse to fake a "success"
        # without the actual bytes in hand.
        return None, {}, "image model returned no PNG bytes"
    try:
        png = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"could not decode image bytes: {e}"
    meta = {
        "prompt": full_prompt,
        "size": size,
        "aspect": chosen_aspect,
        "platform": platform,
        "model": settings.image_model,
        "topic": topic,
    }
    return png, meta, ""


__all__ = ["generate_seed_image", "generate_post_image"]
