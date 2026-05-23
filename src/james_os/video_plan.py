"""Scene-plan generator — script → a shootable plan on a fixed structure.

Default short-form structure (durations tunable; defined as data so it's
trivial to change later):

    hook   3s  — thumb-stopping B-roll (Runway, seeded by an AI still)
    intro  5s  — talking head (HeyGen avatar of James)
    meat  20s  — talking head (the substance)
    outro  5s  — talking head (the call to action)

Grounded, not generic: the brand VOICE (voice_corpus) shapes the voiceover,
and the reference library's STYLE FINGERPRINTS shape pacing/captions, so
plans replicate your formats in James's voice.
"""

import json
from uuid import UUID

from .db import acquire
from .llm import get_llm

# The default content structure. Each segment is one scene.
DEFAULT_STRUCTURE = [
    {"label": "hook",  "kind": "broll",        "source": None,     "duration": 3},
    {"label": "intro", "kind": "talking_head", "source": "avatar", "duration": 5},
    {"label": "meat",  "kind": "talking_head", "source": "avatar", "duration": 20},
    {"label": "outro", "kind": "talking_head", "source": "avatar", "duration": 5},
]

_PLAN_SYSTEM = """You are a short-form video director for a personal brand.
Fill this FIXED structure for a {aspect} {platform} video. Do NOT change the
order, count, kinds, or durations — only write the content of each segment.

STRUCTURE (in order):
{structure_desc}

For each segment return content:
- talking_head: "voiceover" = exactly what James says (in his voice, tight to
  the segment's seconds — ~2.5 words/second), and "on_screen_text" = a short
  caption. Leave "visual_prompt" "".
- broll (the hook): "visual_prompt" = a vivid, THUMB-STOPPING still-image
  description to animate (concrete subject, setting, lighting, motion, mood;
  NO text rendered in the image), and "on_screen_text" = a punchy 3-6 word
  hook caption. "voiceover" may be "" or a 1-line hook line.

Sound like the BRAND VOICE shown. Never invent facts.

Return STRICT JSON:
{{"title": str,
  "segments": [{{"voiceover": str, "on_screen_text": str, "visual_prompt": str}}]}}
(segments MUST be the same count and order as the structure)"""


async def _grounding(tenant_id: UUID | None) -> tuple[str, str, int]:
    async with acquire(tenant_id) as conn:
        voice_rows = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category' = 'voice_corpus' AND superseded_by IS NULL "
            "ORDER BY random() LIMIT 3"
        )
        style_rows = await conn.fetch(
            "SELECT title, notes FROM media_assets "
            "WHERE role = 'style_reference' ORDER BY created_at DESC LIMIT 4"
        )
        james_clips = await conn.fetchval(
            "SELECT count(*) FROM media_assets WHERE role = 'james_clip'"
        )
    voice = "\n---\n".join((r["raw_content"] or "")[:600] for r in voice_rows) or "(no voice corpus yet)"
    style = "\n".join(f"- {r['title']}: {(r['notes'] or '')[:300]}" for r in style_rows) \
        or "(no style references yet)"
    return voice, style, int(james_clips or 0)


def _structure_desc(structure: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(structure):
        what = "B-roll (no speaker)" if seg["kind"] == "broll" else f"talking head ({seg['source']})"
        lines.append(f"{i+1}. {seg['label']} — {seg['duration']}s — {what}")
    return "\n".join(lines)


async def generate_scene_plan(
    script: str,
    platform: str = "instagram",
    aspect: str = "9:16",
    structure: list[dict] | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    structure = structure or DEFAULT_STRUCTURE
    voice, style, james_clips = await _grounding(tenant_id)
    llm = get_llm()
    system = _PLAN_SYSTEM.format(
        aspect=aspect, platform=platform, structure_desc=_structure_desc(structure)
    )
    user = (
        f"BRAND VOICE (sound like this):\n{voice}\n\n"
        f"STYLE REFERENCES (match pacing/captions):\n{style}\n\n"
        f"SCRIPT / TOPIC:\n{script}"
    )
    try:
        out = await llm.complete_json(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1400,
            temperature=0.5,
        )
    except Exception as e:  # noqa: BLE001
        return {"title": "", "scenes": [], "error": f"planning failed: {e}"}

    segs = out.get("segments") or []
    scenes = []
    for i, seg_struct in enumerate(structure):
        filled = segs[i] if i < len(segs) else {}
        kind = seg_struct["kind"]
        source = seg_struct["source"]
        # Honor the structure; only swap avatar→james_clip if real clips exist
        if kind == "talking_head" and source == "james_clip" and james_clips == 0:
            source = "avatar"
        scenes.append({
            "index": i,
            "label": seg_struct["label"],
            "kind": kind,
            "source": source,
            "duration": seg_struct["duration"],
            "voiceover": str(filled.get("voiceover") or "").strip(),
            "on_screen_text": str(filled.get("on_screen_text") or "").strip(),
            "visual_prompt": str(filled.get("visual_prompt") or "").strip(),
        })
    return {"title": str(out.get("title") or "").strip(), "scenes": scenes,
            "structure": [s["label"] for s in structure],
            "james_clips_available": james_clips}


__all__ = ["generate_scene_plan", "DEFAULT_STRUCTURE"]
