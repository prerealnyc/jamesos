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

import contextlib
import contextvars
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_ARCHIVO = os.path.join(_FONT_DIR, "ArchivoBlack-Regular.ttf")
_ANTON = os.path.join(_FONT_DIR, "Anton-Regular.ttf")

# Per-brand typography themes. A theme is {"display": <ttf path>, "body": <ttf path>};
# the brand owner picks one and it flows in here per render (set via the brand_fonts()
# context manager at each render entry point). Every compositor funnels its face through
# _font(), so remapping the two house faces there — Anton (the display/headline face) ->
# theme display, Archivo Black (the body/default face) -> theme body — themes ALL static
# formats with almost no call-site churn. No active theme (the default "Bold" house look)
# => no remap => byte-identical to before. Resolution lives in services layer; here we
# only swap the loaded face.
_render_theme: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "render_theme", default=None)

# Role marker for a HEADLINE face. The shipped "navy" cards render their big statement
# in Archivo Black (not Anton), so mapping Archivo->body would wrongly push their
# headline to the body face. Tag those headlines with _DISPLAY instead: default (no
# theme) -> Archivo Black (current look preserved), themed -> the theme's display face.
_DISPLAY = "@@display"


@contextlib.contextmanager
def brand_fonts(theme: dict | None):
    """Scope a per-brand typography theme over a render. `theme` is
    {"display": path, "body": path} or None (default house faces)."""
    token = _render_theme.set(theme or None)
    try:
        yield
    finally:
        _render_theme.reset(token)


def _remap_face(path: str) -> str:
    """Map a house face path (or the _DISPLAY role marker) to the active brand theme's
    display/body face. No theme => headlines stay Archivo Black (house default)."""
    theme = _render_theme.get()
    if path == _DISPLAY:
        return (theme.get("display") if theme else None) or _ARCHIVO
    if not theme:
        return path
    if path == _ANTON:
        return theme.get("display") or path
    if path == _ARCHIVO:
        return theme.get("body") or path
    return path


# Per-brand "brand look" extras the owner picks (synced from Brand Manager): headline
# CASE, and the LOGO's visibility + corner. Threaded in per render via brand_look().
_render_look: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "render_look", default=None)


@contextlib.contextmanager
def brand_look(look: dict | None):
    """Scope per-brand look extras over a render:
    {"headline_case": upper|title|sentence, "logo_show": bool, "logo_position": str}."""
    token = _render_look.set(look or None)
    try:
        yield
    finally:
        _render_look.reset(token)


# On-image TEXT styling forced by the render knobs (image_text_color /
# image_text_weight), scoped over a single render like the look/fonts above.
# color: "" (auto) | "light" (force white) | "dark" (force black); bold: thicker.
# This is how a "make the text white / thicker" rejection is APPLIED instead of
# only logged — every text path reads it, so the fix needs no per-compositor code.
_render_text_style: "contextvars.ContextVar[dict]" = contextvars.ContextVar(
    "render_text_style", default={})


@contextlib.contextmanager
def text_style(color: str = "", bold: bool = False):
    token = _render_text_style.set({"color": color or "", "bold": bool(bold)})
    try:
        yield
    finally:
        _render_text_style.reset(token)


def _forced_ink() -> tuple | None:
    """The forced on-image text colour as RGB, or None when the auto-contrast
    picker should decide. Near-white / near-black rather than pure, so it never
    clips on a scrim."""
    c = _render_text_style.get().get("color")
    if c == "light":
        return (245, 246, 250)
    if c == "dark":
        return (14, 16, 22)
    return None


def _text_bold() -> bool:
    return bool(_render_text_style.get().get("bold"))


def _text(draw, xy, s, font, fill, **kw) -> None:
    """draw.text, but thickened when the bold text-style is active — a same-colour
    stroke around each glyph, so ANY face reads heavier without needing a bold
    font file. A no-op (plain draw.text) when bold is off, so output is unchanged."""
    if _text_bold() and "stroke_width" not in kw:
        sw = max(1, int(getattr(font, "size", 40) / 26))
        kw = {**kw, "stroke_width": sw, "stroke_fill": fill}
    draw.text(xy, s, font=font, fill=fill, **kw)


