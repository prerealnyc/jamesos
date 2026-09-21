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
    GET  /competitors/gallery             posts + media + analysis, for review
    POST /competitors/niche-reference     one niche picture onto the reference shelf
    POST /competitors/screen              re-read the candidates' bios, cull the
                                          off-niche ones
    POST /competitors/posts/replicate     the brand's verdict on one post
    GET  /competitors/top                 the highest-ranked pages in the niche
    POST /competitors/analyze             run the visual eyes  (background)
    GET  /competitors/analyze/{job_id}    poll the analysis job
    GET  /competitors/analyses            what the eyes saw, per post
    GET  /competitors/gap                 what they post that we don't
    GET  /competitors/status              where the shelf stands (from DATA)
    POST /competitors/refresh             the whole chain, one job
    GET  /competitors/picks               what the brand picked
    POST /competitors/first-posts         picks → drafts in the queue
    POST /competitors/templatize          templatized reels → your template library
    POST /competitors/media/fetch         Apify → download + store the files
    POST /competitors/profiles/build      roll posts+analyses into profiles
    GET  /competitors/profiles            per-competitor: cadence, formats,
                                          topics, hooks, design, strategy
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

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field

from . import (
    competitor_gap,
    competitor_kickoff,
    competitor_media,
    competitor_profile,
    competitor_sync,
    competitor_template,
    competitor_vision,
    competitors,
    niche_reference,
)

router = APIRouter()

# In-memory job stores. Same shape as the post-ideas / script-batch jobs:
# capture a local reference to the dict entry, and only ever prune FINISHED
# entries so a running job can never be evicted out from under its own task.
_DISCOVER_JOBS: dict[str, dict] = {}
_SYNC_JOBS: dict[str, dict] = {}
_ANALYZE_JOBS: dict[str, dict] = {}
_PROFILE_JOBS: dict[str, dict] = {}
_MEDIA_JOBS: dict[str, dict] = {}
_REFRESH_JOBS: dict[str, dict] = {}
_FIRST_JOBS: dict[str, dict] = {}
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
    # 500, not 200: the roster asks for every post across every tracked
    # competitor in one read (240 for seven of them) and a lower cap turned
    # that into a 422 the UI showed as a raw validation blob.
    sort: str = "engagement", limit: int = Query(default=60, le=500),
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


# ── the review gallery: what the brand sees and picks from ────────────

@router.get("/competitors/gallery")
async def competitors_gallery(
    competitor_id: str = "", replicate: str = "", media_only: bool = True,
    # 500, not 200: the roster asks for every post across every tracked
    # competitor in one read (240 for seven of them) and a lower cap turned
    # that into a 422 the UI showed as a raw validation blob.
    sort: str = "engagement", limit: int = Query(default=60, le=500),
) -> dict:
    """Competitor posts with our stored media and what the eyes made of them.

    One query per screen: the brand is deciding "do I want one of these",
    which is much easier with the format and hook beside the picture."""
    posts = await competitor_sync.gallery(
        competitor_id=competitor_id, replicate=replicate,
        media_only=media_only, sort=sort, limit=limit)
    return {"posts": posts, "count": len(posts),
            "counts": await competitor_sync.replicate_counts()}


class ScreenRequest(BaseModel):
    niche: str = ""
    limit: int = 30


@router.post("/competitors/screen")
async def competitors_screen(req: ScreenRequest) -> dict:
    """Read the waiting candidates' bios and reject the ones in the wrong
    industry — the same cull discovery runs, on whatever is sitting there now.

    It matters because a keyword search has loose recall and ranks by size: a
    43M-follower Bollywood account outranks the right answer on "real estate
    investing", and follower counts cannot tell you an account is in the wrong
    industry. Anything rejected keeps its row and its reason — one click back.
    """
    tid = _tenant()
    niche = req.niche.strip()
    if not niche:
        from .brands import get_niche
        n = await get_niche(tid)
        if not n["confirmed"]:
            raise HTTPException(
                422, "This brand has not confirmed its niche yet — screening "
                     "against a guess would reject the wrong accounts.")
        niche = n["niche"]
    rows = await competitors.list_competitors(status="candidate", tenant_id=tid)
    if not rows:
        return {"screened": 0, "kept": 0, "rejected": 0, "verdicts": [],
                "note": "no candidates waiting"}
    return await competitors.screen_candidates(
        niche, rows[:max(1, min(req.limit, 60))], tenant_id=tid)


