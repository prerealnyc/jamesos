"""One picture in the brand's world, on its own — for the card editor.

The generative scene door (main._generate_scene_post_image) only ran inside a
whole new post: it drew the picture, stamped the logo and attached it to an
action. The card editor needs just the picture, to drop in as a background or
in place of a photo ("Munnar tea gardens at dawn" instead of uploading one).

So this draws the same way — the caller's composed description, the brand's
stored colours, up to three of the brand's own photos as style references
(gpt-image-1's edit endpoint, ref_mode="brand_style"), the brand's written
guidelines folded in by imagegen — and stops there:
  * no logo: the card has its own logo layer, and a mark burned into a
    background would sit under (or fight) the one on the card;
  * no action is touched: it is filed in the media library (role post_image,
    tagged generated + card-background) so the next card can pick it from Brand
    images, and its URL is returned.

It reuses main's helpers without changing them, so the post path and this one
cannot drift apart. Spend is recorded by imagegen as for every other draw.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

# The shapes gpt-image-1 actually draws (imagegen._SIZE_FOR_ASPECT); anything
# else silently comes back landscape.
ASPECTS = ("1:1", "9:16", "16:9")


def aspect_for(width: int, height: int) -> str:
    """The drawable shape closest to a card of this size: tall cards get 9:16,
    wide ones 16:9, everything near square 1:1. The card covers and crops."""
    if width <= 0 or height <= 0:
        return "1:1"
    ratio = width / height
    if ratio < 0.9:
        return "9:16"
    if ratio > 1.1:
        return "16:9"
    return "1:1"


async def draw(tenant_id: UUID, prompt: str, *, aspect: str = "1:1",
               title: str = "", brand_context: str = "") -> dict:
    """Draw one brand-styled picture. Returns {url, aspect, reference_photos, prompt}.
    Raises RuntimeError with a plain sentence when nothing could be drawn.

    `brand_context` is who the brand is, from Brand Manager (image_context
    .brand_background); without it the engine's own line is used, so the picture
    carries the same brand background as every other image (imagegen.image_brief)."""
    from . import main as _main
    from .brand_identity import brand_background
    from .imagegen import generate_post_image, generate_post_image_with_refs, image_brief
    from .media import create_media
    from .media import storage as media_storage

    scene = " ".join((prompt or "").split())
    if not scene:
        raise RuntimeError("describe the picture to draw")
    shape = aspect if aspect in ASPECTS else "1:1"
    palette = await _main._palette_line(tenant_id)
    subject = f"{scene} {palette}" if palette else scene
    # Matched on the scene alone, as the post path does: the palette line would
    # otherwise be most of what the photo matcher reads for a short request.
    refs = await _main._scene_reference_photos(tenant_id, scene)
    background = (brand_context or "").strip() or await brand_background(tenant_id)
    png, meta, err = None, {}, ""
    with image_brief(brand=background, topic=scene):
        if refs:
            png, meta, err = await generate_post_image_with_refs(
                topic=subject, references=refs, platform="instagram", brief="",
                aspect=shape, tenant_id=tenant_id, ref_mode="brand_style",
                prompt_cap=_main.SCENE_PROMPT_CAP,
            )
            if err or not png:
                logger.warning("card scene with brand references failed (%s) — drawing from text alone", err)
                refs = []
        if not png:
            png, meta, err = await generate_post_image(
                topic=subject, platform="instagram", brief="", aspect=shape,
                tenant_id=tenant_id, prompt_cap=_main.SCENE_PROMPT_CAP,
            )
    if err or not png:
        raise RuntimeError(err or "the picture could not be drawn — try again")
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, str(tenant_id), png, "card-scene.png"
    )
    used_prompt = str(meta.get("prompt") or scene)[:500]
    try:
        await create_media(
            role="post_image", source_type="generated", uri=served_uri,
            file_path=file_path, title=(title or scene)[:120], platform="instagram",
            mime="image/png", tags=["style:scene", "generated", "card-background"],
            notes=used_prompt, tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001 — library bookkeeping must not lose the URL
        logger.warning("could not file the card scene in the library", exc_info=True)
    return {
        "url": served_uri,
        "aspect": shape,
        # Which of the brand's own photos set the look — the honest answer to
        # "why does this look like us".
        "reference_photos": [name for name, _ in refs],
        "prompt": used_prompt,
    }


__all__ = ["ASPECTS", "aspect_for", "draw"]
