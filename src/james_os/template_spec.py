"""Hand-authored reel templates — the builder's spec, validated and normalised.

Until now a style template could only be BORN from a reference video: upload a
clip, the Design Inspector watches it, and the resulting structured read is
stored. There was no way to simply *state* a reel format — "hook card, talking
head, three B-roll beats, CTA; magenta captions; upbeat bed" — and save it as a
reusable template.

This module is that missing insert path. It takes a flat builder spec (the
shape a form produces) and emits the EXACT same template JSON the inspector
emits, so everything downstream — `template_apply.map_template_to_render`,
`templates._style_similarity`, `/templates/{id}/replicate`, the autopilot's
distinct-template picker — works on an authored template with no changes.

Two rules govern the design:

  * The vocabulary is CLOSED and derived from the render engine itself
    (`compositions.SUPPORTED_LAYOUTS`, `caption_styles.CAPTION_PRESETS`,
    `template_apply._ALLOWED_MODES` / `_MUSIC_MOODS` / `_LOGO_POS`). The builder
    cannot offer a layout, caption preset or music bed the renderer can't
    produce — an authored template is renderable by construction rather than by
    hope. `capabilities()` is that vocabulary, served to the UI.

  * Layout and mode are reconciled HERE, at authoring time. The inspector emits
    them as independent guesses and `map_template_to_render` has to override a
    contradictory pair at render time (emitting an approximation). An authored
    template should never carry that contradiction in the first place.
"""

from .caption_styles import CAPTION_PRESETS, list_presets
from .reel_cards import CARD_STYLES
from .compositions import SUPPORTED_LAYOUTS
from .template_apply import _ALLOWED_MODES, _LOGO_POS, _MUSIC_MOODS

# ── the closed vocabulary ────────────────────────────────────────────

# Layouts the renderer reproduces today, in the order a builder should show
# them. Derived from SUPPORTED_LAYOUTS minus its empty/alias members, so a
# newly built composition goes live in the builder the moment it's registered.
_LAYOUT_LABELS: dict[str, str] = {
    "full_frame": "Full frame — one picture, edge to edge",
    "split_horizontal": "Split 50/50 — speaker on top, B-roll below",
    "split_vertical": "Split left/right — speaker left, B-roll right",
}
LAYOUTS: list[str] = [k for k in _LAYOUT_LABELS if k in SUPPORTED_LAYOUTS]

# A split layout has exactly one renderer; pinning the pair here is what keeps
# an authored template from contradicting itself.
_LAYOUT_MODE: dict[str, str] = {
    "split_horizontal": "split_horizontal",
    "split_vertical": "split_vertical",
}

_MODE_LABELS: dict[str, str] = {
    "engaging_avatar": "Talking head with B-roll cutaways",
    "mixed": "Scene-by-scene (your beats drive the cut)",
    "avatar_only": "Talking head, no cutaways",
    "story_audio": "Voiceover over stills",
    "avatar_story_mix": "Talking head opening, then voiceover stills",
    "long_form_reel": "Cut from your own uploaded footage",
    "timeline": "Timeline (advanced)",
    "split_horizontal": "Split 50/50 (set by the layout)",
    "split_vertical": "Split left/right (set by the layout)",
}
# Modes an author picks directly. The split modes are excluded — they are
# implied by the layout, never chosen separately.
MODES: list[str] = [
    m for m in _MODE_LABELS if m in _ALLOWED_MODES and m not in _LAYOUT_MODE
]

ASPECTS: list[str] = ["9:16", "1:1", "16:9"]
MUSIC_MOODS: list[str] = ["", *sorted(_MUSIC_MOODS)]
LOGO_POSITIONS: list[str] = sorted(_LOGO_POS)
FORMAT_TYPES: list[str] = [
    "talking_head", "b_roll_montage", "text_overlay",
    "mixed", "interview", "skit", "tutorial",
]
BEAT_ROLES: list[str] = ["talking_head", "b_roll", "text_card", "demo", "overlay"]
ENERGIES: list[str] = ["high", "medium", "low"]

# Cards are placed from the transcript, so they only mean anything where
# somebody is speaking on camera.
# Only the modes whose renderer actually DRAWS cards. The split compositions
# have no card layer yet, so offering them here would let a template promise
# something the render silently drops.
_CARD_MODES = {"engaging_avatar", "long_form_reel"}

