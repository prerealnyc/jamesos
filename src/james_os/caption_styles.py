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
        "font_weight": "800",          # heavier for presence
        "font_size_vh": 6.8,           # bigger so it carries on a phone
        "fill_color": "#FFFFFF",
        "stroke_color": "#0A0A0A",     # thin dark edge → legible on any bg
        "stroke_width": "0.22 vh",
        "shadow_color": "rgba(0,0,0,0.85)",
        "shadow_blur": "1.2 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "75%",            # lower-third
        "pop_in": False,
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
        "font_size_vh": 8.0,           # bigger, MrBeast-scale
        "fill_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": "0.6 vh",      # thicker outline, more punch
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
    "karaoke": {
        # Word-by-word reveal — ONE word flashes center-screen at a time, timed
        # to the speech (78.6% of viral clips use word-level animation). The
        # actual per-word rendering lives in karaoke_elements(); this entry lets
        # the style be picked + gives the words their look.
        "label": "Karaoke (word-by-word)",
        "description": "One word at a time, popping to the beat. Max retention.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 10.0,          # single word → go big
        "fill_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": "0.7 vh",
        "shadow_color": "rgba(0,0,0,0.5)",
        "shadow_blur": "0.6 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "58%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0.5%",
        "pop_in": True,
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
        "y_position": "78%",            # low band — inside the safe zone
        "pop_in": False,
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
        "font_size_vh": 7.6,
        "fill_color": "#FFFFFF",
        "stroke_color": "#C8102E",
        "stroke_width": "0.6 vh",
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
    "karaoke_green": {
        # Sibling of tiktok_yellow — same condensed punch, electric green.
        # Pops hard on real-estate / outdoor footage where yellow can wash out.
        "label": "Karaoke green",
        "description": "Electric-green Anton with black stroke. TikTok energy, alt to yellow.",
        "font_family": "Anton",
        "font_weight": "400",
        "font_size_vh": 9.0,
        "fill_color": "#00E676",
        "stroke_color": "#06210F",
        "stroke_width": "0.42 vh",
        "shadow_color": "rgba(0,0,0,0.4)",
        "shadow_blur": "0.6 vh",
        "shadow_x": "0.2 vh",
        "shadow_y": "0.2 vh",
        "background_color": "transparent",
        "y_position": "56%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0.5%",
    },
    "magenta_blocks": {
        # The Serhant/SXSW street-interview look: statements as solid COLOR
        # BLOCKS. Hook = white uppercase on a hot-magenta box; running
        # captions = magenta uppercase on a black box, lower in frame.
        # Fields below describe the BODY phase (and the fallback rendering).
        "label": "Magenta blocks",
        "description": "White-on-magenta box hook, then magenta-on-black box captions. Street-interview pop.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 4.6,
        "fill_color": "#FF00C8",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "",
        "shadow_blur": "0",
        "shadow_x": "0",
        "shadow_y": "0",
        "background_color": "#000000",
        "y_position": "70%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0",
    },
    "magenta_white": {
        # Split-reel variant of magenta_blocks: magenta uppercase on a WHITE
        # box (instead of black). Rendered per-phrase via the standard caption
        # path (NOT a designer builder), so it honors this background_color.
        "label": "Magenta on white",
        "description": "Magenta uppercase on a white box — the split-reel caption look.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 4.6,
        "fill_color": "#FF00C8",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "",
        "shadow_blur": "0",
        "shadow_x": "0",
        "shadow_y": "0",
        "background_color": "#FFFFFF",
        "y_position": "50%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0",
    },
    "editorial_serif": {
        # Magazine title-card look: a small white uppercase sans kicker
        # ("WORLD'S FIRST") over huge YELLOW ITALIC SERIF stacked lines
        # ("Agentic / Video / Editor"). Body captions stay yellow italic
        # serif at a readable size.
        "label": "Editorial serif",
        "description": "Small white kicker + huge yellow italic serif title, then yellow serif captions.",
        "font_family": "Playfair Display",
        "font_weight": "700",
        "font_size_vh": 5.0,
        "fill_color": "#F2E73B",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.55)",
        "shadow_blur": "1.0 vh",
        "shadow_x": "0",
        "shadow_y": "0.25 vh",
        "background_color": "transparent",
        "y_position": "60%",
        "x_alignment": "50%",
        "transform": "none",
        "letter_spacing": "0",
        "pop_in": False,
    },
    "gradient_mint": {
        # The 'Sales reps / don't need / more tools.' ad look: big lowercase
        # rounded sans in a pale mint, phrases SCATTERED across the frame
        # (top-left → right → centre, cycling per flash). Mint is a flat
        # approximation of the reference's white→green gradient.
        "label": "Mint scatter",
        "description": "Big lowercase mint phrases scattered around the frame. Premium ad look.",
        "font_family": "Poppins",
        "font_weight": "800",
        "font_size_vh": 6.6,
        "fill_color": "#BFF2DC",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.35)",
        "shadow_blur": "0.8 vh",
        "shadow_x": "0",
        "shadow_y": "0.2 vh",
        "background_color": "transparent",
        "y_position": "60%",
        "x_alignment": "50%",
        "transform": "none",
        "letter_spacing": "0",
    },
    "viral_hook": {
        # Two-phase "viral hook" pattern (the 'HOW TO GET / THIS QUALITY /
        # IN YOUR VIDEOS' reel): the FIRST ~3s render as a huge stacked
        # 3-line title — white / YELLOW key line (bigger) / white, heavy
        # Montserrat, centered mid-frame — then captions drop to this small,
        # clean, mixed-case white body style for the rest of the video.
        # The stacked hook itself is built by viral_hook_elements(); the
        # fields below describe the BODY phase (and act as a sane fallback
        # anywhere that renders this preset as a plain caption).
        "label": "Viral hook",
        "description": "Huge stacked hook title first (~3s, key line yellow), then small clean white captions.",
        "font_family": "Montserrat",
        "font_weight": "600",
        "font_size_vh": 4.8,
        "fill_color": "#FFFFFF",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.75)",
        "shadow_blur": "1.1 vh",
        "shadow_x": "0",
        "shadow_y": "0.25 vh",
        "background_color": "transparent",
        "y_position": "57%",            # mid-frame, chest height (reference)
        "x_alignment": "50%",
        "transform": "none",            # body is mixed-case ("that good")
        "letter_spacing": "0",
    },
    "highlight_box": {
        # The trending "word box" look — white text on a solid dark pill.
        # Premium, ultra-legible on ANY background (busy B-roll included)
        # because the box guarantees contrast. No stroke needed.
        "label": "Highlight box",
        "description": "White text on a solid dark box. Clean, premium, reads on any footage.",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size_vh": 6.6,
        "fill_color": "#FFFFFF",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.35)",
        "shadow_blur": "0.8 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "rgba(10,10,12,0.82)",  # solid pill behind the text
        "y_position": "58%",
        "x_alignment": "50%",
        "transform": "uppercase",
        "letter_spacing": "0.5%",
    },
    "cinematic_scatter": {
        # Kinetic editorial style — words scattered down the SIDES framing the
        # subject, serif + gold-italic accents (the cinematic-YouTuber look).
        # Built by cinematic_scatter_elements(); the fields below are the BODY
        # fallback (and what list_presets surfaces for the UI selector).
        "label": "Cinematic scatter",
        "description": "Words framing the subject — serif + gold italic accents, scattered to the sides.",
        "font_family": "Playfair Display",
        "font_weight": "700",
        "font_size_vh": 5.2,
        "fill_color": "#FFFFFF",
        "stroke_color": "transparent",
        "stroke_width": "0",
        "shadow_color": "rgba(0,0,0,0.6)",
        "shadow_blur": "1.1 vh",
        "shadow_x": "0",
        "shadow_y": "0.3 vh",
        "background_color": "transparent",
        "y_position": "55%",
        "x_alignment": "50%",
        "transform": "none",
        "letter_spacing": "0",
        "pop_in": False,
    },
}

