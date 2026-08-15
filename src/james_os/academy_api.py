"""Academy endpoints — dump lessons/docs, generate a campaign from them.

  * POST /academy/dump   {title, content}         → file a lesson/source into the
        academy silo (indexed into memory).
  * GET  /academy/sources                         → list dumped academy sources.
  * POST /academy/campaign {topic, silo?, pieces?, channel?, direction?}
        → generate a content campaign GROUNDED on the academy lessons + any PRI
        intelligence (pass silo to lean on that project) + brand voice.

The app is single-tenant (James's brand), so this section is his by construction;
RLS scopes everything to the tenant. To dump FILES (PDF/Word/…) instead of text,
use the existing knowledge upload with silo=academy — same destination.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .academy import (
    ACADEMY_SILO,
    add_academy_source,
    generate_campaign,
    list_academy_sources,
)

router = APIRouter(prefix="/academy", tags=["academy"])


class DumpRequest(BaseModel):
    title: str = ""
    content: str


class CampaignRequest(BaseModel):
    topic: str
    silo: str | None = None
    pieces: int = 5
    channel: str = "mixed"
    direction: str = ""


@router.get("/sources")
async def academy_sources() -> dict:
    rows = await list_academy_sources()
    return {"silo": ACADEMY_SILO, "count": len(rows), "sources": rows}


@router.post("/dump")
async def academy_dump(req: DumpRequest) -> dict:
    if not (req.content or "").strip():
        raise HTTPException(status_code=400, detail="content is required")
    try:
        return await add_academy_source(title=req.title, content=req.content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"academy dump failed: {e}") from e


@router.post("/campaign")
async def academy_campaign(req: CampaignRequest) -> dict:
    if not (req.topic or "").strip():
        raise HTTPException(status_code=400, detail="topic is required")
    try:
        return await generate_campaign(
            req.topic, silo=req.silo, pieces=req.pieces,
            channel=req.channel, extra_context=req.direction,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign generation failed: {e}") from e
