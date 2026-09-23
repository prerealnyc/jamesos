"""Which photo belongs under THIS headline.

Cropping could only bend one photo to fit a layout. With a library, the better
question is which photo belongs under this particular design: a portrait with
the subject dead centre is wrong for a card with copy across the middle and
right for one with copy down the side — and nothing was asking.
"""

import pytest
from PIL import Image, ImageDraw

from james_os import spec_render as sr

pytestmark = pytest.mark.nodb


def _subject(where: str, w=1120, h=1350) -> Image.Image:
    """One big high-contrast subject on the given side of an otherwise calm sky.

    Sized close to the 4:5 card on purpose. A wide photo has so much spare
    width that the crop alone can pan any subject out from under any headline —
    which is the point of the crop, and the reason pairing is a SEPARATE win:
    it matters when the frame has no slack left to give.
    """
    img = Image.new("RGB", (w, h), (150, 190, 220))
    d = ImageDraw.Draw(img)
    x0 = {"left": 60, "right": w - w // 3, "centre": w // 2 - w // 6}[where]
    d.rectangle([x0, 150, x0 + w // 4, h - 150], fill=(15, 20, 15))
    d.ellipse([x0 + 30, 200, x0 + w // 5, h // 2], fill=(245, 245, 235))
    return img


def _spec(copy_box, treatment="full_bleed_photo"):
    return {
        "background": {"treatment": treatment},
        "elements": [{"role": "headline", "box": copy_box}],
    }


def test_the_photo_that_suits_the_layout_scores_better():
    """Copy down the LEFT: the photo whose subject is on the right suits it."""
    spec = _spec({"x": 0.04, "y": 0.1, "w": 0.42, "h": 0.5})
    left = sr.photo_fit_cost(spec, _subject("left"))
    right = sr.photo_fit_cost(spec, _subject("right"))
    assert right < left, f"subject away from the copy should win ({right} vs {left})"


def test_the_same_photo_can_be_right_for_one_design_and_wrong_for_another():
    photo = _subject("left")
    copy_left = sr.photo_fit_cost(_spec({"x": 0.04, "y": 0.1, "w": 0.42, "h": 0.5}), photo)
    copy_right = sr.photo_fit_cost(_spec({"x": 0.54, "y": 0.1, "w": 0.42, "h": 0.5}), photo)
    assert copy_right < copy_left, "the pairing is the point, not the photo alone"


def test_a_template_with_nothing_over_the_photo_suits_everything():
    spec = {"background": {"treatment": "full_bleed_photo"}, "elements": []}
    assert sr.photo_fit_cost(spec, _subject("centre")) == 0.0


def test_a_solid_card_never_prefers_a_photo():
    spec = _spec({"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.3}, treatment="solid")
    assert sr.photo_fit_cost(spec, _subject("centre")) == 0.0


def test_rotation_survives_the_narrowing():
    """Narrowed, not chosen: photos that score close to the best all stay, so
    the picker downstream can still rotate and the brand does not post the same
    picture every week."""
    spec = _spec({"x": 0.04, "y": 0.1, "w": 0.42, "h": 0.5})
    same = [(f"p{i}", _png(_subject("right"))) for i in range(3)]
    assert len(sr.suited_photos(spec, same)) == 3


def test_a_clearly_wrong_photo_is_dropped():
    spec = _spec({"x": 0.04, "y": 0.1, "w": 0.42, "h": 0.5})
    refs = [("good", _png(_subject("right"))), ("bad", _png(_subject("left")))]
    kept = [name for name, _ in sr.suited_photos(spec, refs)]
    assert kept == ["good"]


def _png(img: Image.Image) -> bytes:
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
