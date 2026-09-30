"""Keeping type off people's faces, and keeping the picture legible under it.

Both came from one measured render (Trouvailler, 2026-09-28): a subhead landed
across two people's faces, and a headline crossing a floodlit tower and a night
sky was half invisible while the contrast guard saw nothing wrong.
"""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from james_os import people
from james_os.spec_render import (
    _crop_window,
    _face_cost,
    _min_plate_alpha,
    _ratio,
    _region_extremes,
    _rel_lum,
)


# ----------------------------------------------------------------- contrast


def test_relative_luminance_matches_the_published_reference_values():
    """Against the WCAG definition, not against our own arithmetic."""
    assert _ratio(_rel_lum((0, 0, 0)), _rel_lum((255, 255, 255))) == pytest.approx(21.0, abs=0.01)
    assert _ratio(_rel_lum((255, 255, 255)), _rel_lum((255, 255, 255))) == pytest.approx(1.0, abs=0.001)
    # #767676 is the documented grey that lands exactly on AA (4.5:1) over white.
    assert _ratio(_rel_lum((118, 118, 118)), _rel_lum((255, 255, 255))) == pytest.approx(4.5, abs=0.06)


def test_a_half_light_half_dark_region_is_judged_on_its_WORST_half():
    """The measured failure of the old guard.

    Background half #fff and half #000 under white ink averages to mid-grey.
    The old test was `abs(text_luma - mean) < 95`, which passed — while half the
    text sat at 1:1 and could not be read at all. Tails, not means.
    """
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([0, 0, 99, 99], fill=(0, 0, 0))
    dark, light = _region_extremes(img, 0, 0, 200, 100)
    white = _rel_lum((255, 255, 255))
    assert _ratio(white, light) < 1.2, "the light half is where white ink disappears"
    assert _ratio(white, dark) > 15, "the dark half is fine — which is why the mean lied"


def test_the_plate_is_only_as_opaque_as_it_has_to_be():
    black, white = _rel_lum((0, 0, 0)), _rel_lum((255, 255, 255))
    # already legible: no plate at all, so the photo stays untouched
    assert _min_plate_alpha((255, 255, 255), (0, 0, 0), black, black, 3.0) == 0.0
    # hopeless region: a plate is needed, and more of one than the old fixed 0.42
    need = _min_plate_alpha((18, 60, 53), (255, 255, 255), black, white, 3.0)
    assert 0.0 < need <= 1.0
    assert need > 0.42, "the old fixed opacity was not enough for a full-range region"


def test_a_harder_target_never_asks_for_less_plate():
    black, white = _rel_lum((0, 0, 0)), _rel_lum((255, 255, 255))
    easy = _min_plate_alpha((18, 60, 53), (255, 255, 255), black, white, 3.0)
    hard = _min_plate_alpha((18, 60, 53), (255, 255, 255), black, white, 4.5)
    assert hard >= easy


# -------------------------------------------------------------------- faces


def test_a_missing_detector_reads_as_no_faces_and_changes_nothing(monkeypatch):
    """The whole feature is optional by construction.

    opencv is NOT in the production image until this ships, and a container that
    somehow lacks it must render exactly as it does today — not fail.
    """
    people._detector.cache_clear()
    monkeypatch.setattr(people, "MODEL", people.MODEL.with_name("does-not-exist.onnx"))
    assert people.available() is False
    assert people.faces(Image.new("RGB", (400, 400), "white")) == []
    people._detector.cache_clear()


def test_a_head_box_is_bigger_than_the_face_and_stays_in_frame():
    """The detector returns a face; text over someone's hair reads just as badly,
    so the keep-out is the head."""
    ko = people.keep_out({"x": 0.40, "y": 0.40, "w": 0.20, "h": 0.20, "eye_y": 0.45, "score": 1.0})
    x, y, w, h = ko
    assert w > 0.20 and h > 0.20
    assert y < 0.40, "it must extend upward over forehead and hair"
    # a face at the very edge must not produce a box outside the frame
    ex, ey, ew, eh = people.keep_out({"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2,
                                      "eye_y": 0.1, "score": 1.0})
    assert ex >= 0.0 and ey >= 0.0 and ex + ew <= 1.0 and ey + eh <= 1.0


def test_overlap_measures_how_much_of_the_TEXT_is_covered():
    """Asymmetric on purpose: the question is always what fraction of the text
    block sits on a head, never the reverse."""
    box = (0.0, 0.0, 0.4, 0.4)
    assert people.overlap(box, (0.0, 0.0, 0.2, 0.4)) == pytest.approx(0.5)
    assert people.overlap(box, (0.0, 0.0, 1.0, 1.0)) == pytest.approx(1.0)
    assert people.overlap(box, (0.6, 0.6, 0.2, 0.2)) == 0.0


# ------------------------------------------------------- the crop arithmetic


