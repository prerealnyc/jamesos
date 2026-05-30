"""FastAPI app — the public surface of JAMES OS."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ask import ask
from .config import settings
from .dashboard_api import router as dashboard_api_router
from .db import acquire, close_pool, init_pool
from .documents import document_to_events_async
from .ingestion import ingest, ingest_many, supersede_prior_document_versions
from .media import (
    ROLES as MEDIA_ROLES,
    create_media,
    delete_media,
    get_media_for_analysis,
    list_media,
    media_root,
    save_analysis,
    set_analysis_status,
    storage as media_storage,
    update_media,
)
from .perception import analyze_file, fingerprint_to_notes
from .models import (
    AskRequest,
    AskResponse,
    Event,
    EventCreate,
    ContentBrief,
    ContentDraft,
    MediaLinkRequest,
    MediaUpdate,
    MultiGenerateRequest,
    PlugIn,
    PostImageRequest,
    PlugInCreate,
    ResearchRequest,
    ResearchResponse,
    ResearchSourceOut,
    SceneRenderRequest,
    ScriptRequest,
    TrendDiscoverRequest,
    VideoComposeRequest,
    VideoGenerateRequest,
    VideoPlanRequest,
    VideoProduceRequest,
    WatchlistRefreshRequest,
    WatchlistUpdate,
)
from .content import generate_content
from .research import get_research_provider, research_to_events
from .trends import (
    discover_and_ingest,
    get_watchlist,
    list_trends,
    refresh_watchlist,
    set_watchlist,
    watchlist_by_platform,
)
from .video import list_video_jobs, refresh_video_job, submit_video_job
from .video_plan import generate_scene_plan
from .video_pipeline import (
    get_production,
    list_productions,
    render_one_scene,
    run_production,
    start_production,
)


async def _autopilot_scheduler() -> None:
    """In-process daily tick. Restart-safe: it runs the batch only if today's
    hasn't run yet (checks last_run_date), so a restart can't double-fire.
    Honest limit: scheduled runs only happen while the server is up."""
    import asyncio

    from .autopilot import get_config, run_batch, should_run_today

    while True:
        try:
            if should_run_today(await get_config()):
                await run_batch("scheduled")
        except Exception:  # noqa: BLE001 — a tick failure must not kill the loop
            pass
        await asyncio.sleep(1800)  # check every 30 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    await init_pool()
    # Overlay any tenant-saved API keys from the DB onto the .env baseline
    # so UI-entered credentials are live from the first request.
    from .credentials import load_into_settings

    await load_into_settings()
    # Reap any autopilot runs that were 'running' when the process died.
    from .autopilot import reap_orphaned_runs
    try:
        await reap_orphaned_runs()
    except Exception:  # noqa: BLE001 — never block startup on a reap failure
        pass
    scheduler = asyncio.create_task(_autopilot_scheduler())
    yield
    scheduler.cancel()
    await close_pool()


app = FastAPI(
    title="JAMES OS",
    description="Memory substrate for AI-native operations.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────── ui ──

_STATIC = Path(__file__).parent / "static"
_DASH = Path(__file__).parent / "dashboard"

# Dashboard compatibility API (must be registered before the static mount
# and before the root route so /api/* is owned by the router).
app.include_router(dashboard_api_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Polished JP Brand Manager dashboard is the landing page."""
    return FileResponse(_DASH / "index.html")


@app.get("/classic", include_in_schema=False)
async def classic_ui() -> FileResponse:
    """The minimal, fully-working substrate UI (ask / capture / docs / plug-ins)."""
    return FileResponse(_STATIC / "index.html")


@app.get("/settings", include_in_schema=False)
async def settings_ui() -> FileResponse:
    """Brand voice & guidelines, social connections, profile."""
    return FileResponse(_STATIC / "settings.html")


# Serve the dashboard's hashed assets. index.html references ./assets/...
app.mount("/assets", StaticFiles(directory=_DASH / "assets"), name="dash-assets")
# Uploaded reference/media files, served read-only.
app.mount(
    "/media-files",
    StaticFiles(directory=media_root()),
    name="media-files",
)


# ─────────────────────────────────────────────────────────────────────── ops ──

@app.get("/health")
async def health() -> dict[str, str]:
    async with acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


# ─────────────────────────────────────────────────────────────────── events ──

@app.post("/events", response_model=Event, status_code=201)
async def create_event(event: EventCreate) -> Event:
    return await ingest(event)


@app.get("/events", response_model=list[Event])
async def list_events(
    event_type: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
) -> list[Event]:
    sql = """
        SELECT id, tenant_id, event_type, payload, raw_content,
               embedding_model, source, participants, entities,
               parent_event_id, superseded_by, effective_at, expires_at,
               confidence, created_at, metadata
        FROM events
        WHERE superseded_by IS NULL
    """
    params: list[Any] = []
    if event_type:
        sql += " AND event_type = $1"
        params.append(event_type)
    sql += f" ORDER BY effective_at DESC LIMIT {int(limit)} OFFSET {int(offset)}"

    async with acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_event(r) for r in rows]


