"""What a redo does with the owner's words.

The rule these pin down: a redo is a FIX, not a replacement. The layout and the
photo that were on screen are what the owner was correcting, so they are kept
unless the feedback asks otherwise. The old code kept the photo but handed the
layout back to the art director whenever the feedback matched no keyword — which
is most real feedback — and the art director then reached for the house style.
"keep rest template same and image same" produced a different template with the
photo dropped: the instruction inverted.
"""

import pytest

from james_os.api_v1 import (
    MAX_REGEN_VERSION,
    _keeps_photo,
    _layout_intent,
    _wants_new_photo,
    _wants_photo_present,
)

# Pure string rules — no database, no providers.
pytestmark = pytest.mark.nodb


def test_keep_it_the_same_is_heard_as_an_instruction():
    """The real rejection that exposed this, verbatim."""
    said = ("the design element and text is merging, can we leave space between both? "
            "keep rest template same and image same")
    assert _layout_intent(said) == "same"
    assert _keeps_photo(said) is True
    assert _wants_new_photo(said) is False


def test_no_image_on_it_asks_for_a_picture_not_another_poster():
    """'NO IMAGE ON IT, DESIGN NOT GOOD' came back as a second text-only card."""
    said = "NO IMAGE ON IT, DESIGN NOT GOOD."
    assert _wants_photo_present(said) is True   # steers off the text-only family
    assert _layout_intent(said) == "new"        # and the look itself was rejected


def test_silence_about_the_layout_keeps_the_layout():
    """The common case. 'text outside box' is a defect report about THIS design,
    so re-composing from scratch answers a question nobody asked."""
    assert _layout_intent("text outside box") == ""
    assert _layout_intent("make the text white") == ""
    assert _wants_photo_present("text outside box") is False


def test_an_explicit_complaint_beats_an_explicit_keep():
    """'keep the rest the same, but the design is bad' still wants a new design."""
    assert _layout_intent("keep the rest the same but the design is bad") == "new"
    assert _layout_intent("different template please") == "new"


def test_a_photo_swap_is_still_recognised():
    assert _wants_new_photo("use a different photo") is True
    assert _wants_new_photo("this one is blurry") is True
    assert _keeps_photo("keep the image, just fix the spacing") is True


def test_the_rebuild_chain_is_capped():
    """Rejecting rebuilds automatically now, so an uncapped chain is a spend loop
    with no human in it."""
    assert MAX_REGEN_VERSION == 3


def test_only_a_real_layout_name_can_be_pinned():
    """payload["image_format"] also records HOW an image was made, not just which
    layout: template_clone writes "cloned". _FORMAT_MAP has no key for it, so
    imagegen resolves the pin to the model's own pick — and, being truthy, it
    consumes the branch that gates v2 layouts behind the design switch. Pinning a
    name that cannot be honoured disables a guard and delivers nothing."""
    from james_os.api_v1 import _is_pinnable_format

    assert _is_pinnable_format("bold_statement") is True
    assert _is_pinnable_format("brand_quote") is True
    assert _is_pinnable_format("BOLD_STATEMENT") is True     # case is not the point
    assert _is_pinnable_format("cloned") is False            # the one that bit
    assert _is_pinnable_format("") is False
    assert _is_pinnable_format(None) is False


def test_the_text_only_cards_carry_the_brand_handle():
    """The handle reached every photo layout and was dropped on the two text-only
    cards — one of which is the house default — so a brand whose kit carries
    "@name" saw it on six formats and silently not on the seventh."""
    import inspect

    from james_os.image_compose import bold_statement_card, brand_quote_card

    assert "handle" in inspect.signature(bold_statement_card).parameters
    assert "handle" in inspect.getsource(brand_quote_card)
