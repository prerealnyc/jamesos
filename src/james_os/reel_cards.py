"""Designed graphic cards for reels — built as NATIVE animated elements.

The reference reels cut away from the speaker to a designed card: a greyscale
cutout on a light vignette, a warm gradient with a rounded label chip, a profile
mock, a big stat. The card doesn't just appear — it BUILDS: the background
lands, the image scales up, then the lines arrive one after another.

Two ways to do that, and the choice matters:

  * composite a flat card image (Pillow, like `image_compose`) and animate the
    whole picture in — simple, but the staggered per-element reveal is
    impossible, because it's one flat picture; and
  * emit the card as SEPARATE Creatomate elements on their own tracks, each
    with its own animation delay.

This module does the second. `caption_styles` already proves the pattern — it
emits `animations` with `type: scale` / `type: fade`, `easing` and per-element
`time` — so staggered build-ins are native, not faked, and the text stays live
text (re-timable, re-styleable) instead of baked pixels.

TEXT CONTAINMENT is an invariant here, not a nicety (see
tests/test_text_containment.py for the Pillow side). Creatomate WRAPS overflow
onto a second row inside the same element, which on a card with stacked lines
hides the wrapped row behind the next line. So `fit_card_vh` shrinks the font
until the LONGEST line fits its box without wrapping, using the same em-advance
math as `caption_styles._fit_hook_vh`.

Z-ORDER: cards are full-bleed cutaways that REPLACE the footage, so they sit on
tracks above everything visual (12-15). During a card the watermark and progress
bar are behind it — which is what the reference does too.
"""

import math
from dataclasses import dataclass, field

# Track band for cards. Creatomate track number == z-order, and these must
# cover the speaker (1), b-roll (2) and the caption tracks (3/4).
TRACK_BG, TRACK_IMAGE, TRACK_TEXT_A, TRACK_TEXT_B = 12, 13, 14, 15

CARD_STYLES = ("collage", "label", "profile", "stat", "quote")

# How long after the card starts each layer arrives. The reference builds in
# roughly this order and these gaps read as deliberate rather than laggy.
_STAGGER = {"bg": 0.0, "image": 0.10, "line_a": 0.22, "line_b": 0.38}
_MIN_CARD_S = 1.6      # below this the build-in has no room to read
_MAX_LINES = 2


@dataclass
class Card:
    """One designed cutaway. `lines` are drawn from what is actually spoken in
    the window (see reel_director) — a card that invents copy is a card that
    contradicts the voiceover."""
    style: str
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    accent: str = ""          # a word/phrase inside `lines` to colour
    image_url: str = ""       # cutout / photo, optional for every style
    handle: str = ""          # profile style only

    @property
    def duration(self) -> float:
        return max(0.0, round(self.end - self.start, 3))


# ── palettes ─────────────────────────────────────────────────────────
# Deliberately neutral so a card reads as the BRAND's, not as one client's.
# `accent_color` is overridden from the brand kit when one is set.
_PALETTES: dict[str, dict] = {
    "collage": {"bg": "#EDEBE8", "ink": "#1A1A1A", "sub": "#6B6B6B"},
    "label":   {"bg": "#C2410C", "ink": "#FFFFFF", "sub": "rgba(255,255,255,0.75)",
                "chip": "rgba(0,0,0,0.22)"},
    "profile": {"bg": "#0B0B0F", "ink": "#FFFFFF", "sub": "#9A9AA5",
                "chip": "#1D4ED8"},
    "stat":    {"bg": "#0B0B0F", "ink": "#FFFFFF", "sub": "#9A9AA5"},
    "quote":   {"bg": "#111114", "ink": "#FFFFFF", "sub": "#9A9AA5"},
}
_DEFAULT_ACCENT = "#FFC400"


# Average glyph advance as a fraction of font px, per face. Heavy display
# faces are MUCH wider than a text font — `caption_styles._fit_hook_vh` uses
# 0.74 for exactly this reason, and undershooting it is how text escapes.
EM_DISPLAY = 0.74      # Archivo Black / Anton and friends
EM_TEXT = 0.58         # Inter and other text faces
# Absolute floor — only large enough to be a valid font size. It is deliberately
# NOT a legibility floor: any floor big enough to keep text readable is also big
# enough to let extreme copy overflow, which is the failure this whole function
# exists to prevent. Containment wins; legibility is protected upstream by
# `rewrap` and by the director capping a card line at ~5 spoken words.
_MIN_VH = 0.5


