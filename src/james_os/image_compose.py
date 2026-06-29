"""Multi-format designed-image compositor (Pillow).

The AI generates a TEXT-FREE background/scene; this module overlays crisp,
perfectly-spelled text + branding on top — letting the AI bake text garbles
spelling ("Staten IIswr"), so we never do that. Phase-1 formats:

  * quote_card — full-bleed scene + dark scrim + big centered quote + @handle
  * meme_card  — white canvas, bold top text + image panel + bold bottom text

Everything renders at 1080×1350 (the 4:5 Instagram feed ratio) and returns
PNG bytes. Fonts are bundled OFL faces (Archivo Black, Anton) in assets/fonts.
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_ARCHIVO = os.path.join(_FONT_DIR, "ArchivoBlack-Regular.ttf")
_ANTON = os.path.join(_FONT_DIR, "Anton-Regular.ttf")

W, H = 1080, 1350  # 4:5 Instagram feed

_QUOTE_FILL = (250, 243, 224)   # warm cream — reads as premium on a dark scene
_INK = (17, 17, 17)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _open_rgb(b: bytes) -> Image.Image:
    return Image.open(BytesIO(b)).convert("RGB")


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _line_h(draw: ImageDraw.ImageDraw, font, spacing: float = 1.18) -> float:
    return draw.textbbox((0, 0), "Ag", font=font)[3] * spacing


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in (text or "").split():
        trial = (cur + " " + word).strip()
        if not cur or _text_w(draw, trial, font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit(draw, text: str, font_path: str, max_w: int, max_h: int,
         start: int, minimum: int = 30) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size at which the wrapped text fits within max_w × max_h."""
    size = start
    while size >= minimum:
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, max_w)
        widest = max((_text_w(draw, ln, font) for ln in lines), default=0)
        if widest <= max_w and _line_h(draw, font) * len(lines) <= max_h:
            return font, lines
        size -= 4
    font = _font(font_path, minimum)
    return font, _wrap(draw, text, font, max_w)


def _draw_centered(draw, lines, font, cx: int, top: float, fill,
                   stroke_fill=None, stroke_w: int = 0) -> float:
    lh = _line_h(draw, font)
    y = top
    for ln in lines:
        x = cx - _text_w(draw, ln, font) / 2
        if stroke_w:
            draw.text((x, y), ln, font=font, fill=fill,
                      stroke_width=stroke_w, stroke_fill=stroke_fill)
        else:
            draw.text((x, y), ln, font=font, fill=fill)
        y += lh
    return y


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    # Bias the crop slightly upward so a subject's head/upper-third is kept.
    return ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.4))