def _titlecase(t: str) -> str:
    return " ".join((w[:1].upper() + w[1:].lower()) if w else w for w in (t or "").split(" "))


def _case(text: str, default: str = "") -> str:
    """Apply the brand's chosen headline case. `default` is the call-site's own casing
    when the brand hasn't picked one, so the shipped look is byte-identical unless a
    case is set. Modes: upper | title | sentence (empty = leave text as given)."""
    mode = (_render_look.get() or {}).get("headline_case") or default
    t = text or ""
    if mode == "upper":
        return t.upper()
    if mode == "title":
        return _titlecase(t)
    if mode == "sentence":
        return (t[:1].upper() + t[1:].lower()) if t else t
    return t


def _logo_show() -> bool:
    look = _render_look.get()
    return True if look is None else bool(look.get("logo_show", True))


def _logo_position() -> str:
    return str((_render_look.get() or {}).get("logo_position") or "footer")


def _paste_corner(img: Image.Image, badge: Image.Image, position: str, margin: int = 64) -> None:
    d = badge.size[0]
    xy = {
        "top_left": (margin, margin),
        "top_right": (W - d - margin, margin),
        "bottom_left": (margin, H - d - margin),
        "bottom_right": (W - d - margin, H - d - margin),
    }.get(position)
    if xy:
        img.paste(badge, xy, badge)

W, H = 1080, 1350  # 4:5 Instagram feed

_QUOTE_FILL = (250, 243, 224)   # warm cream — reads as premium on a dark scene
_INK = (17, 17, 17)

# ── PreReal / James Prendamano brand palette (the navy + blue system) ──
_BRAND_BLUE = (46, 128, 228)    # accent — highlighted words, kicker, name
_BRAND_WHITE = (245, 248, 252)
_BRAND_MUTED = (99, 129, 170)   # kickers / handles / footer
_NAVY_BASE = (7, 11, 20)        # deep navy background
_NAVY_GLOW = (28, 62, 116)      # soft blue glow behind the subject/text


def _hex(s, fb):
    s = (s or "").strip().lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, IndexError, TypeError):
        return fb


def _lighten(c, t=0.18):
    return tuple(int(c[i] + (255 - c[i]) * t) for i in range(3))


def _colors(brand_kit: dict | None) -> dict:
    """Per-brand palette for the navy templates. With NO colours in brand_kit
    every value is the EXACT James literal, so the render is byte-identical to
    before this existed. A brand supplies colours via brand_kit['colors']
    {bg,ink,accent,glow,muted} OR a role-list brand_kit['palette'] (exactly the
    shape brand_identity.assess_and_propose emits) — so a proposed theme flows
    straight into generation with no translation."""
    bk = brand_kit or {}
    c = dict(bk.get("colors") or {})
    if not c and isinstance(bk.get("palette"), list):
        roles = {p.get("role"): p.get("hex") for p in bk["palette"] if isinstance(p, dict)}
        c = {"bg": roles.get("background"), "ink": roles.get("ink"),
             "accent": roles.get("accent"), "glow": roles.get("surface")}
    accent = _hex(c.get("accent"), _BRAND_BLUE)
    pal = {
        "base": _hex(c.get("bg"), _NAVY_BASE),
        "glow": _hex(c.get("glow"), _NAVY_GLOW),
        "accent": accent,
        # James's emblem mid-ring is a specific tint; keep it exact when no brand
        # accent is set, else derive a lighter accent.
        "accent_light": _lighten(accent) if c.get("accent") else (70, 150, 240),
        "ink": _hex(c.get("ink"), _BRAND_WHITE),
        "muted": _hex(c.get("muted"), _BRAND_MUTED),
    }
    # A forced text colour (from the render knob) overrides the body ink — the
    # accent line stays branded, so "make the text white" whitens the body while
    # the emphasis phrase keeps its colour.
    forced = _forced_ink()
    if forced is not None:
        pal["ink"] = forced
    return pal


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_remap_face(path), size)


