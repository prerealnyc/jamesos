"""Design Inspector — deep, structured style analysis of a reference video.

Where the perception layer (perception.py) produces a coarse one-paragraph
fingerprint of a clip's opening, the design inspector watches the WHOLE video
and reverse-engineers a *reusable production template*: a beat-by-beat map of
where every element sits (speaker, captions, on-screen text, logo), how it's
paced, what the sound is doing, and which JAMES OS production mode + caption
preset would reproduce it.

    ffmpeg ──┬── audio  → Whisper  → transcript
             └── frames (spread across the ENTIRE clip) → GPT-4o vision
                                   │
                                   ▼
                    structured production TEMPLATE (JSON)

The template deliberately speaks the assembly engine's own vocabulary — logo
positions ("bottom-right"), caption presets ("tiktok_yellow"), music moods
("upbeat"), production modes ("engaging_avatar") — so a stored template can
later drive a real render (Phase 2) with no translation layer.

Honesty rules (frustration-ledger discipline):
  * This reads SAMPLED frames with a vision model, so it captures zones and
    placements ("logo bottom-right, captions lower-third, cuts ~2s"), not
    frame-perfect object tracking. The scope limit is stated, not hidden.
  * Replication targets FORMAT, never verbatim content.
  * No OpenAI key, or an un-decodable file → status is reported plainly
    ("unsupported"/"failed"), never a faked template.
"""

import base64
import json
import tempfile
from pathlib import Path

from .config import settings

# Reuse the proven ffmpeg/whisper plumbing from the perception layer — one
# implementation of audio extraction / duration parsing / transcription.
from .perception import (
    _client,
    _extract_audio,
    _parse_duration,
    _run,
    _transcribe,
)

# Spread this many frames across the whole clip (perception only grabs the
# opening 8). More frames → the model can see how the layout evolves.
_INSPECT_FRAMES = 14


async def _probe_duration(src: Path) -> int:
    """Cheap duration probe — `ffmpeg -i` prints Duration to stderr even with
    no output file (it exits non-zero, which we ignore)."""
    _, log = await _run(["ffmpeg", "-i", str(src)])
    return _parse_duration(log)


async def _extract_spread_frames(
    src: Path, outdir: Path, duration: int, n: int
) -> tuple[list[Path], int, float]:
    """Sample `n` frames evenly across the ENTIRE clip. Returns
    (frames, duration, interval_seconds)."""
    if duration and duration > 0:
        interval = max(0.5, duration / n)
        vf = f"fps=1/{interval:.3f}"
    else:
        # Unknown duration → fall back to one frame every 2s (opening-biased).
        interval = 2.0
        vf = "fps=1/2"
    rc, log = await _run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", vf,
         "-frames:v", str(n), "-q:v", "4", str(outdir / "f_%02d.jpg")]
    )
    frames = sorted(outdir.glob("f_*.jpg"))
    return frames, (duration or _parse_duration(log)), interval


