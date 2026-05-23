"""Scene-plan generator — script → a shootable, structured plan.

Turns an approved script into an ordered list of scenes the production
pipeline can render and stitch. Each scene is either a talking head
(HeyGen avatar OR one of James's real uploaded clips) or B-roll (AI-
generated from a visual prompt), with voiceover, on-screen caption, and a
duration.

Grounded, not generic:
  * the brand's VOICE (voice_corpus) shapes how the voiceover sounds,
  * the reference library's STYLE FINGERPRINTS (from the perception layer)
    shape structure / pacing / caption style — so plans replicate the
    formats you uploaded, in James's voice,
  * whether any james_clip footage exists decides if real-clip scenes are
    even offered (no pretending footage exists that doesn't).
"""

import json
from uuid import UUID

from .db import acquire
from .llm import get_llm

_PLAN_SYSTEM = """You are a short-form video director for a personal brand.
Turn the SCRIPT into a tight {aspect} video scene plan for {platform}.

Rules:
- 4 to 7 scenes, total ~30-60 seconds. Scene 1 MUST be a scroll-stopping hook.
- Each scene is either:
    "talking_head"  — the person speaks to camera. Set "source" to
                      "avatar" (AI avatar reads the voiceover) or
                      "james_clip" (use real footage) — only use
                      "james_clip" if real clips are available (stated below).
    "broll"         — no speaker on screen; supply a vivid "visual_prompt"
                      describing the footage to generate.
- Every scene has: voiceover (what's said; "" for silent b-roll),
  on_screen_text (short caption), duration (seconds, integer).
- Match the STYLE references' structure/pacing/caption style.
- The voiceover MUST sound like the brand voice shown. Never invent facts.

Return STRICT JSON:
{{"title": str,
  "scenes": [{{"kind": "talking_head"|"broll",
              "source": "avatar"|"james_clip"|null,
              "voiceover": str, "on_screen_text": str,
              "visual_prompt": str, "duration": int}}]}}"""


async def _grounding(tenant_id: UUID | None) -> tuple[str, str, int]:
    """Returns (voice_snippet, style_snippet, james_clip_count)."""
    async with acquire(tenant_id) as conn:
        voice_rows = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category' = 'voice_corpus' AND superseded_by IS NULL "
            "ORDER BY random() LIMIT 3"
        )
        style_rows = await conn.fetch(
            "SELECT title, notes, analysis FROM media_assets "
            "WHERE role = 'style_reference' ORDER BY created_at DESC LIMIT 4"
        )
        james_clips = await conn.fetchval(
            "SELECT count(*) FROM media_assets WHERE role = 'james_clip'"
        )
    voice = "\n---\n".join((r["raw_content"] or "")[:600] for r in voice_rows) or "(no voice corpus yet)"
    styles = []
    for r in style_rows:
        a = r["analysis"]
        if isinstance(a, str):
            a = json.loads(a)
        fp = (a or {}).get("fingerprint") or {}
        desc = r["notes"] or json.dumps(fp)[:400]
        styles.append(f"- {r['title']}: {desc[:400]}")
    style = "\n".join(styles) or "(no style references uploaded yet)"
    return voice, style, int(james_clips or 0)


async def generate_scene_plan(
    script: str,
    platform: str = "instagram",
    aspect: str = "9:16",
    tenant_id: UUID | None = None,
) -> dict:
    voice, style, james_clips = await _grounding(tenant_id)
    llm = get_llm()
    system = _PLAN_SYSTEM.format(aspect=aspect, platform=platform)
    user = (
        f"BRAND VOICE (sound like this):\n{voice}\n\n"
        f"STYLE REFERENCES (replicate this structure/pacing/captions):\n{style}\n\n"
        f"REAL JAMES CLIPS AVAILABLE: {james_clips} "
        f"({'you may use james_clip scenes' if james_clips else 'NONE — use avatar for all talking heads'}).\n\n"
        f"SCRIPT:\n{script}"
    )
    try:
        plan = await llm.complete_json(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500,
            temperature=0.5,
        )
    except Exception as e:  # noqa: BLE001
        return {"title": "", "scenes": [], "error": f"planning failed: {e}"}

    # normalize + guard
    scenes = []
    for i, s in enumerate(plan.get("scenes") or []):
        kind = s.get("kind") if s.get("kind") in ("talking_head", "broll") else "broll"
        source = s.get("source")
        if kind == "talking_head":
            source = source if source in ("avatar", "james_clip") else "avatar"
            if source == "james_clip" and james_clips == 0:
                source = "avatar"  # don't reference footage that doesn't exist
        else:
            source = None
        try:
            dur = max(2, min(int(s.get("duration") or 5), 15))
        except (ValueError, TypeError):
            dur = 5
        scenes.append({
            "index": i,
            "kind": kind,
            "source": source,
            "voiceover": str(s.get("voiceover") or "").strip(),
            "on_screen_text": str(s.get("on_screen_text") or "").strip(),
            "visual_prompt": str(s.get("visual_prompt") or "").strip(),
            "duration": dur,
        })
    return {"title": str(plan.get("title") or "").strip(), "scenes": scenes,
            "james_clips_available": james_clips}


__all__ = ["generate_scene_plan"]