# `_clamp_structure` floors/ceils an authored beat to this range at render
# time; enforce it up front so the builder can't save a beat the renderer
# would silently rewrite.
BEAT_MIN_SECONDS, BEAT_MAX_SECONDS = 2, 25
# `_clamp_structure` also caps a mixed-mode render at 6 scenes.
MAX_BEATS = 6


def capabilities() -> dict:
    """The builder's whole vocabulary, with labels — everything the form needs
    to render itself, and nothing the renderer can't reproduce."""
    return {
        "layouts": [{"value": k, "label": _LAYOUT_LABELS[k]} for k in LAYOUTS],
        "modes": [{"value": m, "label": _MODE_LABELS[m]} for m in MODES],
        "aspects": ASPECTS,
        "caption_presets": [
            {"value": "", "label": "Auto — let the mode pick", "description": ""},
            *[
                {"value": p["name"], "label": p["label"], "description": p["description"]}
                for p in list_presets()
            ],
        ],
        "music_moods": [
            {"value": "", "label": "No music bed"},
            *[{"value": m, "label": m.capitalize()} for m in MUSIC_MOODS if m],
        ],
        "logo_positions": LOGO_POSITIONS,
        "format_types": FORMAT_TYPES,
        "beat_roles": BEAT_ROLES,
        "energies": ENERGIES,
        "limits": {
            "beat_min_seconds": BEAT_MIN_SECONDS,
            "beat_max_seconds": BEAT_MAX_SECONDS,
            "max_beats": MAX_BEATS,
        },
        # Said plainly because it decides whether authoring beats is worth the
        # author's time: only 'mixed' turns beats into actual scenes.
        "beats_drive_render_in": ["mixed"],
        # Designed cutaway cards, placed automatically from what is spoken
        # (reel_director). Available on the talking-head modes, where there is
        # speech to read; a montage has nothing to place cards against.
        "card_styles": list(CARD_STYLES),
        "cards_supported_in": sorted(_CARD_MODES),
    }


# ── validation ───────────────────────────────────────────────────────

def _clean_str(v, limit: int = 500) -> str:
    return str(v or "").strip()[:limit]


def _clean_list(v, limit: int = 12, item_limit: int = 300) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = [_clean_str(x, item_limit) for x in v]
    return [x for x in out if x][:limit]


def validate_spec(spec: dict) -> list[str]:
    """Every problem with this spec, in the author's language. Empty list means
    `build_template` will produce a renderable template."""
    spec = spec or {}
    errors: list[str] = []

    if not _clean_str(spec.get("name"), 120):
        errors.append("give the template a name")

    layout = _clean_str(spec.get("layout"), 40).lower() or "full_frame"
    if layout not in LAYOUTS:
        errors.append(
            f"layout {layout!r} isn't one the renderer can build — "
            f"choose one of: {', '.join(LAYOUTS)}"
        )

    # A split layout implies its mode; only a free layout takes a mode choice.
    mode = _clean_str(spec.get("production_mode"), 40).lower()
    if layout not in _LAYOUT_MODE and mode and mode not in MODES:
        errors.append(
            f"production mode {mode!r} isn't available — "
            f"choose one of: {', '.join(MODES)}"
        )

    aspect = _clean_str(spec.get("aspect"), 10)
    if aspect and aspect not in ASPECTS:
        errors.append(f"aspect {aspect!r} isn't supported — use one of: {', '.join(ASPECTS)}")

    caption = _clean_str(spec.get("caption_preset"), 40).lower()
    if caption and caption not in CAPTION_PRESETS:
        errors.append(f"caption preset {caption!r} doesn't exist")

    music = _clean_str(spec.get("music"), 40).lower()
    if music and music not in _MUSIC_MOODS:
        errors.append(
            f"music {music!r} has no track — use one of: {', '.join(sorted(_MUSIC_MOODS))}, "
            "or leave it empty for no bed"
        )

    logo = spec.get("logo") or {}
    if isinstance(logo, dict) and logo.get("present"):
        pos = _clean_str(logo.get("position"), 40).lower()
        if pos and pos not in _LOGO_POS:
            errors.append(
                f"logo position {pos!r} isn't a corner the renderer draws — "
                f"use one of: {', '.join(LOGO_POSITIONS)}"
            )

    fmt = _clean_str(spec.get("format_type"), 40).lower()
    if fmt and fmt not in FORMAT_TYPES:
        errors.append(f"format {fmt!r} isn't a known type")

    beats = spec.get("beats")
    if beats is not None and not isinstance(beats, list):
        errors.append("beats must be a list")
    elif isinstance(beats, list):
        if len(beats) > MAX_BEATS:
            errors.append(
                f"{len(beats)} beats — the renderer cuts a reel at {MAX_BEATS}; "
                "merge some beats"
            )
        for i, b in enumerate(beats, 1):
            if not isinstance(b, dict):
                errors.append(f"beat {i} isn't a beat")
                continue
            role = _clean_str(b.get("role"), 40).lower()
            if role and role not in BEAT_ROLES:
                errors.append(
                    f"beat {i}: role {role!r} isn't one of {', '.join(BEAT_ROLES)}"
                )
            try:
                secs = float(b.get("seconds") or 0)
            except (TypeError, ValueError):
                errors.append(f"beat {i}: seconds must be a number")
                continue
            if secs and not (BEAT_MIN_SECONDS <= secs <= BEAT_MAX_SECONDS):
                errors.append(
                    f"beat {i}: {secs:g}s is outside the renderable "
                    f"{BEAT_MIN_SECONDS}–{BEAT_MAX_SECONDS}s range"
                )

    energy = _clean_str(spec.get("energy"), 20).lower()
    if energy and energy not in ENERGIES:
        errors.append(f"energy {energy!r} isn't one of: {', '.join(ENERGIES)}")

    cards = spec.get("cards")
    if cards is not None:
        if not isinstance(cards, dict):
            errors.append("cards must be an object")
        else:
            for st in cards.get("styles") or []:
                if str(st).strip().lower() not in CARD_STYLES:
                    errors.append(
                        f"card style {st!r} isn't one of: {', '.join(CARD_STYLES)}")
            if cards.get("enabled"):
                # Layout drives the mode, so resolve it the same way build_template does.
                eff = _LAYOUT_MODE.get(layout) or mode or "engaging_avatar"
                if eff not in _CARD_MODES:
                    errors.append(
                        f"cards need somebody speaking on camera — they aren't "
                        f"placed in {eff!r} mode")

    return errors


