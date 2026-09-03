"""Does the brand's palette actually reach the pixels?

Three paths did not. A cloned template rendered in the COMPETITOR's colours,
because spec_render took no palette at all — so the owner's colour picker had no
effect on the very grid it sits above. The `muted` tone (footers, handles,
kickers) fell through to James Prendamano's blue-grey for every brand, because no
producer emits a "muted" role. And the statement layout took no kit, so it
rendered every brand on white with near-black type.
"""

import pytest

from james_os.image_compose import _BRAND_MUTED, _colors
from james_os.spec_render import rebrand_spec

pytestmark = pytest.mark.nodb

# The real palettes, from production.
TURTLEBACK = [
    {"role": "background", "hex": "#ffffff"}, {"role": "ink", "hex": "#000000"},
    {"role": "accent", "hex": "#971d23"}, {"role": "surface", "hex": "#fac82b"},
]
JAMES = [
    {"role": "background", "hex": "#070B14"}, {"role": "ink", "hex": "#F5F8FC"},
    {"role": "accent", "hex": "#2E80E4"}, {"role": "surface", "hex": "#1C3E74"},
]
# A @pgatour-shaped template: navy ground, white type, crimson accent.
COMPETITOR = {
    "palette": {"bg": "#0d2a4f", "ink": "#ffffff", "accent": "#c8102e", "surface": "#1b4c86"},
    "elements": [{"role": "headline", "color": "#ffffff", "box": {}},
                 {"role": "kicker", "color": "#c8102e", "box": {}}],
    "decorations": [{"type": "pill", "color": "#c8102e", "box": {}}],
    "background": {"color": "#0d2a4f"},
}


def test_a_borrowed_template_renders_in_the_brands_colours():
    """Structure is what was borrowed. The colours are the brand's."""
    out = rebrand_spec(COMPETITOR, TURTLEBACK)
    assert out["palette"]["bg"] == "#ffffff"
    assert out["palette"]["accent"] == "#971d23"
    assert out["background"]["color"] == "#ffffff"
    # role for role: their white headline → the brand's ink; their crimson
    # accent → the brand's accent. The contrast that made the layout read holds.
    by_role = {e["role"]: e["color"] for e in out["elements"]}
    assert by_role["headline"] == "#000000"
    assert by_role["kicker"] == "#971d23"
    assert out["decorations"][0]["color"] == "#971d23"
    # and nothing of the competitor's palette survives anywhere
    assert "#0d2a4f" not in str(out) and "#c8102e" not in str(out)


def test_two_brands_get_two_different_cards_from_one_template():
    a = rebrand_spec(COMPETITOR, TURTLEBACK)
    b = rebrand_spec(COMPETITOR, JAMES)
    assert a["palette"]["bg"] != b["palette"]["bg"]
    assert a["palette"]["accent"] != b["palette"]["accent"]


def test_a_brand_with_no_palette_is_left_alone():
    """No palette set is not a licence to recolour — the template renders as it
    was read, which is the behaviour every existing clone had."""
    assert rebrand_spec(COMPETITOR, None) is COMPETITOR
    assert rebrand_spec(COMPETITOR, []) is COMPETITOR


def test_a_partial_palette_keeps_the_source_colour_for_what_it_lacks():
    out = rebrand_spec(COMPETITOR, [{"role": "accent", "hex": "#971d23"}])
    assert out["palette"]["accent"] == "#971d23"
    assert out["palette"]["bg"] == "#0d2a4f"      # not defined by the brand


def test_muted_is_the_brands_own_tone_not_james_blue_grey():
    """The footer / handle / kicker tone. No producer emits a "muted" role, so
    this fell through to James's literal for every brand in the system."""
    tb = _colors({"palette": TURTLEBACK})["muted"]
    assert tb != _BRAND_MUTED, "Turtleback was wearing James's blue-grey"
    # derived from ITS ink stepped toward ITS ground: black → white = grey
    assert tb[0] == tb[1] == tb[2], f"expected a neutral grey, got {tb}"
    # and a brand whose palette IS blue still reads blue
    assert _colors({"palette": JAMES})["muted"] != _colors({"palette": TURTLEBACK})["muted"]


def test_muted_still_falls_back_when_a_brand_has_no_colours_at_all():
    assert _colors({})["muted"] == _BRAND_MUTED
    assert _colors(None)["muted"] == _BRAND_MUTED


def test_the_statement_card_takes_a_kit():
    """It was the one layout that took none, so it rendered every brand alike."""
    import inspect

    from james_os.image_compose import statement_card

    assert "brand_kit" in inspect.signature(statement_card).parameters
