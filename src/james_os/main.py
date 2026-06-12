"""FastAPI app — the public surface of JAMES OS."""

import asyncio
import json
import os
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
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ask import ask
from pydantic import BaseModel

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
    from .research_roster import maybe_weekly_refresh

    while True:
        try:
            if should_run_today(await get_config()):
                await run_batch("scheduled")
        except Exception:  # noqa: BLE001 — a tick failure must not kill the loop
            pass
        try:
            # Self-gates to >7-day-stale; safe to call every tick.
            await maybe_weekly_refresh()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1800)  # check every 30 minutes


async def _reap_orphaned_productions(tenant_id: UUID | None = None) -> int:
    """Flip in-flight video_productions rows to 'failed' on process
    restart. A render spans 5-30 minutes; if the server redeploys or
    crashes mid-way the in-process task dies but the row stays at
    'queued'/'planning'/'rendering_clips'/'assembling' forever — the
    pipeline page polls a spinner that never moves. Same pattern as
    long_form.reap_orphaned_sources (count the RETURNING rows)."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """UPDATE video_productions
                  SET status = 'failed',
                      error  = 'interrupted — server restarted mid-render',
                      updated_at = now(),
                      completed_at = now()
                WHERE status IN ('queued', 'planning',
                                 'rendering_clips', 'assembling')
                RETURNING id"""
        )
    return len(rows)


async def _reap_all_orphans() -> None:
    """Run every startup orphan reaper, once per tenant. With RLS
    enforced (migration 034) each connection only sees the tenant bound
    to it, so a single default-tenant pass would leave every other
    tenant's interrupted rows spinning forever."""
    from .autopilot import reap_orphaned_runs
    from .long_form import reap_orphaned_sources
    from .voice_ingest import reap_orphaned_jobs

    try:
        async with acquire() as conn:
            tenant_ids = [
                r["id"] for r in await conn.fetch("SELECT id FROM tenants")
            ]
    except Exception:  # noqa: BLE001
        tenant_ids = []
    for tid in tenant_ids or [settings.default_tenant_id]:
        for reaper in (
            reap_orphaned_runs,        # autopilot batches
            reap_orphaned_sources,     # long-form ingests
            reap_orphaned_jobs,        # voice-studio ingests
            _reap_orphaned_productions,  # video renders
        ):
            try:
                await reaper(tid)
            except Exception:  # noqa: BLE001 — never block startup on a reap failure
                pass