DEFAULT_CAPTION_STYLE = "clean_white"
AUTO_PICK_KEY = "auto"      # frontend sentinel meaning "let the LLM pick"


# ── platform safe zone (creator guidance, 2026-06-13) ────────────────
# Instagram / TikTok UI covers the very top, very bottom, and the side
# rails. Usable band: y 12-86%, x 12-88%. Rules from the reference:
#   * title / hook → TOP of the safe zone, bigger font
#   * subtitle     → near the CHIN (~55%) so viewers watch the face
#   * never park text in the old bottom band (84-92%) — that's inside
#     the platform UI no-zone and gets covered or cut.
SAFE_TOP_PCT = 12.0
SAFE_BOTTOM_PCT = 86.0
# HARD horizontal cap: the whole caption stays within 20% margins each side,
# i.e. width <= 60% centered (x 20-80). Manager direction: "the whole caption
# can't go beyond the safe space — 20% from each margin left and right."
CAPTION_MAX_WIDTH = "60%"
HOOK_BLOCK_CENTER = 22.0    # hook/title block centre (top of safe zone)
# Where the big boxed headline sits when nobody says otherwise. Below the
# face, above the caption band — the two are held apart in time as well, so
# moving this does not make them collide, it only changes where it reads.
HOOK_DEFAULT_Y = "57%"
SUBTITLE_Y = "78%"          # lower third — well below the face (manager: "30% below, not on the face")


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

# Every role parks captions in the LOWER THIRD, well clear of the speaker's
# face (face ~25-50% from top). Manager direction, repeated: captions must
# sit ~30% below centre, NEVER on the author's face. The y is the block's
# vertical CENTRE (caption_element sets y_anchor=50%), so 78% keeps a ~9vh
# block inside the 86% bottom-safe line.
SAFE_ZONES: dict[str, list[tuple[str, float]]] = {
    "avatar": [("78%", 11.0), ("82%", 9.0)],
    "broll": [("78%", 12.0), ("72%", 12.0)],
    "default": [("78%", 12.0), ("72%", 12.0)],
}


# The owner may place captions anywhere inside the readable band. The limits are
# not taste, they are the platform UI: above CAPTION_Y_MIN the caption collides
# with the top chrome, below CAPTION_Y_MAX it lands in the bottom no-zone and
# gets covered or cut. y is the block's CENTRE (caption_element sets
# y_anchor=50%), so each limit already allows for half a caption block.
CAPTION_Y_MIN = 18.0
CAPTION_Y_MAX = 80.0


