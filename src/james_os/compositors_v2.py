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

from .image_compose import (_ARCHIVO, _case, _cover_safe, _draw_centered, _fit, _font,
                            _forced_ink, _line_h, _open_rgb, _png, _spaced, _spaced_fit,
                            _spaced_w, _text, _text_w, _wrap)

_ANTON = os.path.join(os.path.dirname(__file__), "assets", "fonts", "Anton-Regular.ttf")
W, H = 1080, 1350


def _rgb(s: str, fb=(10, 14, 23)) -> tuple:
    s = (s or "").strip().lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, IndexError):
        return fb


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _pal(p: dict | None) -> dict:
    """Brand palette with a DERIVED ramp: a brand need only give bg/ink/accent —
    surface (a raised bg) and muted (a quiet ink) are derived when absent, so
    every compositor gets depth from ONE small palette instead of ad-hoc greys.
    Also accepts a role-list palette (exactly brand_identity's shape) — whether
    passed bare ([{role,hex},...], as carousel() does) or wrapped ({'palette':[...]})."""
    if isinstance(p, list):                       # bare role-list → wrap it
        p = {"palette": p}
    p = dict(p or {})
    if isinstance(p.get("palette"), list):
        roles = {r.get("role"): r.get("hex") for r in p["palette"] if isinstance(r, dict)}
        for k, role in (("bg", "background"), ("ink", "ink"), ("accent", "accent"), ("surface", "surface")):
            p.setdefault(k, roles.get(role))
    bg = _rgb(p.get("bg"), (10, 14, 23))
    ink = _rgb(p.get("ink"), (255, 255, 255))
    # A forced text colour (render knob) overrides the ink BEFORE the scrim and
    # derived tones are computed off it — so _auto_scrim darkens the band for the
    # forced colour and the text stays readable (e.g. forced white gets a dark
    # scrim under it instead of white-on-bright).
    forced = _forced_ink()
    if forced is not None:
        ink = forced
    accent = _rgb(p.get("accent"), (255, 106, 44))
    surface = _rgb(p.get("surface"), _mix(bg, ink, 0.10))
    muted = _rgb(p.get("muted"), _mix(ink, bg, 0.42))
    return {"bg": bg, "ink": ink, "accent": accent, "surface": surface, "muted": muted}


