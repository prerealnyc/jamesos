"""A photo the copy cannot live on top of is worse than no photo at all.

The layout is borrowed from a competitor whose picture happened to be empty
where their words went. When every photo the brand owns is busy there, forcing
one in buys a headline across somebody's face — which the design review rejects
and which no amount of cropping fixes. Dropping to the design's own solid
background keeps the structure that was borrowed and loses only the photograph.
"""

import io

import pytest
from PIL import Image, ImageDraw

from james_os import template_clone as tc
from james_os import spec_render as sr

pytestmark = pytest.mark.nodb


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _calm() -> bytes:
    return _png(Image.new("RGB", (1120, 1350), (150, 190, 220)))


def _busy() -> bytes:
    img = Image.new("RGB", (1120, 1350), (150, 190, 220))
    d = ImageDraw.Draw(img)
    for i in range(5):                       # big subjects everywhere
        x = 40 + i * 200
        d.rectangle([x, 120, x + 170, 1230], fill=(15, 20, 15))
        d.ellipse([x + 20, 200, x + 150, 600], fill=(245, 245, 235))
    return img and _png(img)


def _spec():
    return {
        "background": {"treatment": "full_bleed_photo"},
        "elements": [{"role": "headline", "box": {"x": 0.08, "y": 0.3, "w": 0.84, "h": 0.3}}],
    }


def test_a_calm_photo_is_kept():
    assert tc._too_busy_for(_spec(), _calm()) is False


def test_a_photo_busy_under_the_copy_is_refused():
    assert tc._too_busy_for(_spec(), _busy()) is True


def test_a_template_that_puts_nothing_over_the_photo_always_keeps_it():
    spec = {"background": {"treatment": "full_bleed_photo"}, "elements": []}
    assert tc._too_busy_for(spec, _busy()) is False


def test_unreadable_bytes_never_cost_the_post():
    assert tc._too_busy_for(_spec(), b"not an image") is False


def test_the_solid_fallback_keeps_the_borrowed_structure():
    """Only the photograph is dropped — the elements, and therefore the design,
    survive; that is the difference between a fallback and a failure."""
    spec = _spec()
    solid = {**spec, "background": {**spec["background"], "treatment": "solid"}}
    assert sr._photo_frame(solid) is None
    assert solid["elements"] == spec["elements"]


def test_a_sparse_layout_keeps_its_photo_even_when_the_fit_is_poor():
    """Dropping the photo from a design built around one leaves a void, not a
    typographic card. The first cut of this fallback traded every "text
    overlaps with the subjects" for an "excessive empty space at the top"."""
    sparse = {
        "background": {"treatment": "full_bleed_photo"},
        "elements": [{"role": "headline", "box": {"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.08}}],
    }
    assert tc._coverage(sparse) < tc._SOLID_MIN_COVERAGE


def test_a_design_that_fills_the_card_can_stand_alone():
    dense = {
        "background": {"treatment": "full_bleed_photo"},
        "elements": [
            {"role": "headline", "box": {"x": 0.08, "y": 0.18, "w": 0.84, "h": 0.28}},
            {"role": "subhead", "box": {"x": 0.08, "y": 0.52, "w": 0.84, "h": 0.18}},
        ],
    }
    assert tc._coverage(dense) >= tc._SOLID_MIN_COVERAGE
