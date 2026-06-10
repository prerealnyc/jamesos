"""Style Template Library API — the "trending video styles" catalog.

Plain APIRouter (no prefix); main.py wires it with include_router. Auth +
tenant are set by the global middleware. Listing is fast and inline; the slow
Design-Inspector pass (ffmpeg + Whisper + vision) runs in a background task —
the UI polls GET /templates and the source asset's analysis_status flips
pending → done. Style references are auto-inspected on upload (see main.py);
this router also lets you (re-)inspect an existing reference on demand.
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from . import templates as T
from .config import settings
from .media import get_media_for_analysis

router = APIRouter()


def _tenant():
    try:
        from .db import _request_tenant

        t = _request_tenant.get()
    except (LookupError, ImportError):
        t = None
    return t or settings.default_tenant_id


@router.get("/templates")
async def templates_list() -> dict:
    """The style library — newest / highest-ranked first."""
    return {"templates": await T.list_templates(_tenant())}


@router.get("/templates/{template_id}")
async def template_detail(template_id: UUID) -> dict:
    row = await T.get_template(template_id, _tenant())
    if row is None:
        raise HTTPException(status_code=404, detail="template not found")
    return row


@router.post("/templates/inspect/{media_id}", status_code=202)
async def template_inspect(media_id: UUID, background: BackgroundTasks) -> dict:
    """Run the Design Inspector on a style_reference asset → build/refresh its
    named template. Returns immediately; the template appears in GET /templates
    once the background pass finishes."""
    tenant = _tenant()
    asset = await get_media_for_analysis(media_id, tenant)
    if asset is None:
        raise HTTPException(status_code=404, detail="reference not found")
    if asset.get("source_type") != "upload" or not asset.get("file_path"):
        raise HTTPException(
            status_code=400,
            detail="only uploaded video files can be inspected (URL references can't be downloaded for analysis)",
        )
    background.add_task(
        T.build_template_from_media, media_id, tenant_id=tenant,
        file_path=asset["file_path"],
    )
    return {
        "started": True,
        "media_id": str(media_id),
        "note": "Inspecting the whole video in the background — the style template "
                "appears in the library when it finishes.",
    }


class ReplicateRequest(BaseModel):
    script: str = ""        # a finished script (wins if provided)
    topic: str = ""         # else: write a script from this topic, in brand voice
    platform: str = "instagram"
    aspect: str = ""        # blank → use the template's aspect
    title: str = ""


@router.post("/templates/{template_id}/replicate", status_code=201)
async def template_replicate(
    template_id: UUID, req: ReplicateRequest, background: BackgroundTasks
) -> dict:
    """Phase 2 — produce a NEW brand video in this template's style. Loads the
    template, maps it to render params (mode, caption preset, music mood, logo,
    aspect, structure), writes a script from the topic if none is pasted, and
    kicks off a durable production. Returns the production + what was applied +
    any honest approximations (e.g. trending-audio substitution)."""
    tenant = _tenant()
    tpl = await T.get_template(template_id, tenant)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    if tpl.get("status") != "ready":
        raise HTTPException(status_code=400, detail="template is not ready to replicate")

    from .template_apply import map_template_to_render

    m = map_template_to_render(tpl.get("template") or {})
    aspect = (req.aspect or m["aspect"]).strip() or "9:16"

    # Content: a pasted script wins; otherwise write one from the topic in the
    # brand voice, steered to match this template's style.
    script = (req.script or "").strip()
    if not script:
        topic = (req.topic or "").strip()
        if not topic:
            raise HTTPException(
                status_code=400, detail="provide a script, or a topic to write one from"
            )
        from .content import generate_content
        from .models import ContentBrief

        style_hint = (tpl.get("summary") or tpl.get("name") or "").strip()
        brief = ContentBrief(
            platform=req.platform, format="reel_script", topic=topic,
            extra_instructions=(
                f"Write a short-form video script in this style: {style_hint}."
                if style_hint else ""
            ),
        )
        draft = await generate_content(brief, tenant_id=tenant)
        script = (draft.draft or "").strip()
        if not script:
            raise HTTPException(
                status_code=502,
                detail=f"couldn't write a script for that topic ({draft.note or draft.status})",
            )

    from .video_pipeline import run_production, start_production

    title = (req.title or f"{tpl.get('name', 'Style')} — replica").strip()[:200]
    logo_position = m["logo_position"] if m["logo_on"] else ""
    try:
        prod = await start_production(
            script, req.platform, aspect, title,
            None, m["mode"], m["caption_style"], m["image_style"],
            music_mood=m["music_mood"], logo_position=logo_position,
            structure=m["structure"], template_id=template_id,
            tenant_id=tenant,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    background.add_task(run_production, UUID(prod["id"]), tenant)
    return {
        "production": prod,
        "applied": {
            "mode": m["mode"],
            "caption_style": m["caption_style"] or "(auto)",
            "music_mood": m["music_mood"] or "(none)",
            "aspect": aspect,
            "logo": logo_position or "(none)",
        },
        "approximations": m["approximations"],
        "script_source": "pasted" if (req.script or "").strip() else "generated",
    }


class BrollReelRequest(BaseModel):
    script: str = ""        # a finished script (used as the content seed)
    topic: str = ""         # else: a topic the planner writes B-roll beats from
    platform: str = "instagram"
    seconds: int = 20       # total reel length (capped 5–30); split into ~5s beats
    engine: str = "higgsfield"  # per-render B-roll engine (higgsfield | runway)


@router.post("/templates/{template_id}/broll-reel", status_code=201)
async def template_broll_reel(
    template_id: UUID, req: BrollReelRequest, background: BackgroundTasks
) -> dict:
    """Produce a short, B-ROLL-ONLY reel in this template's style, animated by
    the chosen engine (Higgsfield by default). Builds an all-B-roll scene
    structure (~5s beats up to `seconds`), themes captions/music from the
    template, and renders B-roll via `engine` without changing the global
    default. Lands in the Approval Queue."""
    tenant = _tenant()
    tpl = await T.get_template(template_id, tenant)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    if tpl.get("status") != "ready":
        raise HTTPException(status_code=400, detail="template is not ready")

    seed = (req.script or req.topic or "").strip()
    if not seed:
        raise HTTPException(status_code=400, detail="provide a topic or a script for the reel")

    from .template_apply import map_template_to_render

    m = map_template_to_render(tpl.get("template") or {})
    aspect = m["aspect"] or "9:16"
    seconds = max(5, min(int(req.seconds or 20), 30))
    n = max(2, min(seconds // 5, 6))  # ~5s beats, 2–6 scenes
    structure = [
        {"label": f"beat_{i + 1}", "kind": "broll", "source": None, "duration": 5}
        for i in range(n)
    ]
    engine = (req.engine or "higgsfield").strip().lower()

    from .video_pipeline import run_production, start_production

    title = f"{tpl.get('name', 'Style')} — B-roll reel"[:200]
    prod = await start_production(
        seed, req.platform, aspect, title,
        None, "mixed", m["caption_style"], "",
        music_mood=m["music_mood"], logo_position="",
        structure=structure, template_id=template_id, video_engine=engine,
        tenant_id=tenant,
    )
    background.add_task(run_production, UUID(prod["id"]), tenant)
    return {
        "production": prod,
        "applied": {
            "engine": engine,
            "beats": n,
            "seconds": n * 5,
            "caption_style": m["caption_style"] or "(auto)",
            "music_mood": m["music_mood"] or "(none)",
            "aspect": aspect,
        },
        "approximations": m["approximations"],
    }


class TemplateUpdate(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    trending_score: float | None = None


@router.patch("/templates/{template_id}")
async def template_update(template_id: UUID, req: TemplateUpdate) -> dict:
    row = await T.rename_template(
        template_id,
        name=req.name,
        tags=req.tags,
        trending_score=req.trending_score,
        tenant_id=_tenant(),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="template not found or nothing to update")
    return row


@router.delete("/templates/{template_id}")
async def template_delete(template_id: UUID) -> dict:
    ok = await T.delete_template(template_id, _tenant())
    if not ok:
        raise HTTPException(status_code=404, detail="template not found")
    return {"deleted": True}


__all__ = ["router"]
