"""Capture a card as layers while it is drawn — so the editor can move them.

The owner's ask (11 Sep): move the logo, the text and the pictures on a generated
card, and swap a picture for a brand photo or an upload. A flat PNG cannot do
that, and "dissecting" one back into parts — reading the text off it, guessing
fonts and positions, painting over where the words were — guesses at things the
renderer knew exactly while it drew them.

So the renderer is asked instead. The card is drawn a second time in CAPTURE
mode, where every line of text and every badge (the logo, the profile mark) is
recorded rather than drawn. What is left on the canvas is the clean background
plate — photo, colour, scrims, bars — and the records are the layers, exact to
the pixel: the words, face, size, colour, tracking and position of every line,
and each badge as its own transparent PNG at its own box.

How it hooks in: PIL's ImageDraw.text and Image.paste are wrapped once, and the
wrapper does anything only while a capture is active in the CURRENT context
(a ContextVar — per task and per thread, so a capture never touches a card
being drawn elsewhere at the same time). Outside a capture both behave exactly
as PIL's own. Hooking there, rather than in each compositor, covers every
layout — the nine, learned and cloned — and any added later.

A badge is anything a compositor marks with mark_badge() before pasting it —
the logo, the profile mark, and a photo that sits on the card as a panel of its
own (nothing drawn over it) and so can move. A full-bleed photo under a scrim
stays in the plate: lifted out, the scrim would be left on nothing. Changing
that photo is a re-plate — the same design drawn again with another picture.
"""

from __future__ import annotations

import contextvars
import io
import logging
from typing import Any, Callable

from PIL import Image, ImageDraw

logger = logging.getLogger("layer_capture")

_CAPTURE: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("layer_capture", default=None)
_BADGE = "_layer_badge"

_orig_text = ImageDraw.ImageDraw.text
_orig_paste = Image.Image.paste


def mark_badge(img: Image.Image, kind: str = "logo") -> Image.Image:
    """Mark an image as a movable layer (logo, profile mark). Returns it."""
    try:
        img.info[_BADGE] = kind
    except Exception:  # noqa: BLE001 — marking must never break a render
        pass
    return img


def _hex(fill: Any) -> tuple[str, float]:
    """(#rrggbb, opacity) for a PIL fill."""
    if isinstance(fill, str):
        return (fill if fill.startswith("#") else "#ffffff"), 1.0
    if isinstance(fill, int):
        return "#%02x%02x%02x" % (fill, fill, fill), 1.0
    if isinstance(fill, (tuple, list)) and len(fill) >= 3:
        r, g, b = (int(c) for c in fill[:3])
        a = (int(fill[3]) / 255.0) if len(fill) >= 4 else 1.0
        return "#%02x%02x%02x" % (r, g, b), round(a, 3)
    return "#ffffff", 1.0


_WEIGHTS = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "book": 400,
            "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
            "extrabold": 800, "heavy": 800, "black": 900}


# Faces whose family name really does end in a weight word.
_ONE_WEIGHT = {"archivo black"}


def _face(font) -> tuple[str, str]:
    """(family, CSS weight) for a PIL font — the names the editor's web fonts use."""
    try:
        family, style = font.getname()
    except Exception:  # noqa: BLE001
        return "Anton", "400"
    s = str(style or "").lower().replace(" ", "").replace("-", "")
    # The longest match: "semibold" is 600, not the "bold" inside it.
    hits = [k for k in _WEIGHTS if k in s]
    weight = _WEIGHTS[max(hits, key=len)] if hits else 400
    fam = str(family or "Anton").strip()
    if fam.lower() in _ONE_WEIGHT:
        return fam, "400"
    # A variable font reports its default instance in the family ("Cormorant
    # Light", style "Bold"); the family is "Cormorant".
    parts = fam.split()
    while len(parts) > 1 and parts[-1].lower() in _WEIGHTS:
        parts.pop()
    return " ".join(parts), str(weight)


def _canvas_sized(draw) -> bool:
    """Only text drawn on the card itself is a layer — not text drawn into a
    mask or a small temporary image an effect is built from."""
    cap = _CAPTURE.get()
    img = getattr(draw, "_image", None)
    size = getattr(img, "size", None)
    return bool(cap) and size == tuple(cap["canvas"])