def _circle(b: bytes, d: int) -> Image.Image:
    img = ImageOps.fit(_open_rgb(b), (d, d), method=Image.LANCZOS, centering=(0.5, 0.35))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d, d), fill=255)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _logo_badge(b: bytes, d: int) -> Image.Image:
    """The brand logo FILLING the circular profile slot. The logo is composited
    onto a dark disc first (so a transparent PNG still has a solid backing),
    then cover-fit to fill the whole circle edge-to-edge — the emblem reads
    full-size, not a small mark floating in padding."""
    src = Image.open(BytesIO(b)).convert("RGBA")
    backing = Image.new("RGBA", src.size, (18, 20, 28, 255))
    flat = Image.alpha_composite(backing, src)
    filled = ImageOps.fit(flat, (d, d), method=Image.LANCZOS, centering=(0.5, 0.5))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d, d), fill=255)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(filled.convert("RGBA"), (0, 0), mask)
    return out


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _brand_footer(img: Image.Image, handle: str, profile_bytes: bytes | None,
                  profile_is_logo: bool = False) -> None:
    """Profile circle (or brand logo badge) + @handle centered near the bottom."""
    draw = ImageDraw.Draw(img)
    cx = W // 2
    base_y = H - 150
    if profile_bytes:
        d = 88
        circ = _logo_badge(profile_bytes, d) if profile_is_logo else _circle(profile_bytes, d)
        img.paste(circ, (cx - d // 2, base_y - d - 6), circ)
    if handle:
        h = handle if handle.startswith("@") else "@" + handle
        hf = _font(_ARCHIVO, 30)
        draw.text((cx - _text_w(draw, h, hf) / 2, base_y + 8), h,
                  font=hf, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))


def quote_card(bg_bytes: bytes, quote: str, handle: str = "",
               profile_bytes: bytes | None = None,
               profile_is_logo: bool = False) -> bytes:
    """Full-bleed scene + dark scrim + big centered quote + @handle footer."""
    base = _cover(_open_rgb(bg_bytes), W, H).convert("RGBA")
    scrim = Image.new("RGBA", (W, H), (8, 10, 20, 145))
    base = Image.alpha_composite(base, scrim).convert("RGB")
    draw = ImageDraw.Draw(base)
    q = (quote or "").strip().strip('"').strip("“”")
    font, lines = _fit(draw, q, _ARCHIVO, int(W * 0.82), int(H * 0.56), start=118, minimum=46)
    total_h = _line_h(draw, font) * len(lines)
    top = (H - total_h) / 2 - 40
    _draw_centered(draw, lines, font, W // 2, top, fill=_QUOTE_FILL,
                   stroke_fill=(0, 0, 0), stroke_w=3)
    _brand_footer(base, handle, profile_bytes, profile_is_logo)
    return _png(base)


def meme_card(bg_bytes: bytes, top_text: str, bottom_text: str, handle: str = "") -> bytes:
    """White canvas: bold top line + image panel + bold bottom punchline."""
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    pad = 56

    tf, tl = _fit(draw, (top_text or "").upper(), _ANTON, W - 2 * pad, 280, start=96, minimum=40)
    ty = _draw_centered(draw, tl, tf, W // 2, 42, fill=_INK)

    bf, bl = _fit(draw, (bottom_text or "").upper(), _ANTON, W - 2 * pad, 280, start=96, minimum=40)
    b_total = _line_h(draw, bf) * len(bl)
    by = H - b_total - 52

    img_top = int(ty + 26)
    img_bottom = int(by - 26)
    if img_bottom - img_top > 120:
        panel = ImageOps.fit(_open_rgb(bg_bytes), (W - 2 * pad, img_bottom - img_top),
                             method=Image.LANCZOS, centering=(0.5, 0.4))
        canvas.paste(panel, (pad, img_top))

    _draw_centered(draw, bl, bf, W // 2, by, fill=_INK)
    if handle:
        h = handle if handle.startswith("@") else "@" + handle
        hf = _font(_ARCHIVO, 24)
        draw.text((W - pad - _text_w(draw, h, hf), H - 40), h, font=hf, fill=(120, 120, 120))
    return _png(canvas)


def statement_card(bg_bytes: bytes, statement: str, handle: str = "",
                   profile_bytes: bytes | None = None,
                   profile_is_logo: bool = False) -> bytes:
    """Brad-Lea style, art-directed: an IG-post header (profile + @handle), a
    bold black STATEMENT optically centered in its own zone, and James framed
    (inset + rounded corners) at the bottom so the whole card breathes."""
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    M = 78                       # generous outer margin (~7%) — room to breathe
    content_w = W - 2 * M

    # ── IG-post header: profile circle (or brand logo badge) + @handle ──
    y = M
    if profile_bytes:
        d = 82
        circ = _logo_badge(profile_bytes, d) if profile_is_logo else _circle(profile_bytes, d)
        canvas.paste(circ, (M, y), circ)
        if handle:
            h = handle if handle.startswith("@") else "@" + handle
            hf = _font(_ARCHIVO, 30)
            draw.text((M + d + 22, y + (d - 30) // 2 - 4), h, font=hf, fill=_INK)
        header_bottom = y + d
    elif handle:
        h = handle if handle.startswith("@") else "@" + handle
        hf = _font(_ARCHIVO, 30)
        draw.text((M, y), h, font=hf, fill=_INK)
        header_bottom = y + 42
    else:
        header_bottom = y

    # ── James, framed at the bottom: inset by the margin, rounded corners,
    # face-biased crop — so there's clean space on every outside edge. ──
    img_h = 624
    img_top = H - M - img_h
    photo = ImageOps.fit(_open_rgb(bg_bytes), (content_w, img_h),
                         method=Image.LANCZOS, centering=(0.5, 0.22))
    rmask = Image.new("L", (content_w, img_h), 0)
    ImageDraw.Draw(rmask).rounded_rectangle((0, 0, content_w, img_h), radius=34, fill=255)
    canvas.paste(photo, (M, img_top), rmask)

    # ── Bold statement, optically centered in the zone between header & photo ──
    zone_top = header_bottom + 26
    zone_bottom = img_top - 30
    sf, sl = _fit(draw, (statement or "").upper(), _ARCHIVO, content_w,
                  max(140, zone_bottom - zone_top), start=104, minimum=40)
    total_h = _line_h(draw, sf) * len(sl)
    sy = zone_top + max(0.0, (zone_bottom - zone_top - total_h) / 2.0)
    _draw_centered(draw, sl, sf, W // 2, sy, fill=_INK)
    return _png(canvas)


__all__ = ["quote_card", "meme_card", "statement_card", "W", "H"]
