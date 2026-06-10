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
