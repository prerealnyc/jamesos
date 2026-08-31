"""The generic template renderer — composite a design_cloner spec with a brand's
own photo (or an AI-generated placeholder) and content.

Where designed_render.py has ~9 hand-built fixed layouts, this draws an ARBITRARY
spec: a background treatment, scrim, decorations, and text elements each placed by
its own normalized box. One renderer rebuilds many competitor designs — the more
the extractor's vocabulary grows, the closer the replication. Text is always
drawn as crisp brand type (never baked into a generated image), so it stays sharp.

Contract: render_spec(spec, content, hero_bytes=None) -> (png_bytes, used_kind).
`content` maps each role the spec asks for to the brand's own words.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .image_compose import W, H, _font, _wrap, _ANTON, _ARCHIVO, _remap_face

# Starting font px per size tier; the fitter shrinks from here to fit the box.
_SIZE_START = {"sm": 38, "md": 60, "lg": 92, "xl": 150, "xxl": 300}
_MIN_PX = 18


def _rgb(h, default=(255, 255, 255)) -> tuple[int, int, int]:
    s = str(h or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, IndexError):
        return default


def _px(box: dict) -> tuple[int, int, int, int]:
    return (int(box["x"] * W), int(box["y"] * H), int(box["w"] * W), int(box["h"] * H))


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Crop-to-cover: fill (w,h) with the image, center-cropping the overflow."""
    return ImageOps.fit(img.convert("RGB"), (max(1, w), max(1, h)),
                        method=Image.Resampling.LANCZOS, centering=(0.5, 0.4))


