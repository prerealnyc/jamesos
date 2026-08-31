"""The design cloner — reverse-engineer a competitor's post into a REUSABLE,
brand-agnostic template spec our compositor can rebuild with a brand's own
photo + content.

design_eye.py GRADES a post and names its design_dna in loose prose; this goes
further: it reads the pixels and emits a STRUCTURED spec — background treatment,
every text element with a normalized position / size / weight / case / colour
role, and decorations (bars, badges, frames) — precise enough to render "our
version" of that design. It never copies the competitor's words or photo, only
the STRUCTURE.

Two kinds of post fall out of the same call:
  * photo_forward — the design IS the photography (a clean shot, maybe a small
    caption). Our version = the brand's own photo in the same framing/format.
  * graphic_card — a built layout (milestone number, listing card, quote poster).
    Our version = the same structure rebuilt with the brand's content.

Honesty rules mirror design_eye: no key / unreadable image → status 'no_key' /
'failed', never a faked spec. The rubric is FROZEN + versioned so specs stay
comparable.
"""

from __future__ import annotations

import base64
import json
import logging

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger("design_cloner")

CLONE_RUBRIC_VERSION = "v1"
_MODEL = "gpt-4o"

# The roles our renderer knows how to fill from a brand's content. The extractor
# must map each text block it sees to ONE of these (or omit it).
_TEXT_ROLES = ("kicker", "headline", "subhead", "stat", "stat_label", "cta", "byline")
# Decoration primitives the generic renderer can draw.
_DECOR_TYPES = ("bar", "pill", "badge", "frame", "scrim")


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


_SYSTEM = (
    "You are a design systems engineer reverse-engineering ONE Instagram post "
    "(1080x1350, 4:5) into a REUSABLE template another brand could rebuild with "
    "its OWN photo and words. Describe the STRUCTURE only — never transcribe the "
    "brand's actual words or describe the specific photo content; a role and a "
    "position, not the copy.\n\n"
    "First decide the KIND:\n"
    "  photo_forward — the post IS the photograph (a strong image, at most a "
    "small caption/logo). The template is really 'a great photo in this framing'.\n"
    "  graphic_card — a BUILT layout: a big number/milestone, a listing card, a "
    "quote poster, a stat, a titled announcement. The template is the layout.\n\n"
    "Then emit the spec. Use NORMALIZED coordinates (0.0–1.0 of width/height); "
    "origin top-left. Colours as #RRGGBB.\n"
    "  background: {treatment: one of [full_bleed_photo, photo_top, photo_bottom, "
    "photo_side, solid, photo_with_scrim], scrim: one of [none, bottom, top, "
    "full] (a dark gradient for text legibility), photo_box: {x,y,w,h} (where the "
    "photo sits when it is not full-bleed, else null)}.\n"
    "  palette: {bg: #hex (dominant background/panel), accent: #hex (the one "
    "punch colour), ink: #hex (main text colour)}.\n"
    f"  elements: a list of text blocks, each {{role: one of {list(_TEXT_ROLES)}, "
    "box: {x,y,w,h}, align: one of [left,center,right], size: one of "
    "[sm,md,lg,xl,xxl] (xxl = a hero number/word filling much of the width), "
    "weight: one of [regular,bold,black], case: one of [none,upper], color: "
    "#hex}. Map what you SEE to the nearest role; omit decorative noise. A "
    "milestone number is role 'stat' at size 'xxl'.\n"
    f"  decorations: a list, each {{type: one of {list(_DECOR_TYPES)}, box: "
    "{x,y,w,h}, color: #hex}} — a colour bar, a pill/tag behind a kicker, a badge "
    "corner, a photo frame border, or a scrim panel. Omit if none.\n"
    "  logo_box: {x,y,w,h} where a small brand LOGO / wordmark / emblem sits "
    "(usually a corner or centered at top), so we can drop THIS brand's logo in "
    "the same spot — else null. Do NOT also list the logo as a text element.\n"
    "  design_notes: one line on what makes this layout work.\n\n"
    "Return STRICT JSON: {\"kind\": \"photo_forward\"|\"graphic_card\", "
    "\"background\": {...}, \"palette\": {...}, \"elements\": [...], "
    "\"decorations\": [...], \"logo_box\": {...}|null, \"design_notes\": str}. If "
    "the image is unreadable, say so in design_notes and return kind "
    "'photo_forward' with empty elements."
)


