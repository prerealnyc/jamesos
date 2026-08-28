"""Live-tunable render knobs, per tenant, stored in tenants.config['render_tuning'].

Read at render time so a feedback-driven tweak (e.g. "B-roll inserts too
short" → longer) takes effect on the NEXT production with no deploy. Mirrors
the autopilot config-slot pattern. Values are clamped on write to safe ranges
so a bad/auto value can never break rendering.

KNOBS below is the single source of truth: it carries each knob's default,
range, unit and a plain-English description. render clamping AND the feedback
interpreter's catalog both derive from it, so a knob added here is instantly
something the interpreter can recognise and set — there is no second list to
keep in sync.
"""

import json
from uuid import UUID

from .db import acquire

# key → default / min / max / unit / help.
#
# `help` is shown verbatim to the feedback interpreter, so it is written as the
# thing a human would complain about, not as an implementation detail.
KNOBS: dict[str, dict] = {
    # ── video: engaging-avatar B-roll inserts ──
    "broll_insert_min_dur": {
        "default": 1.5, "min": 1.0, "max": 4.0, "unit": "seconds",
        "help": "B-roll insert MIN on-screen seconds.",
    },
    "broll_insert_max_dur": {
        "default": 2.0, "min": 1.0, "max": 4.0, "unit": "seconds",
        "help": "B-roll insert MAX on-screen seconds.",
    },
    # ── image: on-image TEXT styling (applies to EVERY layout) ──
    # These are the knobs that answer "the text should be white / is unreadable
    # black" and "the font is too thin" — a styling correction the interpreter can
    # now APPLY instead of only logging. Default 0 = the shipped auto behaviour.
    "image_text_color": {
        "default": 0, "min": 0, "max": 2, "unit": "0=auto 1=white 2=black",
        "help": ("ON-IMAGE TEXT COLOUR (every layout). 0 = auto (pick for contrast). "
                 "1 = force WHITE / light text. 2 = force BLACK / dark text. Use 1 when "
                 "the owner says the text should be white, or that black/dark text is "
                 "unreadable on the photo; use 2 when they want it black/dark. The scrim "
                 "adapts, so forced white stays readable."),
    },
    "image_text_weight": {
        "default": 0, "min": 0, "max": 1, "unit": "0=normal 1=bolder",
        "help": ("ON-IMAGE TEXT WEIGHT (every layout). 0 = normal. 1 = BOLDER / thicker "
                 "strokes. Use 1 when the owner says the font is too thin and wants "
                 "thicker, bolder or heavier text."),
    },
    # ── image: the designed post card (hero_quote layout) ──
    # Defaults are the literals these replaced in image_compose.hero_quote_card,
    # so an empty slot renders exactly as before.
    "image_photo_width": {
        "default": 0.46, "min": 0.34, "max": 0.62, "unit": "fraction of card width",
        "help": ("HERO-QUOTE LAYOUT ONLY (photo beside the quote): how much of the "
                 "card the person's photo occupies. RAISE when he is too small, "
                 "hidden, cropped or hard to see."),
    },
    "image_text_gutter": {
        "default": 48.0, "min": 24.0, "max": 160.0, "unit": "pixels",
        "help": ("HERO-QUOTE LAYOUT ONLY: clear gap between the text column and "
                 "the photo. RAISE when the text crowds, touches or covers him."),
    },
    "image_quote_max_pt": {
        "default": 86.0, "min": 48.0, "max": 110.0, "unit": "points",
        "help": ("HERO-QUOTE LAYOUT ONLY: largest on-image quote type size. LOWER "
                 "when the text is too big, shouty, or takes over the image."),
    },
    # Horizontal, not vertical, deliberately: the photo panel is tall and narrow
    # (~0.37 aspect), so a normal photo is always cropped left/right to cover it
    # — the vertical centering is never reached. A focus_y knob measured as
    # having NO effect on a rendered card, which is the whole failure this
    # catalog exists to avoid.
    "image_photo_focus_x": {
        "default": 0.5, "min": 0.0, "max": 1.0, "unit": "fraction from left",
        "help": ("HERO-QUOTE LAYOUT ONLY: which part of the photo stays in frame "
                 "left-to-right — 0 keeps the left edge, 1 the right. Move it when "
                 "he is cut off at the side or the crop lands wrong."),
    },
    # How much of the photo's inner edge dissolves into the navy. At the default
    # 0.40 the photo is only fully opaque across its RIGHT ~26% — the left 40%
    # is transparent and the next 34% ramps up — which reads as "his face is
    # faded / washed out / blurred into the background". LOWER keeps far more of
    # him crisp. This changes the FADE, not the photo's own focus: a genuinely
    # out-of-focus source photo is a picker problem (sharpness gate), not this.
    "image_photo_fade": {
        "default": 0.40, "min": 0.08, "max": 0.55, "unit": "fraction of photo panel",
        "help": ("HERO-QUOTE LAYOUT ONLY: how much of the photo's inner edge fades "
                 "into the navy background. LOWER when his face looks faded, washed "
                 "out, soft at the edge, half-dissolved, or blended into the "
                 "background — it keeps more of him crisp and clearly visible. "
                 "(This reduces the fade only; it cannot sharpen a genuinely "
                 "out-of-focus or low-resolution photo.)"),
    },
}

# Defaults match the existing code literals, so an empty slot = exactly today's
# behavior. Derived from KNOBS so the two can never disagree.
DEFAULT_RENDER_TUNING = {k: v["default"] for k, v in KNOBS.items()}


async def get_render_tuning(tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    rt = (cfg or {}).get("render_tuning", {}) or {}
    return {**DEFAULT_RENDER_TUNING, **rt}


def _clamp(updates: dict) -> dict:
    """Clamp each knob to ITS OWN range.

    This used to clamp every knob to the B-roll duration bounds (1.0-4.0),
    which was harmless while durations were the only knobs and silently
    destructive the moment anything else existed — a photo width of 0.46 would
    have been "clamped" up to 1.0, i.e. a full-bleed photo, on the first
    feedback that touched it."""
    out: dict = {}
    for k, v in updates.items():
        spec = KNOBS.get(k)
        if spec is None:
            continue  # allow-list: never store an unknown knob
        try:
            out[k] = max(spec["min"], min(float(v), spec["max"]))
        except (TypeError, ValueError):
            continue
    return out


async def set_render_tuning(updates: dict, tenant_id: UUID | None = None) -> dict:
    merged = {**(await get_render_tuning(tenant_id)), **_clamp(updates)}
    # keep min <= max
    if merged["broll_insert_min_dur"] > merged["broll_insert_max_dur"]:
        merged["broll_insert_min_dur"] = merged["broll_insert_max_dur"]
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{render_tuning}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(merged),
        )
    return merged


__all__ = ["KNOBS", "DEFAULT_RENDER_TUNING", "get_render_tuning", "set_render_tuning"]
