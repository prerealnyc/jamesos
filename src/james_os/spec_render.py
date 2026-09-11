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

from .image_compose import _w, _h, _font, _wrap, _ANTON, _ARCHIVO, _remap_face

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
    return (int(box["x"] * _w()), int(box["y"] * _h()), int(box["w"] * _w()), int(box["h"] * _h()))


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
    x1, y1 = min(_w(), int(x + w)), min(_h(), int(y + h))
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
    base = Image.new("RGB", (_w(), _h()), _rgb(pal.get("bg"), (17, 19, 24)))
    t = bg.get("treatment") or "full_bleed_photo"
    if hero is None:
        return base  # solid fallback
    if t in ("full_bleed_photo", "photo_with_scrim"):
        base.paste(_cover(hero, _w(), _h()), (0, 0))
    elif t == "photo_top":
        base.paste(_cover(hero, _w(), int(_h() * 0.6)), (0, 0))
    elif t == "photo_bottom":
        base.paste(_cover(hero, _w(), int(_h() * 0.6)), (0, _h() - int(_h() * 0.6)))
    elif t == "photo_side":
        base.paste(_cover(hero, int(_w() * 0.55), _h()), (_w() - int(_w() * 0.55), 0))
    elif t == "solid":
        pass
    else:  # photo_box or unknown → full-bleed as the safe default
        pbox = bg.get("photo_box")
        if pbox:
            x, y, w, h = _px(pbox)
            base.paste(_cover(hero, w, h), (x, y))
        else:
            base.paste(_cover(hero, _w(), _h()), (0, 0))
    return base


def _scrim(base: Image.Image, where: str) -> None:
    """A dark legibility gradient so text over a photo stays readable."""
    if not where or where == "none":
        return
    mask = Image.new("L", (1, _h()), 0)
    for y in range(_h()):
        t = y / _h()
        if where == "bottom":
            a = max(0.0, (t - 0.40) / 0.60)
        elif where == "top":
            a = max(0.0, (0.60 - t) / 0.60)
        else:  # full
            a = 0.55
        mask.putpixel((0, y), int(255 * min(1.0, a) * 0.78))
    base.paste(Image.new("RGB", (_w(), _h()), (0, 0, 0)), (0, 0), mask.resize((_w(), _h())))


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


def _fit_buttons(spec: dict, content: dict) -> None:
    """Snap a pill/badge's label ONTO the pill. The extractor emits the button
    shape and its text as separate items, sometimes offset — so a CTA renders as
    a floating label above an empty coloured blob. For each pill/badge, find the
    nearest short content-bearing label (cta/kicker/stat) and move it onto the
    pill box (centered, contrasting colour). A pill with no pairable label is then
    left with no text over it and gets suppressed downstream. Mutates the spec in
    place — fine, it's a per-render copy from the pipeline."""
    elements = spec.get("elements") or []
    for d in spec.get("decorations") or []:
        if d.get("type") not in ("pill", "badge"):
            continue
        pb = d.get("box") or {}
        pcx, pcy = pb.get("x", 0) + pb.get("w", 0) / 2, pb.get("y", 0) + pb.get("h", 0) / 2
        best, best_d = None, 0.20
        for e in elements:
            if e.get("role") not in ("cta", "kicker", "stat"):
                continue
            if not str(content.get(e.get("role"), "")).strip():
                continue
            eb = e.get("box") or {}
            dist = abs(eb.get("x", 0) + eb.get("w", 0) / 2 - pcx) + abs(eb.get("y", 0) + eb.get("h", 0) / 2 - pcy)
            if dist < best_d:
                best, best_d = e, dist
        if best is not None:
            best["box"] = dict(pb)
            best["align"] = "center"
            # Contrast the label against the pill colour, not the photo behind it.
            pill_lum = _lum(_rgb(d.get("color"), (201, 162, 75)))
            best["color"] = "#ffffff" if pill_lum < 140 else "#111318"
            best["_on_pill"] = True  # so the contrast plate doesn't fire for it


