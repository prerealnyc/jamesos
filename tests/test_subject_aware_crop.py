"""Move the PHOTO, not the text, so the subject is not under the headline.

The layout is borrowed from a competitor whose photo happened to be empty
where their words went. Ours is not. The design review kept saying it:
"text overlaps with the subjects", "logo overlaps with subject's leg" — five
of every nine rejected clones failed on exactly that, because the crop was a
fixed centering and the people landed under the copy.

The text is the design and does not move. The photo inside its frame is the
one degree of freedom that costs nothing.
"""

import pytest
from PIL import Image, ImageDraw

from james_os import spec_render as sr

# Pure geometry and pixel maths — no database, so this runs anywhere.
pytestmark = pytest.mark.nodb


def _photo_busy_on(side: str, w=1800, h=1200) -> Image.Image:
    """WIDE on purpose: a 4:5 card crops the sides off a landscape photo, and
    the sides are the only thing a horizontal pan can choose between.

    The "subject" is a few LARGE high-contrast shapes, not fine hatching:
    busyness is measured on a thumbnail, and fine texture averages away to flat
    grey there — which is exactly how a first version of this test convinced
    itself the detailed half was the calm one.
    """
    img = Image.new("RGB", (w, h), (150, 190, 220))            # flat sky
    d = ImageDraw.Draw(img)
    x0 = 0 if side == "left" else w // 2
    for i in range(4):                                          # big dark blocks
        bx = x0 + 40 + (i % 2) * (w // 5)
        by = 80 + (i // 2) * (h // 2)
        d.rectangle([bx, by, bx + w // 7, by + h // 3], fill=(15, 20, 15))
        d.ellipse([bx + 20, by + 20, bx + w // 9, by + h // 5], fill=(245, 245, 235))
    return img


def test_the_crop_moves_away_from_the_copy():
    """Copy on the left, subject on the left → pan right to get out from under it."""
    zones = [(0.05, 0.1, 0.4, 0.3)]                            # headline, left
    cx_busy_left, _ = sr._best_centering(_photo_busy_on("left"), 1080, 1350, zones)
    cx_busy_right, _ = sr._best_centering(_photo_busy_on("right"), 1080, 1350, zones)
    assert cx_busy_left > cx_busy_right, (
        "a subject under the copy should push the crop the other way "
        f"(got {cx_busy_left} vs {cx_busy_right})"
    )


def test_nothing_to_avoid_renders_exactly_as_before():
    """No copy over the photo → the old fixed centering, so a full-bleed photo
    card is not silently recomposed."""
    assert sr._best_centering(_photo_busy_on("left"), 1080, 1350, []) == sr._DEFAULT_CENTERING


def test_a_broken_photo_never_costs_the_render():
    class Exploding:
        width = height = 100
        def convert(self, *_a, **_k):
            raise RuntimeError("decode failed")
    assert sr._best_centering(Exploding(), 1080, 1350, [(0, 0, 1, 1)]) == sr._DEFAULT_CENTERING


def test_zones_land_in_the_photos_own_frame():
    """A side-panel photo must only dodge the copy that actually touches it."""
    occupied = [(0.02, 0.02, 0.40, 0.20)]                      # headline, far left
    # Photo down the right-hand side: the headline never touches it.
    assert sr._zones_for((0.45, 0.0, 0.55, 1.0), occupied) == []
    # Full bleed: it does, unchanged.
    assert sr._zones_for((0.0, 0.0, 1.0, 1.0), occupied) == occupied
    # Top-strip photo: only the overlapping part, rescaled into that strip.
    zx, zy, zw, zh = sr._zones_for((0.0, 0.0, 1.0, 0.6), occupied)[0]
    assert abs(zy - 0.02 / 0.6) < 1e-6 and abs(zh - 0.20 / 0.6) < 1e-6


def test_the_logo_slot_counts_as_occupied():
    """'Logo overlaps with subject's leg' was a real review note."""
    spec = {"elements": [], "logo_box": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1}}
    assert sr._occupied(spec) == [(0.7, 0.8, 0.2, 0.1)]
