"""The card editor's backend: the card as parts, and the edited image back.

The endpoints themselves need Postgres; these pin the pure pieces they are built
from, so they run on any machine.
"""

import pytest

from james_os.api_v1 import (
    _EDIT_MAX_BYTES,
    _EDIT_TYPES,
    _edited_photo,
    _first_sentence,
    _layout_urls,
    _palette_roles,
    _rebuilds_in_place,
    _sniff_image,
)

pytestmark = pytest.mark.nodb


def test_the_palette_reaches_the_editor_as_swatches():
    roles = [{"role": "background", "hex": "#070B14"}, {"role": "ink", "hex": "#F5F8FC"},
             {"role": "accent", "hex": "#2E80E4"}, {"role": "surface", "hex": "#1C3E74"}]
    assert _palette_roles(roles) == {
        "bg": "#070B14", "ink": "#F5F8FC", "accent": "#2E80E4", "surface": "#1C3E74"}


def test_a_partial_or_junk_palette_does_not_break_the_editor():
    assert _palette_roles(None) == {}
    assert _palette_roles([]) == {}
    assert _palette_roles(["nonsense", {"role": "accent"}, {"hex": "#fff"}]) == {}
    assert _palette_roles([{"role": "accent", "hex": "#971d23"}]) == {"accent": "#971d23"}


def test_a_headline_falls_back_to_the_captions_first_sentence():
    """A card with no stored spec still needs words to lay out."""
    assert _first_sentence("It's more than taxes. It's your future.") == "It's more than taxes."
    assert _first_sentence("One line, no stop") == "One line, no stop"
    assert _first_sentence("") == ""


def test_an_endless_first_sentence_is_cut_rather_than_swallowing_the_card():
    long = "word " * 80
    assert len(_first_sentence(long)) <= 140


def test_only_real_image_types_are_accepted():
    """An edit is a PNG from the canvas; accepting arbitrary types here would
    let a script or an SVG be stored and served as the post's image."""
    assert _EDIT_TYPES == {"image/png", "image/jpeg", "image/webp"}
    assert "image/svg+xml" not in _EDIT_TYPES
    assert 1_000_000 < _EDIT_MAX_BYTES <= 20 * 1024 * 1024


def test_the_bytes_decide_the_type_not_the_label_on_the_upload():
    """A client can send any content-type it likes. What gets stored and served
    as the post's image is decided by what the bytes actually are."""
    assert _sniff_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == "image/png"
    assert _sniff_image(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "image/jpeg"
    assert _sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    # an SVG or a script labelled image/png is still not an image
    assert _sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") == ""
    assert _sniff_image(b"<script>alert(1)</script>") == ""
    assert _sniff_image(b"") == ""
    assert _sniff_image(b"RIFF\x00\x00\x00\x00WAVEfmt ") == ""


def test_every_url_a_saved_layout_will_fetch_is_found():
    """The layout is replayed in the owner's browser on the next open, so every
    URL in it is checked before it is stored — the background and each picture."""
    layout = {
        "background": {"kind": "image", "url": "https://cdn.test/photo.jpg"},
        "layers": [
            {"type": "text", "text": "hi"},
            {"type": "image", "url": "https://cdn.test/logo.png"},
            "junk",
            {"type": "image", "url": "javascript:alert(1)"},
        ],
    }
    assert _layout_urls(layout) == [
        "https://cdn.test/photo.jpg", "https://cdn.test/logo.png", "javascript:alert(1)"]
    assert _layout_urls({"background": {"kind": "color", "color": "#000"}, "layers": []}) == []
    assert _layout_urls({"background": "nonsense", "layers": "nonsense"}) == []


GEN = {"https://cdn.test/generated.png"}


def test_a_new_photo_on_the_card_becomes_its_photo():
    """A regenerate keeps or excludes the photo named here — it must be the one
    the owner put on, whichever mode they did it in."""
    bg = {"background": {"kind": "image", "url": "https://cdn.test/brand-photo.jpg", "dim": 0},
          "mode": "overlay", "layers": []}
    assert _edited_photo(bg, GEN) == "https://cdn.test/brand-photo.jpg"
    panel = {"background": {"kind": "color", "color": "#000"}, "mode": "parts",
             "layers": [{"type": "image", "fit": "cover", "url": "https://cdn.test/panel.jpg"},
                        {"type": "image", "url": "https://cdn.test/logo.png"}]}
    assert _edited_photo(panel, GEN) == "https://cdn.test/panel.jpg"


def test_a_card_that_still_shows_the_generated_image_keeps_its_photo():
    """The generated card as a background is the old photo, flattened — not a
    photo to pin, and no reason to forget the real one."""
    doc = {"background": {"kind": "image", "url": "https://cdn.test/generated.png", "dim": 0},
           "mode": "overlay", "layers": [{"type": "text", "text": "@j"}]}
    assert _edited_photo(doc, GEN) is None
    assert _edited_photo({}, GEN) is None


def test_a_card_with_no_photo_left_says_so():
    """A plain colour with no photo panel: the owner took the photo off, and a redo
    must not bring it back."""
    doc = {"background": {"kind": "color", "color": "#070B14"}, "mode": "parts",
           "layers": [{"type": "image", "url": "https://cdn.test/logo.png"}]}
    assert _edited_photo(doc, GEN) == ""


def test_a_hand_edited_card_is_redone_in_its_own_design():
    """After an edit the image is the owner's. A redo with feedback reads that
    card back and changes the one thing; composing afresh threw the edit away."""
    assert _rebuilds_in_place({"image_format": "owner_edit"}, "") is True
    assert _rebuilds_in_place({"image_format": "owner_edit"}, "same") is True
    # "I hate this design" is still answered with a new one
    assert _rebuilds_in_place({"image_format": "owner_edit"}, "new") is False


def test_the_in_place_rule_is_unchanged_for_everything_else():
    assert _rebuilds_in_place({"image_format": "learned"}, "") is True
    assert _rebuilds_in_place({"image_format": "cloned"}, "") is True
    assert _rebuilds_in_place({"image_format": "bold_statement", "cloned_from_competitor": True}, "") is True
    assert _rebuilds_in_place({"image_format": "bold_statement"}, "") is False
    assert _rebuilds_in_place({}, "") is False
