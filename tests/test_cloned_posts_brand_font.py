"""Copied and remixed posts render in the brand's font theme.

The nine designed formats scope image_compose.brand_fonts() over every render,
so the typeface the owner picked (POST /v1/brand/font) reaches them. A cloned
competitor template (template_clone.clone_post) and its rebuild
(rebuild_design) called render_spec outside that scope, so they came out in the
house faces whatever the brand had chosen. Both now render inside it; no theme
set (or an unknown key) is the house default exactly as before.
"""

from __future__ import annotations

import pytest

from james_os import brand_identity, font_themes, image_compose
from james_os import template_clone as tc

pytestmark = pytest.mark.nodb

SPEC = {"status": "ok", "kind": "graphic_card",
        "background": {"treatment": "solid"},
        "palette": {"bg": "#111111", "ink": "#ffffff", "accent": "#c9a24b"},
        "elements": [{"role": "headline", "box": {"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.3},
                      "size": "lg", "weight": "bold", "color": "#ffffff"}]}


def _capture_render(monkeypatch) -> list:
    """Replace render_spec with a stub that records the active font theme."""
    seen: list = []

    def render_spec(spec, content, *, hero_bytes=None, logo_bytes=None, palette=None, photos=None):
        seen.append(image_compose._render_theme.get())
        return b"png", "graphic_card"

    monkeypatch.setattr(tc, "render_spec", render_spec)
    return seen


def _common(monkeypatch, font_key: str):
    async def get_brand_font(tenant_id=None):
        return font_key

    async def none(*a, **kw):
        return None

    async def fill(spec, tenant_id, ref, **kw):
        return {"headline": "Hello"}

    async def extract(img):
        return dict(SPEC)

    async def fetch(url):
        return b"competitor-post"

    async def save(*a, **kw):
        return None

    from james_os import design_templates
    monkeypatch.setattr(brand_identity, "get_brand_font", get_brand_font)
    monkeypatch.setattr(tc, "_brand_palette", none)
    monkeypatch.setattr(tc, "_brand_logo", none)
    monkeypatch.setattr(tc, "_fill_copy", fill)
    monkeypatch.setattr(tc, "extract_template_spec", extract)
    monkeypatch.setattr(tc, "_fetch_bytes", fetch)
    monkeypatch.setattr(design_templates, "save", save)


@pytest.mark.asyncio
async def test_clone_post_renders_in_the_brand_font(monkeypatch):
    seen = _capture_render(monkeypatch)
    _common(monkeypatch, "editorial")
    out = await tc.clone_post({"id": "p1", "stored_media_url": "https://x/p.png", "handle": "h"},
                              "tenant", hero_bytes=b"photo", hero_generated=False)
    assert out and out["png"] == b"png"
    assert seen == [font_themes.resolve("editorial")]
    assert seen[0]["display"].endswith("PlayfairDisplay-Black.ttf")


@pytest.mark.asyncio
async def test_no_brand_font_is_the_house_default(monkeypatch):
    seen = _capture_render(monkeypatch)
    _common(monkeypatch, "")
    await tc.clone_post({"id": "p1", "stored_media_url": "https://x/p.png"}, "tenant",
                        hero_bytes=b"photo")
    assert seen == [None]


@pytest.mark.asyncio
async def test_an_unknown_or_unreadable_font_falls_back_to_the_house_default(monkeypatch):
    seen = _capture_render(monkeypatch)
    _common(monkeypatch, "comic-sans")
    await tc.clone_post({"id": "p1", "stored_media_url": "https://x/p.png"}, "tenant",
                        hero_bytes=b"photo")

    async def boom(tenant_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(brand_identity, "get_brand_font", boom)
    await tc.clone_post({"id": "p2", "stored_media_url": "https://x/p.png"}, "tenant",
                        hero_bytes=b"photo")
    assert seen == [None, None]


@pytest.mark.asyncio
async def test_rebuild_renders_every_size_in_the_brand_font(monkeypatch):
    seen = _capture_render(monkeypatch)
    _common(monkeypatch, "condensed")

    async def hero_by_key(tenant_id, key):
        return b"photo"

    monkeypatch.setattr(tc, "_hero_by_key", hero_by_key)
    payload = {"clone_spec": dict(SPEC), "clone_content": {"headline": "Hello"},
               "hero_photo_key": "k1", "topic": "t"}
    out = await tc.rebuild_design(payload, "", "tenant", sizes=((1080, 1080), (1080, 1920)))
    assert out and set(out["by_size"]) == {"1080x1080", "1080x1920"}
    assert seen == [font_themes.resolve("condensed")] * 3
    # The theme is left behind nowhere: the next render starts from the default.
    assert image_compose._render_theme.get() is None


def test_the_theme_really_changes_the_face_spec_render_loads():
    """The scope does the remap — the clone path only needed to enter it."""
    with image_compose.brand_fonts(font_themes.resolve("elegant")):
        assert image_compose._remap_face(image_compose._ANTON).endswith("Cormorant-Bold.ttf")
        assert image_compose._remap_face(image_compose._ARCHIVO).endswith("Montserrat-SemiBold.ttf")
    assert image_compose._remap_face(image_compose._ANTON) == image_compose._ANTON
