"""Bulk content generation API — one endpoint, fire-and-forget.

Exposes POST /autopilot/bulk: kick off `generate_bulk` (autopilot_bulk.py)
in a background task and return immediately, mirroring how /autopilot/run
is shaped in main.py.

Auth: the global middleware in main.py authenticates every request and
sets the tenant contextvar that `db.acquire()` reads — so this router adds
NO `Depends`. It's a plain APIRouter with no prefix; main.py wires it with
`app.include_router(...)`. The work runs in the background, so progress is
observed through the Approval Queue (the pending `actions` rows the batch
creates) and the video productions list, not this response.
"""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from .autopilot_bulk import generate_bulk

router = APIRouter()


class BulkGenerateRequest(BaseModel):
    """How many pieces to generate in one shot.

    `count` is the number of pieces total (split 50/50 text+image vs video,
    text gets the odd one). `days` is an alias the UI may use when it thinks
    in "N days of content" — if `count` is 0/omitted, `days` is used as the
    count instead.
    """

    count: int = Field(default=0, ge=0, le=50)
    days: int = Field(default=0, ge=0, le=50)


@router.post("/autopilot/bulk", status_code=202)
async def autopilot_bulk(
    req: BulkGenerateRequest, background: BackgroundTasks
) -> dict:
    """Generate N pieces of content (half text+image posts, half video
    reels) in the background. Returns immediately; watch the Approval Queue
    and /video/productions for results."""
    n = req.count or req.days
    background.add_task(generate_bulk, req.count, req.days)
    return {
        "started": True,
        "requested": n,
        "note": (
            "Bulk generation running in the background. Text+image posts "
            "appear in the Approval Queue shortly; video reels land there "
            "as each render finishes (a few minutes each)."
        ),
    }


__all__ = ["router"]
