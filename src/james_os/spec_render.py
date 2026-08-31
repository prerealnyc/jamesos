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

from PIL import Image, ImageDraw, ImageOps

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


def _draw_element(base: Image.Image, el: dict, text: str, ink_default) -> None:
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
    cy = y + max(0, (bh - line_h * len(lines)) // 2)  # vertically center within the box
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        if align == "center":
            tx = x + (bw - tw) / 2
        elif align == "right":
            tx = x + bw - tw
        else:
            tx = x
        # a soft shadow keeps light text legible over a busy photo
        draw.text((tx + 2, cy + 2), ln, font=font, fill=(0, 0, 0, 160))
        draw.text((tx, cy), ln, font=font, fill=col)
        cy += line_h


def render_spec(spec: dict, content: dict, *, hero_bytes: bytes | None = None) -> tuple[bytes, str]:
    """Rebuild `spec` with the brand's `content` (role → text) and photo.

    Returns (png_bytes, kind). A photo treatment with no photo falls back to the
    solid palette background, so it never hard-fails."""
    hero = _load(hero_bytes)
    base = _background(spec, hero)
    # Scrim only matters over a photo.
    if hero is not None:
        _scrim(base, (spec.get("background") or {}).get("scrim") or "none")
    _decorations(base, spec, content)
    ink_default = _rgb((spec.get("palette") or {}).get("ink"), (255, 255, 255))
    for el in spec.get("elements") or []:
        _draw_element(base, el, str(content.get(el["role"], "")), ink_default)
    out = io.BytesIO()
    base.save(out, format="PNG")
    return out.getvalue(), spec.get("kind", "graphic_card")


__all__ = ["render_spec"]