def _decorations(base: Image.Image, spec: dict, content: dict) -> None:
    draw = ImageDraw.Draw(base, "RGBA")
    elements = spec.get("elements") or []
    for d in spec.get("decorations") or []:
        box = d["box"]
        x, y, w, h = _px(box)
        col = _rgb(d.get("color"), _rgb((spec.get("palette") or {}).get("accent"), (201, 162, 75)))
        typ = d.get("type")
        # A filled shape big enough to be a button/banner needs a label — a
        # pill/badge always, and a "bar" only when it's thick (a thin bar is a
        # divider and stands on its own). Without a label it's an empty blob.
        needs_label = typ in ("pill", "badge") or (typ == "bar" and box.get("h", 0) > 0.02)
        if needs_label and not _has_text_over(box, content, elements):
            continue
        if typ == "bar":
            draw.rectangle([x, y, x + w, y + h], fill=col)
        elif typ in ("pill", "badge"):
            draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=col)
        elif typ == "frame":
            draw.rectangle([x, y, x + w, y + h], outline=col, width=max(3, h // 40 or 3))
        elif typ == "scrim":
            draw.rectangle([x, y, x + w, y + h], fill=col + (150,))


def _face(weight: str):
    return _ARCHIVO if weight in ("bold", "black") else _ANTON


def _ellipsize(draw, line: str, font, box_w: int) -> str:
    """Trim a line until it and a trailing ellipsis fit the box width."""
    text = line.rstrip()
    while text and draw.textlength(text + "…", font=font) > box_w:
        text = text[:-1].rstrip()
    return (text + "…") if text else "…"


def _fit_block(draw, text: str, face_path: str, box_w: int, box_h: int, start_px: int):
    """Largest font (from start_px down) whose wrapped text fits the box — and
    text that NEVER leaves the box, whatever the copy.

    Two holes this closes, both reachable in production once autopilot renders
    learned layouts unattended:

      The loop shrank by x0.9 and only accepted a too-tall result when px
      landed EXACTLY on _MIN_PX. From the headline sizes (lg 92, xl 150, xxl
      300) it steps 19 -> 17 and skips 18 entirely, so it fell through to the
      last line and returned the whole text as ONE UNWRAPPED line — wider than
      the canvas, on the very sizes that carry the longest copy.

      And at the minimum it returned lines that still did not fit the box
      height, so long copy ran out the bottom of its box and off the frame.

    Now px is clamped to try _MIN_PX exactly, and if even that is too tall the
    text is cut to the lines that fit with the last one ellipsized. A clipped
    headline is a worse post than a whole one; a headline running off the
    picture is not a post at all.
    """
    px = start_px
    while True:
        px = max(px, _MIN_PX)
        font = _font(face_path, px)
        lines = _wrap(draw, text, font, box_w)
        line_h = int(px * 1.12)
        if len(lines) * line_h <= box_h:
            return font, lines, line_h
        if px == _MIN_PX:
            break
        px = int(px * 0.9)
    # At the minimum and still too tall for the box: keep what fits.
    max_lines = max(1, box_h // line_h) if line_h > 0 else 1
    kept = list(lines[:max_lines]) or [text]
    if len(lines) > max_lines:
        kept[-1] = _ellipsize(draw, kept[-1], font, box_w)
    return font, kept, line_h


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
    # always reads — only when actually needed, so the photo stays visible. Skip
    # a label sitting ON a pill: it already has a solid, contrasting background.
    if over_photo and not el.get("_on_pill"):
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


# ── the brand's colours, onto someone else's structure ───────────────────────
#
# A cloned template is borrowed STRUCTURE. Its colours came off the competitor's
# own image, and nothing used to replace them — so a brand's cloned posts came
# out in the competitor's palette, and the owner's colour picker had no effect on
# the very grid it sits above. Remapping rather than overwriting is what keeps
# the design working: a word the competitor set in their accent becomes the
# brand's accent, a panel on their background becomes the brand's background, so
# the contrast relationships that made the layout read survive the swap.

_ROLE_ORDER = ("bg", "ink", "accent", "surface")


def _roles_from(palette) -> dict:
    """{role: (r,g,b)} from a brand role-list or an already-keyed dict."""
    out: dict = {}
    if isinstance(palette, dict) and palette.get("palette"):
        palette = palette["palette"]
    if isinstance(palette, list):
        named = {str(p.get("role") or "").lower(): p.get("hex")
                 for p in palette if isinstance(p, dict)}
        mapping = {"bg": "background", "ink": "ink", "accent": "accent", "surface": "surface"}
        for role, key in mapping.items():
            if named.get(key):
                out[role] = _rgb(named[key], None)
    elif isinstance(palette, dict):
        for role in _ROLE_ORDER:
            if palette.get(role):
                out[role] = _rgb(palette[role], None)
    return {k: v for k, v in out.items() if v}


def _nearest_role(colour, source: dict) -> str | None:
    """Which of the source palette's roles is this colour? Plain RGB distance —
    the question is only "which of four", not a colour-science one."""
    best, best_d = None, None
    for role, rgb in source.items():
        if not rgb:
            continue
        d = sum((int(a) - int(b)) ** 2 for a, b in zip(colour, rgb))
        if best_d is None or d < best_d:
            best, best_d = role, d
    return best


def rebrand_spec(spec: dict, palette) -> dict:
    """Return `spec` with every colour moved from the source brand's palette to
    this brand's, role for role. A no-op when the brand has no palette, so a
    brand that has not set one still gets the template as designed."""
    brand = _roles_from(palette)
    if not brand:
        return spec
    source = _roles_from(spec.get("palette") or {})
    if not source:
        return spec

    def _swap(hexval, fallback_role: str):
        rgb = _rgb(hexval, None)
        if not rgb:
            return None
        role = _nearest_role(rgb, source) or fallback_role
        new = brand.get(role) or brand.get(fallback_role)
        return "#%02x%02x%02x" % new if new else None

    out = dict(spec)
    out["palette"] = {
        role: ("#%02x%02x%02x" % brand[role])
        for role in _ROLE_ORDER if brand.get(role)
    }
    # Keep any role the brand does not define, so a partial palette degrades to
    # the source's colour for that role rather than to nothing.
    for role, val in (spec.get("palette") or {}).items():
        out["palette"].setdefault(role, val)

    out["elements"] = [
        {**e, **({"color": c} if (c := _swap(e.get("color"), "ink")) else {})}
        for e in (spec.get("elements") or [])
    ]
    out["decorations"] = [
        {**d, **({"color": c} if (c := _swap(d.get("color"), "accent")) else {})}
        for d in (spec.get("decorations") or [])
    ]
    bg = dict(spec.get("background") or {})
    if bg.get("color") and (c := _swap(bg["color"], "bg")):
        bg["color"] = c
        out["background"] = bg
    return out


def render_spec(spec: dict, content: dict, *, hero_bytes: bytes | None = None,
                logo_bytes: bytes | None = None, palette=None) -> tuple[bytes, str]:
    """Rebuild `spec` with the brand's `content` (role → text), photo and logo.

    `palette` is the BRAND's own role list. Given one, the template's colours are
    remapped onto it role for role (see rebrand_spec) — the structure is what was
    borrowed, the colours are the brand's. Omitted, the template renders in the
    colours it was read with, which is the old behaviour.

    Returns (png_bytes, kind). A photo treatment with no photo falls back to the
    solid palette background, so it never hard-fails."""
    if palette:
        spec = rebrand_spec(spec, palette)
    hero = _load(hero_bytes)
    base = _background(spec, hero)
    over_photo = hero is not None
    # Unify buttons (label onto pill) before anything is drawn.
    _fit_buttons(spec, content)
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
