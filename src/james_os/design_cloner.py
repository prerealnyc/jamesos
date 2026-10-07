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

from . import spend
from .config import settings

logger = logging.getLogger("design_cloner")

# v2 added photo_boxes: a layout may have SEVERAL photo regions. Bumped rather
# than edited in place so a v1 spec is still readable as what it was — a read
# taken when the vocabulary had no word for a collage.
#
# PLACEMENT, not wording, decided whether this worked. The first v2 described
# photo_boxes only inside the `background` field and left it out of the output
# schema below; run over 24 real monitoring images it found ZERO collages, four
# of which were plainly several photographs. Naming the count as the FIRST thing
# to decide, and listing photo_boxes in the schema, took that to 2 of 4 with 0
# false positives on 7 single-photograph posts (measured 2026-09-30). The other
# two — a three-photo strip and a two-panel stack — are still missed.
CLONE_RUBRIC_VERSION = "v2"
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
    "First decide the KIND. Before anything else, count how many SEPARATE "
    "PHOTOGRAPHS the post contains — a post is very often several "
    "photographs arranged together (a collage, a grid, a strip, a "
    "before/after, framed cutouts), which is extremely common in social "
    "advertising. When it is, you MUST return background.photo_boxes with "
    "one box per photograph.\n\n"
    "The KIND:\n"
    "  photo_forward — the post IS the photograph (a strong image, at most a "
    "small caption/logo). The template is really 'a great photo in this framing'.\n"
    "  graphic_card — a BUILT layout: a big number/milestone, a listing card, a "
    "quote poster, a stat, a titled announcement. The template is the layout.\n\n"
    "Then emit the spec. Use NORMALIZED coordinates (0.0–1.0 of width/height); "
    "origin top-left. Colours as #RRGGBB.\n"
    "  background: {treatment: one of [full_bleed_photo, photo_top, photo_bottom, "
    "photo_side, solid, photo_with_scrim], scrim: one of [none, bottom, top, "
    "full] (a dark gradient for text legibility), photo_box: {x,y,w,h} (where the "
    "photo sits when it is not full-bleed, else null), photo_boxes: a LIST of "
    "{x,y,w,h} when the post shows SEVERAL photographs — a collage, a grid, a "
    "before/after split, a set of framed or overlapping pictures. List them in "
    "reading order, top-left first. This is common and important: do NOT flatten "
    "a four-photo collage into one full-bleed box. Give photo_boxes ONLY for "
    "genuinely separate pictures, not for one photo with shapes drawn over it; "
    "omit it entirely for a single-photograph post.}\n"
    "  palette: {bg: #hex (dominant background/panel), accent: #hex (the one "
    "punch colour), ink: #hex (main text colour)}.\n"
    f"  elements: a list of text blocks, each {{role: one of {list(_TEXT_ROLES)}, "
    "treatment: one of [none,outline,extrude,shadow] — how the glyphs are "
    "FINISHED. extrude for block letters with offset depth behind them, "
    "outline for a deliberate thick contrasting edge, shadow for a single "
    "soft offset, none for flat type. This is a lot of what makes a card "
    "look designed rather than typed, so read it carefully; say none when "
    "the type is genuinely flat, "
    "face: one of [display,body] — DISPLAY for the blocks set in the loud "
    "attention-grabbing face (the headline, a big number, a shouted label), "
    "BODY for the quieter supporting face. Judge the CONTRAST you see between "
    "the blocks, not the specific typeface: we never reuse their font, only "
    "which blocks they chose to shout with. If every block is the same face, "
    "say display for the largest and body for the rest, "
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
    "\"background\": {\"treatment\": ..., \"scrim\": ..., \"photo_box\": "
    "...|null, \"photo_boxes\": [{x,y,w,h}, ...] (omit entirely for a single "
    "photograph)}, \"palette\": {...}, \"elements\": [...], "
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


