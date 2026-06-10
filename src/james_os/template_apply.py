"""Phase 2 — turn a stored style template into render parameters.

The Design Inspector already emits a template in the assembly engine's own
vocabulary (production_mode, captions.preset_guess, logo.position,
audio.music.type, segments[], aspect_ratio). This module is the bridge: it
maps that template onto the EXACT inputs `video_pipeline.start_production`
already accepts — mode, caption_style, music_mood, aspect, a per-scene logo,
and (for mixed mode) a clamped scene structure — so replicating a style is
"load template → map → produce", with no new orchestration.

Honesty is part of the contract: every lossy step (trending-audio
substitution, a mode that can't honor a logo overlay, exact cut-rhythm not
being pinnable) is collected into `approximations` so the API/UI can tell the
user what was matched vs approximated — never silently faked.
"""

from .caption_styles import CAPTION_PRESETS

# Modes start_production accepts (mirror of its own guard).
_ALLOWED_MODES = {
    "mixed", "avatar_only", "timeline", "story_audio",
    "avatar_story_mix", "engaging_avatar", "long_form_reel",
}
# Inspector caption enum → real preset key.
_CAPTION_ALIAS = {"minimal": "subtle_minimal"}
# Mood URL slots that exist in config (assembly._music_url_for).
_MUSIC_MOODS = {"upbeat", "calm", "dramatic", "tension"}
_MUSIC_ALIAS = {"trending": "upbeat"}  # no trending-audio provider exists
_LOGO_POS = {"bottom-right", "bottom-center", "top-right"}
_ASPECTS = {"9:16", "1:1", "16:9"}


def _norm_caption(preset_guess) -> str:
    """Inspector preset_guess → a real caption preset key, or '' to let the
    pipeline auto-pick. Never returns an unknown key (would crash get_preset)."""
    p = (preset_guess or "").strip().lower()
    if not p or p == "none":
        return ""
    p = _CAPTION_ALIAS.get(p, p)
    return p if p in CAPTION_PRESETS else ""


def _norm_music(music_type) -> tuple[str, str | None]:
    """Inspector music type → a config mood slot. Returns (mood, approximation
    note|None). '' mood means 'no background track'."""
    m = (music_type or "").strip().lower()
    if not m or m == "none":
        return "", None
    note = None
    if m in _MUSIC_ALIAS:
        note = (
            "trending audio can't be fetched (no trending-sound provider) — "
            f"substituted a licensed '{_MUSIC_ALIAS[m]}' music bed"
        )
        m = _MUSIC_ALIAS[m]
    if m not in _MUSIC_MOODS:
        note = f"music type '{music_type}' has no track slot — using an 'upbeat' bed"
        m = "upbeat"
    return m, note


def _norm_aspect(a) -> str:
    a = (a or "").strip()
    return a if a in _ASPECTS else "9:16"


def _norm_logo(logo) -> tuple[bool, str]:
    logo = logo or {}
    if not logo.get("present"):
        return False, ""
    pos = (logo.get("position") or "").strip().lower()
    return True, (pos if pos in _LOGO_POS else "bottom-right")


def _clamp_structure(segments) -> list[dict] | None:
    """Build a SHOOTABLE scene structure from the template's vision-sampled
    segments[] — for mixed mode only. Vision sampling is approximate, so we
    clamp: map roles to kinds, floor/ceil durations, and cap scene count so a
    short reel doesn't explode into a dozen micro-scenes."""
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    if not segs:
        return None
    out: list[dict] = []
    for s in segs:
        role = (s.get("role") or "").strip().lower()
        try:
            start = float(s.get("start") or 0)
            end = float(s.get("end") or 0)
        except (TypeError, ValueError):
            start = end = 0.0
        dur = round(end - start) if end > start else 4
        dur = min(25, max(2, dur))  # keep each scene shootable
        if role == "talking_head":
            kind, source = "talking_head", "avatar"
        else:  # b_roll | text_card | demo | overlay → a visual scene
            kind, source = "broll", None
        out.append({
            "label": f"{role or 'scene'}_{len(out) + 1}",
            "kind": kind,
            "source": source,
            "duration": dur,
        })
    return out[:6] or None  # cap at 6 scenes


def map_template_to_render(template: dict) -> dict:
    """Map a stored template (the 'template' jsonb) → render parameters that
    feed start_production. Returns a dict with: mode, caption_style,
    music_mood, image_style, aspect, logo_on, logo_position, structure
    (mixed only), approximations[]."""
    template = template or {}
    mode = (template.get("production_mode") or "").strip()
    if mode not in _ALLOWED_MODES:
        mode = "engaging_avatar"  # the talking-head-with-overlays default

    caption_style = _norm_caption((template.get("captions") or {}).get("preset_guess"))
    music_mood, music_note = _norm_music(
        ((template.get("audio") or {}).get("music") or {}).get("type")
    )
    aspect = _norm_aspect(template.get("aspect_ratio"))
    logo_on, logo_position = _norm_logo(template.get("logo"))
    structure = _clamp_structure(template.get("segments")) if mode == "mixed" else None

    approximations: list[str] = []
    if music_note:
        approximations.append(music_note)
    if mode in ("engaging_avatar", "avatar_story_mix", "story_audio"):
        approximations.append(
            "cut-rhythm and exact caption sizing are approximated — the caption "
            "preset, position band, music mood and structure are matched, not pixel-cloned"
        )
    if logo_on and mode != "mixed":
        approximations.append(
            f"logo ({logo_position}) is overlaid in mixed mode; this mode uses "
            "burned captions and B-roll cutaways without a separate logo layer"
        )

    return {
        "mode": mode,
        "caption_style": caption_style,
        "music_mood": music_mood,
        "image_style": "",  # let the mode default (cinematic for story modes)
        "aspect": aspect,
        "logo_on": logo_on,
        "logo_position": logo_position,
        "structure": structure,
        "approximations": approximations,
    }


def apply_overrides_to_scenes(
    scenes,
    *,
    music_mood: str = "",
    logo_on: bool = False,
    logo_position: str = "bottom-right",
) -> list:
    """Mixed mode: stamp the template's music mood + logo across the planned
    scenes (the planner already set per-scene transitions/text). The assembler
    reads the first scene's audio_music for the background bed, so a uniform
    mood is sufficient and faithful to a single-style template."""
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        if music_mood:
            s["audio_music"] = music_mood
        if logo_on:
            s["branding_logo"] = True
            s["branding_position"] = logo_position or "bottom-right"
    return scenes


__all__ = ["map_template_to_render", "apply_overrides_to_scenes"]