@app.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: UUID) -> Event:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, event_type, payload, raw_content,
                   embedding_model, source, participants, entities,
                   parent_event_id, superseded_by, effective_at, expires_at,
                   confidence, created_at, metadata
            FROM events WHERE id = $1
            """,
            event_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="event not found")
    return _row_to_event(row)


# ───────────────────────────────────────────────────────────────── plug-ins ──

@app.post("/plug-ins", response_model=PlugIn, status_code=201)
async def create_plug_in(payload: PlugInCreate) -> PlugIn:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO plug_ins (slot, name, content, applies_to)
            VALUES ($1, $2, $3::jsonb, $4::text[])
            RETURNING *
            """,
            payload.slot,
            payload.name,
            json.dumps(payload.content),
            payload.applies_to,
        )
    return _row_to_plug_in(row)


@app.get("/plug-ins", response_model=list[PlugIn])
async def list_plug_ins(slot: str | None = None) -> list[PlugIn]:
    sql = "SELECT * FROM plug_ins WHERE active = true"
    params: list[Any] = []
    if slot:
        sql += " AND slot = $1"
        params.append(slot)
    sql += " ORDER BY slot, name"
    async with acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_plug_in(r) for r in rows]


# ───────────────────────────────────────────────────────────── documents ──

@app.post("/ingest/document", status_code=201)
async def ingest_document(
    file: UploadFile = File(...),
    category: str = Form("reference"),
) -> dict[str, Any]:
    """Upload a file → extract text → chunk → store as events, tagged with
    its category (thesis / guideline / frustration / voice_corpus /
    reference) so retrieval and the content engine can weight it.

    Embeddings for all chunks are batched into one provider call, so a big
    document is one embedding request (matters for rate-limited free tiers).
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        events = await document_to_events_async(
            file.filename or "upload", data, category=category
        )
    except Exception as e:  # noqa: BLE001 — surface transcription/parse errors cleanly
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not events:
        raise HTTPException(status_code=422, detail="no extractable text")
    stored = await ingest_many(events)
    # Replace any previous version of this same file in this category so
    # the brand manager uses only the latest guidelines, not stale ones.
    superseded = await supersede_prior_document_versions(
        file.filename or "upload", category, [e.id for e in stored]
    )
    return {
        "filename": file.filename,
        "category": category,
        "chunks_created": len(stored),
        "superseded_chunks": superseded,
        "event_ids": [str(e.id) for e in stored],
    }


# ──────────────────────────────────────────────────────────────────── ask ──

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    return await ask(req)


# ─────────────────────────────────────────────────────────────── research ──

@app.post("/generate", response_model=ContentDraft)
async def generate_endpoint(brief: ContentBrief) -> ContentDraft:
    """Generate an on-voice draft from the memory substrate.

    Assembles category-weighted memory (voice + thesis + research +
    frustration + plug-in rules), writes a draft in the brand's voice,
    runs an independent voice-QA pass, and queues it as a *pending*
    action — a human approves before anything ships. Refuses (queues
    nothing) if there's no voice/thesis grounding.
    """
    if not brief.topic.strip():
        raise HTTPException(status_code=400, detail="topic is required")
    return await generate_content(brief)


@app.post("/generate-multi")
async def generate_multi(req: MultiGenerateRequest) -> dict:
    """One idea → tailored drafts for several platforms (plus an optional
    multi-slide carousel), generated in parallel. Each runs the full
    voice-QA + learned-guardrails pipeline and is queued for approval."""
    import asyncio

    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    platforms = [p.strip().lower() for p in req.platforms if p.strip()]
    if not platforms and not req.carousel:
        raise HTTPException(status_code=400, detail="no platforms or carousel requested")

    briefs: list[ContentBrief] = [
        ContentBrief(
            platform=p,
            format="post",
            pillar=req.pillar,
            topic=topic,
            research_subject=req.research_subject,
            extra_instructions=req.extra_instructions,
        )
        for p in platforms
    ]
    if req.carousel:
        n = max(3, min(req.carousel_slides, 10))
        carousel_steer = (
            f"Produce a {n}-slide Instagram carousel. In the draft, output the "
            f"slides as 'Slide 1:' through 'Slide {n}:', one per line. Slide 1 is "
            f"a scroll-stopping hook; the middle slides build one clear idea, one "
            f"beat each; the last slide is a clear call to action."
        )
        if req.extra_instructions.strip():
            carousel_steer += f" Also: {req.extra_instructions.strip()}"
        briefs.append(
            ContentBrief(
                platform="instagram",
                format="carousel",
                pillar=req.pillar,
                topic=topic,
                research_subject=req.research_subject,
                extra_instructions=carousel_steer,
            )
        )

    drafts = await asyncio.gather(*(generate_content(b) for b in briefs))
    return {
        "topic": topic,
        "drafts": [d.model_dump(mode="json") for d in drafts],
        "queued": sum(1 for d in drafts if d.action_id is not None),
    }


# ─────────────────────────────────────────────────────────────── video ──

@app.post("/video/generate", status_code=201)
async def video_generate(req: VideoGenerateRequest) -> dict:
    """Submit a generative video render. The job is persisted FIRST
    (durable — survives a restart), then sent to the provider. Poll
    GET /video/jobs/{id} for status; on success the clip lands in the
    approval queue as a pending video (a human approves before anything
    ships). With VIDEO_PROVIDER=stub the whole pipeline runs without
    spending render credits and never emits a fake mp4.
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    return await submit_video_job(
        req.prompt.strip(),
        req.prompt_image.strip() or None,
        req.source_action_id,
    )