def _load(b: bytes | None) -> Image.Image | None:
    if not b:
        return None
    try:
        return Image.open(io.BytesIO(b)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _load_rgba(b: bytes | None) -> Image.Image | None:
    if not b:
        return None
    try:
        return Image.open(io.BytesIO(b)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _lum(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _region_lum(base: Image.Image, x: int, y: int, w: int, h: int) -> float:
    """Mean brightness (0-255) of the background under a text block, so we can
    tell whether the text colour will actually read there."""
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return 128.0
    px = list(base.crop((x0, y0, x1, y1)).convert("L").getdata())
    return sum(px) / len(px) if px else 128.0


def _plate(base: Image.Image, box_px: tuple[int, int, int, int], rgb: tuple, alpha: float) -> None:
    """A soft, feathered rounded plate behind text — just enough to guarantee
    legibility over a busy or same-tone photo, without hiding the image."""
    x, y, w, h = box_px
    pad = int(h * 0.18) + 14
    ov = Image.new("RGBA", (base.width, base.height), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle(
        [x - pad, y - pad, x + w + pad, y + h + pad],
        radius=int(h * 0.3) + 12, fill=(rgb[0], rgb[1], rgb[2], int(255 * alpha)))
    ov = ov.filter(ImageFilter.GaussianBlur(18))
    base.paste(Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB"), (0, 0))


def _place_logo(base: Image.Image, box_px: tuple[int, int, int, int], logo_bytes: bytes | None) -> None:
    """Drop the brand's own logo into the slot the design reserves for one,
    contain-fit (aspect preserved) and centered in the box."""
    logo = _load_rgba(logo_bytes)
    if logo is None:
        return
    x, y, w, h = box_px
    lw, lh = logo.size
    if lw <= 0 or lh <= 0:
        return
    scale = min(w / lw, h / lh)
    nw, nh = max(1, int(lw * scale)), max(1, int(lh * scale))
    logo = logo.resize((nw, nh), Image.Resampling.LANCZOS)
    base.paste(logo, (x + (w - nw) // 2, y + (h - nh) // 2), logo)


def _background(spec: dict, hero: Image.Image | None) -> Image.Image:
    """Paint the canvas per the spec's background treatment. A photo treatment
    with no photo degrades to the solid palette colour rather than failing."""
    bg = spec.get("background") or {}
    pal = spec.get("palette") or {}
    base = Image.new("RGB", (W, H), _rgb(pal.get("bg"), (17, 19, 24)))
    t = bg.get("treatment") or "full_bleed_photo"
    if hero is None:
        return base  # solid fallback
    if t in ("full_bleed_photo", "photo_with_scrim"):
        base.paste(_cover(hero, W, H), (0, 0))
    elif t == "photo_top":
        base.paste(_cover(hero, W, int(H * 0.6)), (0, 0))
    elif t == "photo_bottom":
        base.paste(_cover(hero, W, int(H * 0.6)), (0, H - int(H * 0.6)))
    elif t == "photo_side":
        base.paste(_cover(hero, int(W * 0.55), H), (W - int(W * 0.55), 0))
    elif t == "solid":
        pass
    else:  # photo_box or unknown → full-bleed as the safe default
        pbox = bg.get("photo_box")
        if pbox:
            x, y, w, h = _px(pbox)
            base.paste(_cover(hero, w, h), (x, y))
        else:
            base.paste(_cover(hero, W, H), (0, 0))
    return base


def _scrim(base: Image.Image, where: str) -> None:
    """A dark legibility gradient so text over a photo stays readable."""
    if not where or where == "none":
        return
    mask = Image.new("L", (1, H), 0)
    for y in range(H):
        t = y / H
        if where == "bottom":
            a = max(0.0, (t - 0.40) / 0.60)
        elif where == "top":
            a = max(0.0, (0.60 - t) / 0.60)
        else:  # full
            a = 0.55
        mask.putpixel((0, y), int(255 * min(1.0, a) * 0.78))
    base.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), mask.resize((W, H)))


def _has_text_over(box: dict, content: dict, elements: list) -> bool:
    """Does a NON-EMPTY text element sit over this box? A pill/badge with no copy
    on it is just an empty blob — the extractor emits the shape and the text as
    separate items, so we only draw the shape when its label actually landed."""
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    for e in elements or []:
        if not str(content.get(e.get("role"), "")).strip():
            continue
        b = e.get("box") or {}
        if b.get("x", 0) - 0.02 <= cx <= b.get("x", 0) + b.get("w", 0) + 0.02 and \
           b.get("y", 0) - 0.04 <= cy <= b.get("y", 0) + b.get("h", 0) + 0.04:
            return True
    return False


def _decorations(base: Image.Image, spec: dict, content: dict) -> None:
    draw = ImageDraw.Draw(base, "RGBA")
    elements = spec.get("elements") or []
    for d in spec.get("decorations") or []:
        x, y, w, h = _px(d["box"])
        col = _rgb(d.get("color"), _rgb((spec.get("palette") or {}).get("accent"), (201, 162, 75)))
        typ = d.get("type")
        if typ == "bar":
            draw.rectangle([x, y, x + w, y + h], fill=col)
        elif typ in ("pill", "badge"):
            # Only draw a pill/badge if a text label actually lands on it —
            # otherwise it's an empty coloured blob.
            if not _has_text_over(d["box"], content, elements):
                continue
            r = h // 2
            draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=col)
        elif typ == "frame":
            draw.rectangle([x, y, x + w, y + h], outline=col, width=max(3, h // 40 or 3))
        elif typ == "scrim":
            draw.rectangle([x, y, x + w, y + h], fill=col + (150,))


def _face(weight: str):
    return _ARCHIVO if weight in ("bold", "black") else _ANTON


def _fit_block(draw, text: str, face_path: str, box_w: int, box_h: int, start_px: int):
    """Largest font (from start_px down) whose wrapped text fits the box."""
    px = start_px
    while px >= _MIN_PX:
        font = _font(face_path, px)
        lines = _wrap(draw, text, font, box_w)
        line_h = int(px * 1.12)
        if len(lines) * line_h <= box_h or px == _MIN_PX:
            return font, lines, line_h
        px = int(px * 0.9)
    return _font(face_path, _MIN_PX), [text], int(_MIN_PX * 1.12)


def _draw_element(base: Image.Image, el: dict, text: str, ink_default, over_photo: bool) -> None:
    if not (text or "").strip():
        return
    draw = ImageDraw.Draw(base)
    x, y, bw, bh = _px(el["box"])
    if el.get("case") == "upper":
        text = text.upper()
    face = _remap_face(_face(el.get("weight", "bold")))
    start = _SIZE_START.get(el.get("size", "md"), 60)
    font, lines, line_h = _fit_block(draw, text, face, bw, bh, start)
    col = _rgb(el.get("color"), ink_default)
    align = el.get("align", "left")
    widths = [draw.textlength(ln, font=font) for ln in lines]
    maxw = max(widths) if widths else bw
    block_h = line_h * len(lines)
    cy0 = y + max(0, (bh - block_h) // 2)  # vertically center within the box
    if align == "center":
        blk_x = x + (bw - maxw) / 2
    elif align == "right":
        blk_x = x + bw - maxw
    else:
        blk_x = x

    text_lum = _lum(col)
    # Contrast guard: over a photo, if the text tone is too close to what's behind
    # it (light text on bright sky, dark text on shadow), lay a soft plate so it
    # always reads — only when actually needed, so the photo stays visible.
    if over_photo:
        bg_lum = _region_lum(base, blk_x, cy0, maxw, block_h)
        if abs(text_lum - bg_lum) < 95:
            plate_rgb = (0, 0, 0) if text_lum > 128 else (255, 255, 255)
            _plate(base, (int(blk_x), int(cy0), int(maxw), int(block_h)), plate_rgb, 0.42)

    # A crisp contrasting outline keeps every letter legible on any background.
    stroke_col = (0, 0, 0) if text_lum > 128 else (255, 255, 255)
    stroke_w = max(1, font.size // 34)
    cy = cy0
    for i, ln in enumerate(lines):
        if align == "center":
            tx = x + (bw - widths[i]) / 2
        elif align == "right":
            tx = x + bw - widths[i]
        else:
            tx = x
        draw.text((tx, cy), ln, font=font, fill=col, stroke_width=stroke_w, stroke_fill=stroke_col)
        cy += line_h


def render_spec(spec: dict, content: dict, *, hero_bytes: bytes | None = None,
                logo_bytes: bytes | None = None) -> tuple[bytes, str]:
    """Rebuild `spec` with the brand's `content` (role → text), photo and logo.

    Returns (png_bytes, kind). A photo treatment with no photo falls back to the
    solid palette background, so it never hard-fails."""
    hero = _load(hero_bytes)
    base = _background(spec, hero)
    over_photo = hero is not None
    # Scrim only matters over a photo.
    if over_photo:
        _scrim(base, (spec.get("background") or {}).get("scrim") or "none")
    _decorations(base, spec, content)
    ink_default = _rgb((spec.get("palette") or {}).get("ink"), (255, 255, 255))
    for el in spec.get("elements") or []:
        _draw_element(base, el, str(content.get(el["role"], "")), ink_default, over_photo)
    # The brand's own logo in the slot the design reserved for one.
    if logo_bytes and spec.get("logo_box"):
        _place_logo(base, _px(spec["logo_box"]), logo_bytes)
    out = io.BytesIO()
    base.save(out, format="PNG")
    return out.getvalue(), spec.get("kind", "graphic_card")


__all__ = ["render_spec"]
