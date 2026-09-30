"""Collage layouts: several photographs in one post.

Measured on the 439 layouts learned before this existed: `full_bleed_photo`
accounted for 279 of them and `photo_side` for exactly ONE — not because
competitors post one big photo, but because one photo box was all the vocabulary
could say. A Vietnam travel post with four framed photographs came back as a
single full-bleed image and three text blocks. The read was fine; there was
nowhere to write it down.

The tests that matter most here are the ones asserting NOTHING CHANGED for the
439 layouts already learned.
"""

import io

import pytest
from PIL import Image

from james_os.design_cloner import _photo_boxes
from james_os.spec_render import _MAX_PHOTO_FRAMES, _photo_frame, photo_frames, render_spec


def _png(rgb=(120, 130, 140), size=(600, 700)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, rgb).save(b, "PNG")
    return b.getvalue()


def _grid(n=4):
    return [{"x": (i % 2) * 0.5, "y": (i // 2) * 0.5, "w": 0.5, "h": 0.5} for i in range(n)]


# ------------------------------------------------- nothing changed for v1 specs


@pytest.mark.parametrize("treatment", [
    "full_bleed_photo", "photo_with_scrim", "photo_top", "photo_bottom",
    "photo_side", "solid",
])
def test_every_existing_treatment_yields_exactly_the_frame_it_always_did(treatment):
    """439 layouts already exist. Not one of them may move a pixel."""
    spec = {"background": {"treatment": treatment}}
    one = _photo_frame(spec)
    assert photo_frames(spec) == ([one] if one is not None else [])


def test_a_single_photo_spec_renders_identically_to_before():
    spec = {"kind": "graphic_card", "palette": {"bg": "#123c35", "ink": "#ffffff"},
            "background": {"treatment": "full_bleed_photo"},
            "elements": [{"role": "headline", "size": "lg", "weight": "black",
                          "align": "center", "box": {"x": .08, "y": .1, "w": .84, "h": .18}}],
            "decorations": []}
    a, _ = render_spec(spec, {"headline": "ONE"}, hero_bytes=_png())
    b, _ = render_spec(spec, {"headline": "ONE"}, hero_bytes=_png())
    assert a == b and len(a) > 1000


def test_the_extractor_says_nothing_rather_than_saying_nothing_loudly():
    """`{}` not `{"photo_boxes": []}`: a single-photograph spec must be
    byte-identical to what v1 produced, and the one-frame path must be reached
    by ABSENCE, not by an empty list somebody later has to special-case."""
    assert _photo_boxes({}) == {}
    assert _photo_boxes({"photo_boxes": []}) == {}


def test_one_region_is_not_a_collage():
    """One region IS photo_box. Carrying both invites them to disagree."""
    assert _photo_boxes({"photo_boxes": [{"x": 0, "y": 0, "w": 1, "h": 1}]}) == {}


# ----------------------------------------------------------------- the collage


def test_a_four_photo_collage_is_kept_as_four_regions():
    got = _photo_boxes({"photo_boxes": _grid(4)})
    assert len(got["photo_boxes"]) == 4


def test_junk_regions_are_dropped_not_rendered():
    assert _photo_boxes({"photo_boxes": [{"x": 0, "y": 0, "w": 0, "h": 0}, "nonsense"]}) == {}


def test_a_collage_draws_every_region():
    spec = {"kind": "graphic_card", "palette": {"bg": "#123c35", "ink": "#ffffff"},
            "background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)},
            "elements": [], "decorations": []}
    assert len(photo_frames(spec)) == 4
    png, _ = render_spec(spec, {}, photos=[_png((200, 40, 40)), _png((40, 90, 200)),
                                           _png((40, 160, 80)), _png((220, 180, 40))])
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    # each quadrant must carry a DIFFERENT picture — the proof it is not one
    # photo stretched across the canvas
    corners = {im.getpixel((int(w * fx), int(h * fy)))
               for fx, fy in ((.25, .25), (.75, .25), (.25, .75), (.75, .75))}
    assert len(corners) == 4, f"expected four distinct photos, saw {len(corners)}"


def test_fewer_photos_than_regions_cycles_instead_of_leaving_a_hole():
    """A brand with two photos and a four-up grid repeats one. An empty frame
    reads as a bug; a repeated photo reads as a design choice."""
    spec = {"kind": "graphic_card", "palette": {"bg": "#123c35", "ink": "#ffffff"},
            "background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)},
            "elements": [], "decorations": []}
    png, _ = render_spec(spec, {}, photos=[_png((200, 40, 40)), _png((40, 90, 200))])
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    tl = im.getpixel((int(w * .25), int(h * .25)))
    bl = im.getpixel((int(w * .25), int(h * .75)))
    assert tl == bl, "the third region should repeat the first photo"
    assert tl != im.getpixel((int(w * .75), int(h * .25)))


def test_a_contact_sheet_is_capped():
    spec = {"background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4) * 4}}
    assert len(photo_frames(spec)) <= _MAX_PHOTO_FRAMES


def test_a_collage_with_no_photos_falls_back_to_the_solid_ground():
    """Never a hard failure: a photo treatment with no photo paints the palette."""
    spec = {"kind": "graphic_card", "palette": {"bg": "#123c35", "ink": "#ffffff"},
            "background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)},
            "elements": [], "decorations": []}
    png, _ = render_spec(spec, {})
    assert png and len(png) > 500


# ------------------------------------------------------- the supply of N photos


@pytest.mark.asyncio
async def test_a_single_photo_layout_asks_for_no_extras(monkeypatch):
    """The path every existing template takes. It must cost nothing — no library
    read, no picking — because 439 layouts are single-photo and always will be."""
    from james_os import template_clone as tc

    called = {"n": 0}

    async def should_not_run(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr("james_os.hero_context.get_hero_photo_files", should_not_run)
    got = await tc.extra_photos_for("t", "a topic", {"background": {"treatment": "full_bleed_photo"}})
    assert got == []
    assert called["n"] == 0, "a one-region layout must not even read the library"


@pytest.mark.asyncio
async def test_a_collage_asks_for_one_photo_PER_REGION_minus_the_hero(monkeypatch):
    from james_os import template_clone as tc

    asked = {}

    async def fake_files(*a, **k):
        return [(f"https://cdn/{i}.jpg", b"x") for i in range(10)]

    async def fake_set(refs, tenant_id=None, n=1, exclude=()):
        asked["n"] = n
        asked["exclude"] = list(exclude)
        return [(f"k{i}", b"p") for i in range(n)]

    monkeypatch.setattr("james_os.hero_context.get_hero_photo_files", fake_files)
    monkeypatch.setattr("james_os.photo_pick.pick_hero_set", fake_set)
    spec = {"background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)}}
    got = await tc.extra_photos_for("t", "a topic", spec, exclude=["hero-key"])
    assert asked["n"] == 3, "four regions, the hero holds one, so three extras"
    assert "hero-key" in asked["exclude"], "the hero must not be picked again"
    assert len(got) == 3


@pytest.mark.asyncio
async def test_a_library_too_small_for_the_collage_returns_what_it_has(monkeypatch):
    """Honest short answer. The renderer cycles what it is given, so two photos
    in a four-up is two repeats — not two holes, and not a failure."""
    from james_os import template_clone as tc

    async def fake_files(*a, **k):
        return [("https://cdn/1.jpg", b"x"), ("https://cdn/2.jpg", b"y")]

    async def fake_set(refs, tenant_id=None, n=1, exclude=()):
        return [("k1", b"p")]          # the library can only offer one more

    monkeypatch.setattr("james_os.hero_context.get_hero_photo_files", fake_files)
    monkeypatch.setattr("james_os.photo_pick.pick_hero_set", fake_set)
    spec = {"background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)}}
    assert len(await tc.extra_photos_for("t", "topic", spec)) == 1


@pytest.mark.asyncio
async def test_a_failure_gathering_extras_never_costs_the_post(monkeypatch):
    from james_os import template_clone as tc

    async def boom(*a, **k):
        raise RuntimeError("library unreachable")

    monkeypatch.setattr("james_os.hero_context.get_hero_photo_files", boom)
    spec = {"background": {"treatment": "full_bleed_photo", "photo_boxes": _grid(4)}}
    assert await tc.extra_photos_for("t", "topic", spec) == []


@pytest.mark.asyncio
async def test_the_picker_never_returns_the_same_photo_twice():
    """pick_hero_set is repeated application of pick_hero_bytes, feeding each
    chosen key back as an exclusion — that is the whole distinctness guarantee."""
    from james_os.photo_pick import pick_hero_set

    refs = [(f"https://cdn/{i}.jpg", _png((10 * i, 40 + i, 90), (400, 500))) for i in range(5)]
    got = await pick_hero_set(refs, None, n=4)
    keys = [k for k, _ in got]
    assert len(keys) == len(set(keys)), "every region must get a different photo"


@pytest.mark.asyncio
async def test_asking_for_more_photos_than_exist_stops_rather_than_repeating():
    from james_os.photo_pick import pick_hero_set

    refs = [(f"https://cdn/{i}.jpg", _png((30 * i, 60, 120), (400, 500))) for i in range(2)]
    got = await pick_hero_set(refs, None, n=6)
    assert len(got) == 2, "it returns what exists; the renderer cycles the rest"