def _as_data_uri(image: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def _norm_box(b) -> dict | None:
    """Clamp a {x,y,w,h} box to the 0..1 canvas; drop anything unusable."""
    if not isinstance(b, dict):
        return None
    try:
        x, y, w, h = (float(b.get(k, 0)) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        return None
    x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    w, h = max(0.0, min(1.0, w)), max(0.0, min(1.0, h))
    if w <= 0 or h <= 0:
        return None
    return {"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)}


def _hex(v, default: str) -> str:
    s = str(v or "").strip()
    if s.startswith("#") and len(s) in (4, 7):
        return s
    return default


def _sanitize(out: dict) -> dict:
    """Coerce the model's spec into the renderer's vocabulary — clamp boxes, keep
    only known roles/decorations/enums — so a downstream renderer can trust it."""
    kind = out.get("kind") if out.get("kind") in ("photo_forward", "graphic_card") else "photo_forward"
    bg = out.get("background") or {}
    treatment = bg.get("treatment") if bg.get("treatment") in (
        "full_bleed_photo", "photo_top", "photo_bottom", "photo_side", "solid", "photo_with_scrim"
    ) else "full_bleed_photo"
    scrim = bg.get("scrim") if bg.get("scrim") in ("none", "bottom", "top", "full") else "none"
    pal = out.get("palette") or {}
    palette = {"bg": _hex(pal.get("bg"), "#111318"),
               "accent": _hex(pal.get("accent"), "#c9a24b"),
               "ink": _hex(pal.get("ink"), "#ffffff")}

    elements = []
    for e in (out.get("elements") or []):
        if not isinstance(e, dict) or e.get("role") not in _TEXT_ROLES:
            continue
        box = _norm_box(e.get("box"))
        if not box:
            continue
        elements.append({
            "role": e["role"], "box": box,
            "align": e.get("align") if e.get("align") in ("left", "center", "right") else "left",
            "size": e.get("size") if e.get("size") in ("sm", "md", "lg", "xl", "xxl") else "md",
            "weight": e.get("weight") if e.get("weight") in ("regular", "bold", "black") else "bold",
            "case": e.get("case") if e.get("case") in ("none", "upper") else "none",
            "color": _hex(e.get("color"), palette["ink"]),
        })

    decorations = []
    for d in (out.get("decorations") or []):
        if not isinstance(d, dict) or d.get("type") not in _DECOR_TYPES:
            continue
        box = _norm_box(d.get("box"))
        if not box:
            continue
        decorations.append({"type": d["type"], "box": box, "color": _hex(d.get("color"), palette["accent"])})

    return {
        "status": "ok",
        "rubric_version": CLONE_RUBRIC_VERSION,
        "kind": kind,
        "background": {"treatment": treatment, "scrim": scrim, "photo_box": _norm_box(bg.get("photo_box"))},
        "palette": palette,
        "elements": elements,
        "decorations": decorations,
        "logo_box": _norm_box(out.get("logo_box")),
        "design_notes": str(out.get("design_notes") or "").strip(),
    }


async def extract_template_spec(image: bytes | str, *, mime: str = "image/jpeg") -> dict:
    """Read ONE competitor post image → a structured, renderable template spec.

    `image` is bytes (preferred) or an https URL. Returns the sanitized spec
    {status, kind, background, palette, elements, decorations, design_notes};
    status is 'ok', 'no_key', or 'failed' — never a faked spec."""
    client = _client()
    if client is None:
        return {"status": "no_key", "rubric_version": CLONE_RUBRIC_VERSION}
    url = _as_data_uri(bytes(image), mime) if isinstance(image, (bytes, bytearray)) else str(image)
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Reverse-engineer this post into a reusable template spec."},
                    {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
                ]},
            ],
            max_tokens=1100,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — reported, never faked
        return {"status": "failed", "rubric_version": CLONE_RUBRIC_VERSION, "error": str(exc)[:200]}
    return _sanitize(out)


__all__ = ["extract_template_spec", "CLONE_RUBRIC_VERSION"]
