"""Caption presets for burned-in spoken-word subtitles.

The current default (a Montserrat 700 phrase inside an rgba black box)
reads as "PowerPoint exported a video". The viral reference videos the
user pulled in all use one of two patterns:

  * TikTok / Reels style — heavy condensed sans-serif (Anton, Bangers,
    Komika Axis), bright yellow fill with a thick black stroke, NO
    background box, 2-3 words at a time, mid-screen vertical so it
    doesn't obscure the speaker's face.
  * Editorial / minimalist — semi-bold Inter or SF Pro, white fill,
    soft drop shadow, NO background box, lower-third, 2-4 words at a
    time, with one "emphasis word" per flash rendered bolder.

Both patterns share: no background box, larger text than ours, tighter
word grouping, position that respects the subject's face.

This module is just the *config* — the Creatomate source builders in
assembly.py read these preset dicts to emit text elements. New presets
slot in by adding an entry below.

Every preset must keep the same shape (the builders read keys
defensively but unknown values are silently ignored). Comments alongside
each field explain why we picked that value rather than just listing it.
"""

from __future__ import annotations

# All Google Fonts — Creatomate downloads them by family name at render
# time, no upload step needed.
CAPTION_PRESETS: dict[str, dict] = {
    "tiktok_yellow": {
        # The "8M Views Viral Video Hack" pattern. Highest engagement on
        # TikTok and Reels because the yellow-on-black-stroke combo reads
        # at thumbnail scale and against any background.
        "label": "TikTok yellow",
        "description": "Bold yellow karaoke with black stroke. Reels / TikTok energy.",
        "font_family": "Anton",
        "font_weight": "400",          # Anton is single-weight condensed
        "font_size_vh": 9.0,            # large — these read on a phone
        "fill_color": "#FFE600",
        "stroke_color": "#000000",
        "stroke_width": "0.4 vh",
        "shadow_color": "rgba(0,0,0,0.35)",
        "shadow_blur": "0.6 vh",
        "shadow_x": "0.2 vh",
        "shadow_y": "0.2 vh",
        "background_color": "transparent",  # no box, stroke does the work
        "y_position": "56%",            # mid-screen, below the talking head
        "x_alignment": "50%",
        "transform": "uppercase",       # ALL CAPS — matches viral norm
        "letter_spacing": "0.5%",
    },
    "clean_white": {
        # The "decades come off the calendar" / "so loud that" pattern.
        # Quieter, more editorial. Best for thoughtful or narrative scripts.
        "label": "Clean white",
        "description": "Minimal white with drop shadow. Editorial / story-driven.",
        "font_family": "Inter",
        "font_weight": "700",
        "font_size_vh": 6.0,
        "fill_color": "#FFFFFF",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.8)",
        "shadow_blur": "1.4 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "75%",            # lower-third
        "x_alignment": "50%",
        "transform": "none",            # mixed-case for narrative feel
        "letter_spacing": "0",
    },
    "bold_pop": {
        # Compromise between yellow-karaoke and clean white. White text,
        # thick black stroke (no box) — looks like Mr Beast / Alex Hormozi
        # captions. Safe default for most personal-brand reels.
        "label": "Bold pop",
        "description": "White with thick black stroke. Safe, universal Reels look.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 7.5,
        "fill_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": "0.5 vh",
        "shadow_color": "rgba(0,0,0,0.5)",
        "shadow_blur": "0.6 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "60%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0.5%",
    },
    "subtle_minimal": {
        # LinkedIn / institutional. Quiet enough to not steal focus from
        # the spoken word — for posts where the script is the substance
        # and captions are just an accessibility layer.
        "label": "Subtle minimal",
        "description": "Small clean light gray. LinkedIn / institutional.",
        "font_family": "Inter",
        "font_weight": "600",
        "font_size_vh": 4.5,
        "fill_color": "#F5F5F5",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.7)",
        "shadow_blur": "1.2 vh",
        "shadow_x": "0",
        "shadow_y": "0.2 vh",
        "background_color": "transparent",
        "y_position": "85%",            # bottom band — out of the way
        "x_alignment": "50%",
        "transform": "none",
        "letter_spacing": "0",
    },
    "branded_red": {
        # PreReal-aligned. White text, red stroke (PreReal Capital red),
        # heavy weight. For when the brand wants to feel like its own
        # signature thing rather than a generic Reel.
        "label": "Branded red",
        "description": "White with PreReal red stroke. Signature brand look.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 7.0,
        "fill_color": "#FFFFFF",
        "stroke_color": "#C8102E",
        "stroke_width": "0.5 vh",
        "shadow_color": "rgba(0,0,0,0.45)",
        "shadow_blur": "0.6 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "62%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "1%",
    },
}

DEFAULT_CAPTION_STYLE = "clean_white"
AUTO_PICK_KEY = "auto"      # frontend sentinel meaning "let the LLM pick"


