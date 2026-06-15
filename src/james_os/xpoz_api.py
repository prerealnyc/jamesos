"""Xpoz social-intelligence API — account status + cross-platform search.

Mounted under /research/social/* (already proxied by the Next rewrite for
/research/:path*). Auth + tenant scoping come from the global middleware in
main.py, so this is a plain APIRouter with no Depends.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import social_saved, xpoz_intel
from .content import generate_content
from .models import ContentBrief, ContentDraft

router = APIRouter()


async def _draft_from_text(
    text: str, platform: str, author: str | None,
    source_platform: str | None, fmt: str, extra: str = "",
) -> ContentDraft:
    """Shared: trend post → on-brand draft via the content engine + voice-QA."""
    src = (f"a high-performing {source_platform} post" if source_platform
           else "a high-performing social post")
    by = f" by @{author}" if author else ""
    steer = (
        f"Model this on {src}{by} that is going viral in our niche RIGHT NOW. "
        f"Match its HOOK pattern, structure and pacing — but write it 100% in "
        f"our brand voice about our world. Do NOT copy the post's words, "
        f"claims, or specifics. Reference post:\n\"\"\"\n{text[:2500]}\n\"\"\""
    )
    if extra.strip():
        steer += f"\n\nAlso: {extra.strip()}"
    brief = ContentBrief(
        platform=platform, format=fmt,
        topic="a piece inspired by what's trending in our niche right now",
        extra_instructions=steer,
    )
    return await generate_content(brief)


class SocialSearchRequest(BaseModel):
    query: str
    platforms: list[str] | None = None        # subset of x/instagram/tiktok/reddit
    limit: int = Field(default=10, ge=1, le=50)
    start_date: str | None = None             # "YYYY-MM-DD"
    min_likes: int = Field(default=10000, ge=0, le=10_000_000)


class TrendingRequest(BaseModel):
    niche: str
    platforms: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=30)
    days: int = Field(default=7, ge=1, le=90)
    min_likes: int = Field(default=10000, ge=0, le=10_000_000)


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
        req.query, platforms=req.platforms, limit=req.limit,
        start_date=req.start_date, min_likes=req.min_likes,
    )


@router.post("/research/social/trending")
async def xpoz_trending(req: TrendingRequest) -> dict:
    """Top recent posts in a niche, ranked by engagement — content fuel."""
    return await xpoz_intel.trending_in_niche(
        req.niche, platforms=req.platforms, limit=req.limit,
        days=req.days, min_likes=req.min_likes,
    )


@router.post("/research/social/draft-from-post", response_model=ContentDraft)
async def xpoz_draft_from_post(req: DraftFromPostRequest) -> ContentDraft:
    """Turn a trending post into a brand-voice draft (lands in the approval
    queue — 'inspired by', never a copy; a verbatim rip FAILS voice-QA)."""
    return await _draft_from_text(
        req.text, req.platform, req.author, req.source_platform,
        req.format, req.extra_instructions,
    )


# ── Saved-posts shelf: save from search → curate → generate ─────────

class SavePostRequest(BaseModel):
    post: dict                                # a search result row
    niche: str = ""                           # the search term it came from


class DraftSavedRequest(BaseModel):
    platform: str = "instagram"               # output platform
    format: str = "reel_script"
    extra_instructions: str = ""


@router.post("/research/social/saved")
async def xpoz_save(req: SavePostRequest) -> dict:
    """Save a post from search results onto the curation shelf (idempotent)."""
    return await social_saved.save_post(req.post, niche=req.niche)


@router.get("/research/social/saved")
async def xpoz_list_saved() -> dict:
    """The curation shelf — saved posts, newest first."""
    return {"saved": await social_saved.list_saved()}


@router.delete("/research/social/saved/{saved_id}")
async def xpoz_delete_saved(saved_id: UUID) -> dict:
    await social_saved.delete_saved(saved_id)
    return {"ok": True}


@router.post("/research/social/saved/{saved_id}/draft", response_model=ContentDraft)
async def xpoz_draft_saved(saved_id: UUID, req: DraftSavedRequest) -> ContentDraft:
    """Generate an on-brand draft from a saved post, then mark it drafted."""
    saved = await social_saved.get_saved(saved_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="saved post not found")
    draft = await _draft_from_text(
        saved["text"], req.platform, saved.get("author"),
        saved.get("source_platform"), req.format, req.extra_instructions,
    )
    await social_saved.mark_drafted(saved_id, draft.action_id)
    return draft


__all__ = ["router"]