# ── spec → the inspector's template shape ────────────────────────────

def _regions_for(layout: str) -> list[dict]:
    """What sits where, for the layouts the renderer actually pins. These are
    not author choices — `template_apply` puts the speaker top (or left) and
    B-roll in the other half — so they're derived, and the builder shows them
    read-only rather than pretending they're configurable."""
    if layout == "split_horizontal":
        return [
            {"position": "top", "contains": "speaker"},
            {"position": "bottom", "contains": "b-roll"},
        ]
    if layout == "split_vertical":
        return [
            {"position": "left", "contains": "speaker"},
            {"position": "right", "contains": "b-roll"},
        ]
    return [{"position": "full", "contains": "speaker"}]


def _segments_from_beats(beats, logo_on: bool, logo_pos: str) -> list[dict]:
    """Authored beats → the inspector's `segments[]`, with the running
    start/end clock the inspector derives from watching the clip."""
    out: list[dict] = []
    clock = 0.0
    for i, b in enumerate(beats or [], 1):
        if not isinstance(b, dict):
            continue
        role = _clean_str(b.get("role"), 40).lower() or "b_roll"
        try:
            secs = float(b.get("seconds") or 0)
        except (TypeError, ValueError):
            secs = 0.0
        secs = min(BEAT_MAX_SECONDS, max(BEAT_MIN_SECONDS, secs or 4))
        text = _clean_str(b.get("on_screen_text"), 200)
        speaking = role == "talking_head"
        out.append({
            "start": round(clock, 2),
            "end": round(clock + secs, 2),
            "role": role,
            "visual": _clean_str(b.get("visual"), 300),
            "speaker": {
                "present": speaking,
                "position": "center" if speaking else "none",
                "framing": "medium" if speaking else "none",
            },
            "on_screen_text": {
                "present": bool(text),
                "example": text,
                "position": "lower-third" if text else "none",
                "style": _clean_str(b.get("text_style"), 120),
            },
            "logo": {
                "present": logo_on,
                "position": logo_pos if logo_on else "none",
            },
            "transition_out": _clean_str(b.get("transition_out"), 20).lower() or "cut",
        })
        clock += secs
    return out[:MAX_BEATS]


