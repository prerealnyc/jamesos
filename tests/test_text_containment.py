"""Text-containment invariants for the image compositors.

The recurring "text runs off the frame / gets clipped" bug had no render-time
guard — feedback about it just became a to-do. These tests encode the guarantee
the renderer now enforces on ITSELF: for adversarial copy (long URLs, long
hashtags, long agency names, wide all-caps headlines, giant number stats, long
letter-spaced kickers) NO laid-out line is ever wider than its column, and every
compositor renders without drawing text off the frame. If a future change lets
text escape again, one of these fails before it ships.
"""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from james_os.image_compose import (
    W, _ARCHIVO, _fit, _fit_left, _fit_one_line, _font, _hard_break,
    _spaced_fit, _spaced_w, _text_w, _wrap, _wrap_idx,
)

# Copy engineered to overflow every unguarded path the audit found.
LONG_URL = "https://www.prerealinvestments.com/opportunities/staten-island-2026"
LONG_HASHTAG = "#StatenIslandCommercialRealEstateInvesting"
LONG_NAME = "PRENDAMANO REAL ESTATE OF STATEN ISLAND NEW YORK CITY LLC"
WIDE_HEADLINE = "MOUNTAINS MOVE WHEN WOMEN WANT MORE WARMTH WORLDWIDE"
BIG_STAT = "$105,000,000,000,000"
LONG_KICKER = "PREMIER WATERFRONT INVESTMENT PROPERTIES WORLDWIDE"
ADVERSARIAL = [LONG_URL, LONG_HASHTAG, LONG_NAME, WIDE_HEADLINE, BIG_STAT, LONG_KICKER,
               "SHORT", "A round with a view: open fairways and mountain air"]


@pytest.fixture(autouse=True)
def fresh_pool():
    """These are pure Pillow-rendering tests — no DB. Shadow conftest's autouse
    Postgres fixture so the suite runs without a local database."""
    yield

# The real column widths the compositors pass to the fitter.
MAX_WIDTHS = [W - 2 * 88, int(W * 0.80), int(W * 0.82), W - 2 * 96, W - 2 * 72, 440]


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (W, 1350), (10, 14, 23)))


# ----------------------------------------------------------------- primitives

@pytest.mark.parametrize("text", ADVERSARIAL)
@pytest.mark.parametrize("max_w", MAX_WIDTHS)
def test_wrap_never_exceeds_column(text, max_w):
    d = _draw()
    font = _font(_ARCHIVO, 34)
    for ln in _wrap(d, text, font, max_w):
        assert _text_w(d, ln, font) <= max_w, f"_wrap line {ln!r} exceeds {max_w}"


@pytest.mark.parametrize("text", ADVERSARIAL)
@pytest.mark.parametrize("max_w", MAX_WIDTHS)
def test_fit_lines_never_exceed_column(text, max_w):
    d = _draw()
    font, lines = _fit(d, text, _ARCHIVO, max_w, 900, start=150, minimum=40)
    for ln in lines:
        assert _text_w(d, ln, font) <= max_w, f"_fit line {ln!r} exceeds {max_w}"


@pytest.mark.parametrize("word", [LONG_URL, LONG_HASHTAG, "SUPERCALIFRAGILISTICEXPIALIDOCIOUS"])
@pytest.mark.parametrize("max_w", MAX_WIDTHS)
def test_hard_break_pieces_fit(word, max_w):
    d = _draw()
    font = _font(_ARCHIVO, 60)  # big font so even one token is very wide
    for piece in _hard_break(d, word, font, max_w):
        assert _text_w(d, piece, font) <= max_w or len(piece) == 1


@pytest.mark.parametrize("text", ADVERSARIAL)
@pytest.mark.parametrize("max_w", MAX_WIDTHS)
def test_wrap_idx_and_fit_left_fit(text, max_w):
    d = _draw()
    words = text.split()
    font = _fit_left(d, words, _ARCHIVO, max_w, 900, start=126, minimum=42)
    sp = _text_w(d, " ", font)
    for ln in _wrap_idx(d, words, font, max_w):
        w = sum(_text_w(d, tok, font) for tok, _ in ln) + sp * (len(ln) - 1)
        assert w <= max_w, f"_wrap_idx line width {w} exceeds {max_w}"