async def _check_rls_enforced() -> None:
    """Startup tripwire for the tenant boundary. RLS policies are the
    ONLY thing keeping tenants apart (application SQL deliberately
    carries no tenant predicates) — and they are silently inert when
    the connected role is a superuser, has BYPASSRLS, or owns tables
    that lack FORCE ROW LEVEL SECURITY. Warn loudly by default; set
    JOS_REQUIRE_RLS=1 in production to refuse to boot in that state."""
    problems: list[str] = []
    try:
        async with acquire() as conn:
            role = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
            forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class "
                "WHERE relname = 'events' AND relkind = 'r'"
            )
    except Exception:  # noqa: BLE001 — the probe must never break startup
        return
    if role and role["rolsuper"]:
        problems.append("connected role is a SUPERUSER (RLS never applies)")
    if role and role["rolbypassrls"]:
        problems.append("connected role has BYPASSRLS")
    if not forced:
        problems.append(
            "tables lack FORCE ROW LEVEL SECURITY "
            "(run `python migrate.py` to apply 034_rls_enforcement.sql)"
        )
    if problems:
        msg = (
            "[startup] RLS IS NOT ENFORCED — tenant isolation is OFF: "
            + "; ".join(problems)
        )
        if (os.environ.get("JOS_REQUIRE_RLS", "") or "").strip().lower() in (
            "1", "true", "yes",
        ):
            raise RuntimeError(msg)
        print(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    await init_pool()
    # Refuse-to-boot (or at least shout) when tenant isolation is off.
    await _check_rls_enforced()
    # Overlay any tenant-saved API keys from the DB onto the .env baseline
    # so UI-entered credentials are live from the first request.
    from .credentials import load_into_settings

    await load_into_settings()
    # Reap rows stranded mid-flight by the previous process: autopilot
    # batches, long-form ingests, voice ingests, video renders. A restart
    # must never leave a fake 'running' row spinning forever.
    await _reap_all_orphans()
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

# CORS — dev defaults (Next.js on :3000), extend via ALLOWED_ORIGINS env
# (comma-separated list of full origins, no trailing slash). Production
# deploys MUST set ALLOWED_ORIGINS to the frontend's HTTPS origin or the
# browser will refuse the cookie cross-site.
_cors_default = ["http://localhost:3000", "http://127.0.0.1:3000"]
_cors_extra = [
    o.strip() for o in (os.environ.get("ALLOWED_ORIGINS", "") or "").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_default + _cors_extra,
    allow_methods=["*"],
    allow_headers=["*"],
    # Cookies must be allowed cross-origin (3000 → 8001 in dev; frontend
    # ↔ backend in prod) so the session cookie sticks; without this the
    # browser drops it.
    allow_credentials=True,
)


# ── auth middleware ─────────────────────────────────────────────────
#
# Every request:
#   1. If path is public (/auth/*, /health, /docs, static, the SPA's
#      own page assets) → pass through with the default tenant.
#   2. Otherwise → resolve session cookie. On success, bind tenant_id
#      to the request contextvar so db.acquire() picks it up. On
#      failure, return 401.
#
# Putting this here (not on every route via Depends) means we don't
# have to touch 50+ existing endpoints. Routes that need the user
# object explicitly add Depends(require_user) and read it from
# request.state.

from fastapi import Request as _Req
from fastapi.responses import JSONResponse as _JSON
from .auth import (
    COOKIE_NAME as _AUTH_COOKIE, get_session as _auth_get_session,
    is_public_path as _auth_is_public,
)
from .db import set_request_tenant as _db_set_tenant


# Paths that should NEVER hit the auth gate, beyond the /auth/*
# prefix list inside auth.py. These are static frontend / Next.js
# Internals that come in from the browser on a hard reload.
_EXTRA_PUBLIC = (
    "/dashboard/", "/media-files/", "/static/",
    "/favicon.ico", "/robots.txt",
)


def _request_is_public(path: str) -> bool:
    if _auth_is_public(path):
        return True
    return any(path.startswith(p) for p in _EXTRA_PUBLIC)


@app.middleware("http")
async def auth_middleware(request: _Req, call_next):
    path = request.url.path
    if _request_is_public(path):
        return await call_next(request)
    token = request.cookies.get(_AUTH_COOKIE) or ""
    sess = await _auth_get_session(token) if token else None
    if sess is None:
        return _JSON(
            {"detail": "not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": "Cookie"},
        )
    _db_set_tenant(sess["tenant_id"])
    request.state.tenant_id = sess["tenant_id"]
    request.state.user_id = sess["user_id"]
    request.state.user_email = sess.get("email") or ""
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────── ui ──

_STATIC = Path(__file__).parent / "static"
_DASH = Path(__file__).parent / "dashboard"

# Dashboard compatibility API (must be registered before the static mount
# and before the root route so /api/* is owned by the router).
app.include_router(dashboard_api_router)

# Feature routers built as standalone APIRouters (see their modules).
from .autopilot_bulk_api import router as autopilot_bulk_router
from .analytics_live import router as analytics_live_router
from .research_roster_api import router as research_roster_router
from .voice_ingest_api import router as voice_ingest_router
from .templates_api import router as templates_router
from .feedback_changes_api import router as feedback_changes_router
from .brand_kit_api import router as brand_kit_router
app.include_router(autopilot_bulk_router)
app.include_router(analytics_live_router)
app.include_router(research_roster_router)
app.include_router(voice_ingest_router)
app.include_router(templates_router)
app.include_router(feedback_changes_router)
app.include_router(brand_kit_router)


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

# ── auth endpoints ──────────────────────────────────────────────────


class _SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    # Required for every signup after the bootstrap user — must match
    # SIGNUP_INVITE_CODE (signups are closed when that env is unset).
    invite_code: str = ""


class _LoginRequest(BaseModel):
    email: str
    password: str


class _ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _ip_of(request: Request) -> str:
    # Prefer X-Forwarded-For (first hop) when behind a proxy, fall back
    # to the direct client. Capped to 64 chars at the DB layer.
    fwd = request.headers.get("x-forwarded-for") or ""
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.post("/auth/signup", status_code=201)
async def auth_signup(req: _SignupRequest, request: Request, response: Response) -> dict:
    from .auth import signup_user, set_session_cookie, set_csrf_cookie
    cu, token = await signup_user(
        email=req.email, password=req.password,
        display_name=req.display_name,
        user_agent=request.headers.get("user-agent", "")[:300],
        ip=_ip_of(request),
        invite_code=req.invite_code,
    )
    set_session_cookie(response, token)
    set_csrf_cookie(response)
    return {
        "id": str(cu.id), "tenant_id": str(cu.tenant_id),
        "email": cu.email, "display_name": cu.display_name, "role": cu.role,
    }


@app.post("/auth/login")
async def auth_login(req: _LoginRequest, request: Request, response: Response) -> dict:
    from .auth import login_user, set_session_cookie, set_csrf_cookie
    cu, token = await login_user(
        email=req.email, password=req.password,
        user_agent=request.headers.get("user-agent", "")[:300],
        ip=_ip_of(request),
    )
    set_session_cookie(response, token)
    set_csrf_cookie(response)
    return {
        "id": str(cu.id), "tenant_id": str(cu.tenant_id),
        "email": cu.email, "display_name": cu.display_name, "role": cu.role,
    }


@app.post("/auth/logout")
async def auth_logout(request: Request, response: Response) -> dict:
    from .auth import (
        COOKIE_NAME, clear_session_cookie, get_session, revoke_session,
    )
    token = request.cookies.get(COOKIE_NAME) or ""
    if token:
        sess = await get_session(token)
        if sess is not None:
            await revoke_session(sess["session_id"], sess["tenant_id"])
    clear_session_cookie(response)
    response.delete_cookie("jos_csrf")
    return {"ok": True}


@app.get("/auth/me")
async def auth_me(request: Request) -> dict:
    """Identity probe for the SPA. Public path so an unauth'd browser
    can ask 'am I logged in?' and get 401 without the global middleware
    bouncing it (handled by the /auth/* prefix exclusion)."""
    from .auth import COOKIE_NAME, get_session
    token = request.cookies.get(COOKIE_NAME) or ""
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = await get_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="session invalid")
    return {
        "id": str(sess["user_id"]), "tenant_id": str(sess["tenant_id"]),
        "email": sess["email"] or "", "display_name": sess["display_name"] or "",
        "role": sess["role"] or "operator",
    }


