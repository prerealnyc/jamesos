"""Xpoz social-intelligence API — account status + cross-platform search.

Mounted under /research/social/* (already proxied by the Next rewrite for
/research/:path*). Auth + tenant scoping come from the global middleware in
main.py, so this is a plain APIRouter with no Depends.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import xpoz_intel
from .content import generate_content
from .models import ContentBrief, ContentDraft

router = APIRouter()


class SocialSearchRequest(BaseModel):
    query: str
    platforms: list[str] | None = None        # subset of x/instagram/tiktok/reddit
    limit: int = Field(default=10, ge=1, le=50)
    start_date: str | None = None             # "YYYY-MM-DD"


class TrendingRequest(BaseModel):
    niche: str
    platforms: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=30)
    days: int = Field(default=7, ge=1, le=90)


class DraftFromPostRequest(BaseModel):
    """Turn a discovered trending post into an on-brand draft."""
    text: str                                 # the trending post's text
    platform: str = "instagram"               # OUTPUT platform for the draft
    author: str | None = None                 # who posted the trend (context)
    source_platform: str | None = None        # where the trend was found
    format: str = "reel_script"               # reel_script | post | caption | thread
    extra_instructions: str = ""


@router.get("/research/social/account")
async def xpoz_account() -> dict:
    """Connected Xpoz account: plan + remaining usage. Cheap status probe."""
    return await xpoz_intel.account_info()


@router.post("/research/social/search")
async def xpoz_search(req: SocialSearchRequest) -> dict:
    """Normalized brand-listening search across X / Instagram / TikTok / Reddit."""
    return await xpoz_intel.search_social(
        req.query, platforms=req.platforms, limit=req.limit, start_date=req.start_date
    )


@router.post("/research/social/trending")
async def xpoz_trending(req: TrendingRequest) -> dict:
    """Top recent posts in a niche, ranked by engagement — content fuel."""
    return await xpoz_intel.trending_in_niche(
        req.niche, platforms=req.platforms, limit=req.limit, days=req.days
    )


@router.post("/research/social/draft-from-post", response_model=ContentDraft)
async def xpoz_draft_from_post(req: DraftFromPostRequest) -> ContentDraft:
    """Turn a trending post into a brand-voice draft. Grounds the piece in
    the brand's own voice/thesis memory and runs the voice-QA gate, then
    lands in the approval queue — 'inspired by', never a copy. A verbatim
    rip would FAIL voice-QA by design."""
    src = (f"a high-performing {req.source_platform} post" if req.source_platform
           else "a high-performing social post")
    by = f" by @{req.author}" if req.author else ""
    steer = (
        f"Model this on {src}{by} that is going viral in our niche RIGHT NOW. "
        f"Match its HOOK pattern, structure and pacing — but write it 100% in "
        f"our brand voice about our world. Do NOT copy the post's words, "
        f"claims, or specifics. Reference post:\n\"\"\"\n{req.text[:2500]}\n\"\"\""
    )
    if req.extra_instructions.strip():
        steer += f"\n\nAlso: {req.extra_instructions.strip()}"
    brief = ContentBrief(
        platform=req.platform,
        format=req.format,
        topic="a piece inspired by what's trending in our niche right now",
        extra_instructions=steer,
    )
    return await generate_content(brief)


__all__ = ["router"]
