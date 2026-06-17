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


@router.get("/compositions")
async def compositions_queue() -> dict:
    """Render-composition build queue: distinct reference LAYOUTS across the
    library, each tagged live (renderable today) or queued (a composition to
    build). Drives the render roadmap from the styles you upload."""
    from .compositions import composition_queue
    return {"compositions": await composition_queue(_tenant())}


@router.get("/higgsfield/souls")
async def higgsfield_souls_list() -> dict:
    """List the Higgsfield Soul IDs (custom-references / trained characters) on
    the connected account. Uses the in-vault Higgsfield key on the backend —
    surfaces an honest error if the key is missing or the API rejects it."""
    from .higgsfield_souls import configured, list_souls
    if not configured():
        return {"configured": False, "souls": [], "count": 0,
                "error": "Higgsfield API key + secret aren't set. Add them in Settings."}
    return await list_souls()


class SoulImageRequest(BaseModel):
    custom_reference_id: str          # the Soul ID
    prompt: str
    aspect: str = "9:16"
    strength: float = 0.8


@router.post("/higgsfield/soul-image")
async def higgsfield_soul_image(req: SoulImageRequest) -> dict:
    """Generate a single consistent CHARACTER image from a Soul ID (submit +
    poll inline). Returns the image URL on success — the building block for a
    Soul-ID-driven hero like James."""
    import asyncio

    from .higgsfield_souls import configured, generate_character_image, poll_request
    if not configured():
        raise HTTPException(status_code=400, detail="Higgsfield key/secret not set")
    sub = await generate_character_image(
        custom_reference_id=req.custom_reference_id.strip(),
        prompt=req.prompt, aspect_ratio=req.aspect, strength=req.strength,
    )
    if sub.get("error") or not sub.get("request_id"):
        raise HTTPException(status_code=502, detail=sub.get("error") or "submit failed")
    rid = sub["request_id"]
    for _ in range(60):
        res = await poll_request(rid)
        st = res.get("status")
        if st in ("completed", "succeeded") and res.get("image_url"):
            return {"status": "succeeded", "image_url": res["image_url"], "request_id": rid}
        if st in ("failed", "nsfw", "cancelled"):
            raise HTTPException(status_code=502, detail=f"generation {st}: {res.get('error') or ''}")
        await asyncio.sleep(3)
    return {"status": "processing", "request_id": rid,
            "note": "still rendering — check Higgsfield dashboard or retry status"}


class TrainSoulRequest(BaseModel):
    name: str = "James"


@router.post("/higgsfield/train-soul")
async def higgsfield_train_soul(req: TrainSoulRequest) -> dict:
    """Train a NEW Higgsfield Soul ID from the brand-hero photo library
    (role='hero_photo'). Sends the photos' public URLs to Higgsfield's
    custom-references endpoint. Needs 5–20 hero photos and a paid Higgsfield
    plan; returns the new reference id — training runs ~3–5 min, then refresh
    the Soul list and 'Use for James'."""
    from .higgsfield_souls import configured, create_reference
    from .media import list_media
    if not configured():
        return {"ok": False, "error": "Higgsfield API key + secret aren't set. Add them in Settings."}
    photos = await list_media(role="hero_photo", tenant_id=_tenant())
    urls = [p.get("uri") for p in photos if (p.get("uri") or "").startswith("http")]
    if len(urls) < 5:
        return {"ok": False,
                "error": f"Need at least 5 hero photos with public URLs to train a Soul; found {len(urls)}. "
                         "Upload more to the Hero library first."}
    res = await create_reference(name=req.name, image_urls=urls)
    if res.get("error") or not res.get("reference_id"):
        return {"ok": False, "error": res.get("error") or "training did not start",
                "trained_on": res.get("trained_on", 0)}
    return {"ok": True, "reference_id": res["reference_id"], "status": res.get("status"),
            "trained_on": res.get("trained_on"),
            "note": "Soul training started (~3–5 min). Refresh Soul IDs above, then click 'Use for James'."}


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
    aspect: str = ""        # blank → match the reference's measured aspect
                            # (ditto). An explicit value overrides for a recut.
    title: str = ""
    video_engine: str = ""  # B-roll animator: ''=default | runway | higgsfield


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
    # Ditto replication: match the reference's MEASURED aspect by default
    # (m["aspect"] now comes from the inspector's pixel/DAR probe, not a guess).
    # An explicit req.aspect wins so the user can recut to another shape.
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
            video_engine=(req.video_engine or "").strip().lower(),
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
    # Match the reference's measured aspect (an explicit req.aspect wins).
    aspect = (getattr(req, "aspect", "") or m["aspect"] or "9:16").strip() or "9:16"
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