def _open_rgb(b: bytes) -> Image.Image:
    return Image.open(BytesIO(b)).convert("RGB")


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _line_h(draw: ImageDraw.ImageDraw, font, spacing: float = 1.18) -> float:
    return draw.textbbox((0, 0), "Ag", font=font)[3] * spacing


def _hard_break(draw, word: str, font, max_w: int) -> list[str]:
    """Last-resort split of ONE over-wide, unspaceable token (a URL, a long
    compound word, a giant number) into character chunks that each fit max_w — so
    a single word can NEVER be drawn past the frame. Returns [word] unchanged when
    it already fits, so ordinary copy is untouched."""
    if max_w <= 0 or _text_w(draw, word, font) <= max_w:
        return [word]
    parts: list[str] = []
    chunk = ""
    for ch in word:
        if chunk and _text_w(draw, chunk + ch, font) > max_w:
            parts.append(chunk)
            chunk = ch
        else:
            chunk += ch
    if chunk:
        parts.append(chunk)
    return parts or [word]


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Word-wrap so EVERY returned line fits max_w. A single token wider than the
    column is hard-broken at the character level — the containment invariant that
    keeps a long URL/hashtag/compound word from being drawn off the frame (the old
    `not cur` accept emitted such a token whole, which is how text left the canvas)."""
    lines: list[str] = []
    cur = ""
    for word in (text or "").split():
        trial = (cur + " " + word).strip()
        if cur and _text_w(draw, trial, font) <= max_w:
            cur = trial
            continue
        # `word` starts a fresh line — flush the current one, then guarantee the
        # word itself fits by hard-breaking it when it alone exceeds the column.
        if cur:
            lines.append(cur)
            cur = ""
        if _text_w(draw, word, font) > max_w:
            parts = _hard_break(draw, word, font, max_w)
            lines.extend(parts[:-1])
            cur = parts[-1]
        else:
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit(draw, text: str, font_path: str, max_w: int, max_h: int,
         start: int, minimum: int = 30) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size at which the wrapped text fits within max_w × max_h.

    HARD GUARANTEE (both axes): the returned lines never exceed max_w (via
    _wrap, which hard-breaks any single over-wide token) nor max_h (overflow
    lines are dropped, the last kept line ends on an ellipsis). So a caller can
    draw these lines left-aligned within the column, or centered, and text can
    never cross the frame — the "text on image cuts off" rejection is designed
    out, not merely made less likely."""
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


