"""HTTP surface for competitor intelligence.

    GET  /competitors/list                the roster (candidates + tracked)
    POST /competitors/discover            niche → ranked candidates  (background)
    GET  /competitors/discover/{job_id}   poll the discovery job
    POST /competitors/add                 add one by handle (verified on the way in)
    POST /competitors/import-watchlist    adopt the existing research watchlist
    POST /competitors/sync                pull every tracked competitor (background)
    GET  /competitors/sync/{job_id}       poll the sync job
    GET  /competitors/posts               the saved shelf
    GET  /competitors/shelf               how much we hold
    GET  /competitors/top                 the highest-ranked pages in the niche
    POST /competitors/analyze             run the visual eyes  (background)
    GET  /competitors/analyze/{job_id}    poll the analysis job
    GET  /competitors/analyses            what the eyes saw, per post
    POST /competitors/{id}/status         candidate | tracked | rejected

Two hard-won rules from this codebase are load-bearing here:

  * Discovery and sync both make several slow provider calls (30–60s), which
    is well past the edge proxy's synchronous window. Both are BACKGROUND
    JOBS that return a job_id immediately; anything else returns a 500 to the
    browser that looks like a bug in the feature.
  * The tenant is captured BEFORE `add_task` and passed explicitly. A
    background task does not inherit the request's tenant contextvar, and the
    roster refresh in research_roster_api.py shows what that costs: 794
    tenants, and every scrape landed in the default one.

Static routes are declared before `/{competitor_id}` so they aren't swallowed
as an id. The roster read is `/competitors/list`, not a bare `/competitors`,
because the dashboard page owns that path and a rewrite on it would shadow
the page.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from . import competitor_sync, competitor_vision, competitors

router = APIRouter()

# In-memory job stores. Same shape as the post-ideas / script-batch jobs:
# capture a local reference to the dict entry, and only ever prune FINISHED
# entries so a running job can never be evicted out from under its own task.
_DISCOVER_JOBS: dict[str, dict] = {}
_SYNC_JOBS: dict[str, dict] = {}
_ANALYZE_JOBS: dict[str, dict] = {}
_MAX_JOBS = 30


def _prune(store: dict[str, dict]) -> None:
    if len(store) <= _MAX_JOBS:
        return
    for key in [k for k, v in store.items() if v.get("status") in ("done", "failed")]:
        if len(store) <= _MAX_JOBS:
            break
        store.pop(key, None)


def _tenant() -> UUID | None:
    from .db import _request_tenant
    try:
        return _request_tenant.get()
    except LookupError:
        return None


# ── discovery ─────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    niche: str = Field(..., min_length=2)
    platforms: list[str] = Field(default_factory=lambda: list(competitors.PLATFORMS))
    limit: int = 12
    min_followers: int = 1000
    screen: bool = True
    use_research: bool = True


@router.post("/competitors/discover", status_code=202)
async def competitors_discover(
    req: DiscoverRequest, background: BackgroundTasks
) -> dict:
    """Find who owns a niche. Two independent paths — Xpoz keyword search and
    live research with every named handle verified — merged, ranked by
    engagement rate, screened for false positives, and persisted."""
    tid = _tenant()
    job_id = str(uuid4())
    _DISCOVER_JOBS[job_id] = {"status": "running", "niche": req.niche}
    _prune(_DISCOVER_JOBS)
    plats = [p for p in req.platforms if p in competitors.PLATFORMS]
    niche, limit = req.niche.strip(), max(1, min(req.limit, 30))
    floor, screen, research = req.min_followers, req.screen, req.use_research

    async def _run() -> None:
        try:
            res = await competitors.discover(
                niche, platforms=plats, limit=limit, min_followers=floor,
                screen=screen, use_research=research, tenant_id=tid)
            _DISCOVER_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _DISCOVER_JOBS[job_id] = {"status": "failed", "error": str(e)[:300],
                                      "candidates": []}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/discover/{job_id}")
async def competitors_discover_get(job_id: str) -> dict:
    job = _DISCOVER_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "discovery job not found (expired or unknown)")
    return {"job_id": job_id, **job}


# ── roster ────────────────────────────────────────────────────────────

@router.get("/competitors/list")
async def competitors_list(status: str = "", platform: str = "") -> dict:
    rows = await competitors.list_competitors(status=status, platform=platform)
    return {"competitors": rows, "count": len(rows),
            "platforms": list(competitors.PLATFORMS)}


class AddRequest(BaseModel):
    platform: str
    handle: str
    name: str = ""
    niche: str = ""
    status: str = "tracked"


@router.post("/competitors/add", status_code=201)
async def competitors_add(req: AddRequest) -> dict:
    try:
        row = await competitors.add_competitor(
            req.platform, req.handle, name=req.name,
            niche=req.niche, status=req.status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if row is None:
        raise HTTPException(502, "could not store the competitor")
    return row


@router.post("/competitors/import-watchlist")
async def competitors_import_watchlist() -> dict:
    """Adopt the curated research watchlist as tracked competitors — so this
    starts with the cohort already built by hand rather than an empty table."""
    return await competitors.import_watchlist()


# ── sync ──────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    limit: int = 24
    days: int = 90
    video_cap: int = 3


@router.post("/competitors/sync", status_code=202)
async def competitors_sync(req: SyncRequest, background: BackgroundTasks) -> dict:
    """Pull every tracked competitor's recent posts and persist them, media
    included. Runs in the background — a dozen competitors is minutes."""
    tid = _tenant()
    job_id = str(uuid4())
    _SYNC_JOBS[job_id] = {"status": "running"}
    _prune(_SYNC_JOBS)
    limit, days, cap = max(1, min(req.limit, 60)), max(1, req.days), max(0, req.video_cap)

    async def _run() -> None:
        try:
            res = await competitor_sync.sync_all(
                limit=limit, days=days, video_cap=cap, tenant_id=tid)
            _SYNC_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _SYNC_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/sync/{job_id}")
async def competitors_sync_get(job_id: str) -> dict:
    job = _SYNC_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "sync job not found (expired or unknown)")
    return {"job_id": job_id, **job}


# ── the shelf ─────────────────────────────────────────────────────────

@router.get("/competitors/posts")
async def competitors_posts(
    competitor_id: str = "", platform: str = "", media_type: str = "",
    sort: str = "engagement", limit: int = Query(default=60, le=200),
) -> dict:
    rows = await competitor_sync.list_posts(
        competitor_id=competitor_id, platform=platform,
        media_type=media_type, sort=sort, limit=limit)
    return {"posts": rows, "count": len(rows)}


@router.get("/competitors/shelf")
async def competitors_shelf() -> dict:
    return await competitor_sync.shelf_stats()


# ── ranking ───────────────────────────────────────────────────────────

@router.get("/competitors/top")
async def competitors_top(
    limit: int = Query(default=10, le=100), measured_only: bool = True,
) -> dict:
    """The highest-ranked pages for the niche, scored on measured posts:
    median engagement rate x log10(followers). Median so one viral post
    cannot define an account; log10 so reach counts without simply
    re-sorting the list by audience size."""
    rows = await competitors.top_competitors(
        limit=limit, measured_only=measured_only)
    return {"competitors": rows, "count": len(rows),
            "basis": "median engagement rate x log10(followers)"}


@router.post("/competitors/rerank")
async def competitors_rerank() -> dict:
    """Re-rank from the posts on the shelf. Runs automatically after every
    sync; exposed for when posts arrive by another route."""
    ranked = await competitors.recompute_ranks()
    return {"ranked": ranked, "count": len(ranked)}


# ── the visual eyes ───────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    competitor_id: str = ""
    post_cap: int = 8
    video_cap: int = 3


@router.post("/competitors/analyze", status_code=202)
async def competitors_analyze(
    req: AnalyzeRequest, background: BackgroundTasks
) -> dict:
    """Look at what they post: stills through the design eye, reels through
    perception, every post through a text read. Background — a vision pass
    over ten competitors is minutes, not seconds."""
    tid = _tenant()
    job_id = str(uuid4())
    _ANALYZE_JOBS[job_id] = {"status": "running"}
    _prune(_ANALYZE_JOBS)
    cid = req.competitor_id.strip()
    pc, vc = max(1, min(req.post_cap, 40)), max(0, min(req.video_cap, 20))

    async def _run() -> None:
        try:
            if cid:
                res = await competitor_vision.analyze_competitor(
                    cid, post_cap=pc, video_cap=vc, tenant_id=tid)
            else:
                res = await competitor_vision.analyze_all(
                    post_cap=pc, video_cap=vc, tenant_id=tid)
            _ANALYZE_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _ANALYZE_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/analyze/{job_id}")
async def competitors_analyze_get(job_id: str) -> dict:
    job = _ANALYZE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "analysis job not found (expired or unknown)")
    return {"job_id": job_id, **job}


@router.get("/competitors/analyses")
async def competitors_analyses(
    competitor_id: str = "", limit: int = Query(default=60, le=200),
) -> dict:
    rows = await competitor_vision.list_analyses(
        competitor_id=competitor_id, limit=limit)
    stats = await competitor_vision.analysis_stats()
    return {"analyses": rows, "count": len(rows), "stats": stats}


# ── status (declared last: /{competitor_id} would swallow the routes above) ──

class StatusRequest(BaseModel):
    status: str


@router.post("/competitors/{competitor_id}/status")
async def competitors_set_status(competitor_id: str, req: StatusRequest) -> dict:
    try:
        row = await competitors.set_status(competitor_id, req.status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if row is None:
        raise HTTPException(404, "competitor not found")
    return row