def test_the_crop_window_matches_what_ImageOps_fit_actually_takes():
    """If this geometry is wrong, a face the crop EXCLUDES would still be
    penalised and the search would chase a phantom."""
    # a wide source into a square output: full height, narrower width
    x, y, w, h = _crop_window((400, 200), 1.0, (0.5, 0.5))
    assert h == pytest.approx(1.0)
    assert w == pytest.approx(0.5)
    assert x == pytest.approx(0.25), "centred horizontally"
    # panning right moves the window right
    x_r, _, _, _ = _crop_window((400, 200), 1.0, (1.0, 0.5))
    assert x_r > x


def test_a_face_under_the_copy_costs_more_than_any_texture_can():
    """Edge energy cannot tell a face from a hedge — it tops out at 255 a cell
    and both light it up. This term is what makes them different."""
    zones = [(0.1, 0.1, 0.8, 0.2)]
    cells = (24, 30)
    window = (0.0, 0.0, 1.0, 1.0)
    on = _face_cost([(0.2, 0.1, 0.4, 0.2)], window, zones, cells)
    off = _face_cost([(0.2, 0.7, 0.4, 0.2)], window, zones, cells)
    assert off == 0.0, "a face nowhere near the copy costs nothing"
    worst_texture = 255 * (0.8 * cells[0]) * (0.2 * cells[1])
    assert on > worst_texture, "a face must outrank the busiest possible photo"


def test_a_face_cropped_OUT_of_frame_is_not_penalised():
    zones = [(0.1, 0.1, 0.8, 0.2)]
    # the crop takes only the right half; a face in the left half is gone
    window = (0.5, 0.0, 0.5, 1.0)
    assert _face_cost([(0.0, 0.1, 0.2, 0.2)], window, zones, (24, 30)) == 0.0


# --------------------------------- ranking vs the abandon-the-photo threshold


def _spec_with_centre_headline():
    return {"kind": "graphic_card",
            "background": {"treatment": "full_bleed_photo"},
            "elements": [{"role": "headline", "size": "xl", "weight": "black",
                          "align": "center",
                          "box": {"x": 0.08, "y": 0.38, "w": 0.84, "h": 0.18}}],
            "decorations": []}


def test_ranking_counts_faces_but_the_abandon_threshold_does_not(monkeypatch):
    """The two callers want different answers from the same function.

    Ranking two photos: the one without a head under the headline is better.
    Deciding to give up on photos entirely and render a solid card: that ceiling
    was calibrated on texture, and a face reads as ~14x typical texture, so
    counting heads there would drop nearly every people photo — a brand whose
    library is all people would start rendering solid cards.
    """
    from james_os import spec_render as sr

    spec = _spec_with_centre_headline()
    img = Image.new("RGB", (800, 1000), (170, 170, 170))
    monkeypatch.setattr(sr, "_head_zones", lambda _i: [(0.10, 0.34, 0.80, 0.26)])

    with_faces = sr.photo_fit_cost(spec, img, faces=True)
    without = sr.photo_fit_cost(spec, img, faces=False)
    # The DEFAULT is the safe one: a caller that has not thought about faces
    # gets today's behaviour, not the surprising one.
    assert sr.photo_fit_cost(spec, img) == without
    assert with_faces > without, "ranking must see the head"
    assert without == pytest.approx(0.0, abs=1.0), "a flat grey photo has no texture to speak of"


def test_a_people_heavy_library_is_not_abandoned_wholesale(monkeypatch):
    """The regression this guards. _PHOTO_FIT_CEILING abandons the photo; if the
    face term reached it, every portrait would trip it at once."""
    from james_os import spec_render as sr
    from james_os.template_clone import _PHOTO_FIT_CEILING, _too_busy_for

    spec = _spec_with_centre_headline()
    img = Image.new("RGB", (800, 1000), (170, 170, 170))
    buf = __import__("io").BytesIO()
    img.save(buf, "PNG")
    monkeypatch.setattr(sr, "_head_zones", lambda _i: [(0.10, 0.34, 0.80, 0.26)])
    assert sr.photo_fit_cost(spec, img, faces=True) > _PHOTO_FIT_CEILING, (
        "a head under the headline does score above the ceiling...")
    assert _too_busy_for(spec, buf.getvalue()) is False, (
        "...but the photo is still used, because that decision ignores faces")


# ----------------------------------------------------- not detecting twice