@app.get("/video/jobs")
async def video_jobs() -> list[dict]:
    return await list_video_jobs()


@app.get("/video/jobs/{job_id}")
async def video_job(job_id: UUID) -> dict:
    """Returns the job, polling the provider if it's still in flight."""
    try:
        return await refresh_video_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="video job not found") from None


# ── full video productions: script → scene plan → clips → assembled mp4 ──

@app.post("/video/plan")
async def video_plan(req: VideoPlanRequest) -> dict:
    """Preview the scene plan for a script (no rendering). Grounded in voice
    + the reference library's style fingerprints."""
    if not req.script.strip():
        raise HTTPException(status_code=400, detail="script is required")
    return await generate_scene_plan(req.script.strip(), req.platform, req.aspect)


@app.post("/video/compose")
async def video_compose(req: VideoComposeRequest) -> dict:
    """Auto-compose an editable video: research a trending angle → on-voice
    script (guideline + voice-QA) → scene plan (reference-style matched).
    Returns the editable plan; nothing renders."""
    from .video_compose import compose_video
    return await compose_video(req.topic_hint.strip(), req.platform, req.aspect)


@app.post("/video/produce", status_code=201)
async def video_produce(req: VideoProduceRequest, background: BackgroundTasks) -> dict:
    """Kick off a durable production. Accepts either a `script` (auto-planned)
    or an edited `scenes` plan from the visual editor (rendered as-is). Runs
    in the background (plan → clips → assemble → approval queue). Poll
    GET /video/productions/{id}."""
    if not req.script.strip() and not req.scenes:
        raise HTTPException(status_code=400, detail="script or scenes required")
    if req.mode == "avatar_only" and not req.script.strip():
        raise HTTPException(status_code=400, detail="avatar_only mode requires a script")
    if req.mode == "story_audio" and not req.script.strip():
        raise HTTPException(status_code=400, detail="story_audio mode requires a script")
    if req.mode == "avatar_story_mix" and not req.script.strip():
        raise HTTPException(status_code=400, detail="avatar_story_mix mode requires a script")
    if req.mode == "engaging_avatar" and not req.script.strip():
        raise HTTPException(status_code=400, detail="engaging_avatar mode requires a script")
    try:
        prod = await start_production(
            req.script.strip(), req.platform, req.aspect, req.title,
            req.scenes, req.mode, req.caption_style, req.image_style,
        )
    except ValueError as e:
        # start_production rejects malformed timeline payloads — surface as 400
        # so the editor can show the real reason, not a generic 500.
        raise HTTPException(status_code=400, detail=str(e)) from e
    background.add_task(run_production, UUID(prod["id"]))
    return prod


@app.post("/video/render-scene")
async def video_render_scene(req: SceneRenderRequest) -> dict:
    """Render ONE scene for the editor's per-scene preview (avatar / B-roll /
    James clip). Returns the scene with url + clip_status. Reused at assembly,
    so previewing a scene means it isn't re-rendered."""
    if not req.scene:
        raise HTTPException(status_code=400, detail="scene is required")
    return await render_one_scene(req.scene, req.aspect)


@app.get("/video/productions")
async def video_productions() -> list[dict]:
    return await list_productions()


@app.get("/video/productions/{production_id}")
async def video_production(production_id: UUID) -> dict:
    p = await get_production(production_id)
    if p is None:
        raise HTTPException(status_code=404, detail="production not found")
    return p


@app.get("/video/caption-styles")
async def video_caption_styles() -> dict:
    """Caption preset library. Each item: {name, label, description}.

    The "AI pick" option isn't returned here — the frontend renders that
    as its own first chip and passes caption_style='' to defer the
    choice to pick_caption_style() in the pipeline.
    """
    from .caption_styles import list_presets
    return {"presets": list_presets()}


# ── long form cutter (podcast → reel candidates → per-reel render) ──

