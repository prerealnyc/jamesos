"""Seed-image generation for B-roll.

Runway's dev API is image-conditioned — it animates a still. So to make
B-roll from a text idea we first render a still with OpenAI's image model,
then hand it to Runway as the seed. The image is returned as a base64 data
URI so Runway can consume it directly (no public hosting required).

Stub-honest: with no OpenAI key this returns None and a reason — never a
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
    try:
        res = await client.images.generate(
            model=settings.image_model,
            prompt=prompt[:900],
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


__all__ = ["generate_seed_image"]
