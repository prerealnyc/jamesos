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

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_ARCHIVO = os.path.join(_FONT_DIR, "ArchivoBlack-Regular.ttf")
_ANTON = os.path.join(_FONT_DIR, "Anton-Regular.ttf")

W, H = 1080, 1350  # 4:5 Instagram feed

_QUOTE_FILL = (250, 243, 224)   # warm cream — reads as premium on a dark scene
_INK = (17, 17, 17)

# ── PreReal / James Prendamano brand palette (the navy + blue system) ──
_BRAND_BLUE = (46, 128, 228)    # accent — highlighted words, kicker, name
_BRAND_WHITE = (245, 248, 252)
_BRAND_MUTED = (99, 129, 170)   # kickers / handles / footer
_NAVY_BASE = (7, 11, 20)        # deep navy background
_NAVY_GLOW = (28, 62, 116)      # soft blue glow behind the subject/text


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
    """Largest font size at which the wrapped text fits within max_w × max_h.

    HARD GUARANTEE: even at the minimum size, the returned lines never
    exceed max_h — overflow lines are dropped and the last kept line ends
    on an ellipsis (human rejection: "text on image cuts off")."""
    size = start
    while size >= minimum:
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, max_w)
        widest = max((_text_w(draw, ln, font) for ln in lines), default=0)
        if widest <= max_w and _line_h(draw, font) * len(lines) <= max_h:
            return font, lines
        size -= 4
    font = _font(font_path, minimum)
    lines = _wrap(draw, text, font, max_w)
    keep = max(1, int(max_h // max(1, _line_h(draw, font))))
    if len(lines) > keep:
        lines = lines[:keep]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return font, lines


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


def _cover_safe(img: Image.Image, w: int, h: int,
                centering: tuple[float, float]) -> Image.Image:
    """Cover-crop — but when the crop would discard a LARGE share of the
    photo (extreme aspect mismatch is exactly how James's head/body gets
    awkwardly cut off), fall back to contain-on-a-blurred-fill so the
    subject is always fully visible. (Human rejection: "image of james
    gets cut off. in this picture it looks awkward.")"""
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=centering)
    scale = max(w / iw, h / ih)
    discard_w = max(0.0, 1.0 - w / (iw * scale))
    discard_h = max(0.0, 1.0 - h / (ih * scale))
    # Axis-aware, genuinely-extreme-only gate: VERTICAL discard is what cuts
    # off a head/body; horizontal discard of a centered subject is normally
    # fine. Ordinary portrait photos must keep the designed cover crop.
    if discard_h <= 0.58 and discard_w <= 0.80:
        return ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=centering)
    bg = (ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=centering)
          .filter(ImageFilter.GaussianBlur(26)))
    fg = img.copy()
    fg.thumbnail((w, h), Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


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
    photo = _cover_safe(_open_rgb(bg_bytes), content_w, img_h,
                        centering=(0.5, 0.22))
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


# ── branded navy templates (James Prendamano look) ───────────────────

def _navy_bg(glow_xy: tuple[float, float] = (0.5, 0.42), radius: int = 540,
             glow_color: tuple[int, int, int] = _NAVY_GLOW) -> Image.Image:
    """Deep-navy canvas with one soft blue glow — the brand background."""
    base = Image.new("RGBA", (W, H), (*_NAVY_BASE, 255))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gx, gy = int(W * glow_xy[0]), int(H * glow_xy[1])
    ImageDraw.Draw(layer).ellipse(
        (gx - radius, gy - radius, gx + radius, gy + radius),
        fill=(*glow_color, 205),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(190))
    return Image.alpha_composite(base, layer).convert("RGB")


def _spaced_w(draw, text: str, font, tracking: int) -> int:
    return sum(_text_w(draw, ch, font) + tracking for ch in text) - tracking if text else 0


def _spaced(draw, xy, text: str, font, fill, tracking: int) -> None:
    """Draw letter-spaced text (PIL has no native tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += _text_w(draw, ch, font) + tracking


def _stack_lines(text: str, n: int = 3) -> list[str]:
    """Split a short quote into up to `n` visually balanced UPPERCASE lines."""
    words = [w for w in (text or "").strip().strip('"').strip("“”").split() if w]
    if not words:
        return [""]
    n = min(n, len(words))
    if n <= 1:
        return [" ".join(words).upper()]
    total = sum(len(w) for w in words) + len(words) - 1
    target = total / n
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and cur_len + add > target * 1.15 and len(lines) < n - 1:
            lines.append(" ".join(cur))
            cur, cur_len = [w], len(w)
        else:
            cur.append(w)
            cur_len += add
    if cur:
        lines.append(" ".join(cur))
    return [ln.upper() for ln in lines]


def _emph_index(lines: list[str], emphasis: str) -> int:
    """Which line to highlight in brand blue — the one holding the emphasis
    word, else the middle line (the visual anchor)."""
    e = (emphasis or "").strip().upper()
    if e:
        for i, ln in enumerate(lines):
            if e in ln:
                return i
    return len(lines) // 2


def _bare(w: str) -> str:
    return w.strip(".,!?;:\"'").upper()


def _lines_for(quote: str, emphasis: str, n: int = 3) -> tuple[list[str], int]:
    """Split a quote into UPPERCASE lines. When an emphasis phrase is present,
    ISOLATE it on its own line (the big blue anchor — the 'WE ARE / ALL / ONE'
    look); otherwise balance into <=n lines and highlight the middle."""
    words = [w for w in (quote or "").strip().strip('"').strip("“”").split() if w]
    if not words:
        return [""], 0
    eu = [_bare(w) for w in (emphasis or "").split() if w]
    if eu:
        bare = [_bare(w) for w in words]
        for i in range(len(words) - len(eu) + 1):
            if bare[i:i + len(eu)] == eu:
                before = " ".join(words[:i]).upper()
                mid = " ".join(words[i:i + len(eu)]).upper()
                after = " ".join(words[i + len(eu):]).upper()
                lines = [ln for ln in (before, mid, after) if ln]
                return lines, lines.index(mid)
    lines = _stack_lines(quote, n)
    return lines, len(lines) // 2


def brand_quote_card(quote: str, brand_kit: dict | None = None,
                     emphasis: str = "") -> bytes:
    """Text-only branded quote card: navy gradient, brand name + emblem up top,
    a big stacked quote with ONE line in brand blue, footer website + tagline.
    (The 'WE ARE / ALL / ONE' design.) Generates its own background."""
    bk = brand_kit or {}
    base = _navy_bg((0.5, 0.5), 560)
    draw = ImageDraw.Draw(base)
    cx = W // 2

    # ── brand name (letter-spaced kicker) ──
    name = (bk.get("display_name") or "James Prendamano").upper()
    nf = _font(_ARCHIVO, 34)
    nw = _spaced_w(draw, name, nf, 10)
    _spaced(draw, (cx - nw / 2, 92), name, nf, _BRAND_BLUE, 10)

    # ── ripple emblem ──
    ey, er = 262, 60
    for i, rr in enumerate((er, int(er * 0.62), int(er * 0.30))):
        col = _BRAND_BLUE if i != 1 else (70, 150, 240)
        draw.ellipse((cx - rr, ey - rr, cx + rr, ey + rr), outline=col, width=6)
    draw.ellipse((cx - 11, ey - 11, cx + 11, ey + 11), fill=_BRAND_BLUE)

    # ── stacked quote, one line in brand blue ──
    lines, emph = _lines_for(quote, emphasis, 3)
    zone_top, zone_bottom = ey + er + 70, H - 210
    base_font, _ = _fit(draw, max(lines, key=len), _ARCHIVO,
                        int(W * 0.80), 220, start=150, minimum=54)
    ef_sz = min(int(base_font.size * 1.34), 200)
    emph_font = _fit(draw, lines[emph], _ARCHIVO, int(W * 0.80), 240,
                     start=ef_sz, minimum=base_font.size)[0]
    heights = [_line_h(draw, emph_font if i == emph else base_font, 1.12)
               for i in range(len(lines))]
    y = zone_top + max(0.0, (zone_bottom - zone_top - sum(heights)) / 2.0)
    for i, ln in enumerate(lines):
        f = emph_font if i == emph else base_font
        fill = _BRAND_BLUE if i == emph else _BRAND_WHITE
        draw.text((cx - _text_w(draw, ln, f) / 2, y), ln, font=f, fill=fill)
        y += heights[i]

    # ── footer: website · tagline ──
    site = (bk.get("website") or "prendamanoacademy.com").strip()
    tag = (bk.get("footer_tagline") or "free forever").strip()
    foot = f"{site}   ·   {tag}"
    ff = _font(_ARCHIVO, 26)
    fw = _spaced_w(draw, foot, ff, 3)
    _spaced(draw, (cx - fw / 2, H - 118), foot, ff, _BRAND_MUTED, 3)
    return _png(base)


def hero_quote_card(quote: str, hero_bytes: bytes, brand_kit: dict | None = None,
                    kicker: str = "LOOK WITHIN", emphasis: str = "") -> bytes:
    """Hero photo on the RIGHT (edge faded softly into the navy) + quote in a
    fixed LEFT column. The text lives entirely inside that column with firm
    margins and a real gutter to the photo — it is auto-fit so even a long line
    stays inside the column and NEVER crosses onto the photo. Clean, professional,
    margined. (The 'YOU WEREN'T BORN TO PLAY SMALL' design.)"""
    bk = brand_kit or {}
    base = _navy_bg((0.66, 0.40), 430)

    M = 96                              # outer margin on every side
    pw = int(W * 0.46)                 # photo panel width (right side)
    photo_left = W - pw
    gutter = 48                         # guaranteed clear gap: text ↔ photo
    text_left = M
    text_w = max(300, photo_left - gutter - text_left)   # the text's hard column

    # ── hero photo on the right; its LEFT edge fades into navy so the seam is
    #    invisible and the whole text column stays on clean navy ──
    if hero_bytes:
        photo = _cover_safe(_open_rgb(hero_bytes), pw, H,
                            centering=(0.5, 0.26))
        grad = Image.new("L", (pw, 1), 0)
        for x in range(pw):
            # transparent across the left ~40% of the panel, then ramp to opaque
            grad.putpixel((x, 0), min(255, int(255 * max(0.0, (x / pw - 0.40) / 0.34))))
        base.paste(photo, (photo_left, 0), grad.resize((pw, H)))

    draw = ImageDraw.Draw(base)

    # ── quote: keep the semantic line split (emphasis isolated), auto-fit to
    #    the column, then HARD-GUARANTEE containment by re-wrapping if needed ──
    lines, emph = _lines_for(quote, emphasis, 3)
    qfont, _ = _fit(draw, max(lines, key=len), _ARCHIVO, text_w, int(H * 0.44),
                    start=86, minimum=34)
    if max((_text_w(draw, ln, qfont) for ln in lines), default=0) > text_w:
        qfont, lines = _fit(draw, " ".join(lines), _ARCHIVO, text_w,
                            int(H * 0.44), start=qfont.size, minimum=30)
        # re-locate the emphasis: first line containing any emphasis word
        ew = [_bare(w) for w in (emphasis or "").split() if w]
        emph = next((i for i, ln in enumerate(lines)
                     if any(w in ln for w in ew)), len(lines) // 2) if ew else len(lines) // 2

    lh = _line_h(draw, qfont, 1.16)
    block_h = lh * len(lines)
    kick_gap = 62
    top = max(M + 34, (H - (block_h + kick_gap)) / 2)

    # kicker + underline (aligned to the column)
    kf = _font(_ARCHIVO, 28)
    kw = _spaced_w(draw, kicker.upper(), kf, 8)
    _spaced(draw, (text_left, top), kicker.upper(), kf, _BRAND_BLUE, 8)
    draw.line((text_left, top + 44, text_left + min(kw, 160), top + 44),
              fill=_BRAND_BLUE, width=4)

    y = top + kick_gap
    for i, ln in enumerate(lines):
        draw.text((text_left, y), ln, font=qfont,
                  fill=(_BRAND_BLUE if i == emph else _BRAND_WHITE))
        y += lh

    # ── @handle bottom-left, aligned to the text column ──
    handle = (bk.get("handle") or "@j_prendamano").strip()
    if not handle.startswith("@"):
        handle = "@" + handle
    hf = _font(_ARCHIVO, 26)
    draw.text((text_left, H - M - 18), handle, font=hf, fill=_BRAND_MUTED)
    return _png(base)


__all__ = [
    "quote_card", "meme_card", "statement_card",
    "brand_quote_card", "hero_quote_card", "W", "H",
]
