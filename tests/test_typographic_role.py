"""Which of the BRAND's two faces each text block takes.

The renderer inferred this from `weight` alone: bold or black meant one face,
anything else the other. So a reference that set a light geometric headline over
a heavy condensed stat rendered exactly like one that did the reverse — the
typographic RELATIONSHIP, which is most of what makes a layout look like itself,
was discarded at the door.

What is borrowed is which blocks the reference chose to shout with. Never the
competitor's typeface: the brand's own display/body pair is its identity, and
these tests pin that it always wins.
"""

import pytest

from james_os.design_cloner import _sanitize
from james_os.image_compose import _ANTON, _ARCHIVO, _remap_face, brand_fonts
from james_os.spec_render import _face


# ------------------------------------------------- the 454 specs already learned


@pytest.mark.parametrize("weight,expected", [
    ("regular", _ANTON), ("bold", _ARCHIVO), ("black", _ARCHIVO),
])
def test_a_spec_with_no_face_renders_exactly_as_it_always_did(weight, expected):
    """Every layout learned so far is v1 and carries no `face`. Not one of them
    may change."""
    assert _face(weight) == expected
    assert _face(weight, "") == expected


def test_an_unstated_face_is_stored_as_None_not_guessed():
    """A guess here would be indistinguishable from a read, and the whole value
    of the field is that it was actually seen."""
    out = _sanitize({"kind": "graphic_card", "elements": [
        {"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]})
    assert out["elements"][0]["face"] is None


# --------------------------------------------------------- the read relationship


def test_the_read_role_decides_regardless_of_weight():
    """The point of the field: weight no longer determines the face."""
    assert _face("regular", "display") == _face("black", "display")
    assert _face("black", "body") == _face("regular", "body")
    assert _face("black", "display") != _face("black", "body")


def test_a_junk_face_value_falls_back_rather_than_raising():
    assert _face("bold", "fancy") == _face("bold")
    out = _sanitize({"kind": "graphic_card", "elements": [
        {"role": "headline", "face": "wingdings",
         "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]})
    assert out["elements"][0]["face"] is None


# ------------------------------------------------- the brand's identity wins


def test_the_brands_own_faces_win_for_both_roles():
    """We borrow WHICH blocks shout, never WHAT they are set in."""
    theme = {"display": "/fonts/BrandDisplay.ttf", "body": "/fonts/BrandBody.ttf"}
    with brand_fonts(theme):
        assert _remap_face(_face("regular", "display")) == "/fonts/BrandDisplay.ttf"
        assert _remap_face(_face("black", "display")) == "/fonts/BrandDisplay.ttf"
        assert _remap_face(_face("black", "body")) == "/fonts/BrandBody.ttf"
        assert _remap_face(_face("regular", "body")) == "/fonts/BrandBody.ttf"


def test_no_competitor_typeface_can_reach_the_spec():
    """The vocabulary has room for exactly two values, both of which name one of
    OUR slots. There is nowhere to put 'Helvetica'."""
    out = _sanitize({"kind": "graphic_card", "elements": [
        {"role": "headline", "face": "Helvetica Neue Bold",
         "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]})
    assert out["elements"][0]["face"] is None
    assert "Helvetica" not in str(out)
