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
    """Body captions in this style are plain mixed-case ('that good') —
    undo the ALL-CAPS emphasis word the flash builder injects. Short
    acronyms (NYC) are left alone."""
    return " ".join(
        w.lower() if (w.isalpha() and w.isupper() and len(w) > 3) else w
        for w in (text or "").split()
    )


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
    hook_start = min(float(c.get("start") or 0.0) for c in hook)
    hook_end = max(float(c.get("end") or 0.0) for c in hook)
    hook_end = max(hook_end, hook_start + 2.0)   # never flash shorter than 2s

    out: list[dict] = []
    lines = _hook_lines(hook_text)
    line_gap = 8.6                                # vh between line centres
    base = 52.0 - (len(lines) - 1) * line_gap / 2  # block centred ~52%
    for i, (line, is_yellow) in enumerate(lines):
        out.append({
            "type": "text",
            "text": line.upper(),
            "track": track,
            "time": round(hook_start, 2),
            "duration": round(max(0.2, hook_end - hook_start), 2),
            "width": "92%",
            "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
            "y": f"{base + i * line_gap:.1f}%", "y_anchor": "50%",
            "font_family": "Montserrat",
            "font_weight": "800",
            "font_size": f"{8.4 if is_yellow else 7.0} vh",
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
    """Serhant/SXSW block style. Hook: white uppercase Archivo Black lines,
    each on a solid hot-magenta box, stacked mid-frame. Body: magenta
    uppercase on a solid black box, lower in frame."""
    hook, body = _hook_body_split(captions)
    if not hook and not body:
        return []
    out: list[dict] = []
    hook_text = " ".join((c.get("text") or "").strip() for c in hook)
    hook_start = min(float(c.get("start") or 0.0) for c in hook)
    hook_end = max(max(float(c.get("end") or 0.0) for c in hook), hook_start + 2.0)
    lines = _hook_lines(hook_text)
    line_gap = 9.4
    base = 50.0 - (len(lines) - 1) * line_gap / 2
    for i, (line, _y) in enumerate(lines):
        out.append({
            "type": "text", "text": line.upper(), "track": track,
            "time": round(hook_start, 2),
            "duration": round(max(0.2, hook_end - hook_start), 2),
            "width": "84%",
            "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
            "y": f"{base + i * line_gap:.1f}%", "y_anchor": "50%",
            "font_family": "Archivo Black", "font_weight": "900",
            "font_size": "6.6 vh",
            "fill_color": "#FFFFFF",
            "background_color": "#FF00C8",
            "letter_spacing": "0",
        })
    preset = CAPTION_PRESETS["magenta_blocks"]
    for c in body:
        out.append(caption_element(
            text=(c.get("text") or "").strip(),
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
    hook_start = min(float(c.get("start") or 0.0) for c in hook)
    hook_end = max(max(float(c.get("end") or 0.0) for c in hook), hook_start + 2.0)
    kicker_words = words[:2] if len(words) >= 5 else words[:1] if len(words) >= 3 else []
    big_words = words[len(kicker_words):] or words
    # Title Case reads wrong around leftover ALL-CAPS emphasis words — soften
    # them first so the big serif lines come out as clean editorial casing.
    lines = _hook_lines(_soften_emphasis(" ".join(big_words)))
    line_gap = 10.6
    base = 54.0 - (len(lines) - 1) * line_gap / 2
    common = {
        "type": "text", "track": track,
        "time": round(hook_start, 2),
        "duration": round(max(0.2, hook_end - hook_start), 2),
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        "y_anchor": "50%", "width": "90%",
        "shadow_color": "rgba(0,0,0,0.5)", "shadow_blur": "1.0 vh",
        "shadow_x": "0 vh", "shadow_y": "0.25 vh",
    }
    if kicker_words:
        out.append({
            **common,
            "text": " ".join(kicker_words).upper(),
            "y": f"{base - 8.0:.1f}%",
            "font_family": "Montserrat", "font_weight": "700",
            "font_size": "3.4 vh", "fill_color": "#FFFFFF",
            "letter_spacing": "4%",
        })
    for i, (line, _y) in enumerate(lines):
        title = " ".join(w[:1].upper() + w[1:] for w in line.split())
        out.append({
            **common,
            "text": title,
            "y": f"{base + i * line_gap:.1f}%",
            "font_family": "Playfair Display", "font_weight": "700",
            "font_style": "italic",
            "font_size": "9.6 vh", "fill_color": "#F2E73B",
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
_MINT_SPOTS: tuple[tuple[float, float], ...] = ((30.0, 24.0), (68.0, 40.0), (50.0, 62.0))


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
            "width": "58%",
            "x": f"{x:.0f}%", "x_anchor": "50%", "x_alignment": "50%",
            "y": f"{y:.0f}%", "y_anchor": "50%",
            "font_family": "Poppins", "font_weight": "800",
            "font_size": "6.6 vh", "fill_color": "#BFF2DC",
            "shadow_color": "rgba(0,0,0,0.35)", "shadow_blur": "0.8 vh",
            "shadow_x": "0 vh", "shadow_y": "0.2 vh",
            "letter_spacing": "0",
        })
    return out


# Designer styles that emit a complete multi-element caption track instead of
# the builders' one-element-per-flash loop. The assembly builders call
# styled_caption_elements() first and fall back to the standard loop on None.
_STYLED_BUILDERS = {
    "viral_hook": viral_hook_elements,
    "magenta_blocks": magenta_blocks_elements,
    "editorial_serif": editorial_serif_elements,
    "gradient_mint": gradient_mint_elements,
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
    "viral_hook_elements", "magenta_blocks_elements",
    "editorial_serif_elements", "gradient_mint_elements",
    "styled_caption_elements",
]
