"""The "What's changing next" board API.

GET /changes        → {applied_live, queued, done} — the roadmap, plain English.
POST /changes/refresh → run the interpreter over recent feedback (apply live
                        tweaks, queue code changes), then return the board.
POST /changes/{id}/done    → human marks a queued code change as shipped.
POST /changes/{id}/dismiss → drop an item.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from . import feedback_changes as FC
from .config import settings

router = APIRouter()


def _tenant():
    try:
        from .db import _request_tenant
        t = _request_tenant.get()
    except (LookupError, ImportError):
        t = None
    return t or settings.default_tenant_id


def _split(items: list[dict]) -> dict:
    return {
        "applied_live": [i for i in items if i["status"] == "applied"],
        "queued": [i for i in items if i["status"] == "queued"],
        "proposed": [i for i in items if i["status"] == "proposed"],
        "done": [i for i in items if i["status"] == "done"],
    }


@router.get("/changes")
async def changes_board() -> dict:
    return _split(await FC.list_changes(_tenant()))


@router.post("/changes/refresh")
async def changes_refresh() -> dict:
    """Interpret every recent video feedback into roadmap items — applies the
    live knobs, queues the code changes. Idempotent (dedupe)."""
    from .feedback_interpreter import interpret_recent_feedback
    tenant = _tenant()
    res = await interpret_recent_feedback(tenant)
    board = _split(await FC.list_changes(tenant))
    return {**res, **board}


@router.post("/changes/{change_id}/done")
async def changes_done(change_id: UUID) -> dict:
    if not await FC.mark_done(change_id, _tenant()):
        raise HTTPException(status_code=404, detail="change not found")
    return {"ok": True}


@router.post("/changes/{change_id}/dismiss")
async def changes_dismiss(change_id: UUID) -> dict:
    if not await FC.dismiss(change_id, _tenant()):
        raise HTTPException(status_code=404, detail="change not found")
    return {"ok": True}


__all__ = ["router"]