# The template schema is the contract Phase-2 replication reads, so the prompt
# pins exact enum values that match the assembly engine (logo positions,
# caption presets, music moods, production modes).
_INSPECT_SYSTEM = (
    "You are a senior short-form video director and motion designer. You are "
    "given frames sampled EVENLY across an ENTIRE reference video (in "
    "chronological order, with approximate timestamps) plus its transcript. "
    "Reverse-engineer a REUSABLE PRODUCTION TEMPLATE another brand could follow "
    "to make a NEW video in the SAME STYLE — never copying the words.\n\n"
    "YOUR PRIMARY JOB is to identify what is DISTINCTIVE or NEW about THIS "
    "style — the specific, copyable techniques that DEFINE it and set it apart "
    "from a generic talking head. Examples of distinctive techniques: a SPLIT / "
    "STACKED layout (e.g. the speaker in the TOP half and a screen-recording, "
    "demo, or text in the BOTTOM half, both on screen AT THE SAME TIME), "
    "picture-in-picture, kinetic word-by-word captions, a hard/fast cut rhythm, "
    "an on-screen UI/app demo, meme cutaways, etc. Do NOT flatten the video into "
    "a plain 'talking head' or into ALTERNATING shots if elements actually share "
    "the screen simultaneously — describe the real composition. Capture the "
    "novelty even when it is unusual or outside the enums below; the structured "
    "fields are for what we can reproduce, but distinctive_features + layout "
    "must record what the style ACTUALLY is.\n\n"
    "Track WHERE elements sit and HOW they change over time: the speaker's "
    "position and framing, where captions/subtitles are placed, any on-screen "
    "text/titles, where a logo/watermark appears, the layout, the cut "
    "rhythm/pacing, transitions, and what the SOUND is doing (music type, "
    "voiceover, sfx). Express placements as ZONES (e.g. 'lower-third', "
    "'bottom-right'), never pixels.\n\n"
    "Return STRICT JSON with EXACTLY these keys:\n"
    "{\n"
    '  "style_name": "<4-7 word punchy name for THIS style, e.g. '
    "'Fast-cut talking head, bold yellow subs'>\",\n"
    '  "summary": "<one sentence: what makes this style work>",\n'
    '  "distinctive_features": ["<THE MOST IMPORTANT FIELD: the specific, '
    'copyable techniques that define THIS style and make it different from a '
    'plain talking head — e.g. \'speaker in the top half, screen-recording in '
    'the bottom half\', \'word-by-word captions that pop on the beat\'. List '
    'what a creator would have to copy to recreate the feel>"],\n'
    '  "layout": {"type": "full_frame | split_horizontal | split_vertical | '
    'pip | grid | other", "persistent": <bool: is this composition held for '
    'most of the video, vs a one-off?>, "description": "<how the frame is '
    'composed; if split/stacked, what is in each region and roughly where>", '
    '"regions": [{"position": "top|bottom|left|right|inset|full", "contains": '
    '"<speaker | screen-recording | text | b-roll | demo | ...>"}]},\n'
    '  "format_type": "talking_head | b_roll_montage | text_overlay | mixed | '
    'interview | skit | tutorial",\n'
    '  "aspect_ratio": "9:16 | 1:1 | 16:9",\n'
    '  "hook": "<the opening pattern that grabs attention in the first ~2s>",\n'
    '  "pacing": {"energy": "high|medium|low", "avg_cut_seconds": <number>, '
    '"notes": "<cut rhythm>"},\n'
    '  "segments": [\n'
    '    {"start": <seconds>, "end": <seconds>, '
    '"role": "talking_head | b_roll | text_card | demo | overlay", '
    '"visual": "<what is on screen>", '
    '"speaker": {"present": <bool>, "position": "center|left|right|none", '
    '"framing": "close-up|medium|wide|none"}, '
    '"on_screen_text": {"present": <bool>, "example": "<short example>", '
    '"position": "lower-third|center|top|none", "style": "<look>"}, '
    '"logo": {"present": <bool>, "position": '
    '"bottom-right|bottom-center|top-right|top-left|none"}, '
    '"transition_out": "cut|fade|slide|zoom|none"}\n'
    "  ],\n"
    '  "logo": {"present": <bool>, "position": '
    '"bottom-right|bottom-center|top-right|top-left|none", '
    '"persistence": "always|intro|outro|periodic|none"},\n'
    '  "captions": {"present": <bool>, "position": '
    '"lower-third|center|top|none", "look": "<font feel, color, stroke, '
    'background>", "animation": "word-by-word|line|fade|pop|none", '
    '"preset_guess": "tiktok_yellow|clean_white|bold_pop|minimal|none"},\n'
    '  "audio": {"music": {"present": <bool>, "type": '
    '"upbeat|calm|dramatic|tension|trending|none", "mood": "<describe>"}, '
    '"voiceover": <bool>, "sfx": "<notable sfx or none>", '
    '"sound_signature": "<one line>"},\n'
    '  "color_palette": "<dominant colors / grade>",\n'
    '  "vibe": "<the overall feeling in 3-5 words>",\n'
    '  "production_mode": "engaging_avatar | avatar_story_mix | story_audio | '
    'long_form_reel | avatar_only | timeline",\n'
    '  "replication_recipe": ["<3-6 concrete, voice-agnostic steps to '
    'reproduce this style>"]\n'
    "}\n\n"
    "LEAD with distinctive_features and layout — surfacing what is NEW about "
    "this style is the whole point of the analysis; everything else is "
    "secondary. Be specific and grounded in what you actually see. If frames "
    "are uninformative for a field, be honest (set present=false or note "
    "uncertainty) — never invent detail. Output ONLY the JSON object."
)


