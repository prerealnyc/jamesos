"""Designed reel cards — containment, layering, and build-in invariants.

Pure element-building; no DB, no providers, no renders.
"""

import pytest

from james_os.reel_cards import (
    CARD_STYLES,
    TRACK_BG,
    Card,
    card_elements,
    cards_to_elements,
    fit_card_vh,
    rewrap,
    suppress_captions_in_windows,
    windows_from_elements,
)

# Copy engineered to overflow an unguarded text box, mirroring the adversarial
# set in test_text_containment.py.
LONG = [
    "PRENDAMANO REAL ESTATE OF STATEN ISLAND NEW YORK CITY LLC",
    "#StatenIslandCommercialRealEstateInvesting",
    "$105,000,000,000,000",
    "MOUNTAINS MOVE WHEN WOMEN WANT MORE WARMTH WORLDWIDE",
]


@pytest.fixture(autouse=True)
def fresh_pool():
    """Pure builder tests — shadow conftest's autouse Postgres fixture so these
    run with no database."""
    yield


def _card(**over) -> Card:
    base = dict(style="quote", start=5.0, end=9.0, lines=["A hard claim", "held on screen"])
    base.update(over)
    return Card(**base)


# ── containment: the invariant ───────────────────────────────────────

def test_long_copy_never_exceeds_its_column():
    # The guarantee: chosen font size × longest line × em-advance must fit the
    # text column. If a future change lets text escape, this fails first.
    for text in LONG:
        for width_pct, em in ((76.0, 0.62), (60.0, 0.62), (80.0, 0.70)):
            vh = fit_card_vh([text], base_vh=12.0, em=em, width_pct=width_pct)
            line_px = len(text) * em * vh * 19.2
            column_px = width_pct / 100.0 * 1080.0
            assert line_px <= column_px, f"{text!r} overflows at {vh}vh"


def test_fit_always_returns_a_usable_size():
    for text in LONG:
        assert fit_card_vh([text], 12.0) > 0


def test_realistic_card_copy_stays_readable():
    # Legibility is protected by re-wrap + the director's ~5-words-a-line cap,
    # not by a size floor. For copy that actually reaches a card, the result
    # must still be a real on-screen size.
    for lines in (["Own your decisions"], ["After half", "$1 billion"],
                  ["most of the time", "it does not"]):
        assert fit_card_vh(rewrap(lines, 2), 5.6) >= 2.0


def test_rewrap_keeps_every_word():
    assert " ".join(rewrap(["one two three", "four five"], 2)).split() == \
        ["one", "two", "three", "four", "five"]


def test_short_copy_keeps_the_designed_size():
    assert fit_card_vh(["Own it"], 5.0) == 5.0


def test_the_longest_line_governs_not_the_first():
    vh = fit_card_vh(["Hi", LONG[0]], 12.0)
    assert vh == fit_card_vh([LONG[0]], 12.0)


# Measured against em values DELIBERATELY wider than the ones the builder
# assumes (0.74 display / 0.58 text), so the check also proves the safety
# margin holds — not just that the code agrees with itself.
_PESSIMISTIC_EM = {"Archivo Black": 0.78, "Inter": 0.62}


def test_every_style_contains_its_text():
    for style in CARD_STYLES:
        els = card_elements(_card(style=style, lines=[LONG[0], LONG[1]],
                                  image_url="https://x/i.png", handle="@brand"))
        for el in els:
            text = (el.get("text") or "").strip()
            if not text or el.get("type") != "text":
                continue
            vh = float(str(el["font_size"]).split()[0])
            width_pct = float(str(el.get("width", "76%")).rstrip("%"))
            em = _PESSIMISTIC_EM.get(el.get("font_family", ""), 0.78)
            assert len(text) * em * vh * 19.2 <= width_pct / 100.0 * 1080.0, \
                f"{style}: {text!r} at {vh}vh ({el.get('font_family')}) overflows"


# ── layering + build-in ──────────────────────────────────────────────

def test_cards_sit_above_the_caption_tracks():
    # Captions render on tracks 3/4; a card is a full-bleed cutaway and must
    # cover them, not hide behind them.
    for style in CARD_STYLES:
        els = card_elements(_card(style=style, image_url="https://x/i.png", handle="@b"))
        assert els, style
        assert min(e["track"] for e in els) > 4


def test_every_style_lays_a_full_bleed_background():
    for style in CARD_STYLES:
        bg = [e for e in card_elements(_card(style=style)) if e["track"] == TRACK_BG]
        assert len(bg) == 1, style
        assert bg[0]["width"] == "100%" and bg[0]["height"] == "100%"


def test_layers_arrive_staggered_not_all_at_once():
    els = card_elements(_card(style="stat", lines=["$1 billion", "in real estate deals"]))
    firsts = [e["animations"][0]["time"] for e in els if e.get("animations")]
    assert len(set(firsts)) > 1, "every layer animates at the same instant — no build-in"
    assert min(firsts) == 0.0


def test_a_card_too_short_to_build_in_is_refused():
    assert card_elements(_card(start=5.0, end=5.9)) == []


def test_an_unknown_style_draws_nothing_rather_than_something_broken():
    assert card_elements(_card(style="hologram")) == []


def test_elements_stay_inside_the_card_window():
    c = _card(start=7.5, end=12.0)
    for el in card_elements(c):
        assert el["time"] == pytest.approx(c.start)
        assert el["duration"] == pytest.approx(c.duration)


def test_cards_to_elements_orders_by_time():
    late, early = _card(start=20.0, end=24.0), _card(start=5.0, end=9.0)
    els = cards_to_elements([late, early])
    times = [e["time"] for e in els]
    assert times == sorted(times)


def test_at_most_two_text_lines_are_drawn_and_no_word_is_lost():
    # Re-wrap re-flows the copy across the two available lines; dropping a
    # spoken word would put the card at odds with the voiceover.
    els = card_elements(_card(style="quote", lines=["one", "two", "three"]))
    texts = [e["text"] for e in els
             if e.get("type") == "text" and (e.get("text") or "").strip()]
    assert len(texts) <= 2
    assert " ".join(texts).split() == ["one", "two", "three"]


# ── caption suppression ──────────────────────────────────────────────

def test_windows_are_recovered_from_the_background_layer():
    els = cards_to_elements([_card(start=5.0, end=9.0), _card(start=20.0, end=23.0)])
    assert windows_from_elements(els) == [(5.0, 9.0), (20.0, 23.0)]


def test_captions_under_a_card_are_dropped():
    caps = [{"start": 1.0}, {"start": 6.0}, {"start": 8.9}, {"start": 12.0}]
    kept = suppress_captions_in_windows(caps, [(5.0, 9.0)])
    assert [c["start"] for c in kept] == [1.0, 12.0]


def test_no_windows_means_no_captions_lost():
    caps = [{"start": 1.0}, {"start": 6.0}]
    assert suppress_captions_in_windows(caps, []) == caps
