"""The brand's logo on a generated scene.

A scene is a full-bleed photograph with no card around it, so the mark sits on
the picture itself. The things that would go wrong quietly:

  * the logo covering the subject, or vanishing into a background of its own tone;
  * the owner's "logo off" or chosen corner being ignored;
  * a broken or missing logo costing the picture.

No database and no network: the image model, storage and the DB are stand-ins.
"""

import contextlib
import json
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from james_os import brand_identity, hero_context, image_compose, imagegen, main, media, template_clone

pytestmark = pytest.mark.nodb


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _busy_left_plain_right(w=1024, h=1024) -> bytes:
    """Stripes everywhere except a calm bottom-right corner."""
    img = Image.new("RGB", (w, h), (40, 90, 160))
    d = ImageDraw.Draw(img)
    for x in range(0, w, 12):
        d.line([(x, 0), (x, h)], fill=(250, 250, 250), width=3)
    d.rectangle([w // 2, h // 2, w, h], fill=(40, 90, 160))
    return _png(img)


def _mark(color=(230, 30, 30, 255), size=(300, 120), opaque_bg=None) -> bytes:
    img = Image.new("RGBA", size, opaque_bg or (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([20, 20, size[0] - 20, size[1] - 20], fill=color)
    return _png(img)


def _red_bbox(png: bytes):
    """Bounding box of the logo's pure-red pixels in the output."""
    img = Image.open(BytesIO(png)).convert("RGB")
    mask = Image.new("L", img.size, 0)
    px, mp = img.load(), mask.load()
    for y in range(0, img.size[1], 2):
        for x in range(0, img.size[0], 2):
            r, g, b = px[x, y]
            if r > 200 and g < 70 and b < 70:
                mp[x, y] = 255
    return mask.getbbox()


def test_the_mark_goes_in_the_quietest_corner_at_a_modest_size():
    out = image_compose.stamp_logo(_busy_left_plain_right(), _mark())
    box = _red_bbox(out)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 > 512 and y0 > 512                      # the calm bottom-right corner
    assert (x1 - x0) <= 1024 * image_compose._LOGO_W + 4  # ~16% of the width at most
    assert Image.open(BytesIO(out)).size == (1024, 1024)


def test_the_owners_chosen_corner_wins():
    out = image_compose.stamp_logo(_busy_left_plain_right(), _mark(), position="top_left")
    x0, y0, x1, y1 = _red_bbox(out)
    assert x1 < 512 and y1 < 512


def test_a_logo_on_a_solid_background_becomes_a_rounded_tile():
    base = _png(Image.new("RGB", (1000, 800), (20, 120, 60)))
    out = Image.open(BytesIO(image_compose.stamp_logo(
        base, _mark(opaque_bg=(255, 255, 255, 255)), position="bottom_right"))).convert("RGB")
    W, H = out.size
    margin = round(W * image_compose._LOGO_MARGIN)
    # the tile's very corner is rounded off: the photo shows through there
    assert out.getpixel((W - margin - 1, H - margin - 1)) == (20, 120, 60)


def test_a_logo_the_same_tone_as_its_corner_gets_a_plate():
    white_sky = _png(Image.new("RGB", (1000, 1000), (245, 245, 245)))
    white_mark = _mark(color=(250, 250, 250, 255))
    out = Image.open(BytesIO(image_compose.stamp_logo(
        white_sky, white_mark, position="bottom_right"))).convert("L")
    # a dark plate went behind the white mark, so the corner is no longer all white
    margin = round(1000 * image_compose._LOGO_MARGIN)
    plate_px = out.getpixel((1000 - margin - 5, 1000 - margin + 5))
    assert plate_px < 200


def test_an_unreadable_logo_raises_so_the_caller_keeps_the_picture():
    with pytest.raises(Exception):
        image_compose.stamp_logo(_busy_left_plain_right(), b"<svg>not a raster</svg>")


# ------------------------------------------------------------ in the scene path


@pytest.fixture
def scene(monkeypatch):
    seen = {"look": {}, "logo": _mark(), "payload": {}}

    async def _photos(tenant_id=None, limit=None, *, likeness=False):
        return []

    async def _plain(topic, **kw):
        return _busy_left_plain_right(), {"prompt": "p"}, ""

    async def _look(tenant_id=None):
        return seen["look"]

    async def _logo(tenant_id):
        return seen["logo"]

    async def _palette(tenant_id=None):
        return None

    async def _none(*a, **kw):
        return None

    class _Store:
        def save(self, tenant, png, name):
            seen["saved"] = png
            return f"file://{name}", f"/tmp/{name}"

    class _Conn:
        async def execute(self, sql, action_id, payload):
            seen["payload"].update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(hero_context, "get_hero_photo_files", _photos)
    monkeypatch.setattr(imagegen, "generate_post_image", _plain)
    monkeypatch.setattr(brand_identity, "get_brand_look", _look)
    monkeypatch.setattr(brand_identity, "get_brand_palette", _palette)
    monkeypatch.setattr(template_clone, "_brand_logo", _logo)
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", _none)
    monkeypatch.setattr(main, "acquire", _acq)
    return seen


async def _draw():
    return await main._generate_scene_post_image("act-1", "a hawk", "Caption.", "tenant-1")


async def test_the_scene_carries_the_brands_logo(scene):
    await _draw()
    assert scene["payload"]["logo_applied"] is True
    assert _red_bbox(scene["saved"]) is not None


async def test_logo_off_means_no_logo(scene):
    scene["look"] = {"logo_show": False}
    await _draw()
    assert scene["payload"]["logo_applied"] is False
    assert _red_bbox(scene["saved"]) is None


async def test_no_logo_uploaded_leaves_the_picture_alone(scene):
    scene["logo"] = None
    await _draw()
    assert scene["payload"]["logo_applied"] is False
    assert _red_bbox(scene["saved"]) is None


async def test_a_broken_logo_never_costs_the_picture(scene):
    scene["logo"] = b"not an image"
    uri, kind = await _draw()
    assert kind == "scene" and uri == "file://scene-1080x1080.png"
    assert scene["payload"]["logo_applied"] is False
    assert _red_bbox(scene["saved"]) is None


# ------------------------------------------------------------ the logo as a layer


def test_the_logo_layer_is_exactly_what_was_stamped():
    """stamp_logo_layers gives the same picture as stamp_logo, plus the mark as a
    piece that, put back on the clean picture at its box, rebuilds it exactly."""
    clean = _busy_left_plain_right()
    flat = image_compose.stamp_logo(clean, _mark())
    stamped, badge = image_compose.stamp_logo_layers(clean, _mark())
    assert stamped == flat
    base = Image.open(BytesIO(clean)).convert("RGBA")
    piece = Image.open(BytesIO(badge["png"])).convert("RGBA")
    assert piece.size == (badge["w"], badge["h"])
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(piece, (badge["x"], badge["y"]))
    rebuilt = Image.alpha_composite(base, layer)
    assert list(rebuilt.getdata()) == list(Image.open(BytesIO(flat)).convert("RGBA").getdata())


async def test_the_scene_keeps_its_clean_frame_and_logo_as_layers(scene, monkeypatch):
    from james_os import layer_capture

    kept = {}

    async def _keep(action_id, tenant_id, cap, *, of, drift_=0.0, slide=None):
        kept.update(action_id=action_id, cap=cap, of=of)
        return {}

    monkeypatch.setattr(layer_capture, "keep", _keep)
    uri, _ = await _draw()
    assert kept["action_id"] == "act-1" and kept["of"] == uri
    cap = kept["cap"]
    assert tuple(cap["canvas"]) == (1080, 1080) and cap["texts"] == []
    (logo,) = cap["images"]
    assert logo["kind"] == "logo" and logo["w"] > 0 and logo["h"] > 0
    assert _red_bbox(cap["plate"]) is None, "the plate is the picture WITHOUT the logo"
    assert _red_bbox(logo["png"]) is not None


async def test_no_layers_without_a_stamped_logo(scene, monkeypatch):
    from james_os import layer_capture

    calls = []

    async def _keep(*a, **k):
        calls.append(a)
        return {}

    monkeypatch.setattr(layer_capture, "keep", _keep)
    scene["look"] = {"logo_show": False}
    await _draw()
    assert calls == []