@router.post("/competitors/niche-reference", status_code=201)
async def competitors_niche_reference(
    file: UploadFile = File(...),
    source_url: str = Form(""),
    platform: str = Form(""),
    author: str = Form(""),
    interactions: int = Form(0),
    posted_at: str = Form(""),
    image_format: str = Form(""),
    hook: str = Form(""),
    why: str = Form(""),
    recipe: str = Form(""),
    niche: str = Form(""),
    ref: str = Form(""),
) -> dict:
    """File one high-engagement picture from the niche as a layout reference.

    The BYTES come with the request because the caller is the one holding the
    monitoring vendor's credentials — its image host will not serve us, and
    handing those credentials around so we could fetch the picture ourselves
    would be worse than uploading the copy the caller already has.

    A picture we already hold answers 201 with stored=false: a daily scan
    re-reads the same top images, and that is the normal case, not a failure.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty image")
    if len(data) > niche_reference.MAX_IMAGE_BYTES:
        raise HTTPException(413, "reference image too large (max 8 MB)")
    if not niche_reference.sniff(data):
        raise HTTPException(415, "that file is not a PNG, JPEG, GIF or WebP image")
    return await niche_reference.save_reference(
        _tenant(), image=data, source_url=source_url, platform=platform,
        author=author, interactions=interactions, posted_at=posted_at,
        fmt=image_format, hook=hook, why=why, recipe=recipe, niche=niche, ref=ref)


@router.get("/competitors/niche-references")
async def competitors_niche_reference_count() -> dict:
    """How many references this brand holds, and how many are already layouts."""
    return await niche_reference.count(_tenant())


class ReplicateRequest(BaseModel):
    post_id: str
    status: str = "saved"     # '' | saved | skipped | queued
    note: str = ""


@router.post("/competitors/posts/replicate")
async def competitors_post_replicate(req: ReplicateRequest) -> dict:
    """Save a competitor post as something to replicate — or clear it."""
    try:
        return await competitor_sync.set_replicate(
            req.post_id, req.status, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── media: Apify downloads what Xpoz cannot serve ─────────────────────

class MediaFetchRequest(BaseModel):
    competitor_id: str = ""
    limit: int = 30


@router.post("/competitors/media/fetch", status_code=202)
async def competitors_media_fetch(
    req: MediaFetchRequest, background: BackgroundTasks
) -> dict:
    """Download the actual image and video files through Apify and store them
    ourselves.

    Xpoz's media URLs are signed to its own session and 403 within hours;
    Apify re-scrapes fresh, public ones. Background because a profile scrape
    is ~35s and this runs one per competitor."""
    tid = _tenant()
    job_id = str(uuid4())
    _MEDIA_JOBS[job_id] = {"status": "running"}
    _prune(_MEDIA_JOBS)
    cid, lim = req.competitor_id.strip(), max(1, min(req.limit, 60))

    async def _run() -> None:
        try:
            if cid:
                comp = await competitors.get_competitor(cid, tenant_id=tid)
                if not comp:
                    _MEDIA_JOBS[job_id] = {"status": "failed",
                                           "error": "competitor not found"}
                    return
                res = await competitor_media.fetch_media_for_competitor(
                    comp, limit=lim, tenant_id=tid)
            else:
                res = await competitor_media.fetch_all_missing_media(
                    limit=lim, tenant_id=tid)
            _MEDIA_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _MEDIA_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/media/{job_id}")
async def competitors_media_job(job_id: str) -> dict:
    job = _MEDIA_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "media job not found (expired or unknown)")
    return {"job_id": job_id, **job}


@router.get("/competitors/status")
async def competitors_status() -> dict:
    """Where the shelf stands, read from the data rather than from a job.

    In-memory job state dies with the process and is invisible to a second
    worker; these counts survive both, so a UI can always say what is done
    and what is left even if it missed the job entirely."""
    return await competitor_sync.studio_status()


@router.post("/competitors/refresh", status_code=202)
async def competitors_refresh(background: BackgroundTasks, limit: int = 30) -> dict:
    """Pull → download → analyse → profile, in one job. Poll
    GET /competitors/status for progress; it reads the database, so it stays
    correct across restarts and workers."""
    tid = _tenant()
    job_id = str(uuid4())
    _REFRESH_JOBS[job_id] = {"status": "running", "stage": "starting"}
    _prune(_REFRESH_JOBS)
    lim = max(1, min(limit, 60))

    def _progress(p: dict) -> None:
        job = _REFRESH_JOBS.get(job_id)
        if job is not None:
            job.update(p)

    async def _run() -> None:
        try:
            res = await competitor_sync.full_refresh(
                tenant_id=tid, limit=lim, progress=_progress)
            _REFRESH_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _REFRESH_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/refresh/{job_id}")
async def competitors_refresh_job(job_id: str) -> dict:
    job = _REFRESH_JOBS.get(job_id)
    if not job:
        # Not an error: the job may have finished on another worker or before a
        # restart. The DATA is the answer, so hand back the status instead.
        return {"job_id": job_id, "status": "unknown",
                "note": "job state not held here — read /competitors/status",
                **(await competitor_sync.studio_status())}
    return {"job_id": job_id, **job}


@router.get("/competitors/picks")
async def competitors_picks(limit: int = 20, include_queued: bool = False) -> dict:
    """What the brand picked, best first."""
    return {"picks": await competitor_kickoff.picked_posts(
        limit=limit, include_queued=include_queued)}


class FirstPostsRequest(BaseModel):
    n: int = 5
    platform: str = "instagram"


@router.post("/competitors/first-posts", status_code=202)
async def competitors_first_posts(
    req: FirstPostsRequest, background: BackgroundTasks
) -> dict:
    """Turn the brand's picks into drafts in the approval queue.

    Replicate / Templatize / Idea steer the writer differently — the piece,
    the structure, or just the concept. Nothing is copied and every draft
    passes the same voice-QA gate as any other."""
    tid = _tenant()
    job_id = str(uuid4())
    _FIRST_JOBS[job_id] = {"status": "running"}
    _prune(_FIRST_JOBS)
    n, plat = max(1, min(req.n, 15)), req.platform

    def _progress(p: dict) -> None:
        job = _FIRST_JOBS.get(job_id)
        if job is not None:
            job.update(p)

    async def _run() -> None:
        try:
            res = await competitor_kickoff.generate_first_posts(
                n=n, platform=plat, tenant_id=tid, progress=_progress)
            _FIRST_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _FIRST_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/first-posts/{job_id}")
async def competitors_first_posts_job(job_id: str) -> dict:
    job = _FIRST_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found (expired or unknown)")
    return {"job_id": job_id, **job}


class TemplatizeRequest(BaseModel):
    post_id: str = ""
    limit: int = 10


@router.post("/competitors/templatize")
async def competitors_templatize(req: TemplatizeRequest) -> dict:
    """A templatized competitor reel becomes a renderable template you own.

    Mints a style_templates row from the read perception already produced, in
    the render engine's own closed vocabulary — so it works immediately with
    the template library, /templates/{id}/replicate and the autopilot's
    template picker, with no translation layer."""
    if req.post_id.strip():
        return await competitor_template.templatize_post(req.post_id.strip())
    return await competitor_template.templatize_all_picked(limit=req.limit)


# ── the gap: theirs vs ours ───────────────────────────────────────────

@router.get("/competitors/gap")
async def competitors_gap() -> dict:
    """What the peer group posts, what we have, and what we are missing.

    Every figure is computed from stored rows; only the narrative is written
    by a model, and it is handed those figures. Returns
    `insufficient_evidence` rather than a confident answer when either side
    is too thin to support one."""
    stored = await competitor_gap.latest_gap()
    if stored:
        return stored
    return await competitor_gap.content_gap()


@router.post("/competitors/gap/recompute")
async def competitors_gap_recompute() -> dict:
    """Measure the gap again now, and keep the result."""
    return await competitor_gap.content_gap()


# ── profiles: the rollup ──────────────────────────────────────────────

class ProfileRequest(BaseModel):
    competitor_id: str = ""
    synthesise: bool = True


@router.post("/competitors/profiles/build", status_code=202)
async def competitors_profiles_build(
    req: ProfileRequest, background: BackgroundTasks
) -> dict:
    """Roll the shelf into one profile per competitor: cadence, format mix,
    topic clusters, hook performance, posting windows, follower growth,
    design signature, and the growth strategy those facts add up to.

    Every number is computed from stored rows; only the narrative is written
    by a model, and it is handed the numbers rather than asked for them."""
    tid = _tenant()
    job_id = str(uuid4())
    _PROFILE_JOBS[job_id] = {"status": "running"}
    _prune(_PROFILE_JOBS)
    cid, synth = req.competitor_id.strip(), req.synthesise

    async def _run() -> None:
        try:
            if cid:
                res = await competitor_profile.build_profile(
                    cid, synthesise=synth, tenant_id=tid)
            else:
                res = await competitor_profile.build_all_profiles(
                    synthesise=synth, tenant_id=tid)
            _PROFILE_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _PROFILE_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/competitors/profiles/{job_id}")
async def competitors_profiles_job(job_id: str) -> dict:
    job = _PROFILE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "profile job not found (expired or unknown)")
    return {"job_id": job_id, **job}


@router.get("/competitors/profiles")
async def competitors_profiles() -> dict:
    rows = await competitor_profile.list_profiles()
    return {"profiles": rows, "count": len(rows)}


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