def fit_card_vh(lines, base_vh: float, em: float = EM_DISPLAY,
                width_pct: float = 76.0) -> float:
    """Largest font (vh) at which the LONGEST line fits the card's text column
    WITHOUT wrapping — the containment guard.

    Same reasoning as `caption_styles._fit_hook_vh`: Creatomate wraps overflow
    into a second row inside the element, which on stacked card lines hides
    behind the next line, so the cost of overflow is a visibly broken card.
    Bias small. On a 9:16 canvas the frame is 1080px wide and 1 vh = 19.2 px.

    There is no readable floor here on purpose. An earlier cut clamped to a
    minimum size, which meant long copy silently overflowed instead of
    shrinking — the exact failure this function exists to prevent.
    """
    text_lines = [str(ln) for ln in (lines or []) if str(ln).strip()]
    longest = max((len(ln) for ln in text_lines), default=1)
    fit = (width_pct / 100.0 * 1080.0) / (max(1, longest) * em) / 19.2 * 0.92
    # FLOOR, never round: rounding a fit-to-width value UP hands back a size
    # fractionally wider than the column, and at small sizes that increment is
    # a big enough fraction to eat the safety factor and overflow.
    return max(_MIN_VH, math.floor(min(float(base_vh), fit) * 10) / 10)


