"""Auto-compose a video — research → on-voice script → editable scene plan.

One call turns "what's trending" into a fully drafted, editable video:

  1. research the trending angle (reuse Autopilot's intel gathering),
  2. pick a trend-riding idea,
  3. write the SCRIPT through the content engine (so it's on-voice, guideline-
     compliant, and voice-QA'd — brand memory applies automatically),
  4. break it into per-scene text + voiceover via the scene planner (which
     pulls the reference-video style fingerprints).

Nothing renders here — it returns an editable plan for the visual editor.
"""

from uuid import UUID

from .content import generate_content
from .models import ContentBrief
from .video_plan import generate_scene_plan


async def compose_video(
    topic_hint: str = "",
    platform: str = "instagram",
    aspect: str = "9:16",
    tenant_id: UUID | None = None,
) -> dict:
    from .autopilot import _gather_intel, generate_ideas

    # 1) trending intel (live research + scraped trends), best-effort
    intel = await _gather_intel(
        {"topic_hint": topic_hint, "research_focus": ""}, tenant_id
    )

    # 2) a trend-riding idea (falls back to the hint if no live research)
    idea = None
    if intel:
        ideas = await generate_ideas(1, intel, tenant_id)
        idea = ideas[0] if ideas else None
    if idea is None:
        if not topic_hint.strip():
            return {
                "error": "No live research available and no topic hint given. "
                "Add a Perplexity key, or type a topic to compose from.",
                "scenes": [],
            }
        idea = {"title": topic_hint, "topic": topic_hint, "trend_basis": ""}

    # 3) on-voice script via the content engine (voice + guidelines + guardrails + QA)
    draft = await generate_content(
        ContentBrief(
            platform=platform, format="reel_script",
            pillar=idea.get("pillar", ""), topic=idea["topic"],
            extra_instructions="Single-arc first-person story for a short video. "
                               "Match the reference-video style. Decisive ownership, "
                               "concrete specifics — no listicle, no clichés.",
        ),
        tenant_id,
    )
    script = (draft.draft or "").strip() or idea["topic"]

    # 4) editable scene plan (pulls reference style fingerprints + voice)
    plan = await generate_scene_plan(script, platform, aspect, tenant_id=tenant_id)

    return {
        "idea": idea,
        "script": script,
        "voice_score": draft.voice_score,
        "voice_status": draft.status,
        "title": plan.get("title") or idea.get("title", ""),
        "scenes": plan.get("scenes", []),
        "intel": {
            "provider": intel.get("provider"),
            "summary": intel.get("summary", "")[:600],
            "sources": intel.get("sources", []),
        } if intel else None,
        "platform": platform,
        "aspect": aspect,
    }


async def compose_video_batch(
    n: int = 10,
    platform: str = "instagram",
    aspect: str = "9:16",
    tenant_id: UUID | None = None,
) -> dict:
    """Generate N ready-to-pick topic+script options from live trend DATA —
    no topic input. Pulls the tracked influencers' posts (Xpoz) + niche
    trending + research via _gather_intel, ideates N trend-grounded topics,
    and writes a full on-voice reel script for each (in parallel). The caller
    reviews and picks which one to render. Nothing renders here.

    Returns {scripts: [{title, topic, trend_basis, script, voice_score,
    voice_status}], count, niche, error}.
    """
    import asyncio

    from .autopilot import _gather_intel, generate_ideas, get_config, trend_steer

    n = max(1, min(int(n or 10), 10))

    # Steer purely from data: use the brand's configured niche (autopilot
    # topic_hint) as the secondary search seed — the tracked-creator posts are
    # pulled regardless of niche. No per-request topic from the user.
    try:
        cfg = await get_config(tenant_id)
    except Exception:  # noqa: BLE001
        cfg = {}
    niche = (cfg.get("topic_hint") or "").strip()

    intel = await _gather_intel(
        {"topic_hint": niche, "research_focus": cfg.get("research_focus", "")},
        tenant_id,
    )
    if not intel:
        return {"scripts": [], "count": 0, "niche": niche, "platform": platform,
                "aspect": aspect,
                "error": "No live trend data available — connect Xpoz / add "
                "tracked creators in Research (or a Perplexity key), then retry."}

    ideas = await generate_ideas(n, intel, tenant_id)
    if not ideas:
        return {"scripts": [], "count": 0, "niche": niche, "platform": platform,
                "aspect": aspect,
                "error": "Ideation produced no topics from the trend data."}

    sem = asyncio.Semaphore(5)

    async def _one(idea: dict) -> dict:
        async with sem:
            try:
                draft = await generate_content(
                    ContentBrief(
                        platform=platform, format="reel_script",
                        pillar=idea.get("pillar", ""), topic=idea["topic"],
                        extra_instructions=(
                            "Single-arc first-person story for a short video. "
                            "Decisive ownership, concrete specifics — no listicle, "
                            "no clichés."
                        ) + trend_steer(idea),
                    ),
                    tenant_id,
                )
                script = (draft.draft or "").strip()
                vscore, vstatus = draft.voice_score, draft.status
            except Exception as e:  # noqa: BLE001 — one bad script can't kill the batch
                script, vscore, vstatus = "", None, f"error: {e}"
            return {
                "title": idea.get("title") or idea.get("topic", ""),
                "topic": idea.get("topic", ""),
                "trend_basis": idea.get("trend_basis", ""),
                "script": script or idea.get("topic", ""),
                "voice_score": vscore,
                "voice_status": vstatus,
            }

    scripts = [s for s in await asyncio.gather(*[_one(i) for i in ideas]) if s.get("script")]
    return {
        "scripts": scripts, "count": len(scripts), "niche": niche,
        "platform": platform, "aspect": aspect, "error": None,
        "intel": {"provider": intel.get("provider"),
                  "sources": (intel.get("sources") or [])[:10]},
    }


__all__ = ["compose_video", "compose_video_batch"]