def _fit_one_line(draw, text: str, font_path: str, max_w: int, start: int,
                  floor: int = 20) -> ImageFont.FreeTypeFont:
    """Largest font at which `text` fits max_w on ONE line. For an unspaceable
    token — a big number / stat like "$100,000,000,000" that must never wrap,
    char-break, or ellipsize — if it still exceeds max_w at `floor`, shrink
    proportionally below the floor so the whole value stays on-frame at a smaller
    size rather than being clipped."""
    size = int(start)
    while size > floor:
        font = _font(font_path, size)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 4
    font = _font(font_path, floor)
    w = _text_w(draw, text, font)
    if w > max_w and w > 0:
        font = _font(font_path, max(6, int(floor * max_w / w)))
    return font


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
            _text(draw, (x, y), ln, font, fill)  # bold-aware when the weight knob is on
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
    if profile_bytes and _logo_show():
        d = 88
        circ = _logo_badge(profile_bytes, d) if profile_is_logo else _circle(profile_bytes, d)
        pos = _logo_position()
        if pos in ("top_left", "top_right", "bottom_left", "bottom_right"):
            _paste_corner(img, circ, pos)
        else:  # footer (default) — bottom-centre above the handle
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
    font, lines = _fit(draw, q, _DISPLAY, int(W * 0.82), int(H * 0.56), start=118, minimum=46)
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

    tf, tl = _fit(draw, _case(top_text or "", "upper"), _ANTON, W - 2 * pad, 280, start=96, minimum=40)
    ty = _draw_centered(draw, tl, tf, W // 2, 42, fill=_INK)

    bf, bl = _fit(draw, _case(bottom_text or "", "upper"), _ANTON, W - 2 * pad, 280, start=96, minimum=40)
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
    if profile_bytes and _logo_show():
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
    sf, sl = _fit(draw, _case(statement or "", "upper"), _DISPLAY, content_w,
                  max(140, zone_bottom - zone_top), start=104, minimum=40)
    total_h = _line_h(draw, sf) * len(sl)
    sy = zone_top + max(0.0, (zone_bottom - zone_top - total_h) / 2.0)
    _draw_centered(draw, sl, sf, W // 2, sy, fill=_INK)
    return _png(canvas)


# ── branded navy templates (James Prendamano look) ───────────────────

def _navy_bg(glow_xy: tuple[float, float] = (0.5, 0.42), radius: int = 540,
             glow_color: tuple[int, int, int] = _NAVY_GLOW,
             base_color: tuple[int, int, int] = _NAVY_BASE) -> Image.Image:
    """Deep-navy canvas with one soft blue glow — the brand background.
    `base_color`/`glow_color` default to James's navy, so an unstyled call is
    byte-identical; a brand palette recolours the whole ground."""
    base = Image.new("RGBA", (W, H), (*base_color, 255))
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


def _spaced_fit(draw, y, text: str, font_path: str, size: int, fill, tracking: int,
                max_w: int, *, left: float | None = None, center: float | None = None,
                floor: int = 16):
    """Draw letter-spaced text GUARANTEED to stay within max_w.

    Letter-spaced kickers / labels / brand names were drawn at a FIXED size with
    no width check, so a long one ran off the frame (measured with plain textbbox
    while _spaced adds `tracking` per glyph — the drawn run is wider than anything
    the fit ever saw). This shrinks the size, then the tracking, until the spaced
    run fits, then anchors it inside the frame. Give `left` (x of the left edge)
    or `center` (x to centre on). A run that already fits is untouched — same
    pixels as before — so only over-long copy is affected."""
    size = int(size)
    font = _font(font_path, size)
    while size > floor and _spaced_w(draw, text, font, tracking) > max_w:
        size -= 2
        font = _font(font_path, size)
    while tracking > 0 and _spaced_w(draw, text, font, tracking) > max_w:
        tracking -= 1
    tracking = max(0, tracking)
    w = _spaced_w(draw, text, font, tracking)
    x = (center - w / 2) if center is not None else (left if left is not None else 0)
    _spaced(draw, (max(0.0, x), y), text, font, fill, tracking)
    return font, tracking


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
    pal = _colors(bk)
    base = _navy_bg((0.5, 0.5), 560, glow_color=pal["glow"], base_color=pal["base"])
    draw = ImageDraw.Draw(base)
    cx = W // 2

    # ── brand name (letter-spaced kicker) — omit entirely when the brand has
    #    no name yet, rather than forging one from another brand ──
    name = (bk.get("display_name") or "").strip().upper()
    if name:
        _spaced_fit(draw, 92, name, _ARCHIVO, 34, pal["accent"], 10,
                    int(W * 0.86), center=cx)

    # ── ripple emblem ──
    ey, er = 262, 60
    for i, rr in enumerate((er, int(er * 0.62), int(er * 0.30))):
        ring = pal["accent"] if i != 1 else pal["accent_light"]
        draw.ellipse((cx - rr, ey - rr, cx + rr, ey + rr), outline=ring, width=6)
    draw.ellipse((cx - 11, ey - 11, cx + 11, ey + 11), fill=pal["accent"])

    # ── stacked quote, one line in brand blue ──
    lines, emph = _lines_for(quote, emphasis, 3)
    zone_top, zone_bottom = ey + er + 70, H - 210
    base_font, _ = _fit(draw, max(lines, key=len), _DISPLAY,
                        int(W * 0.80), 220, start=150, minimum=54)
    ef_sz = min(int(base_font.size * 1.34), 200)
    emph_font = _fit(draw, lines[emph], _DISPLAY, int(W * 0.80), 240,
                     start=ef_sz, minimum=base_font.size)[0]
    # Containment guard: _fit picks a SIZE by re-wrapping internally, but the
    # lines below are drawn UNWRAPPED — so a wide multi-word line could still be
    # drawn past int(W*0.80) (and off-canvas, centered → negative x). Shrink both
    # faces in lockstep until every ACTUAL drawn line fits, keeping emph >= base.
    _q_max_w = int(W * 0.80)
    def _q_overflow() -> int:
        return max((_text_w(draw, ln, emph_font if i == emph else base_font)
                    for i, ln in enumerate(lines)), default=0)
    while _q_overflow() > _q_max_w and base_font.size > 24:
        base_font = _font(_DISPLAY, base_font.size - 4)
        emph_font = _font(_DISPLAY, max(base_font.size, emph_font.size - 4))
    heights = [_line_h(draw, emph_font if i == emph else base_font, 1.12)
               for i in range(len(lines))]
    y = zone_top + max(0.0, (zone_bottom - zone_top - sum(heights)) / 2.0)
    for i, ln in enumerate(lines):
        f = emph_font if i == emph else base_font
        fill = pal["accent"] if i == emph else pal["ink"]
        _text(draw, (cx - _text_w(draw, ln, f) / 2, y), ln, f, fill)
        y += heights[i]

    # ── footer: website · tagline — only what the brand actually supplies;
    #    no hardcoded fallback, so a brand without a site/tagline shows neither ──
    site = (bk.get("website") or "").strip()
    tag = (bk.get("footer_tagline") or "").strip()
    foot = "   ·   ".join([p for p in (site, tag) if p])
    if foot:
        _spaced_fit(draw, H - 118, foot, _ARCHIVO, 26, pal["muted"], 3,
                    W - 2 * 72, center=cx)
    return _png(base)


def hero_quote_card(quote: str, hero_bytes: bytes, brand_kit: dict | None = None,
                    kicker: str = "LOOK WITHIN", emphasis: str = "",
                    tuning: dict | None = None) -> bytes:
    """Hero photo on the RIGHT (edge faded softly into the navy) + quote in a
    fixed LEFT column. The text lives entirely inside that column with firm
    margins and a real gutter to the photo — it is auto-fit so even a long line
    stays inside the column and NEVER crosses onto the photo. Clean, professional,
    margined. (The 'YOU WEREN'T BORN TO PLAY SMALL' design.)

    `tuning` is the tenant's live render knobs (render_tuning.KNOBS). The four
    numbers below used to be literals, which meant "he's too small" or "the text
    is sitting on his face" could only be answered by a deploy. They default to
    exactly those literals, so an absent or empty dict renders identically to
    before; a knob set from feedback takes effect on the next card."""
    bk = brand_kit or {}
    pal = _colors(bk)
    tn = tuning or {}

    def _knob(key: str, fallback: float) -> float:
        """Tolerate junk in the config slot — a bad value must never break a
        render, it just falls back to the shipped literal."""
        try:
            v = tn.get(key)
            return fallback if v is None else float(v)
        except (TypeError, ValueError):
            return fallback

    base = _navy_bg((0.66, 0.40), 430, glow_color=pal["glow"], base_color=pal["base"])

    M = 96                              # outer margin on every side
    pw = int(W * _knob("image_photo_width", 0.46))   # photo panel width (right)
    photo_left = W - pw
    gutter = int(_knob("image_text_gutter", 48))     # clear gap: text ↔ photo
    text_left = M
    text_w = max(300, photo_left - gutter - text_left)   # the text's hard column
    # Horizontal, because this panel is tall and narrow: a normal photo is
    # cropped left/right to cover it, so the X centering is the one that decides
    # what stays in frame. (The Y value below is only reached by a photo taller
    # than ~1:2.7, so knobbing it would have done nothing on real photos.)
    focus_x = _knob("image_photo_focus_x", 0.5)
    quote_max_pt = int(_knob("image_quote_max_pt", 86))
    # How much of the photo's inner edge dissolves into the navy. At 0.40 the
    # photo is only fully solid across its right ~26%; lower it and far more of
    # him stays crisp. Clamped so the seam never fully vanishes (a hard edge) or
    # eats the whole panel.
    fade = min(0.55, max(0.08, _knob("image_photo_fade", 0.40)))

    # ── hero photo on the right; its LEFT edge fades into navy so the seam is
    #    invisible and the whole text column stays on clean navy ──
    if hero_bytes:
        photo = _cover_safe(_open_rgb(hero_bytes), pw, H,
                            centering=(focus_x, 0.26))
        grad = Image.new("L", (pw, 1), 0)
        for x in range(pw):
            # transparent across the left `fade` of the panel, then ramp to
            # opaque over the next 0.34 — lowering `fade` keeps more of him crisp
            grad.putpixel((x, 0), min(255, int(255 * max(0.0, (x / pw - fade) / 0.34))))
        base.paste(photo, (photo_left, 0), grad.resize((pw, H)))

    draw = ImageDraw.Draw(base)

    # ── quote: keep the semantic line split (emphasis isolated), auto-fit to
    #    the column, then HARD-GUARANTEE containment by re-wrapping if needed ──
    lines, emph = _lines_for(quote, emphasis, 3)
    qfont, _ = _fit(draw, max(lines, key=len), _DISPLAY, text_w, int(H * 0.44),
                    start=quote_max_pt, minimum=34)
    if max((_text_w(draw, ln, qfont) for ln in lines), default=0) > text_w:
        qfont, lines = _fit(draw, " ".join(lines), _DISPLAY, text_w,
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
    _spaced(draw, (text_left, top), kicker.upper(), kf, pal["accent"], 8)
    draw.line((text_left, top + 44, text_left + min(kw, 160), top + 44),
              fill=pal["accent"], width=4)

    y = top + kick_gap
    for i, ln in enumerate(lines):
        draw.text((text_left, y), ln, font=qfont,
                  fill=(pal["accent"] if i == emph else pal["ink"]))
        y += lh

    # ── @handle bottom-left — drawn only when the brand has one; never a
    #    hardcoded default (that is how @j_prendamano leaked onto other brands) ──
    handle = (bk.get("handle") or "").strip()
    if handle:
        if not handle.startswith("@"):
            handle = "@" + handle
        hf = _font(_ARCHIVO, 26)
        draw.text((text_left, H - M - 18), handle, font=hf, fill=pal["muted"])
    return _png(base)


# ─────────────────────────── bold statement poster ───────────────────────────
# A text-only "statement poster": no photo — the brand name across the top, a big
# bold mixed-case statement with the key phrase highlighted INLINE in the brand
# accent, and a byline at the foot. The Content-Pack poster look.


def _emph_word_idx(words: list[str], emphasis: str) -> set[int]:
    """Indices of the words that make up the emphasis phrase (highlighted in the
    accent). Empty when there's no emphasis or it isn't found verbatim."""
    eu = [_bare(w) for w in (emphasis or "").split() if w]
    if not eu:
        return set()
    bare = [_bare(w) for w in words]
    for i in range(len(words) - len(eu) + 1):
        if bare[i:i + len(eu)] == eu:
            return set(range(i, i + len(eu)))
    return set()


def _wrap_idx(draw, words: list[str], font, max_w: int) -> list[list[tuple[str, int]]]:
    """Left-aligned word-wrap that keeps each word's ORIGINAL index, so the
    renderer can colour individual words (inline emphasis)."""
    space = _text_w(draw, " ", font)
    lines: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    cur_w = 0
    for gi, w in enumerate(words):
        # Hard-break a single token wider than the column so no line is ever laid
        # out past max_w; every piece keeps the word's index for emphasis colour.
        pieces = _hard_break(draw, w, font, max_w) if _text_w(draw, w, font) > max_w else [w]
        for pi, piece in enumerate(pieces):
            pw = _text_w(draw, piece, font)
            add = pw + (space if cur else 0)
            # a broken piece after the first always begins its own full-width line
            if cur and (pi > 0 or cur_w + add > max_w):
                lines.append(cur)
                cur, cur_w = [(piece, gi)], pw
            else:
                cur.append((piece, gi))
                cur_w += add
    if cur:
        lines.append(cur)
    return lines or [[]]


def _fit_left(draw, words: list[str], font_path: str, max_w: int, max_h: int,
              start: int, minimum: int) -> ImageFont.FreeTypeFont:
    """Largest font at which the left-wrapped statement fits max_w × max_h."""
    size = start
    while size >= minimum:
        font = _font(font_path, size)
        sp = _text_w(draw, " ", font)
        lines = _wrap_idx(draw, words, font, max_w)
        widest = max((sum(_text_w(draw, w, font) for w, _ in ln) + sp * (len(ln) - 1)
                      for ln in lines), default=0)
        if widest <= max_w and _line_h(draw, font, 1.14) * len(lines) <= max_h:
            return font
        size -= 4
    return _font(font_path, minimum)


def bold_statement_card(statement: str, brand_kit: dict | None = None,
                        emphasis: str = "", byline_name: str = "") -> bytes:
    """Text-only statement poster (no photo): a flat, near-black brand ground; the
    brand name letter-spaced across the top; a short accent rule; a big bold,
    mixed-case statement left-aligned with the emphasis phrase highlighted INLINE
    in the brand accent; and a byline (optional name + website · tagline) at the
    foot. Generates its own background from the brand palette — an unstyled brand
    renders on James's near-black navy."""
    bk = brand_kit or {}
    pal = _colors(bk)
    accent, ink, muted = pal["accent"], pal["ink"], pal["muted"]
    ground = tuple(int(c * 0.26) for c in pal["base"])  # push the base to a flat poster black
    base = Image.new("RGB", (W, H), ground)
    draw = ImageDraw.Draw(base)
    M = 96

    # ── brand name across the top (letter-spaced, centered) ──
    name = (bk.get("display_name") or "").strip().upper()
    if name:
        _spaced_fit(draw, 84, name, _ARCHIVO, 30, accent, 8, W - 2 * M, center=W // 2)

    # ── short accent rule ──
    ry = 300
    draw.rectangle((M, ry, M + 132, ry + 7), fill=accent)

    # ── the statement: big, bold, left-aligned, mixed case, inline highlight ──
    words = [w for w in _case(statement or "").split() if w]
    emph = _emph_word_idx(words, emphasis)
    zone_top, zone_bottom = ry + 62, H - 268
    font = _fit_left(draw, words, _DISPLAY, W - 2 * M, zone_bottom - zone_top,
                     start=134, minimum=46)
    space = _text_w(draw, " ", font)
    lh = _line_h(draw, font, 1.14)
    y = zone_top
    for ln in _wrap_idx(draw, words, font, W - 2 * M):
        x = M
        for w, gi in ln:
            draw.text((x, y), w, font=font, fill=(accent if gi in emph else ink))
            x += _text_w(draw, w, font) + space
        y += lh

    # ── byline: optional name, then website · tagline (only what the brand supplies) ──
    yb = H - 156
    if byline_name.strip():
        _spaced_fit(draw, yb, byline_name.strip().upper(), _ARCHIVO, 26, accent, 6,
                    W - 2 * M, left=M)
        yb += 46
    site = (bk.get("website") or "").strip()
    tag = (bk.get("footer_tagline") or "").strip()
    foot = "   ·   ".join([p for p in (site, tag) if p])
    if foot:
        draw.text((M, yb), foot, font=_font(_ARCHIVO, 22), fill=muted)
    return _png(base)


__all__ = [
    "quote_card", "meme_card", "statement_card",
    "brand_quote_card", "hero_quote_card", "bold_statement_card", "W", "H",
]