def _record_text(draw, xy, text: str, fill, font, anchor, stroke_width: int,
                 stroke_fill=None) -> None:
    cap = _CAPTURE.get()
    s = str(text)
    if font is None or not hasattr(font, "getmetrics") or not s:
        return
    if not s.strip():
        # A space drawn inside a letter-spaced line is part of that line —
        # dropped, "SKELON AGENCY" came back as "SKELONAGENCY".
        prev = cap["texts"][-1] if cap["texts"] else None
        if (len(s) == 1 and prev is not None and prev.get("_chars") is not None
                and abs(prev["y_la"] - (xy[1] if (anchor or "la")[1] == "a" else prev["y_la"])) < 0.5):
            adv = float(font.getlength(s))
            prev["_chars"].append((float(xy[0]), adv))
            prev["text"] += s
        return
    anchor = anchor or "la"
    l, t, r, b = draw.textbbox(xy, s, font=font, anchor=anchor)
    if anchor == "la":
        # Exactly where it was drawn. Read back off the bounding box instead, a
        # line centred at x=123.5 came back at 123 or 124 — half a pixel off on
        # every glyph edge, enough to fail the faithfulness check on a real card.
        ox, oy = float(xy[0]), float(xy[1])
    else:
        l0, t0, _r0, _b0 = draw.textbbox((0, 0), s, font=font, anchor="la")
        ox, oy = l - l0, t - t0                  # where 'la' (left, ascender) sits
    size = int(getattr(font, "size", 0) or 0)
    ascent, descent = font.getmetrics()
    family, weight = _face(font)
    colour, alpha = _hex(fill)
    advance = float(font.getlength(s))
    rec = {
        "text": s, "x": float(ox), "y_la": float(oy),
        # Where the letters stand. Each browser places a line by its own reading
        # of the font's metrics, which differs from FreeType's by as much as a
        # seventh of the size (Anton) — so the editor measures where its own
        # face puts the line and stands it on this baseline.
        "baseline": float(oy + ascent),
        # A first guess for an editor that cannot measure: middles aligned.
        "y": float(oy + (ascent + descent - size) / 2.0),
        "size": size, "family": family, "weight": weight,
        "font_path": str(getattr(font, "path", "") or ""),
        "fill": colour, "opacity": alpha, "advance": advance, "tracking": 0.0,
        "ink": [float(l), float(t), float(r), float(b)],
        "stroke": int(stroke_width or 0),
        "stroke_fill": _hex(stroke_fill)[0] if (stroke_width and stroke_fill is not None) else colour,
        "_chars": [(float(ox), advance)] if len(s) == 1 else None,
        "_font": (getattr(font, "path", ""), size), "_fill": colour,
    }
    # Where it sits in the stack, so a line drawn over a photo stays over it.
    # (A letter-spaced line folded into the one before keeps that one's place.)
    texts = cap["texts"]
    prev = texts[-1] if texts else None
    # Letter-spaced lines are drawn one character at a time (PIL has no
    # tracking): fold consecutive characters on one baseline back into one line.
    if (prev is not None and len(s) == 1 and prev.get("_chars") is not None
            and prev["_font"] == rec["_font"] and prev["_fill"] == colour
            and abs(prev["y_la"] - oy) < 0.5):
        last_x, last_adv = prev["_chars"][-1]
        gap = ox - (last_x + last_adv)
        if -2.0 <= gap <= size * 2.5:
            prev["_chars"].append((float(ox), advance))
            prev["text"] += s
            n = len(prev["_chars"])
            first_x = prev["_chars"][0][0]
            span = (ox + advance) - first_x
            adv_sum = sum(a for _x, a in prev["_chars"])
            prev["tracking"] = (span - adv_sum) / (n - 1) if n > 1 else 0.0
            prev["advance"] = span
            prev["ink"][2] = max(prev["ink"][2], float(r))
            return
    # Words drawn one at a time (a layout that colours one word of a line draws
    # each word itself): the next word in the same style, one space along the
    # same baseline, is the same line — "Clarity" and "is" are "Clarity is".
    if (prev is not None and prev["_font"] == rec["_font"] and prev["_fill"] == colour
            and abs(prev["y_la"] - oy) < 0.5 and not prev.get("tracking")
            and prev.get("stroke") == rec["stroke"] and len(prev.get("_chars") or ()) <= 1):
        gap = ox - (prev["x"] + prev["advance"])
        if abs(gap - float(font.getlength(" "))) <= 1.5:
            prev["text"] += " " + s
            prev["advance"] = (ox + advance) - prev["x"]
            prev["_chars"] = None
            prev["ink"] = [min(prev["ink"][0], float(l)), min(prev["ink"][1], float(t)),
                           max(prev["ink"][2], float(r)), max(prev["ink"][3], float(b))]
            return
    rec["z"] = _next_z(cap)
    texts.append(rec)