@app.post("/auth/password")
async def auth_change_password(
    req: _ChangePasswordRequest, request: Request,
) -> dict:
    from .auth import (
        COOKIE_NAME, get_session, validate_password, verify_password,
        hash_password,
    )
    token = request.cookies.get(COOKIE_NAME) or ""
    sess = await get_session(token) if token else None
    if sess is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    err = validate_password(req.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    async with acquire(sess["tenant_id"]) as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id = $1", sess["user_id"],
        )
        if row is None or not verify_password(req.current_password, row["password_hash"] or ""):
            raise HTTPException(status_code=403, detail="current password incorrect")
        await conn.execute(
            "UPDATE users SET password_hash = $2 WHERE id = $1",
            sess["user_id"], hash_password(req.new_password),
        )
    return {"ok": True}


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    return await ask(req)


# ── agent (Ask the memory → "Do" mode) ──────────────────────────────


class _AgentRunRequest(BaseModel):
    prompt: str


@app.post("/agent/run", status_code=201)
async def agent_run(req: _AgentRunRequest, background: BackgroundTasks) -> dict:
    """Kick an agent run. Returns immediately with the run id — the
    actual tool-use loop runs in the background, persisting state to
    agent_runs as it goes so the UI can poll for live updates."""
    from .agent import create_run, run_agent
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    row = await create_run(req.prompt.strip())
    background.add_task(run_agent, UUID(row["id"]))
    return row


@app.get("/agent/runs")
async def agent_runs_list(limit: int = 30) -> dict:
    """Recent agent runs — for the run-history sidebar on /ask."""
    from .agent import list_runs
    return {"runs": await list_runs(limit=limit)}


@app.get("/agent/runs/{run_id}")
async def agent_run_detail(run_id: UUID) -> dict:
    """One run with its full tool_calls log."""
    from .agent import get_run
    r = await get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return r


@app.get("/agent/tools")
async def agent_tools_list() -> dict:
    """Every tool the agent can currently call — names + descriptions
    so the /ask page can render a "what I can do" panel without
    hardcoding the list."""
    from .agent import _TOOLS
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "writes": t.writes,
            }
            for t in _TOOLS.values()
        ],
    }


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


# ── video review (Output Library Approve / Reject) ────────────────


class _VideoApproveRequest(BaseModel):
    # Optional "approved with notes" — a positive review that STILL
    # carries forward improvement feedback. Empty note = pure approve.
    note: str = ""


class _VideoRejectRequest(BaseModel):
    reason: str