async def _describe_template(
    client, frames: list[Path], timestamps: list[float], transcript: str,
    duration: int,
) -> dict:
    if not frames:
        return {"error": "no frames could be sampled"}
    head = (
        f"Total duration: ~{duration}s.\n"
        f"Transcript:\n{transcript[:6000] or '(no speech detected)'}\n\n"
        f"{len(frames)} frames follow, in chronological order, at approx "
        f"timestamps (seconds): {timestamps}.\n"
        "Look hard at the COMPOSITION of each frame: is it split/stacked "
        "(e.g. speaker in one half, a screen-recording or text in another) or "
        "picture-in-picture? If so, that shared-screen layout is the "
        "distinctive feature — do not report it as alternating shots."
    )
    content: list[dict] = [{"type": "text", "text": head}]
    for f in frames:
        b64 = base64.b64encode(f.read_bytes()).decode()
        # 'high' detail: layout/composition (splits, PIP) is the whole point
        # of this analysis, so the extra image tokens are worth it. This is a
        # one-shot, occasional, high-value pass — not a hot path.
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        })
    try:
        res = await client.chat.completions.create(
            model=settings.llm_model if "gpt-4o" in settings.llm_model else "gpt-4o",
            messages=[
                {"role": "system", "content": _INSPECT_SYSTEM},
                {"role": "user", "content": content},
            ],
            max_tokens=3000,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        return json.loads(res.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001 — surface honestly, never fake
        return {"error": f"design inspection failed: {e}"}


async def inspect_file(path: str) -> dict:
    """Inspect a local video file → a structured production template.

    Returns {status, transcript, duration, template, frames_analyzed} on
    success, or {status, note} on failure/unsupported."""
    client = _client()
    if client is None:
        return {
            "status": "unsupported",
            "note": "No OpenAI key — add it in Settings to inspect style references.",
        }
    src = Path(path)
    if not src.is_file():
        return {"status": "failed", "note": "file not found on disk"}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = tmp / "audio.mp3"
        duration = await _probe_duration(src)
        have_audio = await _extract_audio(src, audio)
        frames, duration, interval = await _extract_spread_frames(
            src, tmp, duration, _INSPECT_FRAMES
        )
        timestamps = [round(i * interval, 1) for i in range(len(frames))]
        transcript = await _transcribe(client, audio) if have_audio else ""
        template = await _describe_template(
            client, frames, timestamps, transcript, duration
        )

    if not frames and not transcript:
        return {
            "status": "failed",
            "note": "could not extract audio or frames (unsupported format?)",
        }
    if not template or "error" in template:
        return {
            "status": "failed",
            "note": (template or {}).get("error", "empty template"),
            "transcript": transcript,
            "duration": duration,
        }
    return {
        "status": "done",
        "transcript": transcript,
        "duration": duration,
        "template": template,
        "frames_analyzed": len(frames),
    }


def template_to_fingerprint(template: dict) -> dict:
    """Map a rich template down to the legacy fingerprint shape so the Media
    Library 'What it sees' card keeps rendering for style references."""
    caps = template.get("captions") or {}
    pac = template.get("pacing") or {}
    segs = template.get("segments") or []
    structure = " → ".join(
        str(s.get("role", "")) for s in segs if s.get("role")
    ) or str(template.get("format_type", ""))
    layout = template.get("layout") or {}
    ltype = str(layout.get("type") or "")
    visual = template.get("vibe", "") or template.get("color_palette", "")
    if ltype and ltype != "full_frame":
        visual = f"{ltype} layout — {layout.get('description', '')}".strip(" —") + (
            f" · {visual}" if visual else ""
        )
    feats = template.get("distinctive_features") or []
    return {
        "hook": template.get("hook", ""),
        "structure": structure,
        "pacing": pac.get("notes", "") or pac.get("energy", ""),
        "captions": caps.get("look", "") or caps.get("position", ""),
        "visual_style": visual,
        # Lead replication tips with what's distinctive, then the recipe.
        "replication_tips": (
            [*feats, *(template.get("replication_recipe") or [])]
            if isinstance(feats, list) else (template.get("replication_recipe") or [])
        ),
    }


def template_to_notes(template: dict) -> str:
    """Flatten a template into plain notes for the media card and the scene-
    plan grounding (video_plan._grounding reads style_reference notes)."""
    if not template:
        return ""
    lines: list[str] = []
    if template.get("style_name"):
        lines.append(f"Style: {template['style_name']}")
    if template.get("summary"):
        lines.append(str(template["summary"]))
    feats = template.get("distinctive_features") or []
    if isinstance(feats, list) and feats:
        lines.append("Distinctive: " + "; ".join(str(f) for f in feats))
    elif feats:
        lines.append(f"Distinctive: {feats}")
    layout = template.get("layout") or {}
    if layout.get("type") and layout.get("type") != "full_frame":
        desc = str(layout.get("description") or "")
        lines.append(f"Layout: {layout['type']}" + (f" — {desc}" if desc else ""))
    caps = template.get("captions") or {}
    if caps.get("present"):
        lines.append(
            f"Captions: {caps.get('position', '')} — {caps.get('look', '')}".strip(" —")
        )
    logo = template.get("logo") or {}
    if logo.get("present"):
        lines.append(
            f"Logo: {logo.get('position', '')} ({logo.get('persistence', '')})"
        )
    music = (template.get("audio") or {}).get("music") or {}
    if music.get("present"):
        lines.append(f"Music: {music.get('type', '')} — {music.get('mood', '')}".strip(" —"))
    recipe = template.get("replication_recipe") or []
    if recipe:
        lines.append("Replicate: " + "; ".join(str(r) for r in recipe))
    return "\n".join(line for line in lines if line)


__all__ = [
    "inspect_file",
    "template_to_fingerprint",
    "template_to_notes",
]
