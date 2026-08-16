"""Instagram carousel compositor — a cohesive multi-slide set, palette-aware.

Synthesized from 5 carousel-design references into one grammar:
  * a COVER that earns the swipe (one photo + one headline + a count-promise),
  * INNER slides that all share ONE frame — only the photo/number, one line, the
    section label, and the incrementing index change,
  * a CTA slide that inverts to the accent ground and drops the swipe cues.

Everything shared is PINNED once (palette, margins, accent rule, handle mark,
index token, type ramp) via _carousel_frame; everything else varies per slide.
Reuses compositors_v2's building blocks (adaptive scrim, ink auto-flip, copy
budget, palette) so a carousel inherits the same legibility guarantees.

`carousel(deck, palette, handle)` returns a list[bytes] — one 1080x1350 PNG per
slide, in order — capped at Instagram's 10, floored at 2.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .compositors_v2 import (_ANTON, H, W, _auto_scrim, _clip, _contrast,
                             _handle_footer, _ink_for, _pal, _region_lum, _rel_lum)
from .image_compose import (_ARCHIVO, _case, _cover_safe, _fit, _font, _line_h,
                            _open_rgb, _png, _spaced, _text_w, _wrap)

M = 88


def _carousel_frame(d, pal, handle, index, total, *, is_cta=False, show_arrow=True):
    """The PINNED layer drawn identically on every slide: accent rule top-left,
    the index/progress token top-right, the handle mark bottom-left, and a swipe
    chevron at the right edge. On the CTA the index + chevron are dropped."""
    if not is_cta:
        d.rectangle((M, 92, M + 108, 101), fill=pal["accent"])
        itxt = f"{index:02d}"
        inf = _font(_ANTON, 56)
        d.text((W - M - _text_w(d, itxt, inf), 74), itxt, font=inf, fill=pal["ink"])
        tf = _font(_ARCHIVO, 22)
        tt = f"/ {total:02d}"
        d.text((W - M - _text_w(d, tt, tf), 140), tt, font=tf, fill=pal["muted"])
        if show_arrow:
            cx, cy = W - 38, H // 2
            d.line([(cx - 9, cy - 19), (cx + 8, cy), (cx - 9, cy + 19)], fill=pal["accent"], width=7)
    _handle_footer(d, handle, pal, H - 92, M)


# ── cover: the swipe hook ──
def carousel_cover(photo, headline, count_promise="", kicker="", handle="",
                   palette=None, total=7, focus=(0.5, 0.40)) -> bytes:
    pal = _pal(palette)
    headline, count_promise, kicker = _clip(headline, 8), _clip(count_promise, 4), _clip(kicker, 4)
    if photo:
        base = _auto_scrim(_cover_safe(_open_rgb(photo), W, H, centering=focus), 0.60, pal["ink"])
    else:
        # No photo in the library → a solid brand-colour cover, so a carousel
        # still renders (great for stat/list decks) instead of failing.
        base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    hf, lines = _fit(d, _case(headline, "upper"), _ANTON, W - 2 * M, int(H * 0.34), start=126, minimum=66)
    lh = _line_h(d, hf, 1.02)
    bottom = H - 172
    top = bottom - lh * len(lines)
    ink = (_ink_for(_region_lum(base, (M, top, W - M, int(bottom))), pal["ink"]) if photo else pal["ink"])
    if count_promise:
        _spaced(d, (M, top - 100), count_promise.upper(), _font(_ARCHIVO, 34), pal["accent"], 6)
    if kicker:
        _spaced(d, (M, top - 50), kicker.upper(), _font(_ARCHIVO, 26), ink, 8)
    y = top
    for ln in lines:
        d.text((M, y), ln, font=hf, fill=ink)
        y += lh
    _carousel_frame(d, pal, handle, 1, total)
    return _png(base)


# ── inner slide: one shared frame, two content variants (photo | stat) ──
def carousel_slide(index, total, headline, section_label="", handle="",
                   palette=None, photo=None, stat="", focus=(0.5, 0.42)) -> bytes:
    pal = _pal(palette)
    headline, section_label = _clip(headline, 9), _clip(section_label, 4)
    base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    if stat:                                    # numeric variant
        sf, sl = _fit(d, stat.upper(), _ANTON, W - 2 * M, int(H * 0.40), start=360, minimum=110)
        y = 250
        for ln in sl:
            d.text((M, y), ln, font=sf, fill=pal["accent"])
            y += _line_h(d, sf, 0.98)
        y += 22
        if section_label:
            _spaced(d, (M, y), section_label.upper(), _font(_ARCHIVO, 26), pal["muted"], 8)
            y += 46
        hf, hl = _fit(d, headline.upper(), _ANTON, W - 2 * M, 240, start=74, minimum=44)
        for ln in hl:
            d.text((M, y), ln, font=hf, fill=pal["ink"])
            y += _line_h(d, hf, 1.06)
    else:                                       # photo band variant (editorial)
        photo_h = int(H * 0.56)
        band = _open_rgb(photo) if photo else Image.new("RGB", (W, photo_h), pal["surface"])
        base.paste(_cover_safe(band, W, photo_h, centering=focus), (0, 0))
        y = photo_h + 54
        if section_label:
            _spaced(d, (M, y), section_label.upper(), _font(_ARCHIVO, 26), pal["accent"], 8)
            y += 48
        hf, hl = _fit(d, headline.upper(), _ANTON, W - 2 * M, H - y - 150, start=94, minimum=48)
        for ln in hl:
            d.text((M, y), ln, font=hf, fill=pal["ink"])
            y += _line_h(d, hf, 1.04)
    _carousel_frame(d, pal, handle, index, total)
    return _png(base)


# ── text-only inner slide: pure typography on the brand ground, no photo/stat ──
def carousel_text_slide(index, total, headline, section_label="", handle="",
                        palette=None) -> bytes:
    """A TEXT-ONLY inner slide — the typographic counterpart to the photo/stat
    variants: the brand-colour ground, a small accent section label, and one big
    statement set large and centred. For a photoless deck (a list / steps / points
    that stand on their words), so a text carousel never shows an empty photo band."""
    pal = _pal(palette)
    headline, section_label = _clip(headline, 16), _clip(section_label, 5)
    base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    hf, hl = _fit(d, headline.upper(), _ANTON, W - 2 * M, int(H * 0.50), start=132, minimum=54)
    lh = _line_h(d, hf, 1.04)
    block_h = lh * len(hl)
    top = int((H - block_h) / 2) + 24  # centred, nudged below the index token
    if section_label:
        _spaced(d, (M, top - 62), section_label.upper(), _font(_ARCHIVO, 28), pal["accent"], 8)
    y = top
    for ln in hl:
        d.text((M, y), ln, font=hf, fill=pal["ink"])
        y += lh
    _carousel_frame(d, pal, handle, index, total)
    return _png(base)


# ── CTA: inverted ground, one action, enlarged brand mark, no swipe cues ──
def carousel_cta(action, ask="", handle="", palette=None) -> bytes:
    pal = _pal(palette)
    action, ask = _clip(action, 3), _clip(ask, 12)
    base = Image.new("RGB", (W, H), pal["accent"])
    d = ImageDraw.Draw(base)
    # ink that clears contrast on the accent ground
    if _contrast(pal["bg"], pal["accent"]) >= 3.5:
        ink = pal["bg"]
    else:
        ink = (14, 16, 22) if _rel_lum(pal["accent"]) > 0.5 else (245, 246, 250)
    af, al = _fit(d, action.upper(), _ANTON, W - 2 * M, int(H * 0.34), start=210, minimum=88)
    lh = _line_h(d, af, 1.02)
    y = (H - lh * len(al)) / 2 - 90
    for ln in al:
        d.text((M, y), ln, font=af, fill=ink)
        y += lh
    if ask:
        y += 26
        bf = _font(_ARCHIVO, 34)
        for ln in _wrap(d, ask, bf, W - 2 * M):
            d.text((M, y), ln, font=bf, fill=ink)
            y += 48
    # enlarged handle mark as the resolving brand sign-off
    if handle:
        h = handle if handle.startswith("@") else "@" + handle
        d.ellipse((M, H - 118, M + 22, H - 96), fill=ink)
        d.text((M + 36, H - 122), h, font=_font(_ARCHIVO, 34), fill=ink)
    return _png(base)


# ── orchestrator ──
def carousel(deck: dict, palette=None, handle="") -> list[bytes]:
    """Render a deck to an ordered list of slide PNGs.

    deck = {
      "cover": {"photo": bytes, "headline": str, "count_promise": str,
                "kicker": str, "focus": (x,y)?},
      "slides": [ {"headline": str, "section_label": str,
                   "photo": bytes?  OR  "stat": str, "focus": (x,y)?}, ... ],
      "cta": {"action": str, "ask": str},
    }
    One atomic idea per inner slide. Capped at 10 slides total, floored at 2.
    """
    slides = list(deck.get("slides") or [])
    # cover + inners + cta, but never exceed IG's 10 (trim inners).
    max_inner = 10 - 2
    slides = slides[:max_inner]
    total = 2 + len(slides)
    out: list[bytes] = []
    c = deck.get("cover") or {}
    out.append(carousel_cover(
        c.get("photo"), c.get("headline", ""), c.get("count_promise", ""),
        c.get("kicker", ""), handle, palette, total, tuple(c.get("focus", (0.5, 0.40)))))
    for i, s in enumerate(slides, start=2):
        if s.get("kind") == "text":  # text-only deck — pure typography, no photo/stat
            out.append(carousel_text_slide(
                i, total, s.get("headline", ""), s.get("section_label", ""), handle, palette))
        else:
            out.append(carousel_slide(
                i, total, s.get("headline", ""), s.get("section_label", ""), handle, palette,
                photo=s.get("photo"), stat=s.get("stat", ""), focus=tuple(s.get("focus", (0.5, 0.42)))))
    cta = deck.get("cta") or {}
    out.append(carousel_cta(cta.get("action", "LEARN MORE"), cta.get("ask", ""), handle, palette))
    return out


__all__ = ["carousel", "carousel_cover", "carousel_slide", "carousel_text_slide", "carousel_cta"]