def _next_z(cap: dict) -> int:
    cap["_z"] = cap.get("_z", 0) + 1
    return cap["_z"]


def _patched_text(self, xy, text, fill=None, font=None, anchor=None, *args, **kwargs):
    if _CAPTURE.get() is None or not _canvas_sized(self):
        return _orig_text(self, xy, text, fill, font, anchor, *args, **kwargs)
    if isinstance(text, str) and "\n" in text:
        # One layer per line. (No compositor draws multi-line strings — they wrap
        # and draw line by line — but never hand this back to PIL: in Pillow 12
        # multiline_text() calls text() again, which would come straight back
        # here.) Lines are spaced the way PIL spaces them: an "A" plus spacing.
        spacing = float(kwargs.get("spacing", args[0] if args else 4) or 0)
        try:
            step = self.textbbox((0, 0), "A", font=font)[3] + spacing
        except Exception:  # noqa: BLE001
            step = float(getattr(font, "size", 40)) * 1.2
        for i, line in enumerate(text.split("\n")):
            try:
                _record_text(self, (xy[0], xy[1] + i * step), line, fill, font,
                             (anchor or "la")[0] + "a", kwargs.get("stroke_width", 0),
                             kwargs.get("stroke_fill"))
            except Exception:  # noqa: BLE001
                logger.warning("could not record a text layer", exc_info=True)
        return None
    try:
        _record_text(self, xy, text, fill, font, anchor, kwargs.get("stroke_width", 0),
                     kwargs.get("stroke_fill"))
    except Exception:  # noqa: BLE001 — a record that fails is a line the editor rebuilds
        logger.warning("could not record a text layer", exc_info=True)
    return None


def _as_layer(im: Image.Image, mask) -> Image.Image:
    """The pasted picture as PIL would lay it down: through `mask` when there is
    one (its alpha band, or the mask itself as greyscale — the picture's own
    alpha is then ignored), and fully opaque when there is not."""
    rgba = im.convert("RGBA")
    if mask is None:
        rgba.putalpha(255)
        return rgba
    if isinstance(mask, Image.Image):
        m = mask.getchannel("A") if mask.mode in ("RGBA", "LA", "PA") else mask.convert("L")
        if m.size == rgba.size:
            rgba.putalpha(m)
    return rgba


def _patched_paste(self, im, box=None, mask=None):
    cap = _CAPTURE.get()
    if (cap is not None and isinstance(im, Image.Image) and im.info.get(_BADGE)
            and self.size == tuple(cap["canvas"])):
        try:
            x, y = (box[0], box[1]) if box else (0, 0)
            buf = io.BytesIO()
            _as_layer(im, mask).save(buf, format="PNG")
            cap["images"].append({"kind": im.info.get(_BADGE), "png": buf.getvalue(),
                                  "x": int(x), "y": int(y), "w": im.size[0], "h": im.size[1],
                                  "z": _next_z(cap)})
            return None
        except Exception:  # noqa: BLE001
            logger.warning("could not record a badge layer", exc_info=True)
    return _orig_paste(self, im, box, mask)


ImageDraw.ImageDraw.text = _patched_text
Image.Image.paste = _patched_paste


def capture(render: Callable[[], Any], canvas: tuple[int, int]) -> dict:
    """Draw a card in capture mode. `render` is the same call that drew it —
    returning png bytes or (png, kind). Returns {plate, texts, images, canvas}:
    the card without its words and badges, and those as layers."""
    cap: dict = {"texts": [], "images": [], "canvas": [int(canvas[0]), int(canvas[1])]}
    token = _CAPTURE.set(cap)
    try:
        out = render()
    finally:
        _CAPTURE.reset(token)
    plate = out[0] if isinstance(out, tuple) else out
    texts = []
    for i, t in enumerate(cap["texts"]):
        texts.append({k: v for k, v in t.items() if not k.startswith("_")} | {"id": f"t{i}"})
    return {"plate": plate, "texts": texts, "images": cap["images"], "canvas": cap["canvas"]}


