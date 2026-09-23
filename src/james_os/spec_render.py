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


# How the photo used to be cropped, whatever was in it and wherever the copy
# was going to land. Kept as the answer when there is nothing to reason about.
_DEFAULT_CENTERING = (0.5, 0.4)
# Candidate crops. Coarse on purpose: this decides WHICH PART of the photo
# survives the crop, and a five-by-five grid already moves a face out from
# under a headline. Finer would cost more and change the picture less.
_PAN = (0.15, 0.3, 0.5, 0.7, 0.85)
_TILT = (0.2, 0.35, 0.5, 0.65, 0.8)
# The probe the search runs on. Busyness is a broad-strokes judgement, so it is
# made on a thumbnail — twenty-five candidate crops of a 96px image cost less
# than one crop of the real one.
_PROBE_W = 96
_PROBE_CELLS = (48, 60)


def _busy(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Edge energy, small. A face, a horizon, foliage and lettering all light
    this up; sky, water, a wall and open sand do not — which is exactly the
    difference between a spot that ruins a headline and a spot that carries one."""
    grey = img.convert("L").resize(size, Image.Resampling.BILINEAR)
    return grey.filter(ImageFilter.FIND_EDGES)


def _zone_cost(edges: Image.Image, zones: list[tuple[float, float, float, float]]) -> float:
    """Total edge energy under the rectangles the copy will occupy."""
    cw, ch = edges.size
    px = edges.load()
    total = 0.0
    for zx, zy, zw, zh in zones:
        x0, y0 = max(0, int(zx * cw)), max(0, int(zy * ch))
        x1, y1 = min(cw, int((zx + zw) * cw)), min(ch, int((zy + zh) * ch))
        for yy in range(y0, max(y0 + 1, y1)):
            for xx in range(x0, max(x0 + 1, x1)):
                total += px[xx, yy]
    return total


def _best_centering(img: Image.Image, w: int, h: int,
                    zones: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """The crop alone. See _centering_and_cost for the score behind it."""
    return _centering_and_cost(img, w, h, zones)[0]


def _centering_and_cost(img: Image.Image, w: int, h: int,
                        zones: list[tuple[float, float, float, float]]
                        ) -> tuple[tuple[float, float], float]:
    """Which crop puts the quiet part of the photo under the copy.

    The layout came from a competitor whose photo happened to be empty where
    their words went. Ours is not: the reviewer kept saying "text overlaps with
    the subjects" and "logo overlaps with subject's leg", because the crop was
    fixed and the people landed under the headline. Nothing here moves the
    text — the design is the design — it moves the PHOTO inside its frame,
    which is the one degree of freedom that costs nothing.

    Ties go to the old centering, so a photo with nothing to avoid renders
    exactly as it did before.
    """
    if not zones:
        return _DEFAULT_CENTERING, 0.0
    try:
        probe_h = max(1, int(_PROBE_W * img.height / max(1, img.width)))
        probe = img.convert("L").resize((_PROBE_W, probe_h), Image.Resampling.BILINEAR)
        cells = (_PROBE_CELLS[0], max(1, int(_PROBE_CELLS[0] * h / max(1, w))))
        def cost_of(c: tuple[float, float]) -> float:
            fitted = ImageOps.fit(probe, cells, method=Image.Resampling.BILINEAR,
                                  centering=c)
            return _zone_cost(_busy(fitted, cells), zones)

        # The incumbent is measured FIRST and is the score to beat. Seeding the
        # search with "no result yet" made the first candidate win by default,
        # so the old centering was never really in the running.
        best = _DEFAULT_CENTERING
        best_cost = cost_of(best)
        for cy in _TILT:
            for cx in _PAN:
                if (cx, cy) == _DEFAULT_CENTERING:
                    continue
                cost = cost_of((cx, cy))
                # Meaningfully better, not merely different: a 2% edge is noise,
                # and recomposing a photo for noise is a change nobody asked for.
                if cost < best_cost * 0.98:
                    best, best_cost = (cx, cy), cost
        return best, best_cost
    except Exception:  # noqa: BLE001 — a crop heuristic must never cost the render
        return _DEFAULT_CENTERING, 0.0


def _cover(img: Image.Image, w: int, h: int,
           zones: list[tuple[float, float, float, float]] | None = None) -> Image.Image:
    """Crop-to-cover: fill (w,h) with the image.

    With `zones` — the rectangles the copy and logo will occupy, in this
    frame's own coordinates — the crop is chosen to keep those rectangles over
    the calmest part of the picture. Without them, the old fixed centering."""
    centering = _best_centering(img, w, h, zones or [])
    return ImageOps.fit(img.convert("RGB"), (max(1, w), max(1, h)),
                        method=Image.Resampling.LANCZOS, centering=centering)


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
    # A movable layer in the card editor (layer_capture), not part of the plate.
    from .layer_capture import mark_badge
    base.paste(mark_badge(logo, "logo"), (x + (w - nw) // 2, y + (h - nh) // 2), logo)


def _occupied(spec: dict) -> list[tuple[float, float, float, float]]:
    """Every rectangle the finished card will put something ON TOP of the photo:
    the text boxes and the logo slot, as fractions of the whole canvas.

    Content is not consulted. A box whose copy turns out empty costs nothing to
    have kept clear, and a box we wrongly skipped is a face under a headline."""
    out: list[tuple[float, float, float, float]] = []
    for el in (spec.get("elements") or []):
        b = el.get("box") or {}
        try:
            r = (float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"]))
        except (KeyError, TypeError, ValueError):
            continue
        if r[2] > 0 and r[3] > 0:
            out.append(r)
    lb = spec.get("logo_box") or {}
    try:
        if float(lb.get("w", 0)) > 0 and float(lb.get("h", 0)) > 0:
            out.append((float(lb["x"]), float(lb["y"]), float(lb["w"]), float(lb["h"])))
    except (TypeError, ValueError):
        pass
    return out


def _zones_for(frame: tuple[float, float, float, float],
               occupied: list[tuple[float, float, float, float]]
               ) -> list[tuple[float, float, float, float]]:
    """The occupied rectangles expressed in ONE photo frame's own coordinates.

    A photo that fills the canvas sees them unchanged; a photo in the top 60%,
    or down one side, sees only the part that lands on it, rescaled. Without
    this a side-panel photo would be cropped to dodge a headline that never
    touches it."""
    fx, fy, fw, fh = frame
    if fw <= 0 or fh <= 0:
        return []
    out = []
    for zx, zy, zw, zh in occupied:
        ix0, iy0 = max(fx, zx), max(fy, zy)
        ix1, iy1 = min(fx + fw, zx + zw), min(fy + fh, zy + zh)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        out.append(((ix0 - fx) / fw, (iy0 - fy) / fh, (ix1 - ix0) / fw, (iy1 - iy0) / fh))
    return out


def _photo_frame(spec: dict) -> tuple[float, float, float, float] | None:
    """Where the photo sits on the canvas, as fractions. None = no photo shown.

    Shared by the renderer and by photo scoring on purpose: if these two ever
    disagreed we would rank photos against a frame the render does not use."""
    bg = spec.get("background") or {}
    t = bg.get("treatment") or "full_bleed_photo"
    if t in ("full_bleed_photo", "photo_with_scrim"):
        return (0.0, 0.0, 1.0, 1.0)
    if t == "photo_top":
        return (0.0, 0.0, 1.0, 0.6)
    if t == "photo_bottom":
        return (0.0, 0.4, 1.0, 0.6)
    if t == "photo_side":
        return (0.45, 0.0, 0.55, 1.0)
    if t == "solid":
        return None
    pbox = bg.get("photo_box")
    if pbox:
        try:
            return (float(pbox["x"]), float(pbox["y"]), float(pbox["w"]), float(pbox["h"]))
        except (KeyError, TypeError, ValueError):
            return (0.0, 0.0, 1.0, 1.0)
    return (0.0, 0.0, 1.0, 1.0)


def photo_fit_cost(spec: dict, img: Image.Image) -> float:
    """How badly this photo suits this template — lower is better.

    The busyness left under the copy once the photo has been cropped as kindly
    as it can be. Normalised by the area being judged so a template with a lot
    of copy is not automatically 'worse' than one with a little, and so the
    number means the same thing across templates.

    A template that puts nothing over the photo scores 0: everything suits it.
    """
    frame = _photo_frame(spec)
    if frame is None:
        return 0.0
    zones = _zones_for(frame, _occupied(spec))
    if not zones:
        return 0.0
    area = sum(zw * zh for _, _, zw, zh in zones) or 1.0
    w = max(1, int(frame[2] * _w()))
    h = max(1, int(frame[3] * _h()))
    try:
        _, cost = _centering_and_cost(img, w, h, zones)
        return cost / area
    except Exception:  # noqa: BLE001 — scoring must never cost a render
        return 0.0


# "Near enough" in score units. Edge energy under copy runs from ~0 over sky to
# six figures over a face, so this admits genuinely comparable photos without
# admitting a subject sitting under the headline.
_FIT_SLACK = 500.0


def suited_photos(spec: dict, refs: list[tuple[str, bytes]],
                  tolerance: float = 1.25) -> list[tuple[str, bytes]]:
    """The photos worth using for THIS template, best first.

    Not just the single best: the picker downstream rotates the library so the
    brand does not post the same picture every time, and collapsing the pool to
    one photo would throw that away. Anything within `tolerance` of the best
    score stays in the running, so rotation continues among the photos that
    genuinely suit the layout — and when a layout does not discriminate, every
    photo survives and nothing changes.
    """
    scored: list[tuple[float, tuple[str, bytes]]] = []
    for ref in refs:
        img = _load(ref[1]) if len(ref) > 1 else None
        if img is None:
            continue
        scored.append((photo_fit_cost(spec, img), ref))
    if not scored:
        return list(refs)
    scored.sort(key=lambda pair: pair[0])
    best = scored[0][0]
    # Ratio AND slack. A ratio alone breaks at both ends: when the best photo
    # scores a perfect zero every other photo is infinitely worse than it, and
    # a first version of this read that as "the template does not discriminate"
    # and kept the lot — including a photo with the subject squarely under the
    # headline. The slack is what says "near enough", in the units of the score
    # rather than as a multiple of something that may be zero.
    keep = [ref for cost, ref in scored if cost <= best * tolerance + _FIT_SLACK]
    return keep or [scored[0][1]]


def _background(spec: dict, hero: Image.Image | None) -> Image.Image:
    """Paint the canvas per the spec's background treatment. A photo treatment
    with no photo degrades to the solid palette colour rather than failing.

    Where a photo goes under copy, the crop is chosen so the busy part of the
    picture lands where the copy is NOT — see _best_centering."""
    pal = spec.get("palette") or {}
    base = Image.new("RGB", (_w(), _h()), _rgb(pal.get("bg"), (17, 19, 24)))
    if hero is None:
        return base  # solid fallback
    occupied = _occupied(spec)

    def place(frame: tuple[float, float, float, float]) -> None:
        fx, fy, fw, fh = frame
        x, y = int(fx * _w()), int(fy * _h())
        w, h = int(fw * _w()), int(fh * _h())
        base.paste(_cover(hero, w, h, _zones_for(frame, occupied)), (x, y))

    frame = _photo_frame(spec)
    if frame is not None:
        place(frame)
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
            # base role: a numbered repeat ("cta#2") pairs like its first
            if str(e.get("role") or "").split("#", 1)[0] not in ("cta", "kicker", "stat"):
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

    A size also only counts as fitting when its LONGEST WORD fits the box width.
    Otherwise the wrapper breaks the word across lines — "FREE COURS / E",
    "$1B / +" — which it did in the narrow boxes of the tallest platform shapes,
    where a slightly smaller size would have kept every word whole.
    """
    words = text.split()
    px = start_px
    while True:
        px = max(px, _MIN_PX)
        font = _font(face_path, px)
        lines = _wrap(draw, text, font, box_w)
        line_h = int(px * 1.12)
        widest = max((draw.textlength(w, font=font) for w in words), default=0)
        if len(lines) * line_h <= box_h and widest <= box_w:
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


__all__ = ["render_spec", "photo_fit_cost", "suited_photos"]
