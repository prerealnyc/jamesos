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

# Style library for post hero images. Each entry is a system-style
# prefix appended to the user's topic. All share the same uncluttered/
# no-text-overlays rules so a post image is never a busy collage. The
# style picker on /images selects between them per-render.
#
#   * editorial — clean flat-vector illustration. Best for metaphors and
#     concept hooks ("market shifting", "underpriced asset"). Cheapest
#     to reuse; can't go uncanny on faces.
#   * photoreal — modern documentary-photography aesthetic. Best for
#     real-world subjects (buildings, neighborhoods, offices, objects).
#     Faces still risk uncanny; the prompt asks for environments not
#     portraits unless the topic requires them.
#   * minimal — high-contrast geometric / abstract. Best for newsletters
#     and Twitter, where a strong shape reads at thumbnail scale.
#   * bw_photo — black-and-white documentary photograph. Quiet, serious;
#     good for institutional or legacy-brand posts.
_BASE_RULES = (
    "Single clear focal point, simple uncluttered composition with plenty "
    "of negative space. No text overlays, no logos, no busy background "
    "detail, no faces unless integral to the topic. Professional but "
    "inviting; suitable as a LinkedIn/Twitter/Instagram post hero image."
)

POST_STYLES: dict[str, str] = {
    "editorial": (
        "Clean editorial illustration, modern flat-vector aesthetic. "
        "Restrained color palette (2–4 tones). " + _BASE_RULES
    ),
    "photoreal": (
        "Photorealistic editorial photograph in the style of a modern "
        "documentary or high-quality stock image. Natural lighting, "
        "shallow depth of field, real-world setting. Prefer environments, "
        "buildings, objects and wide scenes over close-up faces. "
        + _BASE_RULES
    ),
    "minimal": (
        "Bold, high-contrast minimal composition. Strong geometric "
        "shapes, large fields of color, abstracted or symbolic. Reads "
        "clearly at thumbnail scale. " + _BASE_RULES
    ),
    "bw_photo": (
        "Black-and-white documentary photograph. Quiet, serious, "
        "classic editorial framing. Natural lighting, real-world setting. "
        + _BASE_RULES
    ),
    "cinematic": (
        # The "political-ad / Netflix-documentary cutaway" aesthetic.
        # Matches the reference videos the user provided — strong
        # symbolic single-subject framing with hard directional light.
        # Used by story_audio + avatar_story_mix when image_style=
        # 'cinematic'; the prompt-generation system in story_video.py
        # also switches to a metaphor-finding system prompt to pair
        # with this look. The two together (prefix + prompt system)
        # are what makes the output read like a film still, not stock.
        "Cinematic single-subject photograph in the style of a "
        "political ad or Netflix documentary cutaway. Dramatic "
        "directional lighting — spotlight, hard side light, window "
        "beam, or rim light — with deep shadows and high contrast. "
        "Desaturated cool palette: deep blues, muted browns, "
        "occasional warm rim. Shallow depth of field. Atmospheric "
        "details (dust particles, paper in motion, light beams, light "
        "haze) where appropriate. Hero/close-up framing. Heavy mood, "
        "gravitas. Feels like a film still, never like stock photography. "
        + _BASE_RULES
    ),
    "cinematic_real": (
        # DEFAULT for post hero images. Premium, photorealistic, emotionally
        # grounded — a film still, never flat-vector illustration or stock.
        # Allows a real human subject with genuine emotion (the stories are
        # first-person), so it deliberately does NOT inherit the no-faces
        # _BASE_RULES.
        "Cinematic, photorealistic film still in the style of premium editorial "
        "photography or a prestige brand campaign. Emotional realism with a real "
        "human subject when the story is personal. Golden-hour or hard "
        "directional natural light, warm tones, shallow depth of field, "
        "ultra-detailed, upscale real-world setting, subtle ambition and tension. "
        "Looks like a frame from a prestige film — never stock photography, never "
        "illustration. Single strong focal point. Absolutely no text, numbers, "
        "words, logos, charts, or watermarks."
    ),
}
_DEFAULT_STYLE = "cinematic_real"


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


def _build_post_prompt(
    topic: str, brief: str, style: str, brand_guidelines: str = ""
) -> str:
    """Compose the final text prompt for a post hero image. The style
    prefix is the lever — same topic, different prefix = different
    aesthetic. Unknown style falls back to editorial silently rather
    than crashing the render. When brand_guidelines are supplied they are
    woven in so the image follows the brand's visual rules."""
    prefix = POST_STYLES.get(style.lower(), POST_STYLES[_DEFAULT_STYLE])
    parts = [prefix, f"Subject: {topic.strip()}."]
    if brief.strip():
        parts.append(f"Context: {brief.strip()[:240]}")
    if brand_guidelines.strip():
        parts.append(
            "Follow the brand's visual guidelines exactly: "
            f"{brand_guidelines.strip()[:500]}"
        )
    return " ".join(parts)[:1300]


