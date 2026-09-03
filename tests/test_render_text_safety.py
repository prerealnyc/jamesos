"""The renderer must never stamp a glyph the font cannot draw.

U+FFFD draws as a .notdef box in every face we ship, and it is wider than an
apostrophe — so "It's" came out as "It□ s", a box and an apparent space, in the
middle of a headline. It arrives when text has been decoded with the wrong codec
somewhere upstream, and nothing downstream can reason its way out of it: a
rebuild that faithfully preserves the words preserves the box with them. The
owner reported it as "text has some emoji element, text not proper", rejected
twice, and got the same box back both times.
"""

import pytest

from james_os.image_compose import _drawable

pytestmark = pytest.mark.nodb


def test_the_box_in_the_headline_becomes_an_apostrophe():
    """The exact string that shipped on the card, twice."""
    assert _drawable("It�s More Than Taxes. It�s Your Future.") == \
        "It's More Than Taxes. It's Your Future."


def test_a_replacement_char_that_is_not_an_apostrophe_is_dropped_not_drawn():
    """Between letters it was an apostrophe. Anywhere else we cannot know — and a
    box is worse than a missing character either way."""
    assert "�" not in _drawable("Understand � risks")
    assert "�" not in _drawable("�leading")


def test_typographic_punctuation_degrades_to_its_ascii_twin():
    assert _drawable("It’s") == "It's"
    assert _drawable("“quoted”") == '"quoted"'
    assert _drawable("wait…") == "wait..."
    assert _drawable("non‑breaking") == "non-breaking"


def test_invisible_formatting_characters_never_reach_a_face():
    assert _drawable("zero​width⁠joiner﻿") == "zerowidthjoiner"


def test_ordinary_text_is_untouched():
    for s in ("It's Not a Blowout. It's a Battle.", "1,300 HOMESITES", ""):
        assert _drawable(s) == s


def test_an_em_dash_survives_because_the_faces_have_one():
    assert "—" in _drawable("Price climbs—demand follows")


def test_a_face_that_cannot_draw_it_gets_it_removed():
    """The backstop: whatever survived the map, ask the FACE."""
    import os

    from PIL import ImageFont

    from james_os.image_compose import _face_chars

    path = os.path.join("src/james_os/assets/fonts", "Anton-Regular.ttf")
    cov = _face_chars(path)
    if not cov:
        pytest.skip("fontTools unavailable — the guard degrades to no filtering")
    font = ImageFont.truetype(path, 40)
    # a CJK glyph no Latin display face carries
    assert "中" not in _drawable("brand 中 name", font)
    assert "brand" in _drawable("brand 中 name", font)
