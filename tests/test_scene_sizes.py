"""A generated scene in the shapes the owner asked for.

gpt-image-1 draws three native canvases (square, 1024x1536 tall, 1536x1024
wide). A scene is delivered at exact platform pixels — 1080x1080, 1080x1350,
1080x1920, 1920x1080 — cut from the right canvas, and several shapes that share
a canvas share ONE paid draw. The things that would go wrong quietly:

  * a size labelled 1080x1350 that is really some other shape;
  * paying for a draw per size when one draw serves several;
  * one failed extra size costing the whole post;
  * the logo placed before the crop, and then cut off.
"""

import contextlib
import json
from io import BytesIO

import pytest
from PIL import Image

from james_os import api_v1, brand_identity, hero_context, image_compose, imagegen, main, media

pytestmark = pytest.mark.nodb

_NATIVE = {"1:1": (1024, 1024), "9:16": (1024, 1536), "16:9": (1536, 1024)}


def _png(size):
    buf = BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def world(monkeypatch):
    seen = {"draws": [], "saved": {}, "payload": {}, "fail": set(), "logo_calls": 0}

    async def _photos(tenant_id=None, limit=None, *, likeness=False):
        return []

    async def _plain(topic, **kw):
        aspect = kw.get("aspect")
        seen["draws"].append(aspect)
        if aspect in seen["fail"]:
            return None, {}, "the model refused"
        return _png(_NATIVE[aspect]), {"prompt": "p"}, ""

    async def _palette(tenant_id=None):
        return None

    async def _logo(tenant_id, png):
        seen["logo_calls"] += 1
        seen.setdefault("logo_sizes", []).append(Image.open(BytesIO(png)).size)
        return png, True

    async def _none(*a, **kw):
        return None

    class _Store:
        def save(self, tenant, png, name):
            seen["saved"][name] = Image.open(BytesIO(png)).size
            return f"file://{name}", f"/tmp/{name}"

    class _Conn:
        async def execute(self, sql, action_id, payload):
            seen["payload"].update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(hero_context, "get_hero_photo_files", _photos)
    monkeypatch.setattr(imagegen, "generate_post_image", _plain)
    monkeypatch.setattr(brand_identity, "get_brand_palette", _palette)
    monkeypatch.setattr(main, "_scene_logo", _logo)
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", _none)
    monkeypatch.setattr(main, "acquire", _acq)
    return seen


async def _draw(**kw):
    return await main._generate_scene_post_image("act-1", "a hawk", "Caption.", "tenant-1", **kw)


@pytest.mark.parametrize("shape,native,pixels", [
    ("1:1", "1:1", (1080, 1080)),
    ("4:5", "9:16", (1080, 1350)),
    ("9:16", "9:16", (1080, 1920)),
    ("16:9", "16:9", (1920, 1080)),
])
async def test_each_shape_is_drawn_on_its_canvas_and_cut_to_exact_pixels(world, shape, native, pixels):
    uri, _ = await _draw(aspect=shape)
    assert world["draws"] == [native]
    w, h = pixels
    assert uri == f"file://scene-{w}x{h}.png"
    assert world["saved"][f"scene-{w}x{h}.png"] == pixels
    assert world["payload"]["image_urls_by_size"] == {f"{w}x{h}": uri}
    assert world["payload"]["scene_shape"] == shape


async def test_portrait_and_story_share_one_draw(world):
    await _draw(aspect="4:5", extra_shapes=("9:16",))
    assert world["draws"] == ["9:16"], "one paid draw, two crops"
    assert set(world["payload"]["image_urls_by_size"]) == {"1080x1350", "1080x1920"}


async def test_every_size_is_three_draws_and_four_pictures(world):
    uri, _ = await _draw(aspect="1:1", extra_shapes=("4:5", "9:16", "16:9"))
    assert sorted(world["draws"]) == ["16:9", "1:1", "9:16"]
    sizes = world["payload"]["image_urls_by_size"]
    assert set(sizes) == {"1080x1080", "1080x1350", "1080x1920", "1920x1080"}
    assert uri == sizes["1080x1080"]
    assert world["payload"]["image_url"] == uri


async def test_the_logo_goes_on_after_the_crop_on_every_size(world):
    await _draw(aspect="1:1", extra_shapes=("4:5", "9:16", "16:9"))
    assert world["logo_calls"] == 4
    assert sorted(world["logo_sizes"]) == sorted([(1080, 1080), (1080, 1350), (1080, 1920), (1920, 1080)])


async def test_a_failed_extra_size_is_left_out_not_the_post(world):
    world["fail"] = {"16:9"}
    uri, _ = await _draw(aspect="4:5", extra_shapes=("16:9",))
    assert uri == "file://scene-1080x1350.png"
    assert set(world["payload"]["image_urls_by_size"]) == {"1080x1350"}


async def test_a_failed_primary_still_raises_so_a_real_photo_is_used(world):
    world["fail"] = {"9:16"}
    with pytest.raises(RuntimeError):
        await _draw(aspect="4:5", extra_shapes=("16:9",))


async def test_unknown_or_repeated_shapes_are_ignored(world):
    await _draw(aspect="", extra_shapes=("1:1", "3:2", "1:1"))
    assert world["draws"] == ["1:1"]
    assert set(world["payload"]["image_urls_by_size"]) == {"1080x1080"}


def test_fit_to_frame_gives_exact_pixels_without_squashing():
    tall = _png((1024, 1536))
    out = Image.open(BytesIO(image_compose.fit_to_frame(tall, 1080, 1350)))
    assert out.size == (1080, 1350)
    wide = Image.open(BytesIO(image_compose.fit_to_frame(_png((1536, 1024)), 1920, 1080)))
    assert wide.size == (1920, 1080)


def test_the_request_takes_extra_sizes():
    r = api_v1.GenerateRequest(brief="x", scene_aspect="4:5", scene_sizes=["9:16", "16:9"])
    assert r.scene_sizes == ["9:16", "16:9"]
    assert api_v1.GenerateRequest(brief="x").scene_sizes == []
    with pytest.raises(Exception):
        api_v1.GenerateRequest(brief="x", scene_sizes=["3:2"])