# ── safe-zone layout ──────────────────────────────────────────────────
#
# Every visible element (caption, logo, badge) declares the vertical band
# it wants to occupy, and the layout helper guarantees they don't collide
# with the BEAT's primary visual zone.
#
# A beat is one of:
#   "avatar" — James talking on camera. His face is roughly 25-50% from
#              top, hands/torso 50-78%. Captions must NOT land there.
#   "broll"  — AI photoreal still. The focal subject varies per image,
#              but composition tends to centre-of-mass around 40-60%.
#              Lower-third or upper-headroom both work.
#   "default"— pre-mix modes that don't carry per-beat role data.
#
# Each beat type gets a list of "safe bands" — (y_pos, max_height_vh)
# tuples — ranked from preferred to fallback. Overlays pick the first
# band that fits them.

SAFE_ZONES: dict[str, list[tuple[str, float]]] = {
    "avatar": [
        # Bottom band — below body, above the platform UI safe area
        # (TikTok / Reels usually overlay UI bottom 8%).
        ("88%", 11.0),
        # Top band — above James's head, useful for HOOK-style large
        # captions when the avatar is framed lower.
        ("8%",  9.0),
    ],
    "broll": [
        # Lower-third — classic editorial position, works against most
        # photoreal compositions where the subject is mid-screen.
        ("72%", 18.0),
        # Mid-screen — fallback when the still has empty middle (rare).
        ("55%", 12.0),
    ],
    "default": [
        ("75%", 18.0),
        ("60%", 12.0),
    ],
}


def caption_y_for_role(preset: dict, role: str) -> str:
    """Pick the caption y for this beat's role.

    Logic: each preset defines its preferred y in its own dict. If the
    beat is an avatar beat AND the preset's preferred y would land in
    the avatar's visual zone (25-78%), we override to the role's safe
    band. B-roll beats trust the preset's preferred y because photoreal
    stills are composed differently.
    """
    preferred = preset.get("y_position", "75%")
    # Parse "NN%" → int. Robust to whitespace.
    try:
        pref_pct = int(str(preferred).strip().rstrip("%"))
    except (TypeError, ValueError):
        pref_pct = 75
    if role == "avatar":
        # James's visual band — refuse to overlap.
        AVATAR_NO_GO = (25, 78)
        # Caption height ~7vh for our presets, so we check the centre.
        centre = pref_pct + 3
        if AVATAR_NO_GO[0] <= centre <= AVATAR_NO_GO[1]:
            # Take the first safe band the preset can fit in (it always
            # fits in 11vh; preset font sizes are 4.5-9 vh).
            return SAFE_ZONES["avatar"][0][0]
    # Default / broll: trust the preset.
    return preferred


def get_preset(name: str | None) -> dict:
    """Resolve a preset name to its config. Unknown names fall back to
    the default — the caller always gets a usable dict, never a KeyError
    mid-render."""
    if not name or name == AUTO_PICK_KEY:
        return CAPTION_PRESETS[DEFAULT_CAPTION_STYLE]
    return CAPTION_PRESETS.get(name, CAPTION_PRESETS[DEFAULT_CAPTION_STYLE])


def list_presets() -> list[dict]:
    """Surface for the UI selector — name + label + description per
    preset, no internal Creatomate fields."""
    return [
        {"name": name, "label": p["label"], "description": p["description"]}
        for name, p in CAPTION_PRESETS.items()
    ]


def caption_element(
    *, text: str, start: float, end: float, preset: dict, track: int = 3,
    role: str = "default",
) -> dict:
    """Build a single Creatomate text element from a preset.

    `role` is the beat's role at this timestamp ("avatar" | "broll" |
    "default"). When a caption falls on an avatar beat, the y is
    overridden to the avatar safe-zone (bottom band) so it can't
    overlap James's face — this is the layout fix the user flagged.

    Only emits the fields the preset actually configures so we don't
    override Creatomate defaults with empty strings (which it interprets
    as "remove this property" — a subtle bug we hit on an earlier
    iteration).
    """
    elem: dict = {
        "type": "text",
        "text": text,
        "track": track,
        "time": start,
        "duration": max(0.2, end - start),
        "width": "86%",
        "y": caption_y_for_role(preset, role),
        "x_alignment": preset["x_alignment"],
        "font_family": preset["font_family"],
        "font_weight": preset["font_weight"],
        "font_size": f"{preset['font_size_vh']} vh",
        "fill_color": preset["fill_color"],
    }
    if preset.get("stroke_color") and preset["stroke_color"] != "transparent":
        elem["stroke_color"] = preset["stroke_color"]
        elem["stroke_width"] = preset["stroke_width"]
    if preset.get("shadow_color"):
        elem["shadow_color"] = preset["shadow_color"]
        elem["shadow_blur"] = preset["shadow_blur"]
        elem["shadow_x"] = preset["shadow_x"]
        elem["shadow_y"] = preset["shadow_y"]
    if preset.get("background_color") and preset["background_color"] != "transparent":
        elem["background_color"] = preset["background_color"]
    if preset.get("transform") == "uppercase":
        elem["text_transform"] = "uppercase"
    if preset.get("letter_spacing") and preset["letter_spacing"] != "0":
        elem["letter_spacing"] = preset["letter_spacing"]
    return elem


__all__ = [
    "CAPTION_PRESETS", "DEFAULT_CAPTION_STYLE", "AUTO_PICK_KEY",
    "SAFE_ZONES",
    "get_preset", "list_presets", "caption_element", "caption_y_for_role",
]