@app.post("/long-form/upload", status_code=201)
async def long_form_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
) -> dict:
    """Upload a long video (podcast / interview / talk). Persists to
    Supabase Storage, kicks ingest_source() in the background — it
    extracts audio, Whisper-transcribes with word stamps, LLM picks
    3-5 standalone reel candidates. Poll GET /long-form/{id} for
    status flips uploading → transcribing → analyzing → ready.
    """
    from .long_form import create_source, ingest_source
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if not (file.content_type or "").startswith("video/"):
        # Audio-only sources are also valid (raw podcast mp3), but the
        # later cut step assumes a video stream exists. For now refuse
        # non-video uploads with a clean message rather than half-
        # supporting them.
        raise HTTPException(
            status_code=400,
            detail="long_form/upload requires a video file (mp4/mov/webm)",
        )
    tenant = str(settings.default_tenant_id)
    served_uri, _ = media_storage().save(tenant, data, file.filename or "long.mp4")
    src = await create_source(
        title=title or (file.filename or ""), source_url=served_uri,
    )
    background.add_task(ingest_source, UUID(src["id"]))
    return src


@app.post("/long-form/drive-import", status_code=201)
async def long_form_drive_import(
    background: BackgroundTasks,
    drive_url: str = Form(...),
    title: str = Form(""),
) -> dict:
    """Pull a long video from a sharable Drive URL straight into the
    cutter. Same end shape as /long-form/upload — persists the file to
    Supabase Storage and kicks ingest_source in the background.

    Accepts every sharable Drive URL shape (file/d/{id}/view, open?id=,
    uc?id=, docs.google.com/file/d/{id}/preview). The service account
    needs read access to the file (share the file with the service
    account's email).
    """
    from .drive import (
        DriveNotConfigured, extract_drive_file_id, fetch_drive_file_by_url,
    )
    from .long_form import create_source, ingest_source

    url = (drive_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="drive_url is required")
    if not extract_drive_file_id(url):
        raise HTTPException(
            status_code=400,
            detail="not a recognisable Drive URL — share-link or open?id= form",
        )
    try:
        data, name, mime = await fetch_drive_file_by_url(url)
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — wrap any google-api error
        raise HTTPException(
            status_code=502,
            detail=f"Drive download failed (is the file shared with the service account?): {e}",
        ) from e
    if not data:
        raise HTTPException(status_code=502, detail="Drive returned empty file")
    # Reject non-video mimes here — same as the upload route, the cut
    # step assumes a video stream. The user gets a clean 400 instead
    # of an ffmpeg failure 30 seconds in.
    if mime and not mime.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail=f"Drive file is not a video (mime: {mime})",
        )

    tenant = str(settings.default_tenant_id)
    served_uri, _ = media_storage().save(tenant, data, name)
    src = await create_source(title=(title or name), source_url=served_uri)
    background.add_task(ingest_source, UUID(src["id"]))
    return src


@app.get("/long-form/sources")
async def long_form_list() -> dict:
    """List of long-form sources for this tenant, newest first.

    NOTE: this route is /long-form/sources (not bare /long-form) so it
    doesn't collide with the Next.js page route at /long-form.
    """
    from .long_form import list_sources
    return {"sources": await list_sources()}


@app.get("/long-form/{source_id}")
async def long_form_get(source_id: UUID) -> dict:
    """Source + its non-dismissed candidates."""
    from .long_form import get_source_with_candidates
    s = await get_source_with_candidates(source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="source not found")
    return s


@app.post("/long-form/{source_id}/reanalyze", status_code=202)
async def long_form_reanalyze(
    source_id: UUID, background: BackgroundTasks,
) -> dict:
    """Re-run ingest_source on an existing row — useful when the LLM
    candidate finder is updated, or when a source got stuck. Status
    bounces back to transcribing and the worker takes it from there."""
    from .long_form import ingest_source
    async with acquire() as conn:
        await conn.execute(
            "UPDATE long_sources SET status = 'transcribing', "
            "error = NULL, updated_at = now() WHERE id = $1",
            source_id,
        )
    background.add_task(ingest_source, source_id)
    return {"queued": True}