@app.post("/video/productions/{production_id}/approve")
async def video_approve(
    production_id: UUID, req: _VideoApproveRequest,
) -> dict:
    """Approve a finished video render. When `note` is non-empty,
    persists the note to the video-feedback learning loop as an
    `approved_with_notes` event so the next render reads it."""
    from .video_feedback import set_production_review, record_video_feedback
    note = (req.note or "").strip()
    status = "approved_with_notes" if note else "approved"
    ok = await set_production_review(production_id, status, note)
    if not ok:
        raise HTTPException(status_code=404, detail="production not found")
    learned_id = None
    if note:
        learned_id = await record_video_feedback(
            production_id, note, status="approved_with_notes",
        )
    return {
        "ok": True, "id": str(production_id), "status": status,
        "learned_id": learned_id,
    }


@app.post("/video/productions/{production_id}/reject")
async def video_reject(
    production_id: UUID, req: _VideoRejectRequest,
) -> dict:
    """Reject a finished video render. The reason becomes a
    `video_feedback`-category memory event so the next render
    avoids the same mistake. Empty reasons still flip the status
    chip but don't pollute memory."""
    from .video_feedback import set_production_review, record_video_feedback
    reason = (req.reason or "").strip()
    ok = await set_production_review(production_id, "rejected", reason)
    if not ok:
        raise HTTPException(status_code=404, detail="production not found")
    learned_id = await record_video_feedback(
        production_id, reason, status="rejected",
    )
    # Auto-refresh the "What's changing next" board with this feedback.
    from .feedback_interpreter import kick_interpret_background
    kick_interpret_background()
    return {
        "ok": True, "id": str(production_id), "status": "rejected",
        "learned_id": learned_id,
    }


@app.get("/video/feedback")
async def video_feedback_list(limit: int = 50, tag: str = "") -> dict:
    """Recent video-feedback events for display on /library and any
    rendering prompt that wants to see what the team has flagged."""
    from .video_feedback import recent_video_feedback
    return {"feedback": await recent_video_feedback(limit=limit, tag=tag)}


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
    # to_thread: the Supabase storage client is sync HTTP — calling it
    # inline would block the event loop for the whole (multi-minute,
    # chunked TUS) upload and stall every other request, /health included.
    served_uri, _ = await asyncio.to_thread(
        media_storage().save, tenant, data, file.filename or "long.mp4"
    )
    src = await create_source(
        title=title or (file.filename or ""), source_url=served_uri,
    )
    background.add_task(ingest_source, UUID(src["id"]))
    return src


@app.get("/long-form/drive-browse")
async def long_form_drive_browse(folder_id: str = "") -> dict:
    """List videos the service account can see in a Drive folder.

    folder_id can be either:
      * the raw 33-char Drive folder id, OR
      * a sharable folder URL like https://drive.google.com/drive/folders/<ID>
        (we extract the id) OR
      * empty — falls back to the configured GOOGLE_DRIVE_FOLDER_ID

    Returns the same shape as list_drive_videos: each item carries
    {id, name, mimeType, size, modifiedTime}. The frontend renders these
    as tile cards so users can click-import instead of pasting URLs.
    """
    import re as _re
    from .drive import (
        DriveNotConfigured, list_drive_videos,
    )
    fid = (folder_id or "").strip()
    if fid:
        # Tolerate full Drive folder URLs as well as raw IDs.
        m = _re.search(r"/folders/([A-Za-z0-9_-]{20,})", fid)
        if m:
            fid = m.group(1)
        elif not _re.match(r"^[A-Za-z0-9_-]{20,}$", fid):
            raise HTTPException(
                status_code=400,
                detail="folder_id should be a Drive folder URL or its id",
            )
    try:
        files = await list_drive_videos(fid or None)
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Drive list failed (is the folder shared with the service account?): {e}",
        ) from e
    # Surface a default-folder hint so the frontend can display "browsing
    # folder X" without an extra round-trip.
    default = (settings.google_drive_folder_id or "").strip()
    return {
        "folder_id": fid or default,
        "default_folder_id": default,
        "videos": files,
    }


