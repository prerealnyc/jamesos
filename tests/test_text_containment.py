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


# ----------------------------------------------------------------- text style

def test_forced_ink_light_and_dark():
    from james_os.image_compose import _forced_ink, text_style

    assert _forced_ink() is None  # auto by default
    with text_style("light"):
        assert _forced_ink() == (245, 246, 250)
    with text_style("dark"):
        assert _forced_ink() == (14, 16, 22)
    assert _forced_ink() is None  # context restored


def test_resolve_arbitrary_colours():
    from james_os.image_compose import _resolve_color, _forced_ink, text_style

    assert _resolve_color("white") == (245, 246, 250)
    assert _resolve_color("red") is not None and _resolve_color("red") != (245, 246, 250)
    assert _resolve_color("#1b4d3e") == (0x1b, 0x4d, 0x3e)
    assert _resolve_color("#abc") == (0xaa, 0xbb, 0xcc)
    assert _resolve_color("not-a-colour") is None  # unknown → auto (safe)
    assert _resolve_color("") is None
    with text_style("red"):
        assert _forced_ink() == _resolve_color("red")
    with text_style("#1b4d3e"):
        assert _forced_ink() == (0x1b, 0x4d, 0x3e)


def test_pal_honors_forced_colour():
    from james_os.compositors_v2 import _pal
    from james_os.image_compose import text_style

    role_pal = {"palette": [{"role": "background", "hex": "#0B1B2B"},
                            {"role": "ink", "hex": "#111111"},
                            {"role": "accent", "hex": "#2E86DE"}]}
    with text_style("light"):
        assert _pal(role_pal)["ink"] == (245, 246, 250)  # forced white, not the #111 ink
    with text_style("dark"):
        assert _pal(role_pal)["ink"] == (14, 16, 22)


def test_ink_for_forced_never_flips():
    from james_os.compositors_v2 import _ink_for
    from james_os.image_compose import text_style

    bright_ground, dark_ink = 0.95, (10, 10, 10)
    # Without a force, dark ink on a bright ground is KEPT (contrast is high).
    assert _ink_for(bright_ground, dark_ink) == dark_ink
    # Forced white must win even though auto-contrast would keep the dark ink.
    with text_style("light"):
        assert _ink_for(bright_ground, dark_ink) == (245, 246, 250)


@pytest.mark.parametrize("fmt", ["statement", "full_bleed", "brand_quote"])
@pytest.mark.parametrize("style", [("light", True), ("dark", False),
                                   ("red", False), ("#1b4d3e", True)])
def test_render_forced_style_smoke(fmt, style):
    from james_os.designed_render import render_designed
    from james_os.image_compose import text_style

    color, bold = style
    spec = {"quote": WIDE_HEADLINE, "headline": WIDE_HEADLINE, "statement": WIDE_HEADLINE,
            "emphasis": "MORE"}
    with text_style(color, bold):
        png, _used = render_designed(fmt, spec, kit={"display_name": LONG_NAME},
                                     hero_bytes=_photo(), handle="h")
    assert _is_png(png)


def test_carousel_honours_forced_colour():
    # Carousel slides render through _pal/_ink_for, so a forced colour applies to
    # the whole deck — smoke-render cover + stat + photo slides under text_style.
    from james_os import carousel
    from james_os.image_compose import text_style

    pal = {"palette": [{"role": "background", "hex": "#0B1B2B"},
                       {"role": "ink", "hex": "#111111"}, {"role": "accent", "hex": "#2E86DE"}]}
    with text_style("light", True):
        assert _is_png(carousel.carousel_cover(_photo(), WIDE_HEADLINE,
                                               count_promise=LONG_KICKER, palette=pal, total=7))
        assert _is_png(carousel.carousel_slide(2, 7, WIDE_HEADLINE,
                                               section_label=LONG_KICKER, palette=pal, stat=BIG_STAT))
        assert _is_png(carousel.carousel_slide(3, 7, WIDE_HEADLINE,
                                               section_label=LONG_KICKER, palette=pal, photo=_photo()))