@pytest.mark.parametrize("text", [BIG_STAT, LONG_URL, LONG_NAME, "$1,234,567,890,000"])
@pytest.mark.parametrize("max_w", MAX_WIDTHS)
def test_fit_one_line_fits_width(text, max_w):
    d = _draw()
    font = _fit_one_line(d, text, _ARCHIVO, max_w, start=360)
    assert _text_w(d, text, font) <= max_w, f"_fit_one_line {text!r} exceeds {max_w}"


@pytest.mark.parametrize("text", [LONG_KICKER, LONG_NAME, LONG_URL, "SHORT LABEL"])
@pytest.mark.parametrize("mode", ["left", "center"])
def test_spaced_fit_stays_within_budget(text, mode):
    d = _draw()
    max_w = W - 2 * 88
    kw = {"left": 88} if mode == "left" else {"center": W // 2}
    font, tracking = _spaced_fit(d, 100, text, _ARCHIVO, 34, (255, 255, 255), 8, max_w, **kw)
    assert _spaced_w(d, text, font, tracking) <= max_w, "_spaced_fit run exceeds column"


# ----------------------------------------------------------------- renders

def _photo() -> bytes:
    b = BytesIO()
    Image.new("RGB", (1200, 900), (90, 120, 90)).save(b, "JPEG")
    return b.getvalue()


def _is_png(b) -> bool:
    return isinstance(b, (bytes, bytearray)) and b[:8] == b"\x89PNG\r\n\x1a\n"


DESIGNED_FORMATS = [
    "brand_quote", "hero_quote", "statement", "bold_statement", "big_stat",
    "full_bleed", "editorial_split", "minimal_over", "framed_print",
]


@pytest.mark.parametrize("fmt", DESIGNED_FORMATS)
def test_designed_render_smoke(fmt):
    from james_os.designed_render import render_designed

    spec = {
        "quote": WIDE_HEADLINE, "headline": WIDE_HEADLINE, "statement": WIDE_HEADLINE,
        "stat": BIG_STAT, "stat_label": LONG_KICKER, "stat_sub": LONG_URL,
        "kicker": LONG_KICKER, "caption": LONG_URL, "emphasis": "MORE",
        "byline_name": LONG_NAME,
    }
    kit = {"display_name": LONG_NAME, "website": LONG_URL,
           "footer_tagline": "We turn houses into homes across the whole region"}
    png, used = render_designed(fmt, spec, kit=kit, hero_bytes=_photo(),
                                handle="prendamanorealestate")
    assert _is_png(png), f"{fmt} did not return a PNG"


def test_carousel_smoke():
    from james_os import carousel

    pal = {"palette": [
        {"role": "background", "hex": "#0B1B2B"}, {"role": "ink", "hex": "#F4F6F8"},
        {"role": "accent", "hex": "#2E86DE"}, {"role": "surface", "hex": "#12263A"},
    ]}
    assert _is_png(carousel.carousel_cover(
        _photo(), WIDE_HEADLINE, count_promise=LONG_KICKER, kicker=LONG_KICKER,
        palette=pal, handle="h", total=7))
    assert _is_png(carousel.carousel_slide(
        2, 7, WIDE_HEADLINE, section_label=LONG_KICKER, palette=pal, stat=BIG_STAT))
    assert _is_png(carousel.carousel_slide(
        3, 7, WIDE_HEADLINE, section_label=LONG_KICKER, palette=pal, photo=_photo()))


def test_carousel_text_smoke():
    from james_os import carousel_text
    from james_os.image_compose import _colors

    pal = _colors({"display_name": LONG_NAME})
    assert _is_png(carousel_text.text_slide(pal, LONG_NAME, 1, 7, WIDE_HEADLINE,
                                            emphasis="MORE", source=LONG_URL))
    assert _is_png(carousel_text.stat_slide(pal, LONG_NAME, 2, 7, BIG_STAT,
                                            caption=LONG_URL, source=LONG_URL))
    assert _is_png(carousel_text.cover_slide(pal, LONG_NAME, 7, 7, WIDE_HEADLINE,
                                             emphasis="MORE", eyebrow=LONG_KICKER))
    assert _is_png(carousel_text.cover_slide(pal, LONG_NAME, 7, 7, WIDE_HEADLINE,
                                             emphasis="MORE", cta=(LONG_KICKER, LONG_URL)))