def _rel_lum(c: tuple) -> float:
    r, g, b = (x / 255 for x in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _region_lum(img: Image.Image, box) -> float:
    """Mean relative luminance (0..1) of a region — cheap, on a 24px thumbnail."""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    crop = img.crop((x0, y0, x1, y1)).convert("RGB").resize((24, 24))
    px = list(crop.getdata())
    return sum(_rel_lum(p) for p in px) / len(px)


def _contrast(a: tuple, b: tuple) -> float:
    la, lb = _rel_lum(a) + 0.05, _rel_lum(b) + 0.05
    return max(la, lb) / min(la, lb)


def _ink_for(ground_lum: float, ink: tuple) -> tuple:
    """Keep the brand ink if it reads on the (post-scrim) ground; else flip to a
    legible extreme. Guarantees text is never lost on a bright photo region."""
    # A forced text colour (render knob) wins outright — the owner asked for this
    # exact colour, and the paired scrim (built off the same forced ink) keeps it
    # readable, so it must NOT be auto-flipped back on a bright region.
    forced = _forced_ink()
    if forced is not None:
        return forced
    g = (int(ground_lum * 255),) * 3
    if _contrast(ink, g) >= 3.0:
        return ink
    return (245, 246, 250) if ground_lum < 0.5 else (14, 16, 22)


def _clip(text: str, max_words: int) -> str:
    """Copy budget: on-image lines must be short. Trim runaway copy so _fit()
    never has to shrink a headline to nothing to make it fit."""
    w = str(text).split()
    return " ".join(w[:max_words]) if len(w) > max_words else str(text)


def _spaced_c(d: ImageDraw.ImageDraw, y: int, text: str, font, fill, tr: int, m: int = 88):
    """Letter-spaced text centred on the 1080px canvas but kept INSIDE the m-px
    side margins: tighten the tracking until the spaced run fits, then clamp the
    start to the margin so a long kicker never slices its leading letter off the
    frame. Byte-identical for runs that already fit."""
    avail = W - 2 * m
    while tr > 0 and _spaced_w(d, text, font, tr) > avail:
        tr -= 1
    x = max(m, (W - _spaced_w(d, text, font, tr)) // 2)
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


def _auto_scrim(img: Image.Image, frac: float, ink: tuple, top: bool = False) -> Image.Image:
    """Darken the text band ONLY as much as the photo under it needs to give
    `ink` real contrast — a bright sky gets a heavy scrim, an already-dark region
    barely any. Replaces the old fixed strength so text never washes out."""
    band = (0, 0, W, int(H * frac)) if top else (0, int(H * (1 - frac)), W, H)
    lum = _region_lum(img, band)
    if _rel_lum(ink) > 0.5:                       # light ink → ground must be dark
        strength = int(max(40, min(240, 255 * (1 - 0.20 / max(lum, 0.06)))))
    else:                                         # dark ink → ground must be light
        strength = int(max(40, min(215, 255 * lum)))
    return _scrim(img, frac=frac, strength=strength, top=top)


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
    headline, kicker = _clip(headline, 9), _clip(kicker, 4)
    base = _cover_safe(_open_rgb(photo), W, H, centering=focus)
    base = _auto_scrim(base, 0.55, pal["ink"])
    d = ImageDraw.Draw(base)
    M = 88
    hf, lines = _fit(d, _case(headline, "upper"), _ANTON, W - 2 * M, int(H * 0.34), start=132, minimum=68)
    lh = _line_h(d, hf, 1.02)
    block_h = lh * len(lines)
    bottom = H - 150
    top = bottom - block_h
    # after the adaptive scrim, confirm the ink still reads over the exact block
    ink = _ink_for(_region_lum(base, (M, top, W - M, int(bottom))), pal["ink"])
    if kicker:
        _spaced_fit(d, top - 52, kicker.upper(), _ARCHIVO, 28, pal["accent"], 8, W - 2 * M, left=M)
    y = top
    for ln in lines:
        _text(d, (M, y), ln, hf, ink)
        y += lh
    _handle_footer(d, handle, pal, H - 92, M)
    return _png(base)


# ── 2. editorial split: photo in a full-width band, headline on a colour band ──
def editorial_split(photo: bytes, headline: str, kicker: str = "", handle: str = "",
                    palette: dict | None = None, focus=(0.5, 0.42)) -> bytes:
    pal = _pal(palette)
    headline, kicker = _clip(headline, 9), _clip(kicker, 4)
    photo_h = int(H * 0.58)
    base = Image.new("RGB", (W, H), pal["bg"])
    base.paste(_cover_safe(_open_rgb(photo), W, photo_h, centering=focus), (0, 0))
    d = ImageDraw.Draw(base)
    M = 88
    y0 = photo_h + 60
    if kicker:
        _spaced_fit(d, y0, kicker.upper(), _ARCHIVO, 28, pal["accent"], 8, W - 2 * M, left=M)
        y0 += 52
    hf, lines = _fit(d, _case(headline, "upper"), _ANTON, W - 2 * M, H - y0 - 120, start=104, minimum=52)
    lh = _line_h(d, hf, 1.03)
    for ln in lines:
        _text(d, (M, y0), ln, hf, pal["ink"])
        y0 += lh
    _handle_footer(d, handle, pal, H - 92, M)
    return _png(base)


# ── 3. big stat: a single huge number/claim — text-forward, no photo needed ──
def big_stat(stat: str, label: str, sub: str = "", handle: str = "",
             palette: dict | None = None) -> bytes:
    pal = _pal(palette)
    stat, label, sub = _clip(stat, 3), _clip(label, 6), _clip(sub, 16)
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
            d.text((M, y), ln, font=bf, fill=pal["muted"])
            y += 44
    _handle_footer(d, handle, pal, H - 110, M)
    return _png(base)


# ── 4. minimal type-over: full photo, small elegant type, lots of air ──
def minimal_over(photo: bytes, line: str, kicker: str = "", handle: str = "",
                 palette: dict | None = None, focus=(0.5, 0.4)) -> bytes:
    pal = _pal(palette)
    line, kicker = _clip(line, 7), _clip(kicker, 4)
    base = _cover_safe(_open_rgb(photo), W, H, centering=focus)
    base = _auto_scrim(base, 0.45, pal["ink"])
    base = _scrim(base, frac=0.28, strength=120, top=True)
    d = ImageDraw.Draw(base)
    if kicker:
        _spaced_c(d, 150, kicker.upper(), _font(_ARCHIVO, 26), pal["ink"], 12)
    lf, ll = _fit(d, _case(line, "upper"), _ANTON, int(W * 0.82), 300, start=92, minimum=56)
    lh = _line_h(d, lf, 1.05)
    y = H - 240 - lh * len(ll)
    ink = _ink_for(_region_lum(base, (int(W * 0.09), int(y), int(W * 0.91), int(y + lh * len(ll)))), pal["ink"])
    _draw_centered(d, ll, lf, W // 2, y, fill=ink)
    if handle:
        _spaced_c(d, H - 130, (handle if handle.startswith("@") else "@" + handle),
                  _font(_ARCHIVO, 26), pal["accent"], 6)
    return _png(base)


# ── 5. framed print: photo matted on a colour field, caption below ──
def framed_print(photo: bytes, caption: str, kicker: str = "", handle: str = "",
                 palette: dict | None = None) -> bytes:
    pal = _pal(palette)
    caption, kicker = _clip(caption, 7), _clip(kicker, 4)
    base = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(base)
    M = 96
    pw = W - 2 * M
    ph = int(pw * 0.82)          # leave real room for the caption below
    top = 132
    photo_im = _cover_safe(_open_rgb(photo), pw, ph, centering=(0.5, 0.42))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw, ph), radius=26, fill=255)
    base.paste(photo_im, (M, top), mask)
    y = top + ph + 44
    if kicker:
        _spaced_fit(d, y, kicker.upper(), _ARCHIVO, 26, pal["accent"], 8, W - 2 * M, left=M)
        y += 46
    # caption fits the zone between here and the handle footer — never overlaps
    zone = (H - 150) - y
    cf, cl = _fit(d, _case(caption, "upper"), _ANTON, pw, max(120, zone), start=74, minimum=40)
    for ln in cl:
        d.text((M, y), ln, font=cf, fill=pal["ink"])
        y += _line_h(d, cf, 1.06)
    _handle_footer(d, handle, pal, H - 96, M)
    return _png(base)


def _wrap_words(d, text, font, maxw):
    # Delegate to the shared word-wrap, which hard-breaks any single over-wide
    # token so a line can never be laid out past the column (the old inline
    # version kept an over-wide first word whole and let it run off the frame).
    return _wrap(d, str(text), font, maxw)


__all__ = ["full_bleed", "editorial_split", "big_stat", "minimal_over", "framed_print"]