def build_template(spec: dict) -> dict:
    """A validated builder spec → the template JSON the rest of the system
    already speaks. Raises ValueError listing every problem when the spec isn't
    renderable — never emits a half-valid template.

    Layout wins over mode: picking a split layout SETS the split renderer, so
    the stored template can't claim a full-frame mode for a stacked
    composition (the exact contradiction `map_template_to_render` otherwise has
    to catch and flag at render time)."""
    errors = validate_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))

    spec = spec or {}
    layout = _clean_str(spec.get("layout"), 40).lower() or "full_frame"
    mode = _clean_str(spec.get("production_mode"), 40).lower()
    # The split layouts imply their renderer; a free layout keeps the author's
    # choice, defaulting to the talking-head-with-cutaways workhorse.
    mode = _LAYOUT_MODE.get(layout) or mode or "engaging_avatar"

    caption = _clean_str(spec.get("caption_preset"), 40).lower()
    music = _clean_str(spec.get("music"), 40).lower()
    aspect = _clean_str(spec.get("aspect"), 10) or "9:16"

    logo_in = spec.get("logo") or {}
    logo_on = bool(isinstance(logo_in, dict) and logo_in.get("present"))
    logo_pos = _clean_str(logo_in.get("position") if isinstance(logo_in, dict) else "", 40).lower()
    logo_pos = logo_pos if logo_pos in _LOGO_POS else "bottom-right"

    beats = spec.get("beats") if isinstance(spec.get("beats"), list) else []
    segments = _segments_from_beats(beats, logo_on, logo_pos)

    try:
        cut_seconds = float(spec.get("avg_cut_seconds") or 0)
    except (TypeError, ValueError):
        cut_seconds = 0.0
    if not cut_seconds and segments:
        cut_seconds = round(
            sum(s["end"] - s["start"] for s in segments) / len(segments), 1
        )

    name = _clean_str(spec.get("name"), 120)
    preset = CAPTION_PRESETS.get(caption) if caption else None

    return {
        "style_name": name,
        "summary": _clean_str(spec.get("summary")),
        "distinctive_features": _clean_list(spec.get("distinctive_features")),
        "layout": {
            "type": layout,
            "persistent": True,          # an authored layout is held by definition
            "description": _clean_str(spec.get("layout_description")),
            "regions": _regions_for(layout),
        },
        "format_type": _clean_str(spec.get("format_type"), 40).lower() or "talking_head",
        "aspect_ratio": aspect,
        "hook": _clean_str(spec.get("hook"), 400),
        "pacing": {
            "energy": _clean_str(spec.get("energy"), 20).lower() or "medium",
            "avg_cut_seconds": cut_seconds or 4,
            "notes": _clean_str(spec.get("pacing_notes")),
        },
        "segments": segments,
        "logo": {
            "present": logo_on,
            "position": logo_pos if logo_on else "none",
            "persistence": "always" if logo_on else "none",
        },
        "captions": {
            "present": True,
            "position": "lower-third",
            "look": (preset or {}).get("description", "") if preset else "auto",
            "animation": "line",
            # The inspector's guess field IS the render input — an authored
            # template writes the real preset key straight into it.
            "preset_guess": caption or "none",
        },
        # Designed cutaway cards, placed from the transcript at render time.
        # Gated on the mode: cards are pinned to spoken words, so a template
        # with no speaker can't carry them however it was authored.
        "cards": {
            "enabled": bool((spec.get("cards") or {}).get("enabled")) and mode in _CARD_MODES,
            "styles": [
                str(x).strip().lower()
                for x in ((spec.get("cards") or {}).get("styles") or [])
                if str(x).strip().lower() in CARD_STYLES
            ] or list(CARD_STYLES),
        },
        "audio": {
            "music": {
                "present": bool(music),
                "type": music or "none",
                "mood": _clean_str(spec.get("music_notes"), 200),
                # A specific track from the brand's Audio Library. Without it
                # the render picks any track tagged with the mood, which is
                # why "use THIS bed every time" needed a field of its own.
                "track_id": _clean_str(spec.get("music_track_id"), 64),
            },
            "voiceover": True,
            "sfx": "none",
            "sound_signature": "",
        },
        "color_palette": _clean_str(spec.get("color_palette"), 200),
        "vibe": _clean_str(spec.get("vibe"), 200),
        "production_mode": mode,
        "replication_recipe": _clean_list(spec.get("replication_recipe")),
        # Provenance inside the JSON too, so a template that travels between
        # tenants still says how it was made.
        "authored": True,
    }


