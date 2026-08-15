"""Press monitoring endpoints — scan the web/news for mentions of a brand,
file them into memory, and return a grounded digest.

  * GET  /press/status                       → is a live press provider connected?
  * POST /press/scan {brand, silo?, focus?, days?} → scan → file mentions as
        memory → return a digest grounded on the mentions + this brand's memory
        (including PRI intelligence pulled via the plug).

Mentions land as `category:press` events (no bespoke table — same substrate as
research), so Ask and retrieval surface them immediately. Tenant scoping is
automatic via the RLS contextvar.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .ingestion import ingest_many
from .press import generate_press_digest, get_press_provider, scan_to_events

router = APIRouter(prefix="/press", tags=["press"])


class ScanRequest(BaseModel):
    brand: str
    silo: str | None = None      # optional PRI silo to ground the digest on
    focus: str = ""
    days: int = 30


@router.get("/status")
async def press_status() -> dict:
    prov = get_press_provider()
    return {
        "provider": prov.name,                 # 'perplexity' when live, else 'stub'
        "live": prov.name != "stub",
        "note": None if prov.name != "stub" else
                "Set RESEARCH_PROVIDER=perplexity + PERPLEXITY_API_KEY for real press monitoring.",
    }


@router.post("/scan")
async def press_scan(req: ScanRequest) -> dict:
    """Scan press for a brand, file mentions to memory, return a grounded digest."""
    brand = (req.brand or "").strip()
    if not brand:
        raise HTTPException(status_code=400, detail="brand is required")
    days = max(1, min(req.days, 365))
    prov = get_press_provider()
    try:
        scan = await prov.scan(brand, focus=(req.focus or req.silo or "").strip(), days=days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"press scan failed: {e}") from e

    events = scan_to_events(scan)
    stored = await ingest_many(events) if events else []
    digest = await generate_press_digest(scan, silo=req.silo)

    return {
        "brand": brand,
        "silo": req.silo,
        "provider": scan.provider,
        "mentions": len(scan.mentions),
        "filed_to_memory": len(stored),
        "digest": digest,
        "note": None if scan.provider != "stub" else
                "Stub press provider — NOT real coverage. Set RESEARCH_PROVIDER=perplexity "
                "and add PERPLEXITY_API_KEY.",
        "sources": [{"title": m.title, "url": m.url} for m in scan.mentions[:20]],
    }