def clamp_caption_y(y: str | float | None) -> str | None:
    """Normalise an owner-chosen caption position to a Creatomate percentage,
    clamped to the band where a caption is actually readable.

    Accepts "62%", "62", 62, 62.0. Returns None for anything unusable so the
    caller falls back to the automatic safe-zone placement rather than
    rendering a caption off-frame."""
    if y is None:
        return None
    try:
        v = float(str(y).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return None
    v = min(CAPTION_Y_MAX, max(CAPTION_Y_MIN, v))
    return f"{round(v, 1)}%"


def caption_y_for_role(preset: dict, role: str, *, y_override: str | float | None = None) -> str:
    """Where a body caption sits vertically.

    With no override, captions sit in the lower third, off the speaker's face,
    for EVERY role and EVERY preset: the preset's own y_position is
    intentionally ignored so a high/centre preset can never ride the face
    (this was the recurring 'captions on his face' bug). Manager direction:
    '30% below, not in the author's face'.

    `y_override` is the OWNER saying where they want them — the one voice that
    outranks the automatic placement. It is still clamped to the readable band,
    because a caption the platform covers helps nobody."""
    chosen = clamp_caption_y(y_override)
    if chosen is not None:
        return chosen
    bands = SAFE_ZONES.get(role) or SAFE_ZONES["default"]
    return bands[0][0]


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


def _fit_caption_vh(text: str, base_vh: float, font_family: str = "") -> float:
    """Largest font (vh) at which the LONGEST word fits inside the 60% caption
    box on a 9:16 canvas. A single word can't wrap, so without this a long word
    ('UNCOMFORTABLE') bleeds past the side margins even in a narrow box. Short
    captions keep the preset size; only long-word flashes shrink to fit."""
    longest = max((len(w) for w in (text or "").split()), default=1)
    em = 0.42 if "anton" in (font_family or "").lower() else 0.60
    # 9:16: frame 1080px wide, 1 vh = 19.2 px tall; glyph advance ≈ em·font_px.
    box_px = 0.60 * 1080.0
    max_vh = box_px / (max(1, longest) * em * 19.2)
    return round(max(3.0, min(float(base_vh), max_vh * 0.94)), 1)


def caption_element(
    *, text: str, start: float, end: float, preset: dict, track: int = 3,
    role: str = "default", raw_text: str = "",
    y_override: str | float | None = None,
) -> dict:
    """Build a single Creatomate text element from a preset.

    `role` is the beat's role at this timestamp ("avatar" | "broll" |
    "default"). When a caption falls on an avatar beat, the y is
    overridden to the avatar safe-zone (bottom band) so it can't
    overlap James's face — this is the layout fix the user flagged.

    `raw_text` is the natural-case version of the caption (the flash builder
    bakes a per-word ALL-CAPS emphasis into `text`, e.g. "the CALENDAR").
    MIXED-CASE presets (transform != uppercase, e.g. clean_white) must NOT
    show that highlight — they read as NORMAL captions — so we render the
    natural `raw_text` (or, if absent, soften the baked emphasis). Uppercase
    presets are unaffected: the whole line is caps anyway.

    Only emits the fields the preset actually configures so we don't
    override Creatomate defaults with empty strings (which it interprets
    as "remove this property" — a subtle bug we hit on an earlier
    iteration).
    """
    if preset.get("transform") != "uppercase":
        text = (raw_text or "").strip() or _soften_emphasis(text)
    elem: dict = {
        "type": "text",
        "text": text,
        "track": track,
        "time": start,
        "duration": max(0.2, end - start),
        "width": CAPTION_MAX_WIDTH,
        "y": caption_y_for_role(preset, role, y_override=y_override),
        # Anchor the block on its VERTICAL CENTER so the y band is where the
        # text actually sits (Creatomate default top-anchors, which pushed
        # captions lower than the stated % and off the bottom for tall fonts).
        "y_anchor": "50%",
        "x_alignment": preset["x_alignment"],
        "font_family": preset["font_family"],
        "font_weight": preset["font_weight"],
        # Shrink-to-fit so a long word can never bleed past the 60% box.
        "font_size": f"{_fit_caption_vh(text, preset['font_size_vh'], preset.get('font_family', ''))} vh",
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
    if preset.get("pop_in", True):
        # The word-punch: captions scale 82→100% in 0.16s as they appear.
        # Elegant presets (clean_white / subtle_minimal / editorial_serif)
        # opt out via pop_in=False.
        elem["animations"] = [{
            "time": 0, "duration": 0.16, "type": "scale",
            "scope": "element", "easing": "quadratic-out",
            "start_scale": "82%", "end_scale": "100%",
        }]
    return elem


# ── viral_hook: two-phase captions ───────────────────────────────────
#
# Reference pattern: the first ~3 seconds show the hook as a HUGE stacked
# 3-line title — white / YELLOW key line (slightly bigger) / white, heavy
# Montserrat, uppercase, centered mid-frame, soft drop shadow, no stroke —
# then captions drop to a small clean mixed-case white style for the rest.

HOOK_WINDOW_S = 3.2      # flashes starting inside this window form the title
HOOK_MAX_WORDS = 12      # keep the stacked block readable
_HOOK_YELLOW = "#FFDD33"

# Creatomate renders at most ONE element per track per instant — stacked
# hook lines that share a track silently drop all but the last. Each
# simultaneous hook line therefore gets its own track, offset well above
# the builders' caption track AND the polish layer (tracks 6-11) so the
# lines never collide with either.
_HOOK_TRACK_OFFSET = 10


def _fit_hook_vh(lines: list[str], base_vh: float, em: float = 0.74,
                 width_pct: float = 72.0) -> float:
    """Shrink a stacked-hook font so the LONGEST line fits its box width
    WITHOUT wrapping.

    Creatomate WRAPS overflow onto a second row inside the same element —
    and with one solid-box element per line, the wrapped row hides BEHIND
    the next line's box (observed on a real render: 'You CAN'T lower'
    showed as 'YOU CAN'T' with 'lower' swallowed, and the magenta boxes
    overlapped). So the cost of overflow is catastrophic — bias SMALL.

    em is the average glyph advance as a fraction of font px. Archivo
    Black / heavy display faces run ~0.72-0.78 (much wider than a text
    font); 0.74 + a 0.9 safety factor + the inner box width (72% not 76)
    guarantees no wrap. Canvas 9:16 → 1 vh = (1080/56.25)=19.2 px wide.
    """
    longest = max((len(ln) for ln in lines), default=1)
    fit = (width_pct / 100.0 * 1080.0) / (max(1, longest) * em) / 19.2 * 0.9
    return round(min(base_vh, max(3.2, fit)), 1)


def _hook_lines(text: str) -> list[tuple[str, bool]]:
    """Split the hook into 1-3 visually balanced lines; returns
    [(line, is_yellow)]. 3 lines → middle yellow (the reference look);
    2 lines → second yellow; 1 line → all yellow."""
    words = [w for w in (text or "").split() if w][:HOOK_MAX_WORDS]
    if not words:
        return []
    n = 3 if len(words) >= 6 else (2 if len(words) >= 4 else 1)
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
    yellow = {1: 0, 2: 1, 3: 1}.get(len(lines), 1)
    return [(ln, i == yellow) for i, ln in enumerate(lines)]


def _soften_emphasis(text: str) -> str:
    """Body captions in mixed-case styles are plain ('that good') — undo the
    ALL-CAPS emphasis word the flash builder bakes in ('the CALENDAR' → 'the
    calendar'). Short acronyms (NYC) are left alone. Robust to trailing
    punctuation: we test the bare alpha core, so 'CALENDAR.' still softens."""
    out: list[str] = []
    for w in (text or "").split():
        core = "".join(c for c in w if c.isalpha())
        if core and core.isupper() and len(core) > 3:
            out.append(w.lower())
        else:
            out.append(w)
    return " ".join(out)


def _hook_window(hook: list[dict], body: list[dict]) -> tuple[float, float]:
    """Start/end of the stacked hook title block. Held at least 2s for
    readability, but clamped to the first body flash so the block never
    overlaps the body captions that follow on the caption track."""
    hook_start = min(float(c.get("start") or 0.0) for c in hook)
    hook_end = max(max(float(c.get("end") or 0.0) for c in hook), hook_start + 2.0)
    if body:
        first_body = min(float(c.get("start") or 0.0) for c in body)
        if first_body > hook_start:
            hook_end = min(hook_end, first_body)
    return hook_start, hook_end


# Dedicated high tracks for the below-face hook title so it never collides with
# captions (3), polish layers (6-11) or the viral_hook block.
_HOOK_TITLE_TRACK = 20
# 2.6s: grab attention, then CLEAR. James's rejection ("hook stays on screen
# the whole time") — err toward shorter; the caption track takes over after.
_HOOK_TITLE_HOLD_S = 2.6


def hook_hold_seconds(total: float) -> float:
    """How long the below-face hook holds before it clears. The caption track is
    held back until this point so the boxed hook and the live captions never
    share the screen (manager: 'captions start after the hook disappears').
    Returns 0 when there's no clip."""
    if total <= 0:
        return 0.0
    return round(min(float(total), _HOOK_TITLE_HOLD_S), 2)


def hook_title_elements(text: str, total: float,
                        *, y_override: str | float | None = None) -> list[dict]:
    """A BIG BOXED hook/title BELOW the speaker's face for the first few seconds
    — tells the viewer what the reel is about, then CLEARS so the live captions
    own the lower third (they're held until it's gone; see hook_hold_seconds).

    Rendered as ONE multi-line text element with a dark translucent PILL behind
    it (manager: 'hooks can be boxed … boxed out to help it stand out'), so it
    reads as a distinct title card and never blends into the caption text. All
    lines are white (no per-line color needed here, unlike viral_hook), so a
    single element gives one cohesive box. The font auto-fits so the longest
    line never wraps/overflows ('big but not disproportionate or out of frame'),
    and it fades in, then out, within the hold."""
    t = (text or "").strip().strip('"').strip("“”")
    if not t or total <= 0:
        return []
    # Balanced lines, but HARD-CAPPED at 2: a 3-line pill (up to ~9vh/line +
    # padding) centered at 57% can reach down into the caption band (~72%+)
    # — James's rejection: "captions and hook text are overlapping".
    lines = [ln for ln, _ in _hook_lines(t)]
    if not lines:
        return []
    if len(lines) > 2:
        # Re-split the SAME words into 2 balanced lines (never just drop the
        # payoff third). If even the best 2-line split would clamp under the
        # no-wrap font floor (~26 chars/line), shed trailing WHOLE words
        # until it fits and end on an ellipsis — never cut mid-word.
        def _best_split(ws: list[str]) -> tuple[int, list[str]]:
            best: tuple[int, list[str]] = (10 ** 9, [" ".join(ws)])
            for cut in range(1, len(ws)):
                l1, l2 = " ".join(ws[:cut]), " ".join(ws[cut:])
                m = max(len(l1), len(l2))
                if m < best[0]:
                    best = (m, [l1, l2])
            return best
        words = " ".join(lines).split()
        longest, lines = _best_split(words)
        shed = False
        while longest > 26 and len(words) > 3:
            words = words[:-1]
            shed = True
            longest, lines = _best_split(words)
        if shed:
            lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    longest = max(len(ln) for ln in lines)
    # Big, bold WHITE with a thin black edge — scroll-stopping reels hook look
    # (Archivo Black, em ~0.74). Fit the TEXT to 76% — deliberately tighter than
    # the 82% element width below — so the pill's padding (42% of the font each
    # side) still lands inside the safe margin and the line never wraps. Don't
    # naively raise 0.76 toward 0.82 or the box can run off-frame.
    box_px = 0.76 * 1080.0
    max_vh = box_px / (max(1, longest) * 0.74 * 19.2)
    vh = round(min(9.0, max(3.2, max_vh * 0.94)), 1)
    # Holds the first few seconds, then clears; fade in AND out so it doesn't
    # pop off-screen right as the captions begin. Both fades are bounded by the
    # hold so they can never overlap on a degenerate ultra-short clip.
    hold = hook_hold_seconds(total)
    fade = round(min(0.3, hold / 3.0), 2)
    fade_in = round(min(0.25, hold / 2.0), 2)
    return [{
        "type": "text",
        # One element, all lines, all white → a single cohesive box.
        "text": "\n".join(line.upper() for line in lines),
        "track": _HOOK_TITLE_TRACK,
        "time": 0,
        "duration": hold,
        "animations": [
            {"time": 0, "duration": fade_in, "type": "fade"},
            {"time": round(max(0.0, hold - fade), 2), "duration": fade,
             "type": "fade", "reversed": True},
        ],
        "width": "82%",
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        # Below the face (face ≈ 25-50% from top). Captions own the 78% band
        # only AFTER this clears, so the two never overlap. The owner may move
        # it — same clamp as the captions, so it can never land under the
        # platform's own chrome.
        "y": clamp_caption_y(y_override) or HOOK_DEFAULT_Y, "y_anchor": "50%",
        "line_height": "112%",
        "font_family": "Archivo Black",
        "font_weight": "900",
        "font_size": f"{vh} vh",
        "fill_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": "0.4 vh",
        # Dark translucent pill so the hook STANDS OUT and is visually distinct
        # from the strokeless/boxless captions. padding & radius are % of the
        # FONT size; background_align_threshold merges the per-line highlights
        # into one neat block for multi-line hooks.
        "background_color": "rgba(10,12,20,0.78)",
        "background_x_padding": "42%",
        "background_y_padding": "30%",
        "background_border_radius": "22%",
        "background_align_threshold": "40%",
        "shadow_color": "rgba(0,0,0,0.5)",
        "shadow_blur": "1.1 vh",
        "shadow_y": "0.4 vh",
        "letter_spacing": "0.5%",
    }]


_NAMETAG_TRACK = 21           # above the hook/captions
_NAMETAG_ACCENT = "#EAB308"   # gold sub-bar (the podcast lower-third look)


def speaker_nametag_elements(
    handle: str, subtitle: str, start: float, duration: float,
    brand: dict | None = None,
) -> list[dict]:
    """A lower-third NAME-TAG that introduces a speaker: a white pill with the
    bold @handle, and (optionally) a gold sub-bar beneath it with their title —
    shown for `duration` seconds from `start` (the speaker's first appearance),
    so the audience learns who is who. Left-aligned in the lower third, ABOVE
    the captions and clear of the face. Fades in and out."""
    h = (handle or "").strip()
    if not h or duration <= 0:
        return []
    if not h.startswith("@"):
        h = "@" + h
    sub = (subtitle or "").strip()
    accent = (brand or {}).get("nametag_accent") or _NAMETAG_ACCENT
    fade = round(min(0.3, duration / 4.0), 2)
    anim = [
        {"time": 0, "duration": fade, "type": "fade"},
        {"time": round(max(0.0, duration - fade), 2), "duration": fade,
         "type": "fade", "reversed": True},
    ]
    t0, dur = round(start, 2), round(duration, 2)
    y_handle = 66.5   # below the hook band (57%), above the captions (~78%)
    els = [{
        "type": "text", "text": h,
        "track": _NAMETAG_TRACK, "time": t0, "duration": dur, "animations": anim,
        "x": "6%", "x_anchor": "0%", "x_alignment": "0%",
        "y": f"{y_handle}%", "y_anchor": "50%",
        "font_family": "Archivo Black", "font_weight": "900",
        "font_size": "3.2 vh", "fill_color": "#0B0B0B",
        "background_color": "#FFFFFF",
        "background_x_padding": "36%", "background_y_padding": "32%",
        "background_border_radius": "45%",
        "shadow_color": "rgba(0,0,0,0.35)", "shadow_blur": "1 vh", "shadow_y": "0.35 vh",
    }]
    if sub:
        els.append({
            "type": "text", "text": sub,
            "track": _NAMETAG_TRACK, "time": t0, "duration": dur, "animations": anim,
            "x": "6.5%", "x_anchor": "0%", "x_alignment": "0%",
            "y": f"{y_handle + 5.2}%", "y_anchor": "50%",
            "font_family": "Archivo Black", "font_weight": "900",
            "font_size": "1.9 vh", "fill_color": "#0B0B0B",
            "background_color": accent,
            "background_x_padding": "26%", "background_y_padding": "30%",
            "background_border_radius": "35%",
        })
    return els


def viral_hook_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Build the full two-phase caption track for the 'viral_hook' style.

    Phase 1 — every flash starting inside HOOK_WINDOW_S is merged into one
    stacked title block (separate text elements per line so the key line can
    be yellow and bigger). Phase 2 — the remaining flashes render small,
    clean, mixed-case white via the preset's body fields."""
    caps = [c for c in (captions or []) if (c.get("text") or "").strip()]
    if not caps:
        return []
    hook = [c for c in caps if float(c.get("start") or 0.0) < HOOK_WINDOW_S]
    if not hook:
        hook = caps[:1]
    body = [c for c in caps if c not in hook]

    hook_text = " ".join((c.get("text") or "").strip() for c in hook)
    hook_start, hook_end = _hook_window(hook, body)

    out: list[dict] = []
    lines = _hook_lines(hook_text)
    line_gap = 8.6                                # vh between line centres
    base = SAFE_TOP_PCT + 6.5  # first line center; block stacks DOWN inside the zone
    for i, (line, is_yellow) in enumerate(lines):
        out.append({
            "type": "text",
            "text": line.upper(),
            # One track per simultaneous line — see _HOOK_TRACK_OFFSET.
            "track": track + _HOOK_TRACK_OFFSET + i,
            "time": round(hook_start, 2),
            "duration": round(max(0.2, hook_end - hook_start), 2),
            "width": "76%",
            "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
            "y": f"{base + i * line_gap:.1f}%", "y_anchor": "50%",
            "font_family": "Montserrat",
            "font_weight": "800",
            "font_size": f"{_fit_hook_vh([l for l, _ in lines], 8.4 if is_yellow else 7.0)} vh",
            "fill_color": _HOOK_YELLOW if is_yellow else "#FFFFFF",
            "shadow_color": "rgba(0,0,0,0.65)",
            "shadow_blur": "1.3 vh",
            "shadow_x": "0 vh",
            "shadow_y": "0.35 vh",
            "letter_spacing": "0.5%",
        })

    preset = CAPTION_PRESETS["viral_hook"]
    for c in body:
        out.append(caption_element(
            text=_soften_emphasis((c.get("text") or "").strip()),
            start=float(c.get("start") or 0.0),
            end=float(c.get("end") or 0.0),
            preset=preset, track=track, role="default",
        ))
    return out


def _hook_body_split(captions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Shared two-phase split: flashes starting inside HOOK_WINDOW_S form the
    hook block; the rest are body. Falls back to first-flash-as-hook."""
    caps = [c for c in (captions or []) if (c.get("text") or "").strip()]
    if not caps:
        return [], []
    hook = [c for c in caps if float(c.get("start") or 0.0) < HOOK_WINDOW_S]
    if not hook:
        hook = caps[:1]
    return hook, [c for c in caps if c not in hook]


def magenta_blocks_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Magenta caption look — every phrase as a magenta-on-box caption at the
    chin, never over the face. THE DEFAULT STYLE.

    The big stacked white-on-magenta HOOK block at the start was removed
    (2026-06-15, per owner: it covered the speaker's face). Now every caption
    — including the opening lines — renders as the normal per-phrase magenta
    body caption in the safe zone.
    """
    preset = CAPTION_PRESETS["magenta_blocks"]
    out: list[dict] = []
    for c in captions:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append(caption_element(
            text=text,
            start=float(c.get("start") or 0.0), end=float(c.get("end") or 0.0),
            preset=preset, track=track, role="default",
        ))
    return out


def editorial_serif_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Magazine title-card style. Hook: a small white uppercase sans KICKER
    (first 1-2 words) over huge yellow ITALIC serif stacked lines (Title
    Case). Body: yellow italic serif at a readable size."""
    hook, body = _hook_body_split(captions)
    if not hook and not body:
        return []
    out: list[dict] = []
    words = " ".join((c.get("text") or "").strip() for c in hook).split()
    hook_start, hook_end = _hook_window(hook, body)
    kicker_words = words[:2] if len(words) >= 5 else words[:1] if len(words) >= 3 else []
    big_words = words[len(kicker_words):] or words
    # Title Case reads wrong around leftover ALL-CAPS emphasis words — soften
    # them first so the big serif lines come out as clean editorial casing.
    lines = _hook_lines(_soften_emphasis(" ".join(big_words)))
    line_gap = 10.6
    base = SAFE_TOP_PCT + 10.5  # title stacks DOWN below the kicker
    common = {
        "type": "text", "track": track,
        "time": round(hook_start, 2),
        "duration": round(max(0.2, hook_end - hook_start), 2),
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        "y_anchor": "50%", "width": "76%",
        "shadow_color": "rgba(0,0,0,0.5)", "shadow_blur": "1.0 vh",
        "shadow_x": "0 vh", "shadow_y": "0.25 vh",
    }
    if kicker_words:
        out.append({
            **common,
            "text": " ".join(kicker_words).upper(),
            # Kicker + title lines all show at once — one track each.
            "track": track + _HOOK_TRACK_OFFSET,
            "y": f"{SAFE_TOP_PCT + 3.5:.1f}%",
            "font_family": "Montserrat", "font_weight": "700",
            "font_size": "3.4 vh", "fill_color": "#FFFFFF",
            "letter_spacing": "4%",
        })
    for i, (line, _y) in enumerate(lines):
        title = " ".join(w[:1].upper() + w[1:] for w in line.split())
        out.append({
            **common,
            "text": title,
            "track": track + _HOOK_TRACK_OFFSET + 1 + i,
            "y": f"{base + i * line_gap:.1f}%",
            "font_family": "Playfair Display", "font_weight": "700",
            "font_style": "italic",
            "font_size": f"{_fit_hook_vh([l for l, _ in lines], 9.6, em=0.5)} vh", "fill_color": "#F2E73B",
        })
    preset = CAPTION_PRESETS["editorial_serif"]
    for c in body:
        elem = caption_element(
            text=_soften_emphasis((c.get("text") or "").strip()),
            start=float(c.get("start") or 0.0), end=float(c.get("end") or 0.0),
            preset=preset, track=track, role="default",
        )
        elem["font_style"] = "italic"
        out.append(elem)
    return out


# Scatter cycle for gradient_mint — (x%, y%) per flash, looping. Mirrors the
# reference's art direction: top-left → right → centre.
_MINT_SPOTS: tuple[tuple[float, float], ...] = ((34.0, 24.0), (64.0, 42.0), (50.0, 60.0))


def gradient_mint_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Premium-ad scatter style: every flash is big lowercase Poppins in pale
    mint (flat stand-in for the reference's white→green gradient), cycling
    through scattered frame positions. No hook/body phases."""
    caps = [c for c in (captions or []) if (c.get("text") or "").strip()]
    out: list[dict] = []
    for i, c in enumerate(caps):
        x, y = _MINT_SPOTS[i % len(_MINT_SPOTS)]
        out.append({
            "type": "text",
            "text": _soften_emphasis((c.get("text") or "").strip()),
            "track": track,
            "time": round(float(c.get("start") or 0.0), 2),
            "duration": round(max(0.2, float(c.get("end") or 0.0) - float(c.get("start") or 0.0)), 2),
            "width": "44%",
            "x": f"{x:.0f}%", "x_anchor": "50%", "x_alignment": "50%",
            "y": f"{y:.0f}%", "y_anchor": "50%",
            "font_family": "Poppins", "font_weight": "800",
            "font_size": "6.6 vh", "fill_color": "#BFF2DC",
            "shadow_color": "rgba(0,0,0,0.35)", "shadow_blur": "0.8 vh",
            "shadow_x": "0 vh", "shadow_y": "0.2 vh",
            "letter_spacing": "0",
        })
    return out


# ── cinematic_scatter: kinetic editorial captions that FRAME the subject ──
#
# Reference look (cinematic YouTuber style): a phrase's words are laid out as a
# ragged vertical column down ONE side of the frame (alternating per phrase so
# text never sits on the face), accumulating word-by-word as spoken, then fading
# out together before the next phrase. Typographic HIERARCHY carries it:
#   * tiny connector words ("the", "to", "this")  → small clean sans (Montserrat)
#   * content words                                → larger serif (Playfair)
#   * the single strongest word                    → biggest, GOLD Playfair ITALIC
_SCATTER_GOLD = "#E7B24B"
# Words kept SMALL (connectors / fillers). Everything else is content-sized; the
# longest non-small word becomes the gold italic hero.
_SCATTER_SMALL = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "you", "your", "my", "we", "i", "is", "was", "are", "were", "be",
    "been", "will", "this", "that", "it", "as", "by", "from", "if", "so", "no",
    "not", "they", "them", "he", "she", "have", "has", "had", "just", "too",
    "only", "do", "did", "up", "out", "than", "then", "into",
    "don't", "you're", "it's", "i'm", "we're", "that's", "there's", "what's",
})
# Dedicated high track block so scattered words never collide with captions (3),
# polish (6-11), styled hooks (13-16) or the boxed hook title (20). Two blocks
# alternate per phrase so a lingering phrase can't share a track with the next.
_SCATTER_TRACK_BASE = 30
_SCATTER_BLOCK = 8           # max words rendered per phrase (also the block size)


def _scatter_bare(w: str) -> str:
    return "".join(ch for ch in (w or "").lower() if ch.isalpha() or ch == "'")


def _fit_word_vh(word: str, base_vh: float, em: float = 0.52,
                 box_pct: float = 52.0) -> float:
    """Cap a single word's size so it can't wrap or run past the side margin."""
    n = max(1, len(word or "x"))
    max_vh = (box_pct / 100.0 * 1080.0) / (n * em * 19.2)
    return round(min(float(base_vh), max(2.8, max_vh * 0.96)), 1)


def _scatter_phrases(captions: list[dict]) -> list[list[tuple[str, float, float]]]:
    """Flatten flashes into (word, start, end), interpolating per-word times
    inside each flash, then group into phrases — break on sentence-final
    punctuation, a clear pause, or ~7 words (keeps each side-column readable)."""
    words: list[tuple[str, float, float]] = []
    for c in captions:
        raw = (c.get("raw_text") or c.get("text") or "").strip()
        toks = [w for w in raw.split() if w]
        if not toks:
            continue
        s = float(c.get("start") or 0.0)
        e = float(c.get("end") or s)
        step = (e - s) / max(1, len(toks))
        for i, w in enumerate(toks):
            words.append((w, round(s + i * step, 3), round(s + (i + 1) * step, 3)))
    phrases: list[list[tuple[str, float, float]]] = []
    cur: list[tuple[str, float, float]] = []
    for i, (w, ws, we) in enumerate(words):
        cur.append((w, ws, we))
        ends_sentence = w.rstrip("\"'”’)").endswith((".", "!", "?"))
        gap_next = (words[i + 1][1] - we) if i + 1 < len(words) else 99.0
        if ends_sentence or len(cur) >= 7 or gap_next >= 0.8:
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    # Avoid orphaned single-word phrases (a lone gold italic word reads as a
    # mistake) — fold a 1-word phrase into the previous one when there's room.
    merged: list[list[tuple[str, float, float]]] = []
    for ph in phrases:
        if len(ph) == 1 and merged and len(merged[-1]) < _SCATTER_BLOCK:
            merged[-1].extend(ph)
        else:
            merged.append(ph)
    return merged


# Subtle ragged horizontal offsets per stacked line (keeps the column from
# reading as a rigid list — matches the reference's hand-placed feel).
_SCATTER_JITTER = (3.0, -2.0, 1.5, -3.0, 2.5, -1.0, 0.5, -2.5)


def cinematic_scatter_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Kinetic editorial captions framing the subject — see the section header.
    Word-level placement; ignores `track` (uses its own dedicated block)."""
    caps = [c for c in (captions or []) if (c.get("text") or c.get("raw_text") or "").strip()]
    if not caps:
        return []
    out: list[dict] = []
    for p_idx, phrase in enumerate(_scatter_phrases(caps)):
        words = phrase[:_SCATTER_BLOCK]
        if not words:
            continue
        side_right = (p_idx % 2 == 0)
        blk = _SCATTER_TRACK_BASE + (p_idx % 2) * _SCATTER_BLOCK   # 30 or 38
        hold_end = words[-1][2] + 0.5      # hold the full phrase a beat, then fade
        fade_out = 0.3
        # Hero = longest non-small word (the gold italic accent).
        emph_i, emph_len = -1, 0
        for i, (w, _s, _e) in enumerate(words):
            b = _scatter_bare(w)
            if b in _SCATTER_SMALL:
                continue
            if len(b) > emph_len:
                emph_len, emph_i = len(b), i
        # Per-word (text, start, vh, color, family, style, weight).
        specs: list[tuple] = []
        for i, (w, ws, _we) in enumerate(words):
            b = _scatter_bare(w)
            if i == emph_i:
                specs.append((w, ws, _fit_word_vh(w, 6.6, em=0.5), _SCATTER_GOLD,
                              "Playfair Display", "italic", "700"))
            elif b in _SCATTER_SMALL:
                specs.append((w, ws, _fit_word_vh(w, 3.6, em=0.55), "#FFFFFF",
                              "Montserrat", "normal", "600"))
            else:
                specs.append((w, ws, _fit_word_vh(w, 5.2, em=0.5), "#FFFFFF",
                              "Playfair Display", "normal", "700"))
        # Stack vertically, centered ~50%, clamped into the safe band.
        line_h = [vh * 1.16 + 0.8 for (_w, _s, vh, *_r) in specs]
        total_h = sum(line_h)
        y0 = max(SAFE_TOP_PCT + 2.0, 50.0 - total_h / 2.0)
        col_x = 75.0 if side_right else 25.0
        for i, (w, ws, vh, color, fam, style, weight) in enumerate(specs):
            cy = y0 + sum(line_h[:i]) + line_h[i] / 2.0
            if cy > SAFE_BOTTOM_PCT - 2.0:        # ran past the bottom safe line
                break
            cx = col_x + _SCATTER_JITTER[i % len(_SCATTER_JITTER)]
            dur = round(max(0.3, hold_end - ws), 2)
            out.append({
                "type": "text",
                "text": w,
                "track": blk + i,
                "time": round(ws, 2),
                "duration": dur,
                "width": "48%",
                "x": f"{cx:.1f}%", "x_anchor": "50%", "x_alignment": "50%",
                "y": f"{cy:.1f}%", "y_anchor": "50%",
                "font_family": fam,
                "font_weight": weight,
                "font_style": style,
                "font_size": f"{vh} vh",
                "fill_color": color,
                "shadow_color": "rgba(0,0,0,0.6)",
                "shadow_blur": "1.1 vh",
                "shadow_x": "0 vh",
                "shadow_y": "0.3 vh",
                "letter_spacing": "0",
                "animations": [
                    {"time": 0, "duration": 0.22, "type": "fade"},
                    {"time": round(max(0.0, dur - fade_out), 2),
                     "duration": fade_out, "type": "fade", "reversed": True},
                ],
            })
    return out


# Designer styles that emit a complete multi-element caption track instead of
# the builders' one-element-per-flash loop. The assembly builders call
# styled_caption_elements() first and fall back to the standard loop on None.
#
# NOTE: viral_hook is intentionally NOT registered. Its two-phase "huge stacked
# title" emphasised single words and overflowed the frame (captions "outside
# and big / flying everywhere"). Unregistering it makes any residual viral_hook
# request fall back to the uniform, width-constrained standard caption loop.
def karaoke_elements(captions: list[dict], track: int = 3) -> list[dict]:
    """Word-by-word reveal: each spoken word flashes center-screen ONE at a time
    (pop-in), timed by even interpolation within its caption flash — the
    high-retention 'one word at a time' viral caption. Reuses caption_element so
    it inherits the karaoke preset's look + safe-zone y positioning."""
    preset = CAPTION_PRESETS.get("karaoke") or CAPTION_PRESETS[DEFAULT_CAPTION_STYLE]
    els: list[dict] = []
    for c in (captions or []):
        s = float(c.get("start") or 0.0)
        e = float(c.get("end") or s)
        raw = (c.get("raw_text") or c.get("text") or "").strip()
        words = [w for w in raw.split() if w]
        if not words or e <= s:
            continue
        role = c.get("role") or "default"
        step = (e - s) / len(words)
        for i, w in enumerate(words):
            ws = s + i * step
            we = e if i == len(words) - 1 else ws + step
            els.append(caption_element(
                text=w, raw_text=w, start=round(ws, 2), end=round(we, 2),
                preset=preset, track=track, role=role,
            ))
    return els


_STYLED_BUILDERS = {
    "magenta_blocks": magenta_blocks_elements,
    "editorial_serif": editorial_serif_elements,
    "gradient_mint": gradient_mint_elements,
    "cinematic_scatter": cinematic_scatter_elements,
    "karaoke": karaoke_elements,
}


def styled_caption_elements(
    style: str | None, captions: list[dict], track: int = 3,
) -> list[dict] | None:
    fn = _STYLED_BUILDERS.get((style or "").strip())
    return fn(captions, track) if fn else None


__all__ = [
    "CAPTION_PRESETS", "DEFAULT_CAPTION_STYLE", "AUTO_PICK_KEY",
    "SAFE_ZONES",
    "get_preset", "list_presets", "caption_element", "caption_y_for_role",
    "clamp_caption_y", "CAPTION_Y_MIN", "CAPTION_Y_MAX", "HOOK_DEFAULT_Y",
    "hook_title_elements", "hook_hold_seconds",
    "viral_hook_elements", "magenta_blocks_elements",
    "editorial_serif_elements", "gradient_mint_elements",
    "cinematic_scatter_elements",
    "karaoke_elements",
    "styled_caption_elements",
]