def test_styling_override_keyword_detection():
    # Pure keyword logic — no DB. Import the functions directly.
    import importlib.util
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "james_os" / "api_v1.py"
    text = src.read_text()
    # Grab the pure block (constant + both helpers) from the constant up to the
    # next real coroutine, so _styling_override's _COLOR_WORDS reference resolves.
    start = text.index("_COLOR_WORDS = (")
    end = text.index("async def _run_regenerate", start)
    ns: dict = {}
    exec(compile(text[start:end], str(src), "exec"), ns)  # noqa: S102 — isolated defs
    so, wnp = ns["_styling_override"], ns["_wants_new_photo"]

    assert so("text needs to be white color, not black and use thicker fonts") == \
        {"image_text_color_hex": "white", "image_text_weight": 1}
    assert so("change text color to white on this image") == {"image_text_color_hex": "white"}
    assert so("make the text black") == {"image_text_color_hex": "black"}
    assert so("make text red") == {"image_text_color_hex": "red"}
    assert so("i want the text in gold") == {"image_text_color_hex": "gold"}
    assert so("use #1b4d3e for the headline") == {"image_text_color_hex": "#1b4d3e"}
    assert so("love it, ship it") == {}
    # same image by default — a different photo is used ONLY when asked/complained
    assert wnp("change text color to white on this image") is False
    assert wnp("make text red") is False
    assert wnp("make James bigger") is False       # layout change, not a new photo
    assert wnp("make it white but use a different photo") is True
    assert wnp("i hate this photo, it's blurry") is True
    assert wnp("try another image, this one doesn't work") is True


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


# ------------------------------------------------- containment at OTHER canvases
#
# The invariant above was only ever exercised at 4:5, because 4:5 was the only
# shape image_compose could produce. Now a caller can render at the destination
# platform's ratio — 16:9 for X, 9:16 for a Reel, 2:3 for a Pin — and text that
# is contained in a tall frame is not automatically contained in a wide one:
# the same words get a narrower column and fewer lines to use. So the guarantee
# has to be re-proved on every shape we can now ask for.

OTHER_CANVASES = [
    (1600, 900),    # X in-stream, 16:9 — the widest and the hardest
    (1080, 1920),   # TikTok / Reels / Stories, 9:16
    (1000, 1500),   # Pinterest, 2:3
    (1080, 1080),   # square
    (1080, 1440),   # Instagram's 3:4 feed
]


@pytest.mark.parametrize("size", OTHER_CANVASES)
@pytest.mark.parametrize("fmt", ["brand_quote", "statement", "big_stat"])
def test_designed_render_survives_other_canvases(fmt, size):
    """Adversarial copy, rendered at a non-default shape, still produces a PNG
    of exactly the size asked for."""
    from james_os import image_compose as ic
    from james_os.designed_render import render_designed

    spec = {
        "quote": WIDE_HEADLINE, "headline": WIDE_HEADLINE, "statement": WIDE_HEADLINE,
        "stat": BIG_STAT, "stat_label": LONG_KICKER, "stat_sub": LONG_URL,
        "kicker": LONG_KICKER, "caption": LONG_URL, "emphasis": "MORE",
        "byline_name": LONG_NAME,
    }
    kit = {"display_name": LONG_NAME, "website": LONG_URL,
           "footer_tagline": "We turn houses into homes across the whole region"}
    with ic.canvas(*size):
        png, _used = render_designed(fmt, spec, kit=kit, hero_bytes=_photo(),
                                     handle="prendamanorealestate")
    assert _is_png(png), f"{fmt} at {size} did not return a PNG"
    assert Image.open(BytesIO(png)).size == size, f"{fmt} ignored the canvas {size}"


def test_the_canvas_resets_after_a_render():
    """A leaked canvas would silently reshape every later render in the same
    task — the worst kind of bug, because the picture still looks fine."""
    from james_os import image_compose as ic

    before = (ic._w(), ic._h())
    with ic.canvas(1600, 900):
        pass
    assert (ic._w(), ic._h()) == before == (ic.W, ic.H)