@app.post("/long-form/drive-import-id", status_code=201)
async def long_form_drive_import_by_id(
    background: BackgroundTasks,
    file_id: str = Form(...),
    title: str = Form(""),
) -> dict:
    """Same as /long-form/drive-import but accepts a raw file id.

    Returns IMMEDIATELY with a placeholder long_sources row at status
    'uploading'. The Drive download + Supabase upload + transcript +
    candidate selection all happen in a single background task. The
    /long-form page polls every 5s so the user sees the row appear
    instantly and the status flip as work progresses — no 20-min
    HTTP hang on big files.
    """
    from .drive import DriveNotConfigured, _file_metadata_sync
    from .long_form import (
        create_source_placeholder, fetch_from_drive_then_ingest,
    )
    fid = (file_id or "").strip()
    if not fid:
        raise HTTPException(status_code=400, detail="file_id is required")
    # Cheap metadata probe — reject non-video BEFORE creating a row
    # so we don't litter the table with broken placeholders.
    try:
        meta = await asyncio.to_thread(_file_metadata_sync, fid)
        name = str(meta.get("name") or f"drive-{fid}.mp4")
        mime = str(meta.get("mimeType") or "")
        if mime and not mime.startswith("video/"):
            raise HTTPException(
                status_code=400,
                detail=f"file is not a video (mime: {mime})",
            )
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Drive metadata failed (is the file shared with the service account?): {e}",
        ) from e

    src = await create_source_placeholder(
        title=(title or name), drive_file_id=fid,
    )
    background.add_task(
        fetch_from_drive_then_ingest, UUID(src["id"]), fid, name,
    )
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

    from .drive import (
        DriveNotConfigured, _file_metadata_sync, extract_drive_file_id,
    )
    from .long_form import (
        create_source_placeholder, fetch_from_drive_then_ingest,
    )
    url = (drive_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="drive_url is required")
    fid = extract_drive_file_id(url)
    if not fid:
        raise HTTPException(
            status_code=400,
            detail="not a recognisable Drive URL — share-link or open?id= form",
        )
    # Probe metadata before creating a row.
    try:
        meta = await asyncio.to_thread(_file_metadata_sync, fid)
        name = str(meta.get("name") or f"drive-{fid}.mp4")
        mime = str(meta.get("mimeType") or "")
        if mime and not mime.startswith("video/"):
            raise HTTPException(
                status_code=400,
                detail=f"Drive file is not a video (mime: {mime})",
            )
    except DriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Drive download failed (is the file shared with the service account?): {e}",
        ) from e

    src = await create_source_placeholder(
        title=(title or name), drive_file_id=fid,
    )
    background.add_task(
        fetch_from_drive_then_ingest, UUID(src["id"]), fid, name,
    )
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


@app.post("/long-form/{source_id}/reanalyze", status_code=200)
async def long_form_reanalyze(source_id: UUID) -> dict:
    """Re-run the candidate picker on an already-ingested row — no
    re-download, no re-transcribe. Synchronous so the caller gets the
    new count back immediately and can refresh the tile grid.

    Useful after a picker prompt change. For a full re-ingest, delete
    the row and re-import the Drive file."""
    from .long_form import reanalyze_source
    try:
        count = await reanalyze_source(source_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"reanalyze failed: {e}",
        ) from e
    return {"source_id": str(source_id), "new_candidates": count}