def _photo_boxes(bg: dict) -> dict:
    """The collage regions, cleaned — or nothing at all.

    Returns `{}` rather than `{"photo_boxes": []}` when there is nothing to say,
    so a single-photograph spec is byte-identical to what v1 produced and the
    renderer's one-frame path is reached by absence rather than by an empty list.

    A single-entry list is dropped for the same reason: one region IS the
    existing photo_box, and carrying both invites them to disagree.
    """
    raw = bg.get("photo_boxes")
    if not isinstance(raw, list) or len(raw) < 2:
        return {}
    boxes = [b for b in (_norm_box(x) for x in raw[:6]) if b]
    return {"photo_boxes": boxes} if len(boxes) >= 2 else {}


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
            # Which of the BRAND's two faces this block takes. Not the
            # competitor's typeface — never that; the brand's own display/body
            # pair is its identity and must win. What is borrowed is which
            # blocks the reference chose to shout with, which the renderer
            # previously guessed from `weight` alone and so lost entirely: a
            # light geometric headline over a heavy condensed stat rendered
            # exactly like the reverse.
            "face": e.get("face") if e.get("face") in ("display", "body") else None,
            # How the glyphs are finished. None when unstated, so the 454 specs
            # learned before this vocabulary existed keep the thin default
            # stroke rather than inheriting a treatment nobody read.
            "treatment": e.get("treatment") if e.get("treatment") in (
                "none", "outline", "extrude", "shadow") else None,
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
        "background": {"treatment": treatment, "scrim": scrim,
                       "photo_box": _norm_box(bg.get("photo_box")),
                       **_photo_boxes(bg)},
        "palette": palette,
        "elements": elements,
        "decorations": decorations,
        "logo_box": _norm_box(out.get("logo_box")),
        "design_notes": str(out.get("design_notes") or "").strip(),
    }


_COPY_SYSTEM = (
    "You are reading OUR OWN finished social card to recover the words that are "
    "printed on it, so they can be re-rendered unchanged.\n\n"
    "Return STRICT JSON mapping each role you are given to the text that appears "
    "in that position on the card, transcribed EXACTLY — same words, same case, "
    "same punctuation. Do not improve, shorten, or re-title anything.\n"
    "Omit a role you cannot see text for. Ignore the logo, the website, and any "
    "handle in the footer rail."
)


async def read_card_copy(image: bytes | str, roles: list[str], *,
                         mime: str = "image/png") -> dict:
    """Transcribe the copy off a card WE rendered, role by role.

    extract_template_spec deliberately reads only STRUCTURE — it exists to learn
    a competitor's layout without lifting their words. That is right for a clone
    and wrong for a rebuild: recovering our own card's template but not its text
    means a redo re-writes every line, which is what "keep everything the same"
    was asking us not to do. Reading our own card back has no such constraint.

    Returns {role: text}; empty on any failure, which the caller treats as "no
    copy recovered" rather than as empty copy."""
    client = _client()
    if client is None or not roles:
        return {}
    if isinstance(image, bytes):
        url = f"data:{mime};base64," + base64.b64encode(image).decode()
    else:
        url = str(image)
    try:
        res = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _COPY_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text",
                     "text": "Transcribe the text for these roles: " + ", ".join(roles)},
                    {"type": "image_url", "image_url": {"url": url}},
                ]},
            ],
            response_format={"type": "json_object"},
            max_tokens=600,
            temperature=0.0,
        )
        await spend.record_tokens("openai", getattr(res, "model", "") or _MODEL, getattr(res, "usage", None), "design_cloner.read_copy")
        out = json.loads(res.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 — a failed read is "nothing recovered"
        logger.warning("could not read the card copy back", exc_info=True)
        return {}
    if not isinstance(out, dict):
        return {}
    return {r: str(out[r]).strip() for r in roles
            if isinstance(out.get(r), str) and str(out[r]).strip()}


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
        await spend.record_tokens("openai", getattr(resp, "model", "") or _MODEL, getattr(resp, "usage", None), "design_cloner.extract_template_spec")
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — reported, never faked
        return {"status": "failed", "rubric_version": CLONE_RUBRIC_VERSION, "error": str(exc)[:200]}
    return _sanitize(out)


__all__ = ["extract_template_spec", "CLONE_RUBRIC_VERSION"]
