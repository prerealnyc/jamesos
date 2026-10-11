"""The card editor's picture door: one brand-styled picture, no action, no logo."""
from uuid import uuid4

import pytest

from james_os import scene_image

pytestmark = pytest.mark.nodb


def test_card_shapes_map_to_drawable_aspects():
    assert scene_image.aspect_for(1080, 1350) == "9:16"   # a portrait card
    assert scene_image.aspect_for(1080, 1080) == "1:1"
    assert scene_image.aspect_for(1200, 628) == "16:9"
    assert scene_image.aspect_for(0, 0) == "1:1"


@pytest.mark.asyncio
async def test_draw_uses_brand_references_and_files_the_picture(monkeypatch):
    from james_os import imagegen, main, media

    calls = {}

    async def palette(tid):
        return "Brand colours: deep green and coral."

    async def refs(tid, subject):
        calls["ref_subject"] = subject
        return [("own-1.jpg", b"x")]

    async def with_refs(**kw):
        calls["with_refs"] = kw
        return b"PNG", {"prompt": "drawn"}, ""

    async def plain(**kw):  # pragma: no cover — must not be needed
        raise AssertionError("text-only fallback should not run")

    class _Store:
        def save(self, tenant, data, name):
            calls["saved"] = (tenant, data, name)
            return "https://cdn.test/card-scene.png", "/tmp/x.png"

    async def create(**kw):
        calls["media"] = kw
        return {}

    monkeypatch.setattr(main, "_palette_line", palette)
    monkeypatch.setattr(main, "_scene_reference_photos", refs)

    real_brief = imagegen.image_brief

    def brief(brand="", topic=""):
        calls["brief"] = (brand, topic)
        return real_brief(brand=brand, topic=topic)

    monkeypatch.setattr(imagegen, "image_brief", brief)
    monkeypatch.setattr(imagegen, "generate_post_image_with_refs", with_refs)
    monkeypatch.setattr(imagegen, "generate_post_image", plain)
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", create)

    out = await scene_image.draw(uuid4(), "  misty tea gardens at dawn ", aspect="9:16",
                                 brand_context="A Kerala trip planner")

    assert out["url"] == "https://cdn.test/card-scene.png"
    assert out["reference_photos"] == ["own-1.jpg"]
    assert calls["ref_subject"] == "misty tea gardens at dawn"
    assert calls["with_refs"]["ref_mode"] == "brand_style"
    assert calls["with_refs"]["aspect"] == "9:16"
    assert "deep green" in calls["with_refs"]["topic"]
    assert "card-background" in calls["media"]["tags"]
    assert calls["brief"] == ("A Kerala trip planner", "misty tea gardens at dawn")


@pytest.mark.asyncio
async def test_draw_falls_back_to_text_and_refuses_an_empty_prompt(monkeypatch):
    from james_os import imagegen, main, media

    async def none_refs(tid, subject):
        return []

    async def palette(tid):
        return ""

    async def plain(**kw):
        return b"PNG", {}, ""

    class _Store:
        def save(self, tenant, data, name):
            return "https://cdn.test/a.png", "/tmp/a.png"

    async def create(**kw):
        raise RuntimeError("library down")  # bookkeeping failure must not lose the URL

    monkeypatch.setattr(main, "_palette_line", palette)
    monkeypatch.setattr(main, "_scene_reference_photos", none_refs)
    from james_os import brand_identity

    async def bg(tid=None):
        return "Engine's own line"

    monkeypatch.setattr(brand_identity, "brand_background", bg)
    monkeypatch.setattr(imagegen, "generate_post_image", plain)
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", create)

    out = await scene_image.draw(uuid4(), "a beach", aspect="weird")
    assert out["url"] == "https://cdn.test/a.png" and out["aspect"] == "1:1"
    with pytest.raises(RuntimeError):
        await scene_image.draw(uuid4(), "   ")