def rewrap(lines, max_lines: int = 2) -> list[str]:
    """Re-flow copy across up to `max_lines` so the longest line is as short as
    it can be. Shrinking to fit is the guarantee; re-wrapping first is what
    keeps the result legible rather than microscopic."""
    words = " ".join(str(ln) for ln in (lines or []) if str(ln).strip()).split()
    if not words:
        return []
    n = max(1, min(int(max_lines), len(words)))
    per = -(-len(words) // n)          # ceil: balance the lines
    out = [" ".join(words[i:i + per]) for i in range(0, len(words), per)]
    return [ln for ln in out if ln][:n]


def _fade(dur: float, at: float = 0.0, length: float = 0.22) -> list[dict]:
    """Fade in, and fade out at the tail so a card never hard-pops off."""
    length = min(length, max(0.06, dur / 4))
    return [
        {"time": at, "duration": length, "type": "fade", "easing": "quadratic-out"},
        {"time": max(at, dur - length), "duration": length, "type": "fade",
         "reversed": True, "easing": "quadratic-in"},
    ]


def _scale_in(dur: float, at: float, length: float = 0.34) -> list[dict]:
    """The build-in: scale up as it fades on. This is the motion that makes the
    reference cards feel designed rather than pasted."""
    length = min(length, max(0.10, dur / 3))
    return [
        {"time": at, "duration": length, "type": "scale",
         "scope": "element", "easing": "quadratic-out",
         "start_scale": "82%", "end_scale": "100%"},
        {"time": at, "duration": min(length, 0.24), "type": "fade",
         "easing": "quadratic-out"},
        {"time": max(at, dur - 0.2), "duration": 0.2, "type": "fade",
         "reversed": True, "easing": "quadratic-in"},
    ]


def _bg_element(color: str, start: float, dur: float) -> dict:
    """Full-bleed background. Background-only text element — the same idiom
    `assembly._progress_bar_element` uses for a solid rectangle."""
    return {
        "type": "text", "text": " ",
        "track": TRACK_BG, "time": start, "duration": dur,
        "x": "50%", "y": "50%", "x_anchor": "50%", "y_anchor": "50%",
        "width": "100%", "height": "100%",
        "font_size": "1 vh",
        "background_color": color,
        "animations": _fade(dur, _STAGGER["bg"], 0.18),
    }


def _text_element(text: str, *, track: int, start: float, dur: float,
                  at: float, y: str, vh: float, color: str,
                  font: str = "Archivo Black", weight: int = 800,
                  transform: str = "", width: str = "76%") -> dict:
    el = {
        "type": "text", "text": text,
        "track": track, "time": start, "duration": dur,
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        "y": y, "y_anchor": "50%",
        "width": width,
        "font_family": font, "font_weight": weight,
        "font_size": f"{vh} vh",
        "fill_color": color,
        "animations": _scale_in(dur, at),
    }
    if transform:
        el["transform"] = transform
    return el


def _image_element(url: str, *, start: float, dur: float, at: float,
                   y: str = "42%", width: str = "62%") -> dict:
    return {
        "type": "image", "source": url,
        "track": TRACK_IMAGE, "time": start, "duration": dur,
        "x": "50%", "y": y, "x_anchor": "50%", "y_anchor": "50%",
        "width": width, "fit": "contain",
        "animations": _scale_in(dur, at),
    }


def _lines_of(card: Card) -> list[str]:
    """The card's copy, re-flowed across the available lines so long spoken
    spans use the space instead of shrinking to nothing."""
    return rewrap(card.lines, _MAX_LINES)


# ── the styles ───────────────────────────────────────────────────────

def _collage(card: Card, pal: dict, accent: str) -> list[dict]:
    """Light vignette, a cutout image, and one or two quiet lines above it —
    the 'And / most of the time' card."""
    d, s = card.duration, card.start
    out = [_bg_element(pal["bg"], s, d)]
    lines = _lines_of(card)
    vh = fit_card_vh(lines, 4.4, em=EM_TEXT, width_pct=72.0)
    ys = ["26%", "33%"][: len(lines)]
    for i, (ln, y) in enumerate(zip(lines, ys)):
        out.append(_text_element(
            ln, track=TRACK_TEXT_A + i, start=s, dur=d,
            at=_STAGGER["line_a" if i == 0 else "line_b"],
            y=y, vh=vh, color=pal["ink"] if i == 0 else pal["sub"],
            font="Inter", weight=600, width="72%",
        ))
    if card.image_url:
        out.append(_image_element(card.image_url, start=s, dur=d,
                                  at=_STAGGER["image"], y="58%", width="66%"))
    return out


def _label(card: Card, pal: dict, accent: str) -> list[dict]:
    """Warm gradient-ish field with a rounded chip holding one short label —
    the 'Pricing strategy' card. Creatomate has no gradient primitive here, so
    this is a flat warm field: the honest approximation, not a fake gradient."""
    d, s = card.duration, card.start
    lines = _lines_of(card)[:1] or [""]
    vh = fit_card_vh(lines, 5.0, em=EM_DISPLAY, width_pct=60.0)
    return [
        _bg_element(pal["bg"], s, d),
        # The chip is a background-only text element sitting under the label.
        {
            "type": "text", "text": " ",
            "track": TRACK_IMAGE, "time": s, "duration": d,
            "x": "50%", "y": "50%", "x_anchor": "50%", "y_anchor": "50%",
            "width": "68%", "height": "13%",
            "font_size": "1 vh", "background_color": pal["chip"],
            "animations": _scale_in(d, _STAGGER["image"]),
        },
        _text_element(lines[0], track=TRACK_TEXT_A, start=s, dur=d,
                      at=_STAGGER["line_a"], y="50%", vh=vh,
                      color=pal["ink"], font="Archivo Black", weight=800,
                      width="60%"),
    ]


def _profile(card: Card, pal: dict, accent: str) -> list[dict]:
    """A social-profile mock: portrait, handle, and a follow chip."""
    d, s = card.duration, card.start
    lines = _lines_of(card)
    vh = fit_card_vh(lines[:1] or [card.handle or ""], 4.6, em=EM_DISPLAY, width_pct=70.0)
    out = [_bg_element(pal["bg"], s, d)]
    if card.image_url:
        out.append(_image_element(card.image_url, start=s, dur=d,
                                  at=_STAGGER["image"], y="40%", width="44%"))
    if lines:
        out.append(_text_element(lines[0], track=TRACK_TEXT_A, start=s, dur=d,
                                 at=_STAGGER["line_a"], y="60%", vh=vh,
                                 color=pal["ink"], width="70%"))
    if card.handle:
        out.append(_text_element(
            card.handle, track=TRACK_TEXT_B, start=s, dur=d,
            at=_STAGGER["line_b"], y="68%",
            vh=fit_card_vh([card.handle], 2.6, em=EM_TEXT, width_pct=70.0),
            color=pal["sub"], font="Inter", weight=500, width="70%"))
    return out


def _stat(card: Card, pal: dict, accent: str) -> list[dict]:
    """A number that deserves the whole frame, with its qualifier under it."""
    d, s = card.duration, card.start
    lines = _lines_of(card)
    head = lines[0] if lines else ""
    sub = lines[1] if len(lines) > 1 else ""
    out = [
        _bg_element(pal["bg"], s, d),
        _text_element(head, track=TRACK_TEXT_A, start=s, dur=d,
                      at=_STAGGER["line_a"], y="45%",
                      vh=fit_card_vh([head], 11.0, em=EM_DISPLAY, width_pct=80.0),
                      color=accent, width="80%"),
    ]
    if sub:
        out.append(_text_element(
            sub, track=TRACK_TEXT_B, start=s, dur=d, at=_STAGGER["line_b"],
            y="57%", vh=fit_card_vh([sub], 3.4, em=EM_TEXT, width_pct=76.0),
            color=pal["sub"], font="Inter", weight=500))
    return out


def _quote(card: Card, pal: dict, accent: str) -> list[dict]:
    """A spoken line held on screen as a statement."""
    d, s = card.duration, card.start
    lines = _lines_of(card)
    vh = fit_card_vh(lines, 5.6, em=EM_DISPLAY, width_pct=78.0)
    out = [_bg_element(pal["bg"], s, d)]
    ys = ["45%", "55%"][: len(lines)]
    for i, (ln, y) in enumerate(zip(lines, ys)):
        colour = accent if (card.accent and card.accent.lower() in ln.lower()) else pal["ink"]
        out.append(_text_element(
            ln, track=TRACK_TEXT_A + i, start=s, dur=d,
            at=_STAGGER["line_a" if i == 0 else "line_b"],
            y=y, vh=vh, color=colour, width="78%"))
    return out


_BUILDERS = {
    "collage": _collage, "label": _label, "profile": _profile,
    "stat": _stat, "quote": _quote,
}


def card_elements(card: Card, brand_kit: dict | None = None) -> list[dict]:
    """One Card → the Creatomate elements that draw and animate it.

    Returns [] for a card too short to build in (the animation would be a
    flicker) or an unknown style — never a half-drawn card."""
    if card is None or card.duration < _MIN_CARD_S:
        return []
    style = (card.style or "").strip().lower()
    build = _BUILDERS.get(style)
    if build is None:
        return []
    pal = dict(_PALETTES.get(style, _PALETTES["quote"]))
    accent = (brand_kit or {}).get("accent_color") or _DEFAULT_ACCENT
    return build(card, pal, accent)


def cards_to_elements(cards, brand_kit: dict | None = None) -> list[dict]:
    """Every card in a plan → one flat element list, in time order."""
    out: list[dict] = []
    for c in sorted(cards or [], key=lambda x: x.start):
        out.extend(card_elements(c, brand_kit))
    return out


def card_windows(cards) -> list[tuple[float, float]]:
    """The (start, end) spans a card covers. The caller suppresses the normal
    caption track inside these — a card carries its OWN text, and leaving the
    captions running stacks two sets of words on one frame."""
    return [(c.start, c.end) for c in (cards or []) if c.duration >= _MIN_CARD_S]


def windows_from_elements(elements) -> list[tuple[float, float]]:
    """Recover the card windows from already-built elements, by reading the
    background layer. Lets the assembler take a flat element list and still
    know where to mute the captions, without a second parameter to keep in
    sync with the first."""
    out: list[tuple[float, float]] = []
    for el in elements or []:
        if not isinstance(el, dict) or el.get("track") != TRACK_BG:
            continue
        try:
            t = float(el.get("time") or 0.0)
            d = float(el.get("duration") or 0.0)
        except (TypeError, ValueError):
            continue
        if d > 0:
            out.append((t, round(t + d, 3)))
    return sorted(out)


def suppress_captions_in_windows(captions, windows) -> list:
    """Drop caption flashes that begin inside a card window.

    Kept as a pure list filter so it works on whatever flash shape the caller
    holds (dicts with start/end, or objects) without importing them."""
    if not windows:
        return list(captions or [])
    def _start(c):
        return float(c.get("start", 0) if isinstance(c, dict) else getattr(c, "start", 0))
    out = []
    for c in captions or []:
        s = _start(c)
        if any(w0 <= s < w1 for w0, w1 in windows):
            continue
        out.append(c)
    return out


__all__ = [
    "Card", "CARD_STYLES", "card_elements", "cards_to_elements",
    "card_windows", "windows_from_elements", "suppress_captions_in_windows",
    "rewrap", "EM_DISPLAY", "EM_TEXT",
    "fit_card_vh",
    "TRACK_BG", "TRACK_IMAGE", "TRACK_TEXT_A", "TRACK_TEXT_B",
]