@app.post("/long-form/candidates/{candidate_id}/render", status_code=201)
async def long_form_candidate_render(
    candidate_id: UUID, background: BackgroundTasks,
    platform: str = Form("instagram"), aspect: str = Form("9:16"),
    image_style: str = Form(""), caption_style: str = Form(""),
    video_engine: str = Form(""),
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
        # Drive-as-source-of-truth: the worker prefers drive_file_id
        # when present (re-fetches from Drive on every cut, no
        # Supabase round-trip). Falls back to source_url for legacy
        # rows that did the old Supabase upload path.
        "source_url": src["source_url"],
        "drive_file_id": src.get("drive_file_id") or "",
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
            video_engine=video_engine,
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


@app.post("/long-form/{source_id}/render-whole", status_code=201)
async def long_form_render_whole(
    source_id: UUID, background: BackgroundTasks,
    platform: str = Form("instagram"), aspect: str = Form("9:16"),
    image_style: str = Form(""), caption_style: str = Form(""),
    video_engine: str = Form(""),
) -> dict:
    """Render the ENTIRE source as a single reel — for short talking
    clips (1-2 min) where the whole clip already IS the reel and we
    don't want the LLM picker chopping it.

    Synthesizes a candidate row covering [0, duration_s] (idempotent —
    reuses an existing whole-source candidate if one is there) and hands
    it to the long_form_reel worker so the engaging-avatar treatment
    (captions, B-roll cutaways at 5s cadence, music) applies unchanged.
    """
    from .long_form import (
        create_whole_source_candidate, get_source_with_candidates,
        link_candidate_to_production,
    )
    cand = await create_whole_source_candidate(source_id)
    if cand is None:
        raise HTTPException(
            status_code=400,
            detail="source not ready or has no duration_s — finish ingest first",
        )
    src = await get_source_with_candidates(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    payload = [{
        "source_id": cand["source_id"],
        "candidate_id": cand["id"],
        "source_url": src["source_url"],
        "drive_file_id": src.get("drive_file_id") or "",
        "start_s": cand["start_s"],
        "end_s": cand["end_s"],
        "hook_quote": cand["hook_quote"],
        "summary": cand["summary"],
    }]
    try:
        prod = await start_production(
            (cand["hook_quote"] or src["title"])[:200],
            platform, aspect,
            (cand["summary"] or src["title"] or "Reel")[:120],
            payload, "long_form_reel",
            caption_style, image_style,
            video_engine=video_engine,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await link_candidate_to_production(UUID(cand["id"]), UUID(prod["id"]))
    background.add_task(run_production, UUID(prod["id"]))
    return prod


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


class _HeroTalkingVideoRequest(BaseModel):
    script: str = ""
    topic: str = ""
    platform: str = "instagram"
    aspect: str = "9:16"
    title: str = ""


@app.post("/hero/talking-video", status_code=201)
async def hero_talking_video(
    req: _HeroTalkingVideoRequest, background: BackgroundTasks
) -> dict:
    """Clone the hero into a lip-synced talking video: hero photos → a
    hyper-real still → HeyGen Talking Photo, spoken in the brand voice.
    Provide a `script`, or a `topic` (we write one in the brand voice).
    Lands in the Approval Queue like every other piece. Needs hero photos
    on the Hero page + a HeyGen key/voice (else it renders an honest stub)."""
    script = (req.script or "").strip()
    if not script:
        topic = (req.topic or "").strip()
        if not topic:
            raise HTTPException(status_code=400, detail="provide a script, or a topic to write one from")
        from .models import ContentBrief
        draft = await generate_content(
            ContentBrief(platform=req.platform, format="reel_script", topic=topic),
        )
        script = (draft.draft or "").strip()
        if not script:
            raise HTTPException(
                status_code=502,
                detail=f"couldn't write a script for that topic ({draft.note or draft.status})",
            )
    aspect = (req.aspect or "9:16").strip() or "9:16"
    title = (req.title or "Hero talking clip").strip()[:200]
    prod = await start_production(
        script, req.platform, aspect, title, None, "hero_clone",
    )
    background.add_task(run_production, UUID(prod["id"]))
    return prod


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
    # Resolve the request tenant so the post image follows brand guidelines.
    from .db import _request_tenant
    try:
        _img_tid = _request_tenant.get()
    except LookupError:
        _img_tid = None
    png, meta, err = await generate_post_image(
        topic=topic,
        platform=req.platform.strip() or "linkedin",
        brief=req.brief,
        aspect=req.aspect,
        style=req.style,
        tenant_id=_img_tid or settings.default_tenant_id,
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
    # to_thread: sync HTTP under the hood — never block the event loop.
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, png, filename
    )

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


# ── analytics ────────────────────────────────────────────────────────


@app.get("/analytics/handles")
async def analytics_handles() -> dict:
    """Every (platform, handle) pair with at least one scraped post —
    used as the dropdown source on /analytics."""
    from .analytics import list_tracked_handles
    return {"handles": await list_tracked_handles()}


@app.get("/analytics/summary")
async def analytics_summary(
    handle: str = "", platform: str = "", days: int = 30,
) -> dict:
    """Per-handle (or cohort-wide when blank) summary card numbers."""
    from .analytics import handle_summary
    return await handle_summary(handle=handle, platform=platform, days=days)


@app.get("/analytics/posts")
async def analytics_posts(
    handle: str = "", platform: str = "", days: int = 30,
    sort: str = "outlier", limit: int = 30,
) -> dict:
    """Sortable list of recent posts for the analytics table."""
    from .analytics import list_posts
    return {
        "posts": await list_posts(
            handle=handle, platform=platform, days=days,
            sort=sort, limit=limit,
        ),
    }


@app.get("/analytics/timeline")
async def analytics_timeline(
    handle: str = "", platform: str = "", days: int = 30,
) -> dict:
    """Daily aggregates (views / posts / engagement) for the trend chart."""
    from .analytics import daily_timeline
    return {
        "timeline": await daily_timeline(
            handle=handle, platform=platform, days=days,
        ),
    }


# ── Unified connected-accounts view (Meta + PostProxy) ─────────────


@app.get("/integrations/connections")
async def integrations_connections() -> dict:
    """Every brand profile reachable across Meta + PostProxy in one
    list. The Analytics page reads this to render its 'Connected
    accounts' section without caring which integration owns what."""
    from .connections import list_all_connections
    return await list_all_connections()


@app.get("/integrations/profile/{provider}/{profile_id}/posts")
async def integrations_profile_posts(
    provider: str, profile_id: str, platform: str = "", limit: int = 20,
) -> dict:
    """Recent posts for one connected profile, normalized to a common
    card shape. Routes to the right backend based on `provider`
    ('meta' | 'postproxy'). `platform` filters PostProxy posts (e.g.
    'youtube') since one PostProxy workspace spans many platforms."""
    from .connections import list_profile_posts
    return await list_profile_posts(provider, profile_id, platform, limit)


# ── PostProxy integration (X / IG / LinkedIn / TikTok via one API) ──


@app.get("/integrations/postproxy/inspect")
async def integrations_postproxy_inspect() -> dict:
    """Probe — confirms the key works AND lists every connected
    social account so the UI can render 'here's what's reachable'
    before any data fetch."""
    from .postproxy import inspect, PostProxyNotConfigured
    try:
        return await inspect()
    except PostProxyNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/integrations/postproxy/posts")
async def integrations_postproxy_posts(
    platform: str = "twitter", limit: int = 30,
) -> dict:
    """Recent posts on a given platform via PostProxy. `platform` is
    one of: twitter, instagram, tiktok, linkedin, youtube, facebook,
    threads, pinterest, bluesky, telegram, google_business."""
    from .postproxy import list_posts, PostProxyNotConfigured, PostProxyError
    try:
        return await list_posts(
            platforms=[platform] if platform else None,
            per_page=int(limit),
        )
    except PostProxyNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PostProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/integrations/postproxy/post-stats")
async def integrations_postproxy_post_stats(
    post_ids: str = "", profile_ids: str = "",
    since_iso: str = "", until_iso: str = "",
) -> dict:
    """Per-post metrics snapshots. Pass comma-separated `post_ids`
    (up to 50) OR `profile_ids` to get every post under those
    profiles in the time window."""
    from .postproxy import post_stats, PostProxyNotConfigured, PostProxyError
    try:
        return await post_stats(
            post_ids=[p.strip() for p in post_ids.split(",") if p.strip()],
            profile_ids=[p.strip() for p in profile_ids.split(",") if p.strip()],
            since_iso=since_iso, until_iso=until_iso,
        )
    except PostProxyNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PostProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# ── Meta Graph integration ──────────────────────────────────────────


@app.get("/integrations/meta/inspect")
async def integrations_meta_inspect() -> dict:
    """One-shot probe of the configured meta_access_token. Returns
    {ok, token, user, pages, actionable, error?} so the UI can render
    'here's what works' before any real fetch. Verbatim Meta error
    messages — those are the most useful diagnostic."""
    from .meta_graph import inspect, MetaNotConfigured
    try:
        return await inspect()
    except MetaNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/integrations/meta/ig-media")
async def integrations_meta_ig_media(
    ig_business_id: str = "", limit: int = 30,
) -> dict:
    """Recent IG Business media for the configured token. If
    ig_business_id is blank, auto-discover via the first Page that has
    a linked Instagram Business account."""
    from .meta_graph import inspect, ig_recent_media, MetaNotConfigured
    try:
        if not ig_business_id:
            probe = await inspect()
            for p in probe.get("pages", []):
                iba = (p.get("instagram_business_account") or {}).get("id")
                if iba:
                    ig_business_id = iba
                    break
        if not ig_business_id:
            raise HTTPException(
                status_code=404,
                detail="No Instagram Business account found on any Page "
                       "this token can see.",
            )
        return await ig_recent_media(ig_business_id, limit=limit)
    except MetaNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/analytics/cohort")
async def analytics_cohort(platform: str = "", days: int = 30) -> dict:
    """Per-brand-account leaderboard ranked by views in the window —
    one row per configured account so the user can compare their own
    accounts (personal IG vs brand IG vs TikTok). Configured accounts
    with no data yet appear with zeroes."""
    from .analytics import accounts_leaderboard
    return {
        "rows": await accounts_leaderboard(platform=platform, days=days),
    }


# ── brand accounts (the BRAND's own social handles) ─────────────────


@app.get("/analytics/accounts")
async def analytics_accounts_list() -> dict:
    """The configured brand accounts. Empty list when none configured."""
    from .brand_accounts import get_brand_accounts
    return {"accounts": await get_brand_accounts()}


class _BrandAccount(BaseModel):
    platform: str
    handle: str
    name: str = ""


class _BrandAccountsRequest(BaseModel):
    accounts: list[_BrandAccount]


@app.post("/analytics/accounts")
async def analytics_accounts_set(req: _BrandAccountsRequest) -> dict:
    """Wholesale replace the brand's tracked accounts. accounts =
    [{platform, handle, name?}]. Returns the cleaned list."""
    from .brand_accounts import set_brand_accounts
    cleaned = await set_brand_accounts(
        [a.model_dump() for a in req.accounts]
    )
    return {"accounts": cleaned}


@app.post("/analytics/refresh")
async def analytics_refresh(limit: int = 30) -> dict:
    """Scrape recent posts for every configured brand account, ingest
    them. Reuses the watchlist refresh path so the same Apify actors,
    scoring, and ingestion code run unchanged — just over the brand
    accounts instead of the peer watchlist.

    Returns the per-account counts so the UI shows what came in."""
    from .brand_accounts import brand_handles_by_platform
    from .trends import refresh_watchlist
    handles = await brand_handles_by_platform()
    if not handles:
        return {
            "scraped": 0, "stored": 0,
            "note": "No brand accounts configured — add some first.",
        }
    try:
        result = await refresh_watchlist(handles, max(1, min(limit, 50)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"refresh failed: {e}",
        ) from e
    return {
        "scraped": result.get("found", 0),
        "stored": len(result.get("stored_event_ids") or []),
        "provider": result.get("provider"),
    }


# ── trend → script handoff (back to existing routes) ────────────────


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
    # Style references get the deep Design Inspector → a named, reusable style
    # template (the "trending video styles" library). It also refreshes the
    # media card's analysis, so the perception fingerprint stays available.
    if asset.get("role") == "style_reference":
        from .templates import build_template_from_media
        return await build_template_from_media(
            media_id, tenant_id=tenant,
            file_path=asset["file_path"], uri=asset.get("uri", ""),
        )
    # Perception path (other roles). Resolve to a local file first — Supabase-
    # backed uploads aren't on local disk, so we download before ffmpeg.
    import shutil as _shutil

    from .media import fetch_media_local
    await set_analysis_status(media_id, "pending", tenant)
    local_path, tmpdir = await fetch_media_local(asset["file_path"], asset.get("uri", ""))
    if not local_path:
        await set_analysis_status(media_id, "unsupported", tenant)
        return None
    try:
        result = await analyze_file(local_path)
    finally:
        if tmpdir:
            _shutil.rmtree(tmpdir, ignore_errors=True)
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

    # Exact-duplicate cross-check: every upload is content-hashed; re-uploading
    # a byte-identical file into the same role is refused with a pointer to the
    # existing asset — for style references this also means the Design
    # Inspector never burns a second analysis on the same video.
    import hashlib as _hashlib
    _hash_tag = f"sha256:{_hashlib.sha256(data).hexdigest()[:32]}"
    # Explicit tenant predicate as defense-in-depth: RLS scopes this
    # already (migration 034), but a 409 here echoes the matched asset's
    # title back to the caller — never let that cross a tenant boundary
    # even if the connected role bypasses RLS.
    async with acquire() as conn:
        _dup = await conn.fetchrow(
            "SELECT id, title FROM media_assets WHERE role = $1 AND $2 = ANY(tags) "
            "AND tenant_id = current_setting('app.current_tenant', true)::uuid LIMIT 1",
            role, _hash_tag,
        )
    if _dup:
        raise HTTPException(
            status_code=409,
            detail=(f"this exact file is already in the library as "
                    f"'{_dup['title'] or _dup['id']}' — not re-analyzing it"),
        )

    tenant = str(settings.default_tenant_id)
    # to_thread: the Supabase storage client is sync HTTP — a big upload
    # pushed inline would freeze the whole server for its duration.
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, data, file.filename or "upload.mp4"
    )
    created = await create_media(
        role=role,
        source_type="upload",
        uri=served_uri,
        file_path=file_path,
        title=title or (file.filename or ""),
        platform=platform,
        mime=file.content_type or "",
        tags=[t.strip() for t in tags.split(",") if t.strip()] + [_hash_tag],
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