_IMG_DIRECTOR_SYSTEM = (
    "You are a cinematic photo director for a premium personal brand. Given a "
    "first-person story, write ONE image-generation prompt for a single "
    "photorealistic still that captures the story's EMOTIONAL TENSION — never a "
    "literal, generic, or stock scene.\n"
    "Requirements:\n"
    "- Cinematic film-still realism: name the lighting (e.g. golden hour, hard "
    "window light, rim light), shallow depth of field, ultra-realistic detail, "
    "warm tones, emotional realism, an upscale/premium setting.\n"
    "- Ground it in the story's specific scene, stakes, and subtext (e.g. "
    "intuition vs algorithms, confidence under skepticism, momentum and demand, "
    "premium positioning, testing the ceiling).\n"
    "- A real human subject showing genuine, specific emotion is encouraged for "
    "personal stories.\n"
    "- Absolutely NO text, numbers, words, logos, charts, or watermarks in the image.\n"
    "- FORBIDDEN clichés that kill the tension: 'realtor with house', 'happy "
    "couple buying a home', 'businessman holding paperwork', generic smiling stock.\n"
    'Return STRICT JSON: {"prompt": str} — the prompt 40-80 words, no quotes inside.'
)


async def direct_image_scene(story: str, fallback_topic: str = "") -> str:
    """LLM image director: turn a post's story into ONE cinematic, realistic
    scene prompt that carries its emotional tension (the post equivalent of the
    video pipeline's write_image_prompts). Best-effort — returns fallback_topic
    if the LLM is unavailable or errors, so image generation never depends on it."""
    story = (story or "").strip()
    if not story:
        return fallback_topic
    try:
        from .llm import get_llm

        out = await get_llm().complete_json(
            system=_IMG_DIRECTOR_SYSTEM,
            messages=[{"role": "user", "content": story[:2000]}],
            max_tokens=300, temperature=0.7,
        )
        scene = str((out or {}).get("prompt") or "").strip()
        return scene or fallback_topic
    except Exception:  # noqa: BLE001
        return fallback_topic


async def _brand_visual_directive(tenant_id) -> str:
    """Pull the brand's visual/style guidelines from memory so generated
    POST images follow them (colours, imagery, layout, look). Returns ""
    when no tenant is given (e.g. video B-roll callers, which stay
    independent of post guidelines) or no guideline material exists.
    Best-effort — never blocks a render."""
    if not tenant_id:
        return ""
    try:
        from .retrieval import search

        hits = await search(
            "brand visual style: colours, typography, logo usage, imagery and "
            "photography style, layout, what posts should look like",
            tenant_id=tenant_id,
        )
        gl = [h for h in hits if (h.payload or {}).get("category") == "guideline"]
        return " ".join((h.raw_content or "") for h in gl[:4]).strip()[:700]
    except Exception:  # noqa: BLE001
        return ""


async def generate_post_image(
    topic: str,
    platform: str = "linkedin",
    brief: str = "",
    aspect: str = "",
    style: str = _DEFAULT_STYLE,
    tenant_id=None,
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
    chosen_style = (style or _DEFAULT_STYLE).lower()
    if chosen_style not in POST_STYLES:
        chosen_style = _DEFAULT_STYLE
    guidelines = await _brand_visual_directive(tenant_id)
    full_prompt = _build_post_prompt(topic, brief, chosen_style, guidelines)
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
        "style": chosen_style,
    }
    return png, meta, ""


async def generate_post_image_with_refs(
    topic: str,
    *,
    references: list[tuple[str, bytes]],   # [(filename, bytes), ...]
    platform: str = "linkedin",
    brief: str = "",
    aspect: str = "",
    style: str = _DEFAULT_STYLE,
) -> tuple[bytes | None, dict, str]:
    """Topic + reference image bytes → PNG bytes.

    Calls gpt-image-1's edit endpoint instead of generate, passing 1-3
    reference photos as visual conditioning. Used for hero-tagged
    beats so the recurring character stays visually consistent across
    a slideshow, rather than the LLM-text-description path which
    produces a different generic person every beat.

    References should already be resized to <4 MB each (see
    hero_context.get_hero_photo_files). The returned PNG is the same
    shape/aspect as generate_post_image — the worker treats both paths
    identically downstream.

    Honest fallback: if `references` is empty, calls through to
    generate_post_image so the caller can pass refs unconditionally
    without an `if` ladder. Single source of truth for the prompt build.
    """
    if not references:
        return await generate_post_image(
            topic, platform=platform, brief=brief, aspect=aspect, style=style,
        )
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
    chosen_style = (style or _DEFAULT_STYLE).lower()
    if chosen_style not in POST_STYLES:
        chosen_style = _DEFAULT_STYLE
    # The prompt explicitly tells the model to preserve the recurring
    # subject from the references — without this nudge, gpt-image-1 will
    # sometimes ignore the reference identity for stylistic reasons.
    edit_prompt = (
        _build_post_prompt(topic, brief, chosen_style)
        + " The recurring person from the reference photos must be "
        "rendered as the subject of this scene with the same face, "
        "build, hair, beard, and signature dress. Do not substitute "
        "a different person."
    )[:1000]
    # OpenAI's SDK accepts file-like inputs for images.edit. BytesIO
    # works directly; gpt-image-1 reads bytes regardless of extension.
    from io import BytesIO
    image_files = [
        (name, BytesIO(data), "image/png")
        for name, data in references
    ]
    try:
        res = await client.images.edit(
            model=settings.image_model,
            image=image_files,
            prompt=edit_prompt,
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, {}, f"image edit failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        return None, {}, "image model returned no PNG bytes"
    try:
        png = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"could not decode image bytes: {e}"
    meta = {
        "prompt": edit_prompt,
        "size": size,
        "aspect": chosen_aspect,
        "platform": platform,
        "model": settings.image_model,
        "topic": topic,
        "style": chosen_style,
        "used_refs": len(references),
    }
    return png, meta, ""


__all__ = [
    "generate_seed_image", "generate_post_image",
    "generate_post_image_with_refs", "POST_STYLES",
]