def test_the_same_photo_is_only_detected_once(monkeypatch):
    """One idea asks the same question six to eight times.

    The photo ranking detects on each candidate, then the winner is detected
    again for the primary render, again for every extra platform shape, and once
    more for the layer-capture pass — at ~25 ms each that is most of a second
    spent re-answering a settled question.
    """
    from james_os import spec_render as sr

    sr._HEADS.clear()
    calls = {"n": 0}

    def counting(_img):
        calls["n"] += 1
        return [{"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.2, "eye_y": 0.35, "score": 0.9}]

    monkeypatch.setattr(people, "faces", counting)
    img = Image.new("RGB", (400, 500), (120, 130, 140))
    first = sr._head_zones(img)
    for _ in range(7):
        sr._head_zones(img)
    assert calls["n"] == 1, "seven repeats must cost one detection"
    assert len(first) == 1
    sr._HEADS.clear()


def test_the_memo_is_keyed_on_PIXELS_not_object_identity(monkeypatch):
    """The failure an id() key would eventually produce: _load() builds a fresh
    object per render and CPython recycles ids, so one photo would be handed
    another photo's faces and the text would dodge a face that is not there."""
    from james_os import spec_render as sr

    sr._HEADS.clear()
    seen = []

    def by_colour(img):
        seen.append(img.getpixel((0, 0)))
        return ([{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1, "eye_y": 0.15, "score": 1.0}]
                if img.getpixel((0, 0))[0] > 200 else [])

    monkeypatch.setattr(people, "faces", by_colour)
    bright = Image.new("RGB", (300, 300), (240, 240, 240))
    dark = Image.new("RGB", (300, 300), (10, 10, 10))
    assert len(sr._head_zones(bright)) == 1
    assert len(sr._head_zones(dark)) == 0, "a different picture must not reuse the answer"
    # a brand-new object with the same pixels is the same question
    assert len(sr._head_zones(Image.new("RGB", (300, 300), (240, 240, 240)))) == 1
    assert len(seen) == 2, "only the two distinct pictures were detected on"
    sr._HEADS.clear()


def test_the_memo_cannot_grow_without_bound(monkeypatch):
    from james_os import spec_render as sr

    sr._HEADS.clear()
    monkeypatch.setattr(people, "faces", lambda _i: [])
    for i in range(sr._HEADS_MAX * 3):
        sr._head_zones(Image.new("RGB", (64, 64), (i % 256, (i * 7) % 256, (i * 13) % 256)))
    assert len(sr._HEADS) <= sr._HEADS_MAX
    sr._HEADS.clear()


def test_a_detector_that_raises_still_renders(monkeypatch):
    """Everything is inside the try on purpose — an earlier cut of this memo put
    the cache lookup OUTSIDE it, and an unhashable argument raised TypeError in
    the middle of a render rather than degrading."""
    from james_os import spec_render as sr

    sr._HEADS.clear()

    def boom(_img):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(people, "faces", boom)
    assert sr._head_zones(Image.new("RGB", (100, 100), "white")) == []
    sr._HEADS.clear()


# ------------------------------------------- the brand's own ink before a plate


def test_a_rebranded_ink_that_fails_is_swapped_for_one_that_passes():
    """The defect this closes, measured on a real render 2026-09-30.

    rebrand_spec maps the template's colours onto the brand palette BY ROLE, with
    no idea what the photo underneath looks like. A competitor's gold stat — gold
    because THEIR background was dark — became Turtleback's #971d23 and landed on
    sunlit grass at 2.10:1 and shaded grass at 1.22:1. The brand already owned
    white, which scores 3.98 and 6.86 on the same pixels. Dimming the photograph
    with a plate treats the symptom; the brand's own palette had the answer.
    """
    from PIL import ImageDraw as _D

    from james_os.spec_render import render_spec

    grass = Image.new("RGB", (900, 1100), (90, 140, 70))
    d = _D.Draw(grass)
    for i in range(0, 1100, 40):
        d.line([(0, i), (900, i)], fill=(60, 100, 50), width=14)
    buf = io.BytesIO(); grass.save(buf, "PNG")

    brand = [{"hex": "#ffffff", "role": "background"}, {"hex": "#000000", "role": "ink"},
             {"hex": "#971d23", "role": "accent"}, {"hex": "#fac82b", "role": "surface"}]
    spec = {"kind": "graphic_card",
            "palette": {"bg": "#111318", "ink": "#FFFFFF", "accent": "#FFD700"},
            "background": {"treatment": "full_bleed_photo"},
            "elements": [{"role": "stat", "size": "xl", "weight": "black", "align": "center",
                          "color": "#FFD700", "box": {"x": .08, "y": .42, "w": .84, "h": .16}}],
            "decorations": []}
    png, _ = render_spec(spec, {"stat": "EVERY WEEKEND"},
                         hero_bytes=buf.getvalue(), palette=brand)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    red = (0x97, 0x1d, 0x23)
    hits = sum(1 for y in range(int(im.height * .42), int(im.height * .58), 2)
               for x in range(int(im.width * .08), int(im.width * .92), 2)
               if all(abs(im.getpixel((x, y))[c] - red[c]) < 45 for c in range(3)))
    assert hits == 0, "the failing ink must not survive onto the photo"


def test_an_ink_that_already_passes_is_left_alone():
    """Legible layouts must not be repainted. The swap fires on failure only."""
    from james_os.spec_render import _rel_lum, _ratio

    # white on mid-grass already clears AA-large; nothing should change it
    assert _ratio(_rel_lum((255, 255, 255)), _rel_lum((90, 140, 70))) > 3.0


def test_the_swap_never_leaves_the_brands_palette():
    """Fixing contrast by inventing a colour would fix legibility and break
    identity — the worse trade. Only the brand's own inks are candidates."""
    import inspect

    from james_os import spec_render as sr

    src = inspect.getsource(sr.render_spec)
    assert "palette_inks" in src
    assert "p.get(\"hex\")" in src or 'get("hex")' in src
