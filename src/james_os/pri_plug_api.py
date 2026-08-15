"""PRI plug endpoints — pull PreReal Intelligence into this brand's memory,
and retrieve PRI passages live for grounding.

  * GET  /pri/status            → is the plug configured? which provider?
  * POST /pri/pull   {silo}     → land a silo's brief + insights + docs into
                                  memory (idempotent). e.g. {"silo":"spaceport"}
  * POST /pri/retrieve {silo,query,k?} → ranked PRI passages (no ingest), for
                                  press/Academy grounding.

Tenant scoping is automatic (RLS contextvar set by the auth middleware), so a
brand only ever pulls into and reads from its own memory.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .adapters.pri_plug import get_pri_plug_provider
from .config import settings
from .pri_plug_ingest import pull_silo_into_memory, retrieve_from_pri

router = APIRouter(prefix="/pri", tags=["pri-plug"])


class PullRequest(BaseModel):
    silo: str


class RetrieveRequest(BaseModel):
    silo: str
    query: str
    k: int = 10


@router.get("/status")
async def pri_status() -> dict:
    prov = get_pri_plug_provider()
    url_set = bool((settings.pri_plug_url or "").strip())
    key_set = bool((settings.pri_plug_key or "").strip())
    return {
        "configured": url_set and key_set,
        "provider": prov.name,          # 'pri' when live, 'stub' otherwise
        "url_set": url_set,
        "key_set": key_set,
    }


@router.post("/pull")
async def pri_pull(req: PullRequest) -> dict:
    """Pull one PRI silo's intelligence into this brand's memory (idempotent)."""
    silo = (req.silo or "").strip()
    if not silo:
        raise HTTPException(status_code=400, detail="silo is required")
    try:
        return await pull_silo_into_memory(silo)
    except Exception as e:  # noqa: BLE001 — surface plug/ingest errors cleanly
        raise HTTPException(status_code=502, detail=f"PRI pull failed: {e}") from e


@router.post("/retrieve")
async def pri_retrieve(req: RetrieveRequest) -> dict:
    """Ranked PRI passages for grounding generated content (no ingest)."""
    silo = (req.silo or "").strip()
    query = (req.query or "").strip()
    if not silo or not query:
        raise HTTPException(status_code=400, detail="silo and query are required")
    try:
        chunks = await retrieve_from_pri(silo, query, req.k)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"PRI retrieve failed: {e}") from e
    return {"chunks": [asdict(c) for c in chunks], "count": len(chunks)}