# ------------------------------------------------------------ existing cards
#
# Every card already in the queue was drawn before this existed. Its layers are
# captured the first time it is opened in the editor, by drawing it again from
# what the draft keeps — the design spec, the photo it was built on, the brand
# kit — at the size of the picture it actually is, and kept on the draft.


async def _designed_context(tenant_id, hero: bytes | None) -> dict:
    """The brand-level inputs the designed renderer draws with — the same ones
    main._generate_designed_post_image assembles, read the same way."""
    import httpx

    from .brand_kit import get_brand_kit

    kit = dict(await get_brand_kit(tenant_id) or {})
    # The brand-identity palette, merged into the kit exactly as the generator
    # does. Without it every re-drawn card came out in the house blue.
    try:
        from .brand_identity import ensure_brand_palette
        pal = await ensure_brand_palette(tenant_id, kit=kit, hint_image=hero)
        if pal:
            kit["palette"] = pal
    except Exception:  # noqa: BLE001
        pass
    ctx: dict = {"kit": kit, "handle": (kit.get("handle") or "").strip(),
                 "profile_bytes": None, "profile_is_logo": False}
    logo_url = (kit.get("logo_url") or "").strip()
    if logo_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(logo_url)
                r.raise_for_status()
            Image.open(io.BytesIO(r.content)).convert("RGBA")
            ctx["profile_bytes"], ctx["profile_is_logo"] = r.content, True
        except Exception:  # noqa: BLE001 — no raster logo: the card has no badge
            pass
    try:
        from .render_tuning import get_render_tuning
        ctx["tuning"] = await get_render_tuning(tenant_id)
    except Exception:  # noqa: BLE001
        ctx["tuning"] = {}
    try:
        from . import brand_identity as _bi, font_themes as _ftm
        ctx["font_theme"] = _ftm.resolve(await _bi.get_brand_font(tenant_id))
    except Exception:  # noqa: BLE001
        ctx["font_theme"] = None
    try:
        from . import brand_identity as _bil
        ctx["look"] = await _bil.get_brand_look(tenant_id)
    except Exception:  # noqa: BLE001
        ctx["look"] = None
    t = ctx["tuning"] or {}

    def _knob(key: str) -> int:
        try:
            return int(round(float(t.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0
    hexcol = str(t.get("image_text_color_hex") or "").strip()
    tc = _knob("image_text_color")
    ctx["text_color"] = hexcol or ("white" if tc == 1 else "black" if tc == 2 else "")
    ctx["text_bold"] = _knob("image_text_weight") >= 1
    return ctx


async def _photo(tenant_id, key: str) -> bytes | None:
    from .template_clone import _fetch_bytes, _hero_by_key

    if not key:
        return None
    if key.startswith(("http://", "https://")):
        return await _fetch_bytes(key)
    return await _hero_by_key(tenant_id, key)


def _design(payload: dict) -> tuple[dict | None, dict | None, dict | None, str]:
    """(clone_spec, clone_content, image_spec, format) the card was drawn from —
    read from generated_parts too, where a hand edit moves them."""
    from .designed_render import ALL_FORMATS

    gp = payload.get("generated_parts") if isinstance(payload.get("generated_parts"), dict) else {}
    pick = lambda k: payload.get(k) if isinstance(payload.get(k), dict) else gp.get(k)  # noqa: E731
    spec = pick("image_spec")
    fmt = str(payload.get("image_format") or "")
    if fmt not in ALL_FORMATS:          # e.g. "owner_edit" after a hand edit
        fmt = str((spec or {}).get("format") or fmt)
    return pick("clone_spec"), pick("clone_content"), spec, fmt


def takes_photo(payload: dict) -> bool:
    """Does this card's design draw a photo — so another photo can be put in it?"""
    from .designed_render import ALL_FORMATS, needs_photo

    clone_spec, _content, spec, fmt = _design(payload)
    if isinstance(clone_spec, dict) and clone_spec.get("elements"):
        return str((clone_spec.get("background") or {}).get("treatment") or "solid") != "solid"
    return isinstance(spec, dict) and fmt in ALL_FORMATS and needs_photo(fmt)


async def _draw_again(tenant_id, payload: dict, canvas: tuple[int, int], *,
                      hero_override: bytes | None = None) -> dict | None:
    """Capture this card by drawing it again from what it keeps — with its own
    photo, or `hero_override` in its place. None when it cannot be drawn
    faithfully: no spec, or a photo layout whose photo is gone."""
    from . import image_compose
    from .designed_render import ALL_FORMATS, needs_photo, render_designed
    from .spec_render import render_spec
    from . import template_clone

    clone_spec, clone_content, spec, fmt = _design(payload)
    hero = hero_override or await _photo(tenant_id, str(payload.get("hero_photo_key") or ""))

    if isinstance(clone_spec, dict) and clone_spec.get("elements"):
        content = clone_content if isinstance(clone_content, dict) else {}
        palette = None if clone_spec.get("brand_colours") else await template_clone._brand_palette(tenant_id)
        logo = await template_clone._brand_logo(tenant_id) if clone_spec.get("logo_box") else None
        if hero is None and str((clone_spec.get("background") or {}).get("treatment") or "solid") != "solid":
            return None

        def call():
            with image_compose.canvas(*canvas):
                return render_spec(clone_spec, content, hero_bytes=hero, logo_bytes=logo, palette=palette)
        return capture(call, canvas)

    if isinstance(spec, dict) and fmt in ALL_FORMATS:
        if needs_photo(fmt) and hero is None:
            return None
        ctx = await _designed_context(tenant_id, hero)
        spec = dict(spec)
        if not (spec.get("quote") or "").strip():
            text = str(payload.get("content") or payload.get("caption") or payload.get("topic") or "")
            spec["quote"] = text.split(". ")[0].strip()

        def call():
            with image_compose.canvas(*canvas), image_compose.brand_fonts(ctx["font_theme"]), \
                    image_compose.brand_look(ctx["look"]), \
                    image_compose.text_style(ctx["text_color"], ctx["text_bold"]):
                return render_designed(
                    fmt, spec, kit=ctx["kit"], hero_bytes=hero,
                    profile_bytes=ctx["profile_bytes"], profile_is_logo=ctx["profile_is_logo"],
                    handle=ctx["handle"], tuning=ctx["tuning"], palette=ctx["kit"].get("palette"))
        return capture(call, canvas)
    return None


def recompose(cap: dict) -> Image.Image:
    """The plate with every captured layer drawn back where it was recorded —
    what the editor will show before anyone moves anything."""
    from PIL import ImageFont

    base = Image.open(io.BytesIO(cap["plate"])).convert("RGBA")
    d = ImageDraw.Draw(base)
    stack = [("t", t) for t in cap["texts"]] + [("i", im) for im in cap["images"]]
    for kind, layer in sorted(stack, key=lambda e: e[1].get("z", 0)):
        if kind == "i":
            badge = Image.open(io.BytesIO(layer["png"])).convert("RGBA")
            _orig_paste(base, badge, (layer["x"], layer["y"]), badge)
            continue
        t = layer
        try:
            f = ImageFont.truetype(t["font_path"], t["size"])
        except Exception:  # noqa: BLE001
            continue
        stroke = {"stroke_width": t["stroke"], "stroke_fill": t.get("stroke_fill") or t["fill"]} \
            if t.get("stroke") else {}
        if t.get("tracking") and len(t["text"]) > 1:
            x = t["x"]
            for ch in t["text"]:
                _orig_text(d, (x, t["y_la"]), ch, t["fill"], f, None, **stroke)
                x += f.getlength(ch) + t["tracking"]
        else:
            _orig_text(d, (t["x"], t["y_la"]), t["text"], t["fill"], f, None, **stroke)
    return base.convert("RGB")


# How far (mean absolute difference, 0-255) the layers may sit from the real
# picture and still be offered as that picture. A faithful capture lands well
# under 1; a card whose saved spec has drifted from what was drawn (a later
# rewording, a palette change) lands far over — and must not open as a
# different card than the one on the board.
MAX_DRIFT = 2.0


def drift(cap: dict, stored: Image.Image) -> float:
    a = recompose(cap).convert("L")
    b = stored.convert("L")
    if b.size != a.size:
        b = b.resize(a.size)
    pa, pb = a.tobytes(), b.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / max(1, len(pa))


async def ensure_layers(action_id, tenant_id, payload: dict) -> dict | None:
    """This card's layers for the editor — captured once and kept on the draft.

    {version, of, canvas:[w,h], plate_url, texts:[...], images:[{url,x,y,w,h,kind,z}]}
    (every text and image carries z, its place in the stack as drawn), or None
    when the card cannot be captured (the editor then falls back to rebuilding
    it from its ingredients)."""
    from .template_clone import _fetch_bytes

    # The picture these layers must be OF: the card as generated (an owner's
    # save keeps it as original_image_url). Layers of any other picture — a redo
    # drawn on another path, a swapped photo — would open a card that is not the
    # one on the board, so they are captured again instead of served.
    src = str(payload.get("original_image_url") or payload.get("image_url") or "")
    have = payload.get("render_layers")
    if isinstance(have, dict) and have.get("plate_url") and src and have.get("of") == src:
        return have
    img = await _fetch_bytes(src) if src else None
    if not img:
        return None
    try:
        canvas = Image.open(io.BytesIO(img)).size
    except Exception:  # noqa: BLE001
        return None
    try:
        cap = await _draw_again(tenant_id, payload, canvas)
    except Exception:  # noqa: BLE001 — never block the editor on a capture
        logger.warning("could not capture layers for %s", action_id, exc_info=True)
        return None
    if not cap or not cap.get("plate"):
        return None
    try:
        d = drift(cap, Image.open(io.BytesIO(img)))
    except Exception:  # noqa: BLE001
        d = 999.0
    if d > MAX_DRIFT:
        logger.info("layers for %s drift %.2f from the picture — not offered", action_id, d)
        return None

    return await keep(action_id, tenant_id, cap, of=src, drift_=d)


async def _store(tenant_id, cap: dict, *, of: str, drift_: float = 0.0) -> dict:
    """The layers document for a capture — the plate and each badge as files in
    our own storage, the text layers inline."""
    import asyncio

    from .media import storage as media_storage

    tenant = str(tenant_id)
    store = media_storage()
    plate_url, _ = await asyncio.to_thread(store.save, tenant, cap["plate"], "card-plate.png")
    images = []
    for i, im in enumerate(cap["images"]):
        url, _ = await asyncio.to_thread(store.save, tenant, im["png"], f"card-{im['kind']}-{i}.png")
        images.append({"url": url, "kind": im["kind"], "x": im["x"], "y": im["y"],
                       "w": im["w"], "h": im["h"], "z": im.get("z", 0)})
    return {"version": 1, "of": str(of or ""), "canvas": list(cap["canvas"]),
            "plate_url": plate_url, "texts": cap["texts"], "images": images,
            "drift": round(float(drift_), 3)}


async def replate(tenant_id, payload: dict, photo: bytes, canvas: tuple[int, int]) -> dict | None:
    """This card's design drawn again around another photo — "change the
    picture" without losing the scrim, the frame or the colour panel it sits in.

    Returns a layers document (plate, badges, lines) for the editor to take the
    new plate and photo from, or None when the design has no photo to replace.
    Nothing is written to the draft: the owner's save is what changes the card."""
    if not takes_photo(payload):
        return None
    cap = await _draw_again(tenant_id, payload, canvas, hero_override=photo)
    if not cap or not cap.get("plate"):
        return None
    return await _store(tenant_id, cap, of="")


async def keep(action_id, tenant_id, cap: dict, *, of: str, drift_: float = 0.0) -> dict:
    """Store a capture on the draft and return the layers document. `of` is the
    picture they are the layers of (see ensure_layers)."""
    import json

    from .db import acquire

    layers = await _store(tenant_id, cap, of=of, drift_=drift_)
    try:
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                action_id, json.dumps({"render_layers": layers}))
    except Exception:  # noqa: BLE001 — serve it anyway; the next open re-captures
        logger.warning("could not keep the captured layers for %s", action_id, exc_info=True)
    return layers


__all__ = ["capture", "mark_badge", "ensure_layers", "keep", "replate", "takes_photo",
           "recompose", "drift", "MAX_DRIFT"]
