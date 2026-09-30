"""How the glyphs are FINISHED — outline, extrude, shadow, or flat.

Every line used to get the same thin auto-flip stroke, so our version of a card
whose headline is extruded four ways came out flat. The renderer had exactly one
treatment and never read which the reference used.

This is the decoration gap in practice. Measured across the corpus: 31% of
`graphic_card` layouts carry ZERO decorations and 70% of `photo_forward` do —
but the richness of the cards we are losing to is mostly not shapes at all, it
is what is done to the type.
"""

import io

import pytest
from PIL import Image, ImageDraw

from james_os.design_cloner import _sanitize
from james_os.spec_render import render_spec


def _photo(rgb=(70, 120, 170)) -> bytes:
    im = Image.new("RGB", (900, 1100), rgb)
    d = ImageDraw.Draw(im)
    for i in range(0, 1100, 60):
        d.line([(0, i), (900, i)], fill=tuple(max(0, c - 18) for c in rgb), width=20)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def _spec(treatment=None):
    el = {"role": "headline", "size": "xl", "weight": "black", "align": "center",
          "case": "upper", "color": "#ffffff",
          "box": {"x": .06, "y": .36, "w": .88, "h": .28}}
    if treatment is not None:
        el["treatment"] = treatment
    return {"kind": "graphic_card",
            "palette": {"bg": "#0b2545", "ink": "#ffffff", "accent": "#f0b429"},
            "background": {"treatment": "full_bleed_photo"},
            "elements": [el], "decorations": []}


def _render(treatment=None) -> bytes:
    png, _ = render_spec(_spec(treatment), {"headline": "COURSE RECORD"},
                         hero_bytes=_photo())
    return png


# --------------------------------------------- the 454 specs already learned


def test_an_unstated_treatment_renders_exactly_as_it_always_did():
    """Every layout learned before this vocabulary existed carries no
    `treatment`. Not one may change."""
    assert _render(None) == _render(None)
    # and it is NOT the same as any deliberate treatment
    assert _render(None) != _render("none")
    assert _render(None) != _render("extrude")


def test_an_unstated_treatment_is_stored_as_None_not_guessed():
    out = _sanitize({"kind": "graphic_card", "elements": [
        {"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]})
    assert out["elements"][0]["treatment"] is None


# ------------------------------------------------------- each one is distinct


@pytest.mark.parametrize("treatment", ["none", "outline", "shadow", "extrude"])
def test_every_treatment_draws_something_different(treatment):
    assert _render(treatment) != _render(None)


def test_the_four_treatments_are_distinct_from_each_other():
    seen = {t: _render(t) for t in ("none", "outline", "shadow", "extrude")}
    assert len(set(seen.values())) == 4, "two treatments produced identical pixels"


def test_flat_type_really_has_no_stroke():
    """`none` is a design choice — flat type — not a fallback."""
    flat = Image.open(io.BytesIO(_render("none"))).convert("RGB")
    outlined = Image.open(io.BytesIO(_render("outline"))).convert("RGB")
    band = lambda im: [im.getpixel((x, int(im.height * .44)))
                       for x in range(int(im.width * .1), int(im.width * .9), 3)]
    dark = lambda px: sum(1 for c in px if sum(c) < 180)
    assert dark(band(outlined)) > dark(band(flat)), \
        "the outline must add dark edge pixels the flat version has none of"


def test_a_junk_treatment_falls_back_rather_than_raising():
    out = _sanitize({"kind": "graphic_card", "elements": [
        {"role": "headline", "treatment": "neon-glow-3000",
         "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]})
    assert out["elements"][0]["treatment"] is None
    png, _ = render_spec(_spec("neon-glow-3000"), {"headline": "X"}, hero_bytes=_photo())
    assert png and len(png) > 500


def test_treatment_changes_only_the_FINISH_never_the_layout():
    """Containment is an invariant about where text sits. A finish that moved a
    box would break it silently — extrusion draws OUTSIDE the glyph, so this
    pins that it is decoration, not layout."""
    sizes = {t: Image.open(io.BytesIO(_render(t))).size
             for t in (None, "none", "outline", "shadow", "extrude")}
    assert len(set(sizes.values())) == 1, "the canvas must not change with the finish"