@app.post("/long-form/candidates/{candidate_id}/render", status_code=201)
async def long_form_candidate_render(
    candidate_id: UUID, background: BackgroundTasks,
    platform: str = Form("instagram"), aspect: str = Form("9:16"),
    image_style: str = Form(""), caption_style: str = Form(""),
) -> dict:
    """Take a candidate window and produce a Reel — kicks a
    long_form_reel production. Returns the production row so the
    caller can poll /video/productions/{id} for the final URL."""
    from .long_form import (
        get_candidate, get_source_with_candidates,
        link_candidate_to_production,
    )
    cand = await get_candidate(candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    src = await get_source_with_candidates(UUID(cand["source_id"]))
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    # Stuff the candidate window onto the production's scenes jsonb so
    # the long_form_reel worker can pick it up without a join.
    payload = [{
        "source_id": cand["source_id"],
        "candidate_id": cand["id"],
        "source_url": src["source_url"],
        "start_s": cand["start_s"],
        "end_s": cand["end_s"],
        "hook_quote": cand["hook_quote"],
        "summary": cand["summary"],
    }]
    try:
        prod = await start_production(
            cand["hook_quote"][:200],     # script slot carries the hook for logs
            platform, aspect,
            cand["summary"][:120] or "Reel from long-form",
            payload, "long_form_reel",
            caption_style, image_style,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await link_candidate_to_production(
        UUID(candidate_id) if isinstance(candidate_id, str) else candidate_id,
        UUID(prod["id"]),
    )
    background.add_task(run_production, UUID(prod["id"]))
    return prod


@app.post("/long-form/candidates/{candidate_id}/dismiss")
async def long_form_candidate_dismiss(candidate_id: UUID) -> dict:
    """Hide a candidate from the list (keeps the row for audit)."""
    from .long_form import dismiss_candidate
    await dismiss_candidate(candidate_id)
    return {"ok": True}


@app.get("/hero/context")
async def hero_context_get() -> dict:
    """Current hero description + sample photos.

    Reads the in-process cache; computes on first call when the user has
    uploaded hero_photo assets. Returns null fields when nothing is
    uploaded yet — frontend renders an empty-state prompt to upload."""
    from .hero_context import get_hero_context as _get
    ctx = await _get()
    if ctx is None:
        return {"description": "", "photo_count": 0, "photo_urls": [], "video_urls": []}
    return {
        "description": ctx.description,
        "photo_count": ctx.photo_count,
        "photo_urls": ctx.photo_urls,
        "video_urls": ctx.video_urls,
    }


@app.post("/hero/context/refresh")
async def hero_context_refresh() -> dict:
    """Force-recompute the hero description even if cached.

    Used by the /hero page after the user uploads a new batch of photos
    — the upload endpoint invalidates the cache automatically, but a
    manual refresh button lets the user pull a fresh description
    without re-uploading.
    """
    from .hero_context import get_hero_context as _get
    from .hero_context import invalidate_cache as _bust
    _bust()
    ctx = await _get(force_refresh=True)
    if ctx is None:
        return {"description": "", "photo_count": 0, "photo_urls": [], "video_urls": []}
    return {
        "description": ctx.description,
        "photo_count": ctx.photo_count,
        "photo_urls": ctx.photo_urls,
        "video_urls": ctx.video_urls,
    }


@app.get("/video/image-styles")
async def video_image_styles() -> dict:
    """Image style library (the look-and-feel for AI-generated B-roll
    stills). Each item: {name, label, description}.

    Default chip on the frontend is "story default" (sends image_style
    blank, which the pipeline resolves to 'cinematic' for story modes).
    """
    from .imagegen import POST_STYLES
    # Human-friendly labels + one-line descriptions; the long prefix
    # in POST_STYLES isn't shown to users.
    META = {
        "cinematic": {
            "label": "Cinematic",
            "description": "Film-still drama: one symbolic object, hard light, moody color grade.",
        },
        "photoreal": {
            "label": "Photoreal",
            "description": "Documentary photography: real-world subjects, natural light.",
        },
        "editorial": {
            "label": "Editorial",
            "description": "Clean flat-vector illustration. Metaphor-friendly.",
        },
        "minimal": {
            "label": "Minimal",
            "description": "Bold geometric / abstract. Reads at thumbnail.",
        },
        "bw_photo": {
            "label": "B&W photo",
            "description": "Black-and-white documentary. Institutional / serious.",
        },
    }
    return {"presets": [
        {"name": name,
         "label": META.get(name, {}).get("label", name),
         "description": META.get(name, {}).get("description", "")}
        for name in POST_STYLES
    ]}


@app.get("/video/clips/library")
async def video_clips_library() -> dict:
    """Every assemblable clip in the system, in one shot, for the timeline
    editor's library panel. Three buckets:

      * production_final — a past production's final stitched mp4
      * production_scene — a single rendered scene clip from a past production
      * reference       — a media library upload (james_clip or broll)

    Each item carries `assemblable: bool` — Creatomate needs a publicly
    reachable https URL, so local /media-files/* paths are flagged not
    assemblable. The editor shows them but warns when picked.
    """
    prods = await list_productions()
    media = await list_media()

    items: list[dict] = []

    for p in prods:
        if p.get("status") != "succeeded":
            continue
        final = (p.get("final_url") or "").strip()
        if final and not final.startswith("stub://"):
            items.append({
                "kind": "production_final",
                "label": p.get("title") or "Untitled production",
                "url": final,
                "duration": None,
                "aspect": p.get("aspect") or "9:16",
                "source_id": p.get("id"),
                "source_meta": {"mode": p.get("mode") or "mixed"},
                "assemblable": final.startswith("http"),
            })
        for s in (p.get("scenes") or []):
            su = (s.get("url") or "").strip()
            if not su or su.startswith("stub://"):
                continue
            items.append({
                "kind": "production_scene",
                "label": (
                    f"{p.get('title') or 'Production'} · "
                    f"scene {s.get('index', 0) + 1}"
                    + (f" — {s['label']}" if s.get("label") else "")
                ),
                "url": su,
                "duration": s.get("duration"),
                "aspect": p.get("aspect") or "9:16",
                "source_id": p.get("id"),
                "source_meta": {
                    "scene_index": s.get("index"),
                    "scene_source": s.get("source"),
                    "scene_kind": s.get("kind"),
                },
                "assemblable": su.startswith("http"),
            })

    for m in media:
        if m.get("role") not in ("james_clip", "broll"):
            continue
        uri = (m.get("uri") or "").strip()
        if not uri:
            continue
        items.append({
            "kind": "reference",
            "label": m.get("title") or m.get("role"),
            "url": uri,
            "duration": m.get("duration"),
            "aspect": None,
            "source_id": m.get("id"),
            "source_meta": {
                "role": m.get("role"),
                "platform": m.get("platform"),
                "mute_audio": m.get("mute_audio"),
            },
            # Local /media-files/* paths aren't reachable from Creatomate.
            "assemblable": uri.startswith("http"),
        })

    return {"items": items}


@app.post("/images/generate", status_code=201)
async def images_generate(req: PostImageRequest) -> dict:
    """Generate a shareable post hero image (LinkedIn / Twitter / IG).

    Calls OpenAI gpt-image-1 with an editorial style prefix tuned for
    'uncluttered, single-focal-point, no text overlays' — the kind of
    image you'd actually attach to a post, not a busy collage. Persists
    the PNG to media storage (Supabase if configured, local-disk fallback)
    and creates a media_assets row with role='post_image', so the image
    is reusable from /images and the timeline editor library.

    Stub-honest: with no OPENAI_API_KEY this returns 400 with a clear
    reason — never a fake image.
    """
    from .imagegen import generate_post_image
    from .media import storage as media_storage

    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    png, meta, err = await generate_post_image(
        topic=topic,
        platform=req.platform.strip() or "linkedin",
        brief=req.brief,
        aspect=req.aspect,
        style=req.style,
    )
    if not png:
        raise HTTPException(status_code=400, detail=err or "image generation failed")

    # Persist the bytes through the same storage backend the media
    # library uses, so the returned URL works for both browser preview
    # and downstream consumers (Creatomate, social schedulers).
    tenant = "00000000-0000-0000-0000-000000000001"  # single-tenant for now
    filename = (
        f"post-{meta['platform']}-{meta['style']}-"
        f"{meta['aspect'].replace(':','x')}.png"
    )
    served_uri, file_path = media_storage().save(tenant, png, filename)

    # Prepend a style tag so the library can render it as a chip without
    # parsing the prompt. User-supplied tags are preserved after.
    asset = await create_media(
        role="post_image",
        source_type="upload",
        uri=served_uri,
        file_path=file_path,
        title=(req.title or topic)[:120],
        platform=req.platform.strip() or "linkedin",
        mime="image/png",
        tags=[f"style:{meta['style']}", *(req.tags or [])],
        notes=meta["prompt"][:500],
    )
    asset["generation"] = meta
    return asset


@app.post("/research", response_model=ResearchResponse)
async def research_endpoint(req: ResearchRequest) -> ResearchResponse:
    """Research a company or person on the open internet, then ingest the
    findings into the SAME memory substrate tagged `category:research`.

    The intelligence becomes retrievable/citable by Ask and the content
    engine immediately — provenance (source URLs, provider, subject) is
    preserved on every stored event. With RESEARCH_PROVIDER=stub this
    proves the loop without inventing facts; set it to `perplexity` (and
    add the key) for real internet intelligence.
    """
    subject = req.subject.strip()
    if not subject:
        raise HTTPException(status_code=400, detail="subject is required")

    provider = get_research_provider()
    try:
        result = await provider.research(subject, req.focus.strip())
    except Exception as e:  # noqa: BLE001 — surface provider errors cleanly
        raise HTTPException(status_code=502, detail=f"research failed: {e}") from e

    events = research_to_events(result)
    stored = await ingest_many(events) if events else []

    note = None
    if result.provider == "stub":
        note = (
            "Stub provider: this is NOT real research. Set "
            "RESEARCH_PROVIDER=perplexity and add PERPLEXITY_API_KEY for "
            "live internet intelligence."
        )

    return ResearchResponse(
        subject=result.subject,
        provider=result.provider,
        summary=result.summary,
        findings=result.findings,
        sources=[ResearchSourceOut(url=s.url, title=s.title) for s in result.sources],
        stored_event_ids=[e.id for e in stored],
        ingested_into_memory=bool(stored),
        note=note,
    )


# ─────────────────────────────────────────────────────────── trend radar ──

def _apify_note() -> str | None:
    if not (settings.apify_api_key or "").strip():
        return (
            "No Apify key connected — add APIFY_API_KEY in Settings to scrape "
            "live Instagram / TikTok / YouTube trends. Nothing was fabricated."
        )
    return None


@app.post("/trends/discover")
async def trends_discover(req: TrendDiscoverRequest) -> dict:
    """Discover top-performing posts on a topic across IG/TikTok/YouTube,
    score virality (outlier vs creator median + views/hour), and ingest
    each into the SAME memory substrate (category:trend) so it's citable
    and can seed a brand-voice script. Returns the ranked feed."""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    platforms = [p for p in req.platforms if p in ("instagram", "tiktok", "youtube")]
    if not platforms:
        raise HTTPException(status_code=400, detail="no valid platforms")
    try:
        result = await discover_and_ingest(topic, platforms, max(1, min(req.limit, 50)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"discovery failed: {e}") from e
    result["note"] = _apify_note()
    return result


@app.get("/trends")
async def trends_list(platform: str = "", limit: int = Query(default=60, le=200)) -> dict:
    """Ranked viral feed from stored trend events (cheap read — scores were
    computed at ingest)."""
    return {"trends": await list_trends(platform=platform, limit=limit), "note": _apify_note()}


@app.get("/trends/watchlist")
async def trends_watchlist_get() -> dict:
    return {"creators": await get_watchlist()}


@app.post("/trends/watchlist")
async def trends_watchlist_set(req: WatchlistUpdate) -> dict:
    # Destructive-default guard: an empty list is wholesale-replace, which
    # would wipe a curated watchlist. Require an explicit confirm_clear=true
    # so a buggy client or stale form post can't destroy the cohort.
    if not req.creators and not req.confirm_clear:
        existing = await get_watchlist()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refusing to wipe the watchlist ({len(existing)} creators) "
                "with an empty list. Pass confirm_clear=true to acknowledge."
            ),
        )
    creators = [
        {
            "platform": c.platform,
            "handle": c.handle,
            "name": c.name,
            "interests": c.interests,
        }
        for c in req.creators
    ]
    return {"creators": await set_watchlist(creators)}


@app.post("/trends/refresh")
async def trends_refresh(req: WatchlistRefreshRequest) -> dict:
    """Scrape recent posts for every creator on the watchlist, score and
    ingest them. Returns the ranked feed for the refreshed set."""
    creators = await get_watchlist()
    if not creators:
        return {"found": 0, "trends": [], "stored_event_ids": [],
                "note": "Watchlist is empty — add creators first."}
    handles = watchlist_by_platform(creators)
    try:
        result = await refresh_watchlist(handles, max(1, min(req.limit, 50)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"refresh failed: {e}") from e
    result["note"] = _apify_note()
    return result


@app.post("/generate-script", response_model=ContentDraft)
async def generate_script(req: ScriptRequest) -> ContentDraft:
    """Turn a trend event into a brand-voice shooting script. Pulls the
    trend's hook + transcript and feeds it to the content engine as a brief,
    which grounds the script in the brand's own voice/thesis memory and runs
    the independent voice-QA gate. Lands in the approval queue like any draft
    — a verbatim copy would FAIL voice-QA, so 'replicate' means match the
    structure/format in our voice, never copy the creator's words."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT payload FROM events WHERE id = $1", req.event_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail="trend event not found")
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if payload.get("category") != "trend":
        raise HTTPException(status_code=400, detail="event is not a trend")

    handle = payload.get("handle", "a creator")
    platform = req.platform.strip() or payload.get("platform", "instagram")
    caption = payload.get("caption", "")
    transcript = (payload.get("text") or "")[:2500]
    outlier = payload.get("outlier_score", 0)

    steer = (
        f"Model this script on a high-performing {payload.get('platform')} post "
        f"by @{handle} (outlier score {outlier}x its creator's median). Match its "
        f"HOOK pattern, structure and pacing — but write it 100% in our brand "
        f"voice about our world. Do NOT copy the creator's words, claims, or "
        f"specifics. Reference post:\n\"\"\"\n{caption}\n{transcript}\n\"\"\""
    )
    if req.extra_instructions.strip():
        steer += f"\n\nAlso: {req.extra_instructions.strip()}"

    brief = ContentBrief(
        platform=platform,
        format="reel_script",
        topic=f"a short-form video inspired by what's trending from @{handle}",
        extra_instructions=steer,
    )
    return await generate_content(brief)


# ─────────────────────────────────────────────────────────── autopilot ──

@app.get("/autopilot/config")
async def autopilot_get_config() -> dict:
    from .autopilot import get_config
    return await get_config()


@app.post("/autopilot/config")
async def autopilot_set_config(body: dict = Body(default={})) -> dict:
    from .autopilot import set_config
    return await set_config(body)


@app.post("/autopilot/run", status_code=202)
async def autopilot_run(background: BackgroundTasks) -> dict:
    """Run a content batch now (in the background). Poll /autopilot/runs."""
    from .autopilot import run_batch
    background.add_task(run_batch, "manual")
    return {"started": True, "note": "Batch running — watch /autopilot/runs."}


@app.get("/autopilot/runs")
async def autopilot_runs() -> list[dict]:
    from .autopilot import list_runs
    return await list_runs()


# ────────────────────────────────────────────────── reference library ──

async def _run_media_analysis(media_id: UUID) -> dict | None:
    """Watch an uploaded reference and persist its style fingerprint. URL
    references can't be analyzed without the file → marked unsupported."""
    tenant = settings.default_tenant_id
    asset = await get_media_for_analysis(media_id, tenant)
    if asset is None:
        return None
    if asset["source_type"] != "upload" or not asset["file_path"]:
        await set_analysis_status(media_id, "unsupported", tenant)
        return None
    await set_analysis_status(media_id, "pending", tenant)
    result = await analyze_file(asset["file_path"])
    return await save_analysis(
        media_id,
        status=result.get("status", "failed"),
        transcript=result.get("transcript", ""),
        analysis=result,
        notes=fingerprint_to_notes(result),
        duration=result.get("duration", 0),
        tenant_id=tenant,
    )


@app.get("/media")
async def media_list(role: str = "") -> dict:
    """Reference library: style references, James's clips, and B-roll."""
    if role and role not in MEDIA_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {MEDIA_ROLES}")
    return {"media": await list_media(role), "roles": list(MEDIA_ROLES)}


@app.post("/media/{media_id}/analyze")
async def media_analyze(media_id: UUID) -> dict:
    """Run (or re-run) the perception layer on a reference now."""
    updated = await _run_media_analysis(media_id)
    if updated is None:
        raise HTTPException(
            status_code=400,
            detail="not analyzable (not found, or a URL reference without a file)",
        )
    return updated


@app.post("/media/upload", status_code=201)
async def media_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    role: str = Form("style_reference"),
    title: str = Form(""),
    platform: str = Form(""),
    notes: str = Form(""),
    tags: str = Form(""),  # comma-separated
) -> dict:
    """Upload a reference/clip/B-roll video file into the library. Perception
    (transcript + visual style fingerprint) runs in the background; poll
    GET /media to see analysis_status flip pending → done."""
    if role not in MEDIA_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {MEDIA_ROLES}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    tenant = str(settings.default_tenant_id)
    served_uri, file_path = media_storage().save(tenant, data, file.filename or "upload.mp4")
    created = await create_media(
        role=role,
        source_type="upload",
        uri=served_uri,
        file_path=file_path,
        title=title or (file.filename or ""),
        platform=platform,
        mime=file.content_type or "",
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        notes=notes,
    )
    background.add_task(_run_media_analysis, UUID(created["id"]))
    created["analysis_status"] = "pending"
    # Hero uploads change the brand's recurring-character context; the
    # in-process description cache needs to refresh so the next story
    # render sees the new photos.
    if role in ("hero_photo", "hero_video"):
        from .hero_context import invalidate_cache as _hero_bust
        _hero_bust()
    return created


@app.post("/media/link", status_code=201)
async def media_link(req: MediaLinkRequest) -> dict:
    """Add a reference by URL (YouTube/Drive/CDN link) without uploading."""
    if req.role not in MEDIA_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {MEDIA_ROLES}")
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    created = await create_media(
        role=req.role,
        source_type="url",
        uri=req.url.strip(),
        title=req.title,
        platform=req.platform,
        tags=req.tags,
        notes=req.notes,
    )
    if req.role in ("hero_photo", "hero_video"):
        from .hero_context import invalidate_cache as _hero_bust
        _hero_bust()
    return created


@app.patch("/media/{media_id}")
async def media_update(media_id: UUID, req: MediaUpdate) -> dict:
    updated = await update_media(
        media_id,
        title=req.title,
        notes=req.notes,
        platform=req.platform,
        tags=req.tags,
        mute_audio=req.mute_audio,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="media not found or nothing to update")
    return updated


@app.delete("/media/{media_id}")
async def media_delete(media_id: UUID) -> dict:
    ok = await delete_media(media_id)
    if not ok:
        raise HTTPException(status_code=404, detail="media not found")
    return {"ok": True, "deleted": str(media_id)}


@app.get("/media/drive/preview")
async def media_drive_preview(folder_id: str = "") -> dict:
    """List the videos in the configured Drive folder without importing."""
    from .drive import DriveNotConfigured, list_drive_videos
    try:
        files = await list_drive_videos(folder_id or None)
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Drive list failed: {e}") from None
    return {"count": len(files), "files": files}


@app.post("/media/drive-import")
async def media_drive_import(body: dict = Body(default={})) -> dict:
    """Pull new videos from the Drive folder into the Reference Library
    (default role = james_clip). Idempotent via Drive file_id."""
    from .drive import DriveNotConfigured, import_drive_folder
    try:
        return await import_drive_folder(
            folder_id=(body.get("folder_id") or "").strip() or None,
            role=body.get("role") or "james_clip",
            limit=max(1, min(int(body.get("limit") or 25), 200)),
        )
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Drive import failed: {e}") from None


# ─────────────────────────────────────────────────────────────── helpers ──

def _row_to_event(row) -> Event:
    d = dict(row)
    for k in ("payload", "source", "metadata"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return Event(**d)


def _row_to_plug_in(row) -> PlugIn:
    d = dict(row)
    if isinstance(d.get("content"), str):
        d["content"] = json.loads(d["content"])
    d.pop("created_by", None)
    return PlugIn(**d)