def template_to_spec(template: dict) -> dict:
    """The inverse — a stored template back into builder-form fields, so an
    INSPECTED template can be opened in the builder, adjusted and saved. This
    is what lets a reference reel become an editable starting point instead of
    a locked read-only artifact."""
    template = template or {}
    layout = template.get("layout") or {}
    caps = template.get("captions") or {}
    music = ((template.get("audio") or {}).get("music") or {})
    logo = template.get("logo") or {}
    pacing = template.get("pacing") or {}

    ltype = _clean_str(layout.get("type"), 40).lower()
    ltype = ltype if ltype in LAYOUTS else "full_frame"

    preset = _clean_str(caps.get("preset_guess"), 40).lower()
    # The inspector's enum uses 'minimal' and 'none' where the renderer uses
    # 'subtle_minimal' and ''. Normalise so a round-trip through the builder
    # doesn't silently drop the caption style.
    preset = {"minimal": "subtle_minimal", "none": ""}.get(preset, preset)
    preset = preset if preset in CAPTION_PRESETS else ""

    mtype = _clean_str(music.get("type"), 40).lower()
    mtype = {"trending": "upbeat", "none": ""}.get(mtype, mtype)
    mtype = mtype if mtype in _MUSIC_MOODS else ""

    beats = []
    for s in template.get("segments") or []:
        if not isinstance(s, dict):
            continue
        try:
            secs = float(s.get("end") or 0) - float(s.get("start") or 0)
        except (TypeError, ValueError):
            secs = 0.0
        ost = s.get("on_screen_text") or {}
        beats.append({
            "role": _clean_str(s.get("role"), 40).lower() or "b_roll",
            "seconds": min(BEAT_MAX_SECONDS, max(BEAT_MIN_SECONDS, round(secs) or 4)),
            "visual": _clean_str(s.get("visual"), 300),
            "on_screen_text": _clean_str(ost.get("example"), 200),
            "transition_out": _clean_str(s.get("transition_out"), 20).lower() or "cut",
        })

    mode = _clean_str(template.get("production_mode"), 40).lower()
    return {
        "name": _clean_str(template.get("style_name"), 120),
        "summary": _clean_str(template.get("summary")),
        "distinctive_features": _clean_list(template.get("distinctive_features")),
        "layout": ltype,
        "layout_description": _clean_str(layout.get("description")),
        "production_mode": mode if mode in MODES else "engaging_avatar",
        "aspect": _clean_str(template.get("aspect_ratio"), 10) or "9:16",
        "format_type": _clean_str(template.get("format_type"), 40).lower() or "talking_head",
        "caption_preset": preset,
        "music": mtype,
        "music_notes": _clean_str(music.get("mood"), 200),
        "music_track_id": _clean_str(music.get("track_id"), 64),
        "cards": {
            "enabled": bool((template.get("cards") or {}).get("enabled")),
            "styles": [
                str(x).strip().lower()
                for x in ((template.get("cards") or {}).get("styles") or [])
                if str(x).strip().lower() in CARD_STYLES
            ],
        },
        "logo": {
            "present": bool(logo.get("present")),
            "position": _clean_str(logo.get("position"), 40).lower(),
        },
        "hook": _clean_str(template.get("hook"), 400),
        "energy": _clean_str(pacing.get("energy"), 20).lower() or "medium",
        "avg_cut_seconds": pacing.get("avg_cut_seconds") or 0,
        "pacing_notes": _clean_str(pacing.get("notes")),
        "beats": beats[:MAX_BEATS],
        "color_palette": _clean_str(template.get("color_palette"), 200),
        "vibe": _clean_str(template.get("vibe"), 200),
        "replication_recipe": _clean_list(template.get("replication_recipe")),
    }


def preview_render(spec: dict) -> dict:
    """What this spec will ACTUALLY render as — the mapped render params plus
    the approximations — computed before anything is saved. The builder shows
    it live so an author sees 'renders as split_horizontal, magenta_white
    captions, upbeat bed' rather than discovering it after a production."""
    from .template_apply import map_template_to_render

    template = build_template(spec)          # raises on an invalid spec
    m = map_template_to_render(template)
    return {
        "applied": {
            "mode": m["mode"],
            "caption_style": m["caption_style"] or "(auto)",
            "music_mood": m["music_mood"] or "(none)",
            "aspect": m["aspect"],
            "logo": m["logo_position"] if m["logo_on"] else "(none)",
            "scenes": len(m["structure"] or []),
        },
        "approximations": m["approximations"],
        "beats_drive_render": m["mode"] == "mixed",
    }


__all__ = [
    "capabilities", "validate_spec", "build_template",
    "template_to_spec", "preview_render",
    "LAYOUTS", "MODES", "ASPECTS", "MUSIC_MOODS", "LOGO_POSITIONS",
    "FORMAT_TYPES", "BEAT_ROLES", "MAX_BEATS",
]
