"""Candidate v2 compositors — palette-aware layouts that show the photo WHOLE.

The three shipped templates crop a full-scene photo into a tall narrow strip
(hero_quote) or ignore its colour system, so a stunning aerial becomes a random
vertical sliver and every brand renders in James's navy. These fix both:

  * the photo is cover-cropped around a focal point and shown edge-to-edge (or
    in a full-width band), with a gradient scrim only where text sits — so the
    image reads as itself, and the words sit WITH it, not over a random slice.
  * every colour comes from a `palette` dict {bg, ink, accent, surface}, so the
    same layout renders in ANY brand's colours (James's values are just one
    possible palette, not baked in).

These are brand-agnostic building blocks; the design-intelligence archetype
foundry decides which to use and with what copy. Nothing here is James-specific.
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw

from .image_compose import (_ARCHIVO, _cover_safe, _draw_centered, _fit, _font,
                            _line_h, _open_rgb, _png, _spaced, _spaced_w, _text_w)

_ANTON = os.path.join(os.path.dirname(__file__), "assets", "fonts", "Anton-Regular.ttf")
W, H = 1080, 1350


def _rgb(s: str, fb=(10, 14, 23)) -> tuple:
    s = (s or "").strip().lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, IndexError):
        return fb


def _pal(p: dict | None) -> dict:
    p = p or {}
    return {
        "bg": _rgb(p.get("bg", "#0A0E17")),
        "ink": _rgb(p.get("ink", "#FFFFFF")),
        "accent": _rgb(p.get("accent", "#FF6A2C")),
        "surface": _rgb(p.get("surface", "#14203A")),
    }


def _muted(ink: tuple, bg: tuple, t: float = 0.55) -> tuple:
    return tuple(int(ink[i] * t + bg[i] * (1 - t)) for i in range(3))


def _spaced_c(d: ImageDraw.ImageDraw, y: int, text: str, font, fill, tr: int):
    """Letter-spaced text, horizontally centred on the 1080px canvas."""
    x = (W - _spaced_w(d, text, font, tr)) // 2
    _spaced(d, (x, y), text, font, fill, tr)


def _scrim(img: Image.Image, frac: float = 0.5, strength: int = 205, top: bool = False) -> Image.Image:
    """Darken a `frac` band at the bottom (or top) with a vertical gradient so
    overlaid text stays legible without hiding the photo."""
    w, h = img.size
    grad = Image.new("L", (1, h), 0)
    for y in range(h):
        pos = (y / h)
        t = ((1 - pos) - (1 - frac)) / frac if top else (pos - (1 - frac)) / frac
        grad.putpixel((0, y), max(0, min(strength, int(strength * t))))
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(black, img.convert("RGB"), grad.resize((w, h)))


def _handle_footer(d: ImageDraw.ImageDraw, handle: str, pal: dict, y: int, x: int = 88):
    if not handle:
        return
    h = handle if handle.startswith("@") else "@" + handle
    d.ellipse((x, y + 6, x + 14, y + 20), fill=pal["accent"])
    d.text((x + 26, y), h, font=_font(_ARCHIVO, 28), fill=pal["ink"])


# ── 1. full-bleed hero: the photo IS the post; headline anchored bottom-left ──
def full_bleed(photo: bytes, headline: str, kicker: str = "", handle: str = "",
               palette: dict | None = None, focus=(0.5, 0.40)) -> bytes:
    pal = _pal(palette)
    base = _cover_safe(_open_rgb(photo), W, H, centering=focus)
    base = _scrim(base, frac=0.55, strength=200)
    d = ImageDraw.Draw(base)
    M = 88
    hf, lines = _fit(d, headline.upper(), _ANTON, W - 2 * M, int(H * 0.34), start=132, minimum=64)
    lh = _line_h(d, hf, 1.02)
    block_h = lh * len(lines)
    bottom = H - 150
    top = bottom - block_h
    if kicker:
        _spaced(d, (M, top - 52), kicker.upper(), _font(_ARCHIVO, 28), pal["accent"], 8)
    y = top
    for ln in lines:
        d.text((M, y), ln, font=hf, fill=pal["ink"])
        y += lh
    _handle_footer(d, handle, pal, H - 92, M)
    return _png(base)


# ── 2. editorial split: photo in a full-width band, headline on a colour band ──
def editorial_split(photo: bytes, headline: str, kicker: str = "", handle: str = "",
                    palette: dict | None = None, focus=(0.5, 0.42)) -> bytes:
    pal = _pal(palette)
    photo_h = int(H * 0.58)
    base = Image.new("RGB", (W, H), pal["bg"])
    base.paste(_cover_safe(_open_rgb(photo), W, photo_h, centering=focus), (0, 0))
    d = ImageDraw.Draw(base)
    M = 88
    y0 = photo_h + 60
    if kicker:
        _spaced(d, (M, y0), kicker.upper(), _font(_ARCHIVO, 28), pal["accent"], 8)
        y0 += 52
    hf, lines = _fit(d, headline.upper(), _ANTON, W - 2 * M, H - y0 - 120, start=104, minimum=52)
    lh = _line_h(d, hf, 1.03)
    for ln in lines:
        d.text((M, y0), ln, font=hf, fill=pal["ink"])
        y0 += lh
    _handle_footer(d, handle, pal, H - 92, M)
    return _png(base)


# ── 3. big stat: a single huge number/claim — text-forward, no photo needed ──
def big_stat(stat: str, label: str, sub: str = "", handle: str = "",
             palette: dict | None = None) -> bytes:
    pal = _pal(palette)
    base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    M = 88
    # accent rule top-left
    d.rectangle((M, 150, M + 120, 160), fill=pal["accent"])
    sf, sl = _fit(d, stat.upper(), _ANTON, W - 2 * M, int(H * 0.42), start=430, minimum=120)
    sh = _line_h(d, sf, 0.98) * len(sl)
    y = 230
    for ln in sl:
        d.text((M, y), ln, font=sf, fill=pal["accent"])
        y += _line_h(d, sf, 0.98)
    y += 24
    lf, ll = _fit(d, label.upper(), _ANTON, W - 2 * M, 260, start=76, minimum=40)
    for ln in ll:
        d.text((M, y), ln, font=lf, fill=pal["ink"])
        y += _line_h(d, lf, 1.06)
    if sub:
        y += 18
        bf = _font(_ARCHIVO, 30)
        for ln in _wrap_words(d, sub, bf, W - 2 * M):
            d.text((M, y), ln, font=bf, fill=_muted(pal["ink"], pal["bg"]))
            y += 44
    _handle_footer(d, handle, pal, H - 110, M)
    return _png(base)


# ── 4. minimal type-over: full photo, small elegant type, lots of air ──
def minimal_over(photo: bytes, line: str, kicker: str = "", handle: str = "",
                 palette: dict | None = None, focus=(0.5, 0.4)) -> bytes:
    pal = _pal(palette)
    base = _cover_safe(_open_rgb(photo), W, H, centering=focus)
    base = _scrim(base, frac=0.42, strength=150)
    base = _scrim(base, frac=0.30, strength=110, top=True)
    d = ImageDraw.Draw(base)
    if kicker:
        _spaced_c(d, 150, kicker.upper(), _font(_ARCHIVO, 26), pal["ink"], 12)
    lf, ll = _fit(d, line.upper(), _ANTON, int(W * 0.82), 300, start=92, minimum=48)
    lh = _line_h(d, lf, 1.05)
    y = H - 240 - lh * len(ll)
    _draw_centered(d, ll, lf, W // 2, y, fill=pal["ink"])
    if handle:
        _spaced_c(d, H - 130, (handle if handle.startswith("@") else "@" + handle),
                  _font(_ARCHIVO, 26), pal["accent"], 6)
    return _png(base)


# ── 5. framed print: photo matted on a colour field, caption below ──
def framed_print(photo: bytes, caption: str, kicker: str = "", handle: str = "",
                 palette: dict | None = None) -> bytes:
    pal = _pal(palette)
    base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    M = 96
    pw = W - 2 * M
    ph = int(pw * 1.0)  # square-ish frame
    top = 150
    photo_im = _cover_safe(_open_rgb(photo), pw, ph, centering=(0.5, 0.42))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw, ph), radius=26, fill=255)
    base.paste(photo_im, (M, top), mask)
    y = top + ph + 46
    if kicker:
        _spaced(d, (M, y), kicker.upper(), _font(_ARCHIVO, 26), pal["accent"], 8)
        y += 46
    cf, cl = _fit(d, caption.upper(), _ANTON, pw, 240, start=72, minimum=40)
    for ln in cl:
        d.text((M, y), ln, font=cf, fill=pal["ink"])
        y += _line_h(d, cf, 1.05)
    _handle_footer(d, handle, pal, H - 96, M)
    return _png(base)


def _wrap_words(d, text, font, maxw):
    out, line = [], ""
    for w in str(text).split():
        t = (line + " " + w).strip()
        if _text_w(d, t, font) > maxw and line:
            out.append(line); line = w
        else:
            line = t
    if line:
        out.append(line)
    return out


__all__ = ["full_bleed", "editorial_split", "big_stat", "minimal_over", "framed_print"]
