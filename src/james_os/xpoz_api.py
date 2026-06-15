"""Xpoz social-intelligence API — account status + cross-platform search.

Mounted under /research/social/* (already proxied by the Next rewrite for
/research/:path*). Auth + tenant scoping come from the global middleware in
main.py, so this is a plain APIRouter with no Depends.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import xpoz_intel

router = APIRouter()


class SocialSearchRequest(BaseModel):
    query: str
    platforms: list[str] | None = None        # subset of x/instagram/tiktok/reddit
    limit: int = Field(default=10, ge=1, le=50)
    start_date: str | None = None             # "YYYY-MM-DD"


@router.get("/research/social/account")
async def xpoz_account() -> dict:
    """Connected Xpoz account: plan + remaining usage. Cheap status probe —
    powers the 'is it connected / how much quota?' surface."""
    return await xpoz_intel.account_info()


@router.post("/research/social/search")
async def xpoz_search(req: SocialSearchRequest) -> dict:
    """Normalized brand-listening search across X / Instagram / TikTok / Reddit."""
    return await xpoz_intel.search_social(
        req.query, platforms=req.platforms, limit=req.limit, start_date=req.start_date
    )


__all__ = ["router"]
