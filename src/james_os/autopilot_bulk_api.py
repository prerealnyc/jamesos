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

from typing import Literal

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from .autopilot_bulk import generate_bulk

router = APIRouter()


class BulkGenerateRequest(BaseModel):
    """How many pieces to generate in one shot, and of what kind.

    `count` is the number of pieces total. `mix` picks the content type:
    "video" → all video reels, "text" → all text+image posts, "mixed"
    (default) → 50/50 with text getting the odd one. `days` is an alias
    the UI may use when it thinks in "N days of content" — if `count` is
    0/omitted, `days` is used as the count instead.
    """

    count: int = Field(default=0, ge=0, le=50)
    days: int = Field(default=0, ge=0, le=50)
    mix: Literal["mixed", "video", "text"] = "mixed"


_NOTES = {
    "mixed": (
        "Bulk generation running in the background. Text+image posts "
        "appear in the Approval Queue shortly; video reels land there "
        "as each render finishes (a few minutes each)."
    ),
    "video": (
        "Video generation running in the background. Each reel lands in "
        "the Approval Queue as its render finishes (a few minutes each)."
    ),
    "text": (
        "Post generation running in the background. Text+image posts "
        "appear in the Approval Queue shortly."
    ),
}


@router.post("/autopilot/bulk", status_code=202)
async def autopilot_bulk(
    req: BulkGenerateRequest, background: BackgroundTasks
) -> dict:
    """Generate N pieces of content (videos, text posts, or a 50/50 mix)
    in the background. Returns immediately; watch the Approval Queue and
    /video/productions for results."""
    n = req.count or req.days
    background.add_task(generate_bulk, req.count, req.days, mix=req.mix)
    return {
        "started": True,
        "requested": n,
        "mix": req.mix,
        "note": _NOTES[req.mix],
    }


__all__ = ["router"]
