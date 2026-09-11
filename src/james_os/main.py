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
from pydantic import BaseModel, Field

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
    AttachPostImageRequest,
    BackfillImagesRequest,
    CreateBatchRequest,
    IdeaStatusRequest,
    MultiGenerateRequest,
    PlugIn,
    PostComposeRequest,
    PostImageRequest,
    SetPostImageRequest,
    SoulImageRequest,
    SpeakerCreate,
    SpeakerUpdate,
    SpeakerTagsRequest,
    VideoTrimRequest,
    WhitepaperRequest,
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
    # Platform heartbeat: table-driven recurring jobs (daily brand research,
    # and every future scheduled engine — playbooks, press scans, analytics).
    from .scheduler import scheduler_loop
    jobs_loop = asyncio.create_task(scheduler_loop())
    # Load the shared marketing canon (Brand Intelligence Corpus) once —
    # idempotent + version-gated, non-blocking so boot never waits on it. Every
    # brand's generation + strategy grounds in it via house_knowledge.
    from .house_knowledge import ensure_ingested
    app.state.house_knowledge_task = asyncio.create_task(ensure_ingested())
    yield
    scheduler.cancel()
    jobs_loop.cancel()
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
    # Public /v1 service API: bypasses the browser cookie gate; the /v1 router
    # enforces its own service-API-key auth (see api_v1.require_service).
    "/v1/",
)


def _request_is_public(path: str) -> bool:
    if _auth_is_public(path):
        return True
    return any(path.startswith(p) for p in _EXTRA_PUBLIC)


# The service API key (machine-to-machine) may drive these product-feature path
# families, NOT just /v1. It is an EXPLICIT ALLOWLIST — anything not listed
# (credentials, connections, integrations, the autonomous agent, /auth, /api/*
# admin/debug/seed, /plug-ins) stays cookie-only and unreachable by the key.
_SERVICE_ALLOWED = (
    "/ask", "/ingest", "/knowledge", "/content-library", "/research", "/trends",
    "/analytics", "/media", "/suggestions", "/changes", "/voice", "/hero",
    "/higgsfield", "/templates", "/speakers", "/brand-profile", "/brand-kit",
    "/intake", "/post", "/video", "/long-form", "/autopilot", "/compositions",
    "/events", "/strategy", "/generate", "/generate-multi", "/generate-script",
    "/images", "/competitors",
    # The tool-using agent. This is the only entry here that can ACT on its own
    # — approve_item, generate_post, generate_reel, run_autopilot all change
    # live state — so the caller is responsible for asking a human first. BM2's
    # copilot starts a run only on an explicit button press, never from a typed
    # message. Adding it is what lets the copilot do the things the platform
    # can already do, instead of only talking about them.
    "/agent",
)


def _service_key_allowed(path: str) -> bool:
    # Boundary match so "/video" can't also open "/videox".
    return any(path == p or path.startswith(p + "/") for p in _SERVICE_ALLOWED)


@app.middleware("http")
async def auth_middleware(request: _Req, call_next):
    path = request.url.path
    if _request_is_public(path):
        return await call_next(request)
    # Machine-to-machine: a valid service key authorizes the allowlisted product
    # API as the key's bound tenant. Invalid key / non-allowlisted path falls
    # through to the browser cookie gate below (→ 401 without a session).
    authz = request.headers.get("authorization") or ""
    if authz.lower().startswith("bearer ") and _service_key_allowed(path):
        from .api_v1 import is_platform_key, service_key_tenant, tenant_is_real
        tid = service_key_tenant(authz, request.headers.get("x-tenant-id"))
        if tid is not None:
            # A platform key may name any tenant; reject a bad/stale X-Tenant-Id
            # cleanly instead of writing/reading under a nonexistent (ghost) tenant.
            if is_platform_key(authz) and not await tenant_is_real(tid):
                return _JSON(
                    {"detail": "unknown tenant: X-Tenant-Id does not match any "
                               "provisioned tenant"},
                    status_code=404)
            _db_set_tenant(str(tid))
            request.state.tenant_id = str(tid)
            request.state.user_id = None
            request.state.user_email = ""
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


# Security headers on every response + a body-size guard for the document-ingest
# endpoints. Large video uploads legitimately stream GBs, so those paths are NOT
# capped here (streaming-to-disk is the proper fix for those). Registered after
# auth_middleware so it runs OUTERMOST and its headers wrap every response
# (including the /media-files static mount — nosniff kills MIME-sniff XSS there).
# Only /ingest/document is a pure text-doc path safe to cap. /knowledge/ingest
# legitimately accepts (and preserves) large video files, so it is NOT capped
# here — its zip path is bounded separately by the decompression-bomb guard.
_DOC_INGEST_PATHS = ("/ingest/document",)
_MAX_DOC_BYTES = 100 * 1024 * 1024


@app.middleware("http")
async def security_headers(request: _Req, call_next):
    if request.url.path in _DOC_INGEST_PATHS:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > _MAX_DOC_BYTES:
            return _JSON(
                {"detail": "file too large (documents are capped at 100 MB)"},
                status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # SAMEORIGIN (not DENY) so the app's own same-origin embeds still work while
    # cross-origin clickjacking is blocked.
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    # Behind Railway's TLS-terminating proxy uvicorn sees http, so trust the
    # forwarded proto too — else HSTS never fires in production.
    if (request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"):
        resp.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


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
from .xpoz_api import router as xpoz_router
from .pri_plug_api import router as pri_plug_router
from .press_api import router as press_router
from .academy_api import router as academy_router
from .competitors_api import router as competitors_router
from .api_v1 import router as v1_router
app.include_router(autopilot_bulk_router)
app.include_router(analytics_live_router)
app.include_router(research_roster_router)
app.include_router(voice_ingest_router)
app.include_router(templates_router)
from .reel_api import router as reel_router  # noqa: E402
app.include_router(reel_router)
app.include_router(feedback_changes_router)
app.include_router(brand_kit_router)
app.include_router(xpoz_router)
# Competitor intelligence — niche → competitors → their posts →
# visual analysis → strategy. See competitors.py / competitor_sync.py.
app.include_router(competitors_router)
# Starter samples — classify the brand's own photos and turn the best ones into
# EXAMPLE posts to react to (no template picking). See hero_templatize.py.
from .starter_samples_api import router as starter_samples_router  # noqa: E402
app.include_router(starter_samples_router)
# PRI plug — pull PreReal Intelligence (per silo) into this brand's memory.
app.include_router(pri_plug_router)
# Press monitoring — scan mentions, file to memory, grounded digest.
app.include_router(press_router)
# Academy — dump lessons/docs → generate a grounded content campaign.
app.include_router(academy_router)
# Public /v1 service façade (API-key auth) — lets another platform drive
# JAMES OS headlessly. See api_v1.py.
app.include_router(v1_router)


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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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


@app.get("/plug-ins/brain")
async def plug_ins_brain() -> dict[str, Any]:
    """The full 'what is the brain behind this' picture for the Brand page:
    every source that steers generation, with counts + the learned-rules
    ledger. Manual plug-ins are just ONE of the inputs — the page must never
    look empty while 100+ learned rules and 1,000+ voice exemplars are live."""
    from .learning import recent_guardrails
    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 (SELECT count(*) FROM plug_ins WHERE active = true) AS manual_rules,
                 (SELECT count(*) FROM events
                   WHERE payload->>'category' = 'frustration') AS learned_rules,
                 (SELECT count(*) FROM events
                   WHERE payload->>'category' = 'voice_corpus') AS voice_exemplars,
                 (SELECT count(*) FROM document_metadata) AS knowledge_docs,
                 (SELECT count(*) FROM events
                   WHERE source->>'dedupe_key' LIKE 'kb-%') AS knowledge_chunks""")
    return {
        "counts": dict(row) if row else {},
        "learned": await recent_guardrails(limit=25),
    }


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


# ────────────────────────────────────────────── Knowledge Base ──
# The common entry point for a brand's company documents (intelligence
# parity, Phase 1): upload files or a ZIP → full-format extraction →
# tenant-isolated memory → immediately askable + groundable.

@app.post("/knowledge/ingest", status_code=201)
async def knowledge_ingest(
    file: UploadFile = File(...),
    category: str = Form("company_doc"),
    notes: str = Form(""),
    auto_classify: str = Form("1"),
) -> dict[str, Any]:
    """Ingest a company document — or a whole ZIP of them (≤60 files/50 MB,
    junk entries filtered, per-file results). Every file is PRESERVED and
    filed even when no text is extractable; readable ones are chunked +
    embedded into the tenant's memory so Ask answers from them at once."""
    from .knowledge import ingest_knowledge_document, ingest_zip, is_zip
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    name = file.filename or "upload.bin"
    classify = auto_classify not in ("0", "false", "no", "off")
    try:
        if is_zip(name, file.content_type or ""):
            return await ingest_zip(
                data=data, category=category, notes=notes,
                auto_classify=classify,
            )
        result = await ingest_knowledge_document(
            data=data, original_name=name,
            mime=file.content_type or "", category=category, notes=notes,
            auto_classify=classify,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": result.get("ok", False), "expanded": False, "total": 1,
        "filed": 1 if result.get("ok") else 0,
        "skipped": 1 if result.get("skipped") else 0,
        "failed": 0 if (result.get("ok") or result.get("skipped")) else 1,
        "results": [result],
    }


@app.get("/knowledge/documents")
async def knowledge_documents() -> dict[str, Any]:
    """The tenant's document ledger — every filed company doc + its
    extraction/indexing status."""
    from .knowledge import list_documents
    return {"documents": await list_documents()}


@app.delete("/knowledge/documents/{doc_id}")
async def knowledge_delete(doc_id: UUID) -> dict[str, Any]:
    """Remove a document everywhere: ledger, stored file, and its memory chunks."""
    from .knowledge import delete_document
    ok = await delete_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True, "id": str(doc_id)}


@app.get("/knowledge/documents/{doc_id}/download")
async def knowledge_download(doc_id: UUID) -> dict[str, Any]:
    """Signed, time-limited download URL (knowledge storage is private)."""
    from .knowledge import document_download_url
    url = await document_download_url(doc_id)
    if not url:
        raise HTTPException(status_code=404, detail="document or file not found")
    return {"url": url}


@app.post("/knowledge/whitepaper", status_code=202)
async def knowledge_whitepaper(req: WhitepaperRequest) -> dict[str, Any]:
    """Generate a publication-grade white paper GROUNDED in the tenant's own
    Knowledge Base (retrieve → rerank → learn the best-in-class structure via
    live research → write with [n] citations). Generation takes 1-2 minutes,
    so it runs in the background — poll GET /knowledge/whitepaper/{job_id}.
    The finished paper is saved back into the Knowledge Base
    (category='research'), so Ask cites it and the content engine grounds
    posts/reels on it — thesis → paper → content."""
    from .whitepaper import start_whitepaper_job
    if not (req.topic or "").strip():
        raise HTTPException(status_code=400, detail="topic is required")
    job_id = start_whitepaper_job(
        topic=req.topic, audience=req.audience, goal=req.goal,
    )
    return {"job_id": job_id, "status": "running"}


@app.get("/knowledge/whitepaper/{job_id}")
async def knowledge_whitepaper_status(job_id: str, request: Request) -> dict[str, Any]:
    """Poll a white-paper job: {status: running|done|failed, result?, error?}."""
    from .whitepaper import get_whitepaper_job
    job = get_whitepaper_job(job_id, getattr(request.state, "tenant_id", None))
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="job not found (server may have restarted — check the "
                   "Knowledge Base ledger; a finished paper is saved there)")
    return job


@app.post("/knowledge/thesis/{doc_id}/develop", status_code=202)
async def knowledge_thesis_develop(doc_id: UUID, full: bool = False) -> dict[str, Any]:
    """Develop the CEO's weekly thesis: extract its theme + claims → run
    topic intelligence on the theme (briefs filed into memory) → write a
    white paper that ARGUES the thesis. `?full=1` is the ONE BUTTON: it
    continues into the content pack (posts + reels) and a podcast episode —
    a whole week's output from one dropped thesis. Runs in the background —
    poll GET /knowledge/thesis/develop/{job_id}. Idempotent while running."""
    from .thesis import start_develop_job
    return {"job_id": start_develop_job(doc_id, full=full), "status": "running"}


@app.get("/strategy/state")
async def strategy_state() -> dict[str, Any]:
    """Everything the Morning Brief needs in one call: latest prescription,
    playbooks (with changed flags), queue snapshot, suggestions count,
    interview stats."""
    from .brands import list_suggestions
    from .db import acquire as _acq
    from .intake_agent import interview_stats
    from .strategy import latest_playbooks, latest_prescription
    async with _acq() as conn:
        pending = await conn.fetchval(
            "SELECT count(*) FROM actions WHERE status='pending'")
    return {
        "prescription": await latest_prescription(),
        "playbooks": [
            {k: p[k] for k in ("platform", "version", "changed", "refreshed_at",
                               "key_points")}
            for p in await latest_playbooks()
        ],
        "queue_pending": int(pending or 0),
        "suggestions": await list_suggestions(status="suggested"),
        "interview": await interview_stats(),
    }


@app.post("/strategy/prescribe", status_code=202)
async def strategy_prescribe() -> dict[str, Any]:
    """Compose a fresh Prescription NOW (the scheduler also does this
    weekly). Refreshes nothing itself — run playbooks/peers first if stale."""
    import asyncio as _aio

    from .db import _request_tenant
    tid = _request_tenant.get() or settings.default_tenant_id
    from .strategy import compose_prescription

    async def _run() -> None:
        try:
            await compose_prescription(tid)
        except Exception as e:  # noqa: BLE001
            print(f"[strategy] manual prescribe failed: {e}")

    task = _aio.create_task(_run())
    _SUGGESTION_TASKS.add(task)
    task.add_done_callback(_SUGGESTION_TASKS.discard)
    return {"started": True}


@app.post("/strategy/refresh-inputs", status_code=202)
async def strategy_refresh_inputs() -> dict[str, Any]:
    """Refresh the prescription's inputs NOW: platform playbooks + peer
    snapshots (also on weekly cadences via the scheduler)."""
    import asyncio as _aio

    from .db import _request_tenant
    tid = _request_tenant.get() or settings.default_tenant_id
    from .strategy import run_peer_snapshot, run_playbook_refresh

    async def _run() -> None:
        try:
            await run_playbook_refresh(tid)
            await run_peer_snapshot(tid)
        except Exception as e:  # noqa: BLE001
            print(f"[strategy] refresh-inputs failed: {e}")

    task = _aio.create_task(_run())
    _SUGGESTION_TASKS.add(task)
    task.add_done_callback(_SUGGESTION_TASKS.discard)
    return {"started": True}


@app.post("/strategy/prescription/{prescription_id}/accept", status_code=202)
async def strategy_accept(
    prescription_id: UUID, body: dict = Body(default={}),
) -> dict[str, Any]:
    """Accept the plan (all lines, or body.items=[indexes]) — each line
    produces its pieces through the standard machinery into the Approval
    Queue, capped per accept for cost sanity."""
    from .strategy import accept_prescription
    items = (body or {}).get("items")
    try:
        return await accept_prescription(
            prescription_id,
            [int(i) for i in items] if isinstance(items, list) else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/intake/research")
async def intake_research(body: dict = Body(default={})) -> dict[str, Any]:
    """The 'is this your brand?' step: type a name (+ optional hints) and
    the Researcher agent pulls what's online into a PROPOSED profile with
    sources. Nothing is saved until the operator accepts."""
    from .intake_agent import research_brand
    try:
        return await research_brand(
            str((body or {}).get("name") or ""),
            str((body or {}).get("hints") or ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/intake/niche")
async def intake_niche_get() -> dict[str, Any]:
    """The brand's niche: what it confirmed, and a proposal if it hasn't.

    Onboarding asks this and keeps the answer. Competitor discovery refuses
    to run until it exists, because the niche decides which accounts get
    studied and a wrong one misdirects every stage after it."""
    from .brands import get_niche
    current = await get_niche()
    if current["confirmed"]:
        return {**current, "proposal": None}
    from .intake_agent import propose_niche
    try:
        proposal = await propose_niche()
    except Exception as e:  # noqa: BLE001 — a failed proposal still lets the
        # operator type their own, which is the authoritative path anyway.
        proposal = {"niche": "", "terms": [], "error": str(e)[:200]}
    return {**current, "proposal": proposal}


class NicheConfirm(BaseModel):
    niche: str
    terms: list[str] = []


@app.post("/intake/niche/confirm")
async def intake_niche_confirm(req: NicheConfirm) -> dict[str, Any]:
    """Record the brand's own answer — edited or accepted as proposed."""
    from .brands import confirm_niche
    try:
        return await confirm_niche(req.niche, req.terms)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/intake/questions")
async def intake_questions(status: str = "") -> dict[str, Any]:
    """The deep-interview ledger: research-answered ones first (confirm
    them), then open ones (answer or leave for the Researcher)."""
    from .intake_agent import interview_stats, list_questions
    return {"questions": await list_questions(status=status),
            "stats": await interview_stats()}


@app.post("/intake/questions/{question_id}/confirm")
async def intake_question_confirm(
    question_id: UUID, body: dict = Body(default={}),
) -> dict[str, Any]:
    """Confirm a research answer as-is, or supply/correct the answer —
    either way it becomes citable brand memory."""
    from .intake_agent import confirm_answer
    try:
        return await confirm_answer(
            question_id, (body or {}).get("answer"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/intake/questions/{question_id}/dismiss")
async def intake_question_dismiss(question_id: UUID) -> dict[str, Any]:
    from .db import acquire as _acq
    async with _acq() as conn:
        tag = await conn.execute(
            "UPDATE brand_questions SET status='dismissed' WHERE id=$1",
            question_id)
    if not tag.endswith("1"):
        raise HTTPException(status_code=404, detail="question not found")
    return {"ok": True}


@app.post("/intake/interview/run", status_code=202)
async def intake_interview_run() -> dict[str, Any]:
    """Run one interview cycle NOW (the scheduler also runs it every 12h
    once intake is done): Interviewer tops up questions, Researcher answers
    what it can."""
    import asyncio as _aio

    from .db import _request_tenant
    tid = _request_tenant.get() or settings.default_tenant_id
    from .intake_agent import run_brand_interview

    async def _run() -> None:
        try:
            # Manual runs skip the intake_done gate via a direct cycle.
            from .intake_agent import generate_questions, research_answers
            await generate_questions(tid)
            await research_answers(tid)
        except Exception as e:  # noqa: BLE001
            print(f"[intake_agent] manual run failed: {e}")

    task = _aio.create_task(_run())
    _SUGGESTION_TASKS.add(task)
    task.add_done_callback(_SUGGESTION_TASKS.discard)
    return {"started": True}


@app.get("/brand-profile")
async def brand_profile_get() -> dict[str, Any]:
    """The tenant's brand identity (the Intake's output) — or intake_done:
    false when onboarding hasn't happened."""
    from .brands import get_brand_profile
    prof = await get_brand_profile()
    return prof or {"intake_done": False}


@app.put("/brand-profile")
async def brand_profile_put(body: dict = Body(default={})) -> dict[str, Any]:
    """Create/update the brand identity. Setting intake_done=true enables
    the daily research job — the brand manager starts doing its homework
    the next morning."""
    from .brands import upsert_brand_profile
    return await upsert_brand_profile(body or {})


@app.get("/suggestions/list")
async def suggestions_list(status: str = "suggested") -> dict[str, Any]:
    """What the brand manager proposes on its own (daily research, and every
    future proactive engine). status='' returns all."""
    from .brands import list_suggestions
    return {"suggestions": await list_suggestions(status=status)}


@app.post("/suggestions/{suggestion_id}/accept", status_code=202)
async def suggestions_accept(suggestion_id: UUID) -> dict[str, Any]:
    """Turn a suggestion into queued content (post → voice engine + image
    gates; reel → the locked video template) — lands in the Approval Queue."""
    from .brands import accept_suggestion
    try:
        return await accept_suggestion(suggestion_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/suggestions/{suggestion_id}/dismiss")
async def suggestions_dismiss(suggestion_id: UUID) -> dict[str, Any]:
    from .brands import dismiss_suggestion
    if not await dismiss_suggestion(suggestion_id):
        raise HTTPException(status_code=404, detail="suggestion not found")
    return {"ok": True}


@app.post("/suggestions/refresh", status_code=202)
async def suggestions_refresh() -> dict[str, Any]:
    """Run the daily research pass NOW for the current tenant (the scheduler
    also runs it every 24h once intake is done)."""
    import asyncio as _aio

    from .db import _request_tenant
    tid = _request_tenant.get() or settings.default_tenant_id
    from .brand_research import run_daily_brand_research

    async def _run() -> None:
        try:
            await run_daily_brand_research(tenant_id=tid, config={})
        except Exception as e:  # noqa: BLE001
            print(f"[brand_research] manual refresh failed: {e}")

    task = _aio.create_task(_run())
    _SUGGESTION_TASKS.add(task)
    task.add_done_callback(_SUGGESTION_TASKS.discard)
    return {"started": True}


_SUGGESTION_TASKS: set = set()


@app.post("/knowledge/content-pack", status_code=202)
async def knowledge_content_pack(body: dict = Body(default={})) -> dict[str, Any]:
    """Fan a white paper (or any research doc) into a content PACK: N posts
    + M reels, each from a different angle of the paper, all through the
    autopilot machinery into the Approval Queue. Poll
    GET /knowledge/content-pack/{job_id}."""
    from .content_pack import start_content_pack_job
    doc_id = (body or {}).get("doc_id")
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")
    try:
        job_id = start_content_pack_job(
            UUID(str(doc_id)),
            posts=int((body or {}).get("posts") or 3),
            reels=int((body or {}).get("reels") or 2),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid doc_id") from e
    return {"job_id": job_id, "status": "running"}


@app.get("/knowledge/content-pack/{job_id}")
async def knowledge_content_pack_status(job_id: str, request: Request) -> dict[str, Any]:
    """Poll: {status, stage?: angles|posts|reels, result?, error?}."""
    from .content_pack import get_content_pack_job
    job = get_content_pack_job(job_id, getattr(request.state, "tenant_id", None))
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="job not found (server may have restarted — queued items "
                   "are in the Approval Queue)")
    return job


@app.get("/knowledge/thesis/develop/{job_id}")
async def knowledge_thesis_develop_status(job_id: str, request: Request) -> dict[str, Any]:
    """Poll: {status: running|done|failed, stage?: reading|researching|writing,
    result?, error?}."""
    from .thesis import get_develop_job
    job = get_develop_job(job_id, getattr(request.state, "tenant_id", None))
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="job not found (server may have restarted — the artifacts "
                   "of a finished run are saved in the Knowledge Base)")
    return job


@app.post("/knowledge/podcast", status_code=202)
async def knowledge_podcast(body: dict = Body(default={})) -> dict[str, Any]:
    """Turn a Knowledge-Base document (weekly thesis or white paper) into a
    podcast episode: script in the brand voice → narrated with the brand's
    cloned ElevenLabs voice → mp3 in the media store + approval-queue entry.
    Poll GET /knowledge/podcast/{job_id}."""
    from .podcast import start_podcast_job
    from .tts import tts_configured
    doc_id = (body or {}).get("doc_id")
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")
    if not tts_configured():
        raise HTTPException(
            status_code=400,
            detail="brand voice not configured — set the ElevenLabs API key "
                   "and voice id in Settings first")
    try:
        return {"job_id": start_podcast_job(UUID(str(doc_id))), "status": "running"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid doc_id") from e


@app.get("/knowledge/podcasts")
async def knowledge_podcasts() -> dict[str, Any]:
    """Every generated episode (newest first) with its review status."""
    from .podcast import list_podcasts
    return {"episodes": await list_podcasts()}


@app.get("/knowledge/podcast/{job_id}")
async def knowledge_podcast_status(job_id: str, request: Request) -> dict[str, Any]:
    """Poll: {status: running|done|failed, stage?: scripting|narrating|
    publishing, result?, error?}."""
    from .podcast import get_podcast_job
    job = get_podcast_job(job_id, getattr(request.state, "tenant_id", None))
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="job not found (server may have restarted — finished "
                   "episodes are listed under /knowledge/podcasts)")
    return job


# ── Knowledge governance: vocab, silos, entities ──

@app.get("/knowledge/vocab")
async def knowledge_vocab() -> dict[str, Any]:
    """Controlled vocabularies (BUs, asset classes, doc types, statuses,
    sensitivity tiers + their plain-English meaning, entity type codes)."""
    from . import vocab
    return {
        "business_units": vocab.BUSINESS_UNITS,
        "asset_classes": vocab.ASSET_CLASSES,
        "doc_type_groups": vocab.DOC_TYPE_GROUPS,
        "statuses": vocab.STATUSES,
        "sensitivities": vocab.SENSITIVITIES,
        "sensitivity_info": vocab.SENSITIVITY_INFO,
        "default_sensitivity": vocab.DEFAULT_SENSITIVITY,
        "entity_types": vocab.ENTITY_TYPES,
    }


@app.get("/knowledge/silos")
async def knowledge_silos(stats: bool = False) -> dict[str, Any]:
    from .silos import list_silos
    return {"silos": await list_silos(stats=stats)}


class _SiloCreate(BaseModel):
    name: str
    id: str = ""
    description: str = ""


@app.post("/knowledge/silos", status_code=201)
async def knowledge_silo_create(req: _SiloCreate) -> dict[str, Any]:
    from .silos import create_silo
    try:
        return {"ok": True, "silo": await create_silo(req.name, req.id, req.description)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/knowledge/silos/{silo_id}")
async def knowledge_silo_delete(silo_id: str) -> dict[str, Any]:
    from .silos import delete_silo
    ok = await delete_silo(silo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="silo not found")
    return {"ok": True, "id": silo_id}


@app.get("/knowledge/entities")
async def knowledge_entities() -> dict[str, Any]:
    """The entity registry — stable EntityIDs auto-created during ingest."""
    from .entities import list_entities
    return {"entities": await list_entities()}


# ── Topic Intelligence + cross-silo synthesis (background jobs) ──

class _IntelRequest(BaseModel):
    topic: str
    auto_plan: bool = True
    force: bool = False


@app.post("/knowledge/intelligence", status_code=202)
async def knowledge_intelligence(req: _IntelRequest) -> dict[str, Any]:
    """Build decision-grade TOPIC INTELLIGENCE: coverage check → AI-planned
    gap research (parallel web sweeps, each saved as corpus) → connect-the-dots
    synthesis (also saved). Minutes of work → background job + poll."""
    from .intelligence import start_intelligence_job
    if not (req.topic or "").strip():
        raise HTTPException(status_code=400, detail="topic is required")
    job_id = start_intelligence_job(
        topic=req.topic, auto_plan=req.auto_plan, force=req.force,
    )
    return {"job_id": job_id, "status": "running"}


@app.get("/knowledge/intelligence/{job_id}")
async def knowledge_intelligence_status(job_id: str, request: Request) -> dict[str, Any]:
    from .intelligence import get_intelligence_job
    job = get_intelligence_job(job_id, getattr(request.state, "tenant_id", None))
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="job not found (server may have restarted — gathered briefs "
                   "are saved in the Knowledge Base ledger)")
    return job


class _SynthRequest(BaseModel):
    theme: str = ""
    silo_ids: list[str] = Field(default_factory=list)


@app.post("/knowledge/synthesize")
async def knowledge_synthesize(req: _SynthRequest) -> dict[str, Any]:
    """Cross-silo portfolio synthesis: the patterns / synergies / tensions /
    risks / opportunities that only emerge ACROSS project silos."""
    from .intelligence import synthesize_portfolio
    try:
        return await synthesize_portfolio(
            theme=req.theme, silo_ids=req.silo_ids or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# ── Commitments (action items mined from docs) ──

@app.get("/knowledge/commitments")
async def knowledge_commitments(status: str = "") -> dict[str, Any]:
    from .commitments import list_commitments
    return {"commitments": await list_commitments(status=status)}


class _CommitmentStatus(BaseModel):
    status: str


@app.patch("/knowledge/commitments/{commitment_id}")
async def knowledge_commitment_update(
    commitment_id: UUID, req: _CommitmentStatus,
) -> dict[str, Any]:
    from .commitments import update_commitment_status
    try:
        ok = await update_commitment_status(commitment_id, req.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="commitment not found")
    return {"ok": True, "id": str(commitment_id), "status": req.status}


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
        # Revoke ALL of this user's sessions so a stolen cookie can't survive a
        # password change (the user re-logs in with the new password).
        await conn.execute(
            "UPDATE sessions SET revoked_at = now() "
            "WHERE user_id = $1 AND revoked_at IS NULL",
            sess["user_id"],
        )
    return {"ok": True, "sessions_revoked": True}


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    return await ask(req)


# ── agent (Ask the memory → "Do" mode) ──────────────────────────────


class _AgentRunRequest(BaseModel):
    prompt: str


@app.post("/agent/run", status_code=201)
async def agent_run(
    req: _AgentRunRequest, background: BackgroundTasks, request: Request
) -> dict:
    """Kick an agent run. Returns immediately with the run id — the
    actual tool-use loop runs in the background, persisting state to
    agent_runs as it goes so the UI can poll for live updates.

    Runs under the caller's brand: BM2's service client sends X-Tenant-Id, so
    the agent's write tools (generate_post/carousel/reel, approve, edit_caption)
    land on THAT brand's tenant, not the default. Absent header (BM1's own /ask
    'Do' UI) falls back to the default tenant, exactly as before."""
    from .agent import create_run, run_agent
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    tenant_id: UUID | None = None
    raw_tid = request.headers.get("x-tenant-id")
    if raw_tid:
        try:
            tenant_id = UUID(raw_tid.strip())
        except ValueError:
            tenant_id = None
    row = await create_run(req.prompt.strip(), tenant_id)
    background.add_task(run_agent, UUID(row["id"]), tenant_id)
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


@app.post("/post/compose")
async def post_compose(req: PostComposeRequest) -> dict:
    """One topic → an on-voice written post AND a matching hero image, together.

    Generates the post (full voice + voice-QA pipeline, queued for approval),
    then — when include_image — directs a cinematic scene from the *draft* and
    renders it baselined on the brand hero's uploaded photos (same person every
    post), attaching it to the same queued action so the reviewer sees text +
    image as one item. The image is additive: a render failure never loses the
    already-queued text.
    """
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")

    draft = await generate_content(
        ContentBrief(
            platform=req.platform or "instagram",
            format="post",
            pillar=req.pillar,
            topic=topic,
            research_subject=req.research_subject,
            extra_instructions=req.extra_instructions,
        )
    )

    image_url: str | None = None
    image_error: str | None = None
    if req.include_image:
        if draft.action_id is None:
            image_error = "post was not queued, so no image was attached"
        else:
            from .db import _request_tenant
            try:
                _tid = _request_tenant.get()
            except LookupError:
                _tid = None
            _tid = _tid or settings.default_tenant_id
            from .autopilot_bulk import _attach_image_to_action
            try:
                image_url = await _attach_image_to_action(
                    action_id=draft.action_id,
                    idea={"topic": topic, "title": topic},
                    platform=req.platform or "instagram",
                    draft_text=draft.draft or topic,
                    tenant_id=_tid,
                )
                if image_url is None:
                    image_error = (
                        "image generation unavailable — add OPENAI_API_KEY in "
                        "Settings to attach a hero image"
                    )
            except Exception as e:  # noqa: BLE001 — text already queued; image is additive
                image_error = f"image generation failed: {e}"

    return {
        "draft": draft.model_dump(mode="json"),
        "image_url": image_url,
        "image_error": image_error,
    }


@app.post("/post/attach-image")
async def post_attach_image(req: AttachPostImageRequest) -> dict:
    """Render a hero-referenced image for an already-queued post and attach it
    to that action (browser composer's 2nd step — see /generate for the 1st).

    Directs a cinematic scene from the draft, renders the brand hero into it
    (consistent person across posts), patches image_url onto the action's
    payload, and registers it in the media library. Additive: on any failure
    the queued text stands on its own and image_error explains why.
    """
    from .db import _request_tenant
    try:
        _tid = _request_tenant.get()
    except LookupError:
        _tid = None
    _tid = _tid or settings.default_tenant_id

    from .autopilot_bulk import _attach_image_to_action
    topic = (req.topic or "").strip()
    try:
        image_url = await _attach_image_to_action(
            action_id=req.action_id,
            idea={"topic": topic, "title": topic},
            platform=req.platform or "instagram",
            draft_text=(req.draft_text or topic),
            tenant_id=_tid,
        )
    except Exception as e:  # noqa: BLE001 — text already queued; image is additive
        return {"image_url": None, "image_error": f"image generation failed: {e}"}
    return {
        "image_url": image_url,
        "image_error": None if image_url else (
            "image generation unavailable — add OPENAI_API_KEY in Settings to "
            "attach a hero image"
        ),
    }


@app.post("/post/set-image")
async def post_set_image(req: SetPostImageRequest) -> dict:
    """Attach an EXISTING image (a real hero photo the user picked) to a queued
    post — no generation. Patches image_url/media_url/has_image onto the
    action's payload so the reviewer sees text + the chosen photo as one item.
    """
    from .db import _request_tenant
    try:
        _tid = _request_tenant.get()
    except LookupError:
        _tid = None
    url = (req.image_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="image_url is required")
    async with acquire(_tid) as conn:
        status = await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            req.action_id,
            json.dumps({"image_url": url, "media_url": url, "has_image": True}),
        )
    if status.endswith(" 0"):
        raise HTTPException(status_code=404, detail="queued post not found")
    return {"ok": True, "image_url": url}


# Soul-image generation is backgrounded (Higgsfield render is 30-90s, which
# would blow the synchronous gateway timeout). Start → poll, like topic ideas.
_SOUL_IMAGE_JOBS: dict[str, dict] = {}


async def _generate_soul_post_image(
    action_id, topic: str, draft_text: str, aspect: str, soul_id: str, tenant_id
) -> str:
    """Direct a scene from the draft → render James from the Soul ID → download,
    persist, and attach to the queued post. Returns the served image URL."""
    import httpx

    from . import higgsfield_souls as hs
    from .imagegen import direct_image_scene
    from .media import create_media
    from .media import storage as media_storage

    scene = await direct_image_scene(draft_text or "", fallback_topic=topic or "")
    prompt = (scene or topic or "James Prendamano").strip()
    # Keep the whole head/face in frame — at feed ratios the Soul model can
    # otherwise frame full-body with the head against the top edge.
    prompt = (
        prompt + " Framing: head-and-shoulders to waist-up, the full head and "
        "face clearly within the frame (never cropped at the top), eyes toward "
        "camera, centered."
    )[:1500]
    sub = await hs.generate_character_image(
        custom_reference_id=soul_id, prompt=prompt, aspect_ratio=aspect or "4:5",
        strength=0.85,
    )
    rid = sub.get("request_id")
    if not rid:
        raise RuntimeError(sub.get("error") or "Higgsfield submit failed")
    image_url = ""
    for _ in range(45):  # ~3 min
        p = await hs.poll_request(rid)
        st = p.get("status")
        if st == "completed" and p.get("image_url"):
            image_url = p["image_url"]
            break
        if st in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"Higgsfield render {st}")
        await asyncio.sleep(4)
    if not image_url:
        raise RuntimeError("Higgsfield render timed out")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(image_url)
        r.raise_for_status()
        png = r.content
    tenant = str(tenant_id or settings.default_tenant_id)
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, png, "soul-post.png"
    )
    try:
        await create_media(
            role="post_image", source_type="upload", uri=served_uri,
            file_path=file_path, title=(topic or "Soul image")[:120],
            platform="instagram", mime="image/png",
            tags=["style:soul", "soul"], notes=prompt[:500], tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001 — library bookkeeping must not lose the URL
        pass
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            action_id,
            json.dumps({
                "image_url": served_uri, "media_url": served_uri,
                "has_image": True, "image_prompt": prompt,
            }),
        )
    return served_uri


@app.post("/post/soul-image", status_code=202)
async def post_soul_image_start(req: SoulImageRequest, background: BackgroundTasks) -> dict:
    """Render James from the trained Higgsfield Soul ID and attach to a queued
    post. Backgrounded — returns a job_id; poll GET /post/soul-image/{job_id}."""
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    tid = tid or settings.default_tenant_id
    soul = (settings.higgsfield_soul_id or "").strip()
    if not soul:
        raise HTTPException(
            status_code=400,
            detail="No Higgsfield Soul ID configured. Train one on the Hero page first.",
        )
    job_id = str(uuid4())
    _SOUL_IMAGE_JOBS[job_id] = {"status": "running", "image_url": None, "error": None}
    while len(_SOUL_IMAGE_JOBS) > 30:
        _SOUL_IMAGE_JOBS.pop(next(iter(_SOUL_IMAGE_JOBS)))

    async def _run() -> None:
        try:
            url = await _generate_soul_post_image(
                req.action_id, req.topic, req.draft_text, req.aspect or "9:16",
                soul, tid,
            )
            _SOUL_IMAGE_JOBS[job_id] = {
                "status": "done", "image_url": url,
                "error": None if url else "render produced no image",
            }
        except Exception as e:  # noqa: BLE001
            _SOUL_IMAGE_JOBS[job_id] = {"status": "failed", "image_url": None, "error": str(e)}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@app.get("/post/soul-image/{job_id}")
async def post_soul_image_get(job_id: str) -> dict:
    j = _SOUL_IMAGE_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found (expired or unknown)")
    return {"job_id": job_id, **j}


# Multi-format "designed image" — an LLM art director picks a format (quote
# card / top-bottom meme), generates a TEXT-FREE background, and Pillow overlays
# crisp text + branding. Backgrounded (bg render is slow); start → poll.
_DESIGNED_JOBS: dict[str, dict] = {}


async def _brand_voice_and_profile(tenant_id) -> tuple[str, str]:
    """The brand's voice corpus + profile block for the image art directors, so
    even the on-image card/carousel copy sounds like THIS brand (voice first,
    the hook/CTA playbook second). Best-effort — a miss returns ('', '') and the
    render proceeds voice-blind, exactly as before."""
    voice = bp = ""
    try:
        from .autopilot import _voice_for_ideation
        voice = (await _voice_for_ideation(tenant_id)) or ""
    except Exception:  # noqa: BLE001 — voice is additive, never blocks a render
        voice = ""
    try:
        from .brands import brand_profile_block
        bp = (await brand_profile_block(tenant_id)) or ""
    except Exception:  # noqa: BLE001
        bp = ""
    return voice, bp


async def _generate_carousel_post(action_id, topic, draft_text, tenant_id,
                                  text_only: bool = False) -> tuple[str, str]:
    """Render a designed CAROUSEL: art-director deck → N palette-aware slides
    (cover → inner → CTA) on the brand's OWN photos → store all N and attach a
    `media_urls` list to the action. Returns (cover_url, "carousel"). Only reached
    when the tenant's design brain is on (the art director gates 'carousel').

    `text_only` renders the TYPOGRAPHIC carousel: no photos at all — the cover and
    every inner slide are clean type on the brand ground. Best for a list / steps /
    points that stand on their words."""
    from .brand_kit import get_brand_kit
    from .carousel import carousel as render_carousel
    from .hero_context import get_hero_photo_files
    from .imagegen import direct_carousel_deck
    from .media import create_media
    from .media import storage as media_storage
    from .photo_pick import pick_hero_bytes

    kit = await get_brand_kit(tenant_id)
    try:
        # Guarantee a brand-specific palette: stored theme, else derived from the
        # brand's OWN logo/photos, else neutral — NEVER James's navy (D10).
        from .brand_identity import ensure_brand_palette
        _palette = await ensure_brand_palette(tenant_id, kit=kit)
        if _palette:
            kit["palette"] = _palette
    except Exception:  # noqa: BLE001
        pass
    palette = kit.get("palette")
    handle = (kit.get("handle") or "").strip()
    brand_name = (kit.get("display_name") or "").strip()

    # Brand typography theme (owner's font pick) — scopes over the slide render below.
    from . import image_compose as _ic
    try:
        from . import brand_identity as _bi2, font_themes as _ftm2
        _cfont = _ftm2.resolve(await _bi2.get_brand_font(tenant_id))
    except Exception:  # noqa: BLE001
        _cfont = None
    try:
        from . import brand_identity as _bi2l
        _clook = await _bi2l.get_brand_look(tenant_id)
    except Exception:  # noqa: BLE001
        _clook = None
    # On-image text styling knobs (make-it-white / thicker) — same as single
    # posts, applied across every slide of the deck.
    try:
        from .render_tuning import get_render_tuning
        _ctun = await get_render_tuning(tenant_id)
    except Exception:  # noqa: BLE001
        _ctun = {}

    def _cknob(key: str) -> int:
        try:
            return int(round(float(_ctun.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0
    _chex = str(_ctun.get("image_text_color_hex") or "").strip()
    _ctc = _cknob("image_text_color")
    _ctext_color = _chex or ("white" if _ctc == 1 else "black" if _ctc == 2 else "")
    _ctext_bold = _cknob("image_text_weight") >= 1

    _voice, _bp = await _brand_voice_and_profile(tenant_id)
    deck = await direct_carousel_deck(draft_text or "", topic or "", brand_name,
                                      voice=_voice, brand_profile=_bp, text_only=text_only)

    if text_only:
        # PREMIUM photoless carousel — the brand's locked visual system: a radial
        # brand-palette ground, the name up top, a big statement with its key phrase
        # in the accent, huge stat slides, an N/total index. NO photos at all.
        from .carousel_text import render_text_carousel
        with _ic.brand_fonts(_cfont), _ic.brand_look(_clook), \
                _ic.text_style(_ctext_color, _ctext_bold):
            slides = render_text_carousel(deck, kit, handle)
        cover_key = None
    else:
        # Assign DISTINCT photos from the brand's library to the cover + each photo
        # slide (stat slides need none) — least-recently-used, blur-gated, no repeats.
        try:
            # Full rotation pool (not the 3-ref AI cap) so carousel slides pull
            # DISTINCT photos from across the library, not the same 3.
            refs = await get_hero_photo_files(tenant_id=tenant_id, limit=None)
        except Exception:  # noqa: BLE001
            refs = []
        used: list[str] = []

        async def _pick():
            try:
                p = await pick_hero_bytes(refs, tenant_id, exclude=tuple(used))
                if p is None and refs:
                    # Small library — recycle so every photo slide still gets a photo.
                    used.clear()
                    p = await pick_hero_bytes(refs, tenant_id, exclude=())
                if p:
                    used.append(p[0])
                    return p[1]
            except Exception:  # noqa: BLE001
                pass
            return None

        cover_photo = await _pick()
        cover_key = used[-1] if used else None
        deck_r: dict = {"cover": {**deck["cover"], "photo": cover_photo}, "slides": [], "cta": deck["cta"]}
        for s in deck["slides"]:
            if s.get("kind") == "photo":
                deck_r["slides"].append({"section_label": s.get("section_label", ""),
                                         "headline": s.get("headline", ""), "photo": await _pick()})
            else:
                deck_r["slides"].append({"section_label": s.get("section_label", ""),
                                         "headline": s.get("headline", ""), "stat": s.get("stat", "")})
        with _ic.brand_fonts(_cfont), _ic.brand_look(_clook), \
                _ic.text_style(_ctext_color, _ctext_bold):
            slides = render_carousel(deck_r, palette, handle)

    # ── DESIGN QA GATE (carousel) ───────────────────────────────────────
    # Check the cover + a couple of inner slides for execution flaws before we
    # save anything. A broken carousel is held back (return "" → the caller
    # degrades to a clean single post) rather than shipped. Fail-OPEN if the
    # reviewer is down. Retrying a whole deck is deferred; the gate just prevents
    # a flawed carousel from reaching the owner.
    if settings.design_qa_enabled and slides:
        try:
            from . import render_reviewer
            for _sl in [slides[0], *slides[1:3]]:
                _r = await render_reviewer.review_post(_sl, mime="image/png")
                if _r.get("status") == "ok" and not _r.get("passed"):
                    try:
                        async with acquire(tenant_id) as conn:
                            await conn.execute(
                                "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                                action_id, json.dumps({
                                    "design_qa_failed": True,
                                    "design_qa_flaws": _r.get("critical_flaws") or [],
                                    "design_qa_issues": _r.get("issues") or []}))
                    except Exception:  # noqa: BLE001
                        pass
                    return "", ""
        except Exception:  # noqa: BLE001 — QA must never break generation
            pass

    tenant = str(tenant_id or settings.default_tenant_id)
    urls: list[str] = []
    cover_fp = ""
    for i, png in enumerate(slides):
        u, fp = await asyncio.to_thread(media_storage().save, tenant, png, f"carousel-{i}.png")
        urls.append(u)
        if i == 0:
            cover_fp = fp
    cover_url = urls[0] if urls else ""
    try:
        await create_media(
            role="post_image", source_type="upload", uri=cover_url, file_path=cover_fp,
            title=(topic or "carousel")[:120], platform="instagram", mime="image/png",
            tags=["style:designed_carousel", "designed", "carousel"],
            notes=(deck["cover"].get("headline") or "")[:300], tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001
        pass
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            action_id,
            json.dumps({
                "image_url": cover_url, "media_url": cover_url,
                # The full ordered set of slide images — the carousel itself. The
                # cover is mirrored into image_url so single-image readers (queue
                # card, thumbnails) still show the cover.
                "media_urls": urls,
                "has_image": True, "image_format": "carousel", "slide_count": len(urls),
                **({"hero_photo_key": cover_key} if cover_key else {}),
            }),
        )
    return cover_url, "carousel"


async def _generate_learned_post_image(
    action_id, topic: str, draft_text: str, tenant_id,
    *, extra_sizes: tuple[tuple[int, int], ...] = (), guidance: str = "",
) -> tuple[str, str] | None:
    """Render a post from a layout in the brand's design-template library.

    Returns (served_uri, "learned"), or None to fall back to the nine formats —
    which is what happens whenever the library is too thin to rotate or the
    render does not pass design QA. It never ships a flawed learned layout:
    the nine hand-built formats carry a proven text-containment guarantee, and a
    learned one only replaces them when it clears the same QA gate.

    Every platform shape: the primary is drawn at whatever canvas the caller set
    (image_compose.canvas), and each extra size re-lays-out the SAME layout, copy
    and photo — a learned spec's boxes are fractions of the frame, so it reflows.
    """
    from . import design_templates, image_compose, template_clone
    from .media import create_media
    from .media import storage as media_storage
    from .spec_render import render_spec

    learned = await design_templates.pick(tenant_id)
    if not learned:
        return None
    spec = learned["spec"]
    tid = learned["id"]

    # Copy for THIS post, in the brand's voice — grounded in the post's own topic,
    # not in whatever the reference post happened to be about.
    content = await template_clone._fill_copy(
        spec, tenant_id, {"topic": (topic or draft_text or "")[:300]}, guidance=guidance)
    headline = (content.get("headline") or content.get("stat") or topic or "").strip() \
        or "a moment that captures the brand"
    hero_bytes, _generated, hero_key = await template_clone._hero_or_placeholder(
        tenant_id, headline)
    palette = await template_clone._brand_palette(tenant_id)
    logo = await template_clone._brand_logo(tenant_id) if spec.get("logo_box") else None

    png, _kind = render_spec(spec, content, hero_bytes=hero_bytes, logo_bytes=logo,
                             palette=palette)
    if not png:
        return None

    # The same design-QA gate the nine go through. A learned layout that fails
    # is recorded against the layout (three strikes retires it — never deletes
    # it) and this post falls back to the nine.
    if settings.design_qa_enabled:
        try:
            from . import render_reviewer
            _rev = await render_reviewer.review_post(png, mime="image/png")
        except Exception:  # noqa: BLE001 — QA must never break generation
            _rev = {"status": "failed"}
        if _rev.get("status") == "ok" and not _rev.get("passed"):
            await design_templates.mark_qa(tenant_id, tid, False)
            return None
        if _rev.get("status") == "ok":
            await design_templates.mark_qa(tenant_id, tid, True)

    tenant = str(tenant_id or settings.default_tenant_id)
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, png, "learned.png")
    try:
        await create_media(
            role="post_image", source_type="upload", uri=served_uri, file_path=file_path,
            title=(topic or "learned layout")[:120], platform="instagram", mime="image/png",
            tags=["designed", "learned_layout"], notes=headline[:300], tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001
        pass

    # Every other platform's shape: the same layout, copy and photo, re-laid-out.
    by_size: dict[str, str] = {}
    for (_w, _h) in extra_sizes:
        if not (_w > 0 and _h > 0):
            continue
        try:
            with image_compose.canvas(_w, _h):
                _png, _ = render_spec(spec, content, hero_bytes=hero_bytes,
                                      logo_bytes=logo, palette=palette)
            if not _png:
                continue
            _uri, _ = await asyncio.to_thread(
                media_storage().save, tenant, _png, f"learned-{_w}x{_h}.png")
            by_size[f"{_w}x{_h}"] = _uri
        except Exception:  # noqa: BLE001 — one shape must never cost the post
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "learned layout %s failed at %sx%s", tid, _w, _h, exc_info=True)

    await design_templates.mark_used(tenant_id, tid)
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            action_id,
            json.dumps({
                "image_url": served_uri, "media_url": served_uri, "has_image": True,
                "image_format": "learned",
                # Which library layout this is, and where it was learned from —
                # so approvals can teach the library, and provenance can say
                # "layout learned from @handle's post".
                "design_template_id": tid,
                "design_template_source": {
                    "kind": learned.get("source_kind"), "handle": learned.get("source_handle"),
                    "url": learned.get("source_url"), "platform": learned.get("source_platform"),
                },
                # The layout AND the words on it, so a redo can rebuild this card
                # in its own design (api_v1._rebuild_cloned_action) instead of
                # replacing it with an unrelated one.
                "clone_spec": spec,
                "clone_content": content,
                **({"hero_photo_key": hero_key} if hero_key else {}),
                **({"image_urls_by_size": by_size} if by_size else {}),
            }),
        )
    return served_uri, "learned"


async def _generate_designed_post_image(
    action_id, topic: str, draft_text: str, tenant_id, avoid: str = "",
    feedback: str = "", force_format: str = "", exclude_photos: tuple[str, ...] = (),
    force_photo: str = "", base_spec: dict | None = None, _qa_attempt: int = 0,
    extra_sizes: tuple[tuple[int, int], ...] = (),
) -> tuple[str, str]:
    """Art-director → text-free background (Soul James or cinematic scene) →
    Pillow-composited quote card / meme → persist + attach to the action.

    Returns (served_uri, format) — the format ("quote" | "meme" | "statement")
    is surfaced so batch callers can vary it across many posts. `avoid` is a
    soft variety hint forwarded to the art director.

    The last three are the regeneration path (v1_post_regenerate): `feedback` is
    the owner's own words about what was wrong, stated to the art director as a
    correction it must satisfy; `force_format` pins the layout outright; and
    `exclude_photos` drops the hero photo they just rejected from the running,
    so the redo cannot serve the same picture back. `force_photo` is the opposite:
    a styling-only redo (e.g. "make the text white") passes the rejected version's
    photo key to REUSE the exact same picture, so the redo IS the same image with
    only the requested restyle changed — not a new photo. It wins over
    exclude_photos, and falls back to a normal pick if that photo is gone."""
    import httpx

    from .brand_kit import get_brand_kit
    from .designed_render import ALL_FORMATS, PHOTO_FORMATS, render_designed
    from .hero_context import get_hero_photo_files
    from .imagegen import direct_designed_image, generate_post_image
    from .media import create_media
    from .media import storage as media_storage

    # ── A LEARNED LAYOUT, WHEN ASKED FOR ────────────────────────────────
    # "learned" is the rotation slot BM2 gives a share of its image orders: draw
    # this post from the brand's design-template library (layouts read from
    # reference and competitor posts) instead of the nine hand-built formats.
    #
    # It can only ADD variety, never cost a post. A library too thin to rotate,
    # a render that design QA rejects, or any failure at all falls through to the
    # nine exactly as if "learned" had never been asked for. Only a fresh post
    # takes this path — a redo edits the design it already has.
    if force_format == "learned" and not base_spec and not force_photo and _qa_attempt == 0:
        try:
            _learned = await _generate_learned_post_image(
                action_id, topic, draft_text, tenant_id, extra_sizes=extra_sizes,
                guidance="\n".join(x for x in ((feedback or "").strip(), (avoid or "").strip()) if x))
        except Exception:  # noqa: BLE001 — the nine are always the safety net
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "learned layout failed for %s — using the nine", action_id, exc_info=True)
            _learned = None
        if _learned:
            return _learned
        force_format = ""   # let the art director choose among the nine

    # Per-tenant design-intelligence switch: enables the 8-format brain + the
    # brand palette for THIS brand only, without touching the rest.
    try:
        from .brand_identity import get_design_intel_enabled
        _allow_v2 = await get_design_intel_enabled(tenant_id)
    except Exception:  # noqa: BLE001
        _allow_v2 = None
    # Per-brand template control (synced from the Brand Manager admin): the set of
    # designed formats this brand may produce. None = no restriction (every path,
    # autonomous or forced, then behaves exactly as before).
    try:
        from .brand_identity import get_enabled_formats
        _allowed = await get_enabled_formats(tenant_id)
    except Exception:  # noqa: BLE001
        _allowed = None
    # None = no restriction; an EMPTY set = the admin turned OFF every designed
    # template for this brand → don't produce a designed image at all (the caller
    # falls back to a plain post). Only an explicit empty set short-circuits.
    if _allowed is not None and not _allowed:
        return "", ""
    # Voice first: give the art director THIS brand's real cadence so the card
    # headline sounds like the brand, with the hook/CTA playbook as structure only.
    if base_spec:
        # EDIT the card that exists rather than authoring a new one. Without this
        # a redo re-ran the art director at temperature 0.7, so "keep everything
        # the same, just change X" came back with every line of on-image copy
        # rewritten — same layout, same photo, and still not the owner's card.
        from .imagegen import edit_designed_spec
        spec = await edit_designed_spec(base_spec, feedback)
    else:
        _voice, _bp = await _brand_voice_and_profile(tenant_id)
        spec = await direct_designed_image(
            draft_text or "", topic or "", avoid=avoid,
            feedback=feedback, force_format=force_format, allow_v2=_allow_v2,
            voice=_voice, brand_profile=_bp, allowed=_allowed,
        )
    fmt = spec.get("format") or "quote"
    # A carousel is a MULTI-image post — a wholly separate render/store path.
    # text_carousel is the photoless (typographic) variant of the same path.
    if fmt in ("carousel", "text_carousel"):
        return await _generate_carousel_post(action_id, topic, draft_text, tenant_id,
                                             text_only=(fmt == "text_carousel"))
    bg_prompt = (spec.get("bg_prompt") or topic or "cinematic golden-hour scene").strip()
    bg_kind = spec.get("bg_kind") or "scene"

    # Designed images use REAL photos or clean type only — NO AI-generated
    # scenes. hero_quote & statement place James's real uploaded photo; a random
    # one is picked for variety (and to fit different concepts across a batch).
    hero_bytes: bytes | None = None
    hero_key: str | None = None
    if fmt in PHOTO_FORMATS:
        try:
            # Full rotation pool (not the 3-ref AI cap) so the picker rotates
            # across the whole library — the fix for 'same photo every visual'.
            _refs = await get_hero_photo_files(tenant_id=tenant_id, limit=None)
            # Gated pick (James's rejections): skip blurry photos, prefer the
            # least-recently-used one instead of random choice.
            from .photo_pick import photo_key, pick_hero_bytes
            _picked = None
            # A styling-only redo REUSES the exact rejected photo (match by its
            # stored key — a URL name, else a content hash) so only the restyle
            # changes. Falls through to a normal pick if that photo is gone.
            if force_photo:
                for _name, _b in _refs:
                    _k = _name if _name.startswith("http") else photo_key(_b)
                    if _k == force_photo:
                        _picked = (_k, _b)
                        break
            if _picked is None:
                _picked = await pick_hero_bytes(_refs, tenant_id, exclude=exclude_photos)
            if _picked is None and exclude_photos:
                # The library has nothing else. Better an honest repeat than a
                # silent one: fall back to the full set, and the layout change
                # driven by `feedback` is what makes the redo differ.
                # This module has no logger of its own — a bare `logger` here
                # would NameError on the very path meant to recover, and the
                # enclosing `except` would swallow it into a blank image.
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "hero library exhausted by exclusion; reusing an existing photo"
                )
                _picked = await pick_hero_bytes(_refs, tenant_id)
            if _picked:
                hero_key, hero_bytes = _picked
        except Exception:  # noqa: BLE001
            hero_bytes = None

    bg_bytes: bytes | None = None
    soul = (settings.higgsfield_soul_id or "").strip()
    if fmt in ALL_FORMATS:
        pass                                  # real photo / type only; no AI gen
    elif bg_kind == "james" and soul:
        from . import higgsfield_souls as hs
        # Make sure James's FACE renders clearly and isn't cropped — these
        # cards lean on his likeness, so frame him face-forward.
        james_prompt = (
            bg_prompt
            + " Framing: medium head-and-shoulders to waist-up shot, James's "
            "full face clearly visible, sharp and well-lit, looking toward "
            "camera, not cropped at the top, centered."
        )[:1500]
        sub = await hs.generate_character_image(
            custom_reference_id=soul, prompt=james_prompt, aspect_ratio="4:5", strength=0.85,
        )
        rid = sub.get("request_id")
        url = ""
        if rid:
            for _ in range(45):
                p = await hs.poll_request(rid)
                if p.get("status") == "completed" and p.get("image_url"):
                    url = p["image_url"]
                    break
                if p.get("status") in ("failed", "nsfw", "canceled"):
                    break
                await asyncio.sleep(4)
        if url:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                bg_bytes = r.content
    if bg_bytes is None and fmt not in ALL_FORMATS:
        png, _meta, err = await generate_post_image(
            topic=bg_prompt + " — photoreal cinematic scene, absolutely no text, "
            "no words, no letters, no signs",
            platform="instagram", aspect="4:5", style="cinematic_real",
            tenant_id=tenant_id,
        )
        if not png:
            raise RuntimeError(err or "background generation failed")
        bg_bytes = png

    kit = await get_brand_kit(tenant_id)
    # Brand palette from the brand-identity engine. Merged into the kit so BOTH
    # the shipped cards and the v2 layouts render in the brand's OWN colours:
    # stored theme → derived from THIS brand's logo/photos (the hero already in
    # hand seeds it) → neutral floor. Never another brand's palette (D10).
    try:
        from .brand_identity import ensure_brand_palette
        _palette = await ensure_brand_palette(tenant_id, kit=kit, hint_image=hero_bytes)
        if _palette:
            kit["palette"] = _palette
    except Exception:  # noqa: BLE001 — a palette read must never stop a render
        pass
    handle = (kit.get("handle") or "").strip()
    # Profile mark next to the @handle: prefer the brand LOGO (PreReal emblem,
    # uploaded on the Brand page → brand_kit.logo_url); fall back to James's
    # hero photo only when no logo is configured.
    profile_bytes = None
    profile_is_logo = False
    logo_url = (kit.get("logo_url") or "").strip()
    if logo_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(logo_url)
                r.raise_for_status()
            # Validate it decodes as a raster image BEFORE committing — an SVG
            # (the brand page accepts svg), an HTML/redirect body, or an empty
            # response would otherwise crash the whole card render in Pillow.
            from io import BytesIO as _BIO

            from PIL import Image as _PILImage
            _PILImage.open(_BIO(r.content)).convert("RGBA")
            profile_bytes = r.content
            profile_is_logo = True
        except Exception:  # noqa: BLE001 — bad/non-raster logo → fall back to hero
            profile_bytes = None
            profile_is_logo = False
    if profile_bytes is None:
        try:
            # Rotate the profile mark too (LRU across the full library) instead of
            # always stamping refs[0] — the same face on every statement card.
            refs = await get_hero_photo_files(tenant_id=tenant_id, limit=None)
            if refs:
                from .photo_pick import pick_hero_bytes
                picked = await pick_hero_bytes(refs, tenant_id)
                profile_bytes = picked[1] if picked else refs[0][1]
        except Exception:  # noqa: BLE001
            profile_bytes = None

    # Live render knobs for this tenant — photo width, text gutter, type size
    # and crop point. Feedback like "he's hidden behind the text" is applied
    # here, on the next card, with no deploy. Never let a config read stop a
    # render: an empty dict is exactly the shipped defaults.
    try:
        from .render_tuning import get_render_tuning
        _tuning = await get_render_tuning(tenant_id)
    except Exception:  # noqa: BLE001
        _tuning = {}
    # The brand's typography theme (owner's font pick, synced from Brand Manager).
    # Resolves to None for the default house look — best-effort, never blocks a render.
    from . import image_compose
    try:
        from . import brand_identity as _bi, font_themes as _ftm
        _font_theme = _ftm.resolve(await _bi.get_brand_font(tenant_id))
    except Exception:  # noqa: BLE001
        _font_theme = None
    try:
        from . import brand_identity as _bil
        _look = await _bil.get_brand_look(tenant_id)
    except Exception:  # noqa: BLE001
        _look = None
    # Backfill the quote the shipped cards rely on, then route to the right
    # compositor (shipped OR v2), threading the brand palette. render_designed
    # returns the format it ACTUALLY rendered — a photo layout with no photo
    # falls back to a text card — so the stamp below reflects reality.
    if not (spec.get("quote") or "").strip():
        spec["quote"] = ((draft_text or topic or "").split(". ")[0]).strip()
    # On-image text styling from the render knobs — "make the text white / thicker"
    # is applied here on the NEXT render (no deploy), across every layout.
    def _knob_int(key: str) -> int:
        try:
            return int(round(float(_tuning.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0
    # A free-form colour ("red", "#1b4d3e") wins over the numeric white/black enum.
    _hexcol = str(_tuning.get("image_text_color_hex") or "").strip()
    _tc = _knob_int("image_text_color")
    _text_color = _hexcol or ("white" if _tc == 1 else "black" if _tc == 2 else "")
    _text_bold = _knob_int("image_text_weight") >= 1
    with image_compose.brand_fonts(_font_theme), image_compose.brand_look(_look), \
            image_compose.text_style(_text_color, _text_bold):
        out, fmt = render_designed(
            fmt, spec, kit=kit, hero_bytes=hero_bytes,
            profile_bytes=profile_bytes, profile_is_logo=profile_is_logo,
            handle=handle, tuning=_tuning, palette=kit.get("palette"),
        )

    # ── DESIGN QA GATE ──────────────────────────────────────────────────
    # A pro-designer reviewer checks the rendered bytes for execution flaws
    # (clipped/overlapping/illegible text, empty shapes, text over a face, broken
    # logo) BEFORE we save or attach anything. On a fail we retry a DIFFERENT
    # layout, feeding the critique back to the art director; if it still won't
    # pass we hold it back (return "" — the same contract callers already handle,
    # degrading to a plain photo or an imageless held post) rather than ship a
    # mistake. Fail-OPEN if the reviewer itself is unavailable. Video/reels never
    # reach this function, so they are unaffected.
    if settings.design_qa_enabled and out:
        try:
            from . import render_reviewer
            _rev = await render_reviewer.review_post(out, mime="image/png")
        except Exception:  # noqa: BLE001 — QA must never break generation
            _rev = {"status": "failed"}
        if _rev.get("status") == "ok" and not _rev.get("passed"):
            _issues = "; ".join(_rev.get("issues") or [])[:400]
            if _qa_attempt < int(settings.design_qa_max_retries or 0):
                # Retry: avoid the layout that failed, hand the art director the
                # critique, and drop this photo (unless a specific photo was
                # pinned for a restyle). force_format is cleared so a cleaner
                # layout can be chosen.
                _next_exclude = exclude_photos if force_photo else tuple(
                    set(exclude_photos) | ({hero_key} if hero_key else set()))
                return await _generate_designed_post_image(
                    action_id, topic, draft_text, tenant_id,
                    avoid=(f"{avoid} {fmt}").strip(),
                    feedback=(f"{feedback}; {_issues}").strip("; "),
                    force_format="", exclude_photos=_next_exclude,
                    force_photo=force_photo,
                    # A QA retry deliberately drops the base spec: the design QA
                    # said this composition is broken, so re-composing is the
                    # point. Editing the broken spec again would return it.
                    base_spec=None, _qa_attempt=_qa_attempt + 1,
                    extra_sizes=extra_sizes,
                )
            # Exhausted retries — never ship the flaw. Record why, hold it back.
            try:
                async with acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                        action_id, json.dumps({
                            "design_qa_failed": True,
                            "design_qa_flaws": _rev.get("critical_flaws") or [],
                            "design_qa_issues": _rev.get("issues") or [],
                        }))
            except Exception:  # noqa: BLE001
                pass
            return "", ""

    tenant = str(tenant_id or settings.default_tenant_id)
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, out, f"designed-{fmt}.png"
    )
    try:
        await create_media(
            role="post_image", source_type="upload", uri=served_uri, file_path=file_path,
            title=(topic or fmt)[:120], platform="instagram", mime="image/png",
            tags=[f"style:designed_{fmt}", "designed"],
            notes=(spec.get("quote") or spec.get("top_text") or "")[:300], tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001
        pass
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            action_id,
            json.dumps({
                "image_url": served_uri, "media_url": served_uri,
                "has_image": True,
                # Which card layout was rendered. The three layouts have
                # different geometry, so the live render knobs only apply to the
                # one they were measured against — without this stamp a
                # rejection could set a knob that layout never reads, and the
                # owner would be told it was fixed while nothing changed.
                "image_format": fmt,
                # The spec that produced this card, kept so a later redo can EDIT
                # it — change the one thing asked and leave every other line
                # byte-identical — instead of composing a new card from scratch.
                "image_spec": spec,
                # Reuse memory: which hero photo this post consumed, so the
                # picker can rotate away from it on the next posts.
                **({"hero_photo_key": hero_key} if hero_key else {}),
            }),
        )

    # ── ONE DESIGN, EVERY PLATFORM'S SHAPE ──────────────────────────────
    # The same post has to go out on Instagram at 4:5, X at 16:9 and TikTok at
    # 9:16. Calling generate once per size does NOT do that: each call runs the
    # art director again and rotates to a different hero photo, so four sizes
    # came back as four different posts.
    #
    # So the design is composed ONCE (above: spec, photo, copy, QA) and then
    # re-laid-out at each extra canvas from that exact same spec and those exact
    # same photo bytes. No second art-director call, no second photo pick, no
    # model call at all — only compositing — so it is cheap and it is the same
    # post. The canvas ContextVar (image_compose.canvas) is what every compositor
    # reads, so nesting it here is all that changes between sizes.
    #
    # Best-effort per size: a shape that fails to render is simply absent from
    # the map, and the caller falls back to the primary image for that network.
    if extra_sizes and out:
        by_size: dict[str, str] = {}
        for (_w, _h) in extra_sizes:
            if not (_w > 0 and _h > 0):
                continue
            try:
                with image_compose.canvas(_w, _h), \
                        image_compose.brand_fonts(_font_theme), \
                        image_compose.brand_look(_look), \
                        image_compose.text_style(_text_color, _text_bold):
                    _out, _ = render_designed(
                        fmt, spec, kit=kit, hero_bytes=hero_bytes,
                        profile_bytes=profile_bytes, profile_is_logo=profile_is_logo,
                        handle=handle, tuning=_tuning, palette=kit.get("palette"),
                    )
                if not _out:
                    continue
                _uri, _ = await asyncio.to_thread(
                    media_storage().save, tenant, _out, f"designed-{fmt}-{_w}x{_h}.png"
                )
                by_size[f"{_w}x{_h}"] = _uri
            except Exception:  # noqa: BLE001 — one shape must never cost the post
                # main.py has no module-level logger; an undefined name here would
                # raise INSIDE this handler and take the whole post down with it.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "could not render %s at %sx%s", action_id, _w, _h, exc_info=True)
        if by_size:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                    action_id, json.dumps({"image_urls_by_size": by_size}),
                )
    return served_uri, fmt


@app.post("/post/designed-image", status_code=202)
async def post_designed_image_start(req: SoulImageRequest, background: BackgroundTasks) -> dict:
    """Render a striking multi-format DESIGNED image (quote card / meme) and
    attach it to a queued post. Backgrounded — poll GET /post/designed-image/{id}."""
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    tid = tid or settings.default_tenant_id
    job_id = str(uuid4())
    _DESIGNED_JOBS[job_id] = {"status": "running", "image_url": None, "error": None}
    while len(_DESIGNED_JOBS) > 30:
        _DESIGNED_JOBS.pop(next(iter(_DESIGNED_JOBS)))

    async def _run() -> None:
        try:
            url, _fmt = await _generate_designed_post_image(
                req.action_id, req.topic, req.draft_text, tid,
            )
            _DESIGNED_JOBS[job_id] = {
                "status": "done", "image_url": url,
                "error": None if url else "render produced no image",
            }
        except Exception as e:  # noqa: BLE001
            _DESIGNED_JOBS[job_id] = {"status": "failed", "image_url": None, "error": str(e)}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@app.get("/post/designed-image/{job_id}")
async def post_designed_image_get(job_id: str) -> dict:
    j = _DESIGNED_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found (expired or unknown)")
    return {"job_id": job_id, **j}


# Batch post creation (text + image per topic) — powers per-card "Create post"
# (one topic) and "Create all kept" (many). Backgrounded: text + Soul renders
# are slow, so kick it and let it fill the Approval Queue.
_CREATE_BATCHES: dict[str, dict] = {}


async def _create_one_post(
    topic: str, pillar: str, platform: str, image_mode: str,
    image_url: str, soul_id: str, tenant_id, avoid: str = "",
) -> dict:
    """Generate a post for one topic and attach an image per image_mode.
    Returns {topic, action_id, image_url, format, error}. Image is additive.
    `avoid` is a soft variety hint forwarded to the designed art director."""
    out: dict = {
        "topic": topic, "action_id": None, "image_url": None,
        "format": None, "error": None,
    }
    try:
        draft = await generate_content(
            ContentBrief(
                platform=platform or "instagram", format="post",
                pillar=pillar or "", topic=topic,
            ),
            tenant_id,
        )
    except Exception as e:  # noqa: BLE001
        out["error"] = f"text generation failed: {e}"
        return out
    out["action_id"] = str(draft.action_id) if draft.action_id else None
    if draft.action_id is None:
        out["error"] = "post was not queued"
        return out
    try:
        if image_mode == "photo" and image_url:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                    draft.action_id,
                    json.dumps({
                        "image_url": image_url, "media_url": image_url,
                        "has_image": True,
                    }),
                )
            out["image_url"] = image_url
        elif image_mode == "soul" and soul_id:
            out["image_url"] = await _generate_soul_post_image(
                draft.action_id, topic, draft.draft or topic, "4:5", soul_id,
                tenant_id,
            )
        elif image_mode == "designed":
            url, fmt = await _generate_designed_post_image(
                draft.action_id, topic, draft.draft or topic, tenant_id, avoid=avoid,
            )
            out["image_url"] = url
            out["format"] = fmt
    except Exception as e:  # noqa: BLE001 — text already queued; image is additive
        out["error"] = f"image failed: {e}"
    return out


@app.post("/post/create-batch", status_code=202)
async def post_create_batch(req: CreateBatchRequest, background: BackgroundTasks) -> dict:
    """Create posts (text + image) for one or more topics in the background.
    Returns a job_id; poll GET /post/create-batch/{job_id}."""
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    tid = tid or settings.default_tenant_id

    topics = [t for t in (req.topics or []) if (t.topic or "").strip()]
    if not topics:
        raise HTTPException(status_code=400, detail="no topics to create")
    image_mode = req.image_mode if req.image_mode in ("photo", "soul", "designed", "none") else "photo"
    soul_id = (settings.higgsfield_soul_id or "").strip()
    if image_mode == "soul" and not soul_id:
        raise HTTPException(
            status_code=400,
            detail="No Higgsfield Soul ID configured. Train one on the Hero page first.",
        )

    job_id = str(uuid4())
    job = {"status": "running", "total": len(topics), "done": 0, "results": []}
    _CREATE_BATCHES[job_id] = job
    # Prune only FINISHED jobs — never evict one that's still running (its
    # background task holds a local ref to `job`, so eviction can't KeyError it,
    # but we still must not drop a live job the client is polling).
    finished = [k for k, v in _CREATE_BATCHES.items() if v.get("status") != "running"]
    while len(_CREATE_BATCHES) > 20 and finished:
        _CREATE_BATCHES.pop(finished.pop(0), None)

    async def _run() -> None:
        sem = asyncio.Semaphore(2)
        # Fetch hero photos here (not in the handler) so the 202 returns fast —
        # get_hero_context can fire a cold-cache OpenAI vision call. No specific
        # photo in photo mode → rotate the library across the batch.
        hero_urls: list[str] = []
        if image_mode == "photo" and not (req.image_url or "").strip():
            try:
                from .hero_context import get_hero_context
                ctx = await get_hero_context(tid)
                hero_urls = list(ctx.photo_urls) if ctx else []
            except Exception:  # noqa: BLE001
                hero_urls = []

        # Designed mode: keep a running tally of which card formats have been
        # used so the art director can vary them across the batch (anti-repeat).
        format_counts: dict[str, int] = {}

        def _avoid_fmt() -> str:
            if not format_counts:
                return ""
            top = max(format_counts, key=lambda k: format_counts[k])
            # Only nudge once a format starts to dominate, so a 2-post batch
            # isn't forced into an awkward format.
            return top if format_counts[top] >= 2 else ""

        async def _one(idx: int, t) -> None:
            async with sem:
                img = (req.image_url or "").strip()
                if image_mode == "photo" and not img and hero_urls:
                    img = hero_urls[idx % len(hero_urls)]
                avoid = _avoid_fmt() if image_mode == "designed" else ""
                r = await _create_one_post(
                    (t.topic or "").strip(), t.pillar, req.platform,
                    image_mode, img, soul_id, tid, avoid,
                )
                f = r.get("format")
                if f:
                    format_counts[f] = format_counts.get(f, 0) + 1
                # Mutate the captured `job` ref, not _CREATE_BATCHES[job_id] —
                # safe even if the entry was evicted from the global dict.
                job["results"].append(r)
                job["done"] += 1

        try:
            await asyncio.gather(
                *[_one(i, t) for i, t in enumerate(topics)],
                return_exceptions=True,
            )
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(e)

    background.add_task(_run)
    return {"job_id": job_id, "status": "running", "total": len(topics)}


@app.get("/post/create-batch/{job_id}")
async def post_create_batch_get(job_id: str) -> dict:
    j = _CREATE_BATCHES.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found (expired or unknown)")
    return {"job_id": job_id, **j}


# Backfill: give every queued post that's missing an image one. Designed cards
# preferred; a rotated hero photo is the fallback so NO post is left imageless.
# Re-runnable (skips posts that already have an image), so it doubles as a retry
# for any render that failed.
_BACKFILL_JOBS: dict[str, dict] = {}
# Action ids currently being rendered by ANY backfill run in this process — a
# cheap in-process guard so two overlapping runs (second tab, or a backfill
# overlapping a single-post designed render) don't both pay for the same image.
_BACKFILL_INFLIGHT: set = set()


@app.post("/post/backfill-images", status_code=202)
async def post_backfill_images(
    req: BackfillImagesRequest, background: BackgroundTasks,
) -> dict:
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    tid = tid or settings.default_tenant_id

    limit = max(1, min(int(req.limit or 50), 100))
    mode = req.mode if req.mode in ("designed", "photo") else "designed"
    async with acquire(tid) as conn:
        rows = await conn.fetch(
            """SELECT id, payload->>'topic' AS topic, payload->>'content' AS content
                 FROM actions
                WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
                  AND action_type = 'content' AND status = 'pending'
                  AND coalesce(payload->>'image_url', '') = ''
                ORDER BY created_at DESC
                LIMIT $1""",
            limit,
        )
    targets = [(r["id"], r["topic"] or "", r["content"] or "") for r in rows]

    job_id = str(uuid4())
    job = {
        "status": "running", "total": len(targets), "done": 0,
        "generated": 0, "failed": 0, "skipped": 0,
    }
    _BACKFILL_JOBS[job_id] = job
    finished = [k for k, v in _BACKFILL_JOBS.items() if v.get("status") != "running"]
    while len(_BACKFILL_JOBS) > 20 and finished:
        _BACKFILL_JOBS.pop(finished.pop(0), None)

    if not targets:
        job["status"] = "done"
        return {"job_id": job_id, "status": "done", "total": 0}

    soul_id = (settings.higgsfield_soul_id or "").strip()  # noqa: F841 (read in render)

    async def _attach_photo(action_id, url: str) -> None:
        async with acquire(tid) as conn:
            await conn.execute(
                "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
                action_id,
                json.dumps({"image_url": url, "media_url": url, "has_image": True}),
            )

    async def _run() -> None:
        sem = asyncio.Semaphore(2)
        hero_urls: list[str] = []
        try:
            from .hero_context import get_hero_context
            ctx = await get_hero_context(tid)
            hero_urls = list(ctx.photo_urls) if ctx else []
        except Exception:  # noqa: BLE001
            hero_urls = []

        counts: dict[str, int] = {}

        def _avoid_fmt() -> str:
            if not counts:
                return ""
            top = max(counts, key=lambda k: counts[k])
            return top if counts[top] >= 2 else ""

        async def _one(idx: int, action_id, topic: str, content: str) -> None:
            async with sem:
                # Skip if it already has an image (filled since our query) or is
                # being rendered by an overlapping run in this process.
                async with acquire(tid) as conn:
                    existing = await conn.fetchval(
                        "SELECT coalesce(payload->>'image_url','') FROM actions WHERE id = $1",
                        action_id,
                    )
                if existing or action_id in _BACKFILL_INFLIGHT:
                    job["skipped"] += 1
                    job["done"] += 1
                    return
                _BACKFILL_INFLIGHT.add(action_id)
                ok = False
                try:
                    # Designed when asked — and also as the only option when there
                    # are no hero photos to fall back to (so 'photo' mode on a
                    # tenant with no http hero URLs still produces an image).
                    if mode == "designed" or not hero_urls:
                        try:
                            url, fmt = await _generate_designed_post_image(
                                action_id, topic, content or topic, tid, avoid=_avoid_fmt(),
                            )
                            if url:
                                ok = True
                                if fmt:
                                    counts[fmt] = counts.get(fmt, 0) + 1
                        except Exception:  # noqa: BLE001 — fall back to a photo below
                            ok = False
                    if not ok and hero_urls:
                        # Photo mode, or designed failed → never leave it imageless.
                        await _attach_photo(action_id, hero_urls[idx % len(hero_urls)])
                        ok = True
                finally:
                    _BACKFILL_INFLIGHT.discard(action_id)
                job["generated" if ok else "failed"] += 1
                job["done"] += 1

        try:
            await asyncio.gather(
                *[_one(i, a, t, c) for i, (a, t, c) in enumerate(targets)],
                return_exceptions=True,
            )
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(e)

    background.add_task(_run)
    return {"job_id": job_id, "status": "running", "total": len(targets)}


@app.get("/post/backfill-images/{job_id}")
async def post_backfill_images_get(job_id: str) -> dict:
    j = _BACKFILL_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found (expired or unknown)")
    return {"job_id": job_id, **j}


# Suggested-topics jobs run in the background — _gather_intel (Xpoz creator
# search + niche + research) is 30-60s, which blows the gateway's synchronous
# request timeout (→ 500). Same background-job + poll pattern as the video
# script batch above.
_TOPIC_BATCHES: dict[str, dict] = {}


@app.post("/post/ideas", status_code=202)
async def post_ideas_start(background: BackgroundTasks, n: int = 10) -> dict:
    """Kick a background job that ideates N data-steered post topics — the same
    ideation as the video 'Generate 10 scripts' (tracked creators + niche
    trends + research, grounded in James's real topics, pillar-quota'd), topics
    only. Returns a batch_id; poll GET /post/ideas/{batch_id} for the result."""
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    tid = tid or settings.default_tenant_id
    n = max(1, min(int(n or 10), 30))
    batch_id = str(uuid4())
    _TOPIC_BATCHES[batch_id] = {"status": "running", "ideas": [], "error": None}
    while len(_TOPIC_BATCHES) > 30:
        _TOPIC_BATCHES.pop(next(iter(_TOPIC_BATCHES)))

    async def _run() -> None:
        try:
            from .topic_suggestions import save_batch
            from .video_compose import suggest_topics
            res = await suggest_topics(n=n, tenant_id=tid)
            ideas = res.get("ideas", [])
            # Persist so we don't re-ideate on the next page load. Keeps any
            # accepted suggestions pinned; returns the full saved list (with
            # ids + status) so the UI can render keep/reject immediately.
            saved = await save_batch(tid, ideas) if ideas else []
            _TOPIC_BATCHES[batch_id] = {
                "status": "done", "ideas": saved or ideas,
                "count": len(saved) if saved else res.get("count", 0),
                "niche": res.get("niche", ""), "error": res.get("error"),
            }
        except Exception as e:  # noqa: BLE001
            _TOPIC_BATCHES[batch_id] = {
                "status": "failed", "ideas": [], "error": str(e),
            }

    background.add_task(_run)
    return {"batch_id": batch_id, "status": "running"}


# These STATIC subpaths must be declared before /post/ideas/{batch_id} so the
# router doesn't capture "saved"/"status" as a batch_id.
@app.get("/post/ideas/saved")
async def post_ideas_saved() -> dict:
    """The persisted suggestion list — shown on load so we don't regenerate."""
    from .db import _request_tenant
    from .topic_suggestions import get_saved
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    return {"ideas": await get_saved(tid or settings.default_tenant_id)}


@app.post("/post/ideas/status")
async def post_ideas_status(req: IdeaStatusRequest) -> dict:
    """Keep (accepted), drop (rejected), or reset (pending) one suggestion."""
    from .db import _request_tenant
    from .topic_suggestions import set_status
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    items = await set_status(tid or settings.default_tenant_id, req.id, req.status)
    return {"ideas": items}


@app.get("/post/ideas/{batch_id}")
async def post_ideas_get(batch_id: str) -> dict:
    b = _TOPIC_BATCHES.get(batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch not found (expired or unknown)")
    return {"batch_id": batch_id, **b}


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


# ── Trend-driven script batch: "generate N scripts to pick from" ──
# Background job (the trend pull + N script gens exceed the request timeout).
# In-memory, keyed by batch_id — a short-lived interactive flow (generate →
# pick within minutes); pruned to the most recent few dozen.
_SCRIPT_BATCHES: dict[str, dict] = {}


class ScriptBatchRequest(BaseModel):
    n: int = 10
    platform: str = "instagram"
    aspect: str = "9:16"


@app.post("/video/scripts/batch", status_code=202)
async def video_scripts_batch(req: ScriptBatchRequest, background: BackgroundTasks) -> dict:
    """Kick a background batch that pulls trend data (tracked creators via
    Xpoz + niche + research — NO topic input) and drafts N ready topic+script
    options to pick from. Returns a batch_id; poll GET for the results."""
    from uuid import uuid4

    from .db import _request_tenant
    try:
        tid = _request_tenant.get()
    except LookupError:
        tid = None
    n = max(1, min(int(req.n or 10), 10))
    plat, asp = (req.platform or "instagram"), (req.aspect or "9:16")
    batch_id = str(uuid4())
    _SCRIPT_BATCHES[batch_id] = {"status": "running", "scripts": [], "error": None}
    # Prune so the dict can't grow unbounded across a long-lived process.
    while len(_SCRIPT_BATCHES) > 30:
        _SCRIPT_BATCHES.pop(next(iter(_SCRIPT_BATCHES)))

    async def _run() -> None:
        try:
            from .video_compose import compose_video_batch
            res = await compose_video_batch(n, plat, asp, tid)
            _SCRIPT_BATCHES[batch_id] = {
                "status": "done", "scripts": res.get("scripts", []),
                "count": res.get("count", 0), "niche": res.get("niche", ""),
                "error": res.get("error"),
            }
        except Exception as e:  # noqa: BLE001
            _SCRIPT_BATCHES[batch_id] = {
                "status": "failed", "scripts": [], "error": str(e),
            }

    background.add_task(_run)
    return {"batch_id": batch_id, "status": "running"}


@app.get("/video/scripts/batch/{batch_id}")
async def video_scripts_batch_get(batch_id: str) -> dict:
    b = _SCRIPT_BATCHES.get(batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch not found (expired or unknown)")
    return {"batch_id": batch_id, **b}


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
            broll_pacing=req.broll_pacing,
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


@app.post("/video/productions/{production_id}/cancel")
async def video_cancel(production_id: UUID) -> dict:
    """Cancel an in-flight render. Marks it 'canceled'; the worker stops at its
    next stage checkpoint (before the next paid provider call). Renders that
    already finished are left untouched (ok=False with their real status)."""
    from .video_pipeline import cancel_production
    res = await cancel_production(production_id)
    if res.get("status") is None:
        raise HTTPException(status_code=404, detail="production not found")
    return res


@app.post("/video/productions/{production_id}/trim")
async def video_trim(production_id: UUID, req: VideoTrimRequest) -> dict:
    """Trim a finished render to [start_s, end_s] and re-host it, pointing the
    production + its queued item at the trimmed video. Cuts excess head/tail
    footage. end_s<=0 means 'to the end'."""
    from .video_pipeline import trim_production
    res = await trim_production(production_id, req.start_s, req.end_s)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "trim failed")
    return res


@app.delete("/video/productions/{production_id}")
async def video_delete(production_id: UUID) -> dict:
    """Hard-delete a finished video production from the Output Library.
    Removes the catalog row (the rendered file lives on the provider CDN)."""
    from .video_pipeline import delete_production
    ok = await delete_production(production_id)
    if not ok:
        raise HTTPException(status_code=404, detail="production not found")
    return {"ok": True, "id": str(production_id)}


# ─────────────────────────────────── Speaker directory ──

@app.get("/speakers")
async def speakers_list() -> list[dict]:
    """The saved speaker directory (@handle + subtitle) for on-screen name-tags."""
    from .speakers import list_speakers
    return await list_speakers()


@app.post("/speakers", status_code=201)
async def speakers_create(req: SpeakerCreate) -> dict:
    """Add a speaker to the directory (e.g. @j_prendamano · CEO at PreReal Estate)."""
    from .speakers import create_speaker
    try:
        return await create_speaker(req.handle, req.subtitle, req.face_ref)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/speakers/{speaker_id}")
async def speakers_update(speaker_id: UUID, req: SpeakerUpdate) -> dict:
    from .speakers import update_speaker
    out = await update_speaker(
        speaker_id, handle=req.handle, subtitle=req.subtitle, face_ref=req.face_ref,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="speaker not found (or no fields)")
    return out


@app.delete("/speakers/{speaker_id}")
async def speakers_delete(speaker_id: UUID) -> dict:
    from .speakers import delete_speaker
    ok = await delete_speaker(speaker_id)
    if not ok:
        raise HTTPException(status_code=404, detail="speaker not found")
    return {"ok": True, "id": str(speaker_id)}


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


@app.post("/long-form/youtube-import", status_code=201)
async def long_form_youtube_import(
    background: BackgroundTasks,
    youtube_url: str = Form(...),
    title: str = Form(""),
) -> dict:
    """Pull a long video from a YouTube URL straight into the cutter. Resolves +
    downloads the video via Apify (residential proxy — reliable where a server-side
    yt-dlp is IP-blocked), then the same transcribe → reel-candidate pipeline as
    upload/Drive. Returns instantly; the download runs in the background (poll GET
    /long-form/{id} for status)."""
    from .long_form import (
        create_source_placeholder, fetch_from_youtube_then_ingest, is_youtube_url,
    )

    url = (youtube_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="youtube_url is required")
    if not is_youtube_url(url):
        raise HTTPException(
            status_code=400,
            detail="not a recognisable YouTube URL (watch, shorts, live, or youtu.be)",
        )
    src = await create_source_placeholder(title=(title or "YouTube import"))
    background.add_task(fetch_from_youtube_then_ingest, UUID(src["id"]), url)
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


@app.get("/content-library/data")
async def content_library_get() -> dict:
    """Unified Content Library: all footage + the clippable topic-reels inside
    each, with live clip status (clippable / clipping / clipped).
    Served at /content-library/data — the bare path is the Next.js PAGE, and
    the browser proxy resolves pages before rewrites (same pattern as
    /long-form/sources)."""
    from .long_form import content_library
    return await content_library()


@app.post("/content-library/topics/refresh", status_code=202)
async def content_library_topics_refresh() -> dict:
    """Re-mine topic suggestions across ALL footage in the background. The
    library page's normal 6s poll picks the fresh list up when it lands;
    `topics_mining` in /content-library/data flips while a pass runs."""
    from .long_form import refresh_topics_detached
    return {"started": refresh_topics_detached()}


@app.post("/content-library/topics/{topic_id}/build", status_code=202)
async def content_library_topic_build(topic_id: UUID) -> dict:
    """Click a topic → the clipper builds it (cuts every segment, stitches,
    runs the full engaging treatment, lands in the approval queue)."""
    from .long_form import build_topic
    res = await build_topic(topic_id)
    if not res:
        raise HTTPException(status_code=404, detail="topic not found or empty")
    return res


@app.post("/content-library/topics/{topic_id}/dismiss")
async def content_library_topic_dismiss(topic_id: UUID) -> dict:
    from .long_form import dismiss_topic
    if not await dismiss_topic(topic_id):
        raise HTTPException(status_code=404, detail="topic not found")
    return {"ok": True}


@app.post("/long-form/{source_id}/auto-clip", status_code=202)
async def long_form_auto_clip(source_id: UUID, top_n: int = 0) -> dict:
    """Kick the auto-clipper on a source NOW: render its top scored candidates
    into reels (they land in the approval queue). `top_n`>0 overrides the config
    default. Returns how many renders were started."""
    from .long_form import auto_clip_source
    kicked = await auto_clip_source(source_id, top_n=top_n or None)
    return {"source_id": str(source_id), "clips_started": kicked}


@app.post("/long-form/{source_id}/detect-speakers", status_code=200)
async def long_form_detect_speakers(source_id: UUID) -> dict:
    """Enumerate the distinct on-camera speakers in a source (with a face-crop
    preview + horizontal position each) for the 'who is this?' step."""
    from .long_form import detect_speakers_for_source
    speakers = await detect_speakers_for_source(source_id)
    return {"source_id": str(source_id), "speakers": speakers}


@app.get("/long-form/{source_id}/speaker-tags")
async def long_form_get_speaker_tags(source_id: UUID) -> dict:
    """The saved speaker assignment for this source (name-tags)."""
    from .long_form import get_source
    src = await get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    return {"source_id": str(source_id), "speaker_tags": src.get("speaker_tags") or []}


@app.put("/long-form/{source_id}/speaker-tags")
async def long_form_set_speaker_tags(source_id: UUID, req: SpeakerTagsRequest) -> dict:
    """Save who-is-who for this source; applies to every reel cut from it."""
    from .long_form import set_speaker_tags
    tags = [t.model_dump() for t in req.tags]
    ok = await set_speaker_tags(source_id, tags)
    if not ok:
        raise HTTPException(status_code=404, detail="source not found")
    return {"source_id": str(source_id), "speaker_tags": tags}


@app.post("/long-form/candidates/{candidate_id}/render", status_code=201)
async def long_form_candidate_render(
    candidate_id: UUID, background: BackgroundTasks,
    platform: str = Form("instagram"), aspect: str = Form("9:16"),
    image_style: str = Form(""), caption_style: str = Form(""),
    video_engine: str = Form(""), broll_pacing: str = Form(""),
    broll_style: str = Form(""),
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
            broll_pacing=broll_pacing,
            broll_style=broll_style,
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
    video_engine: str = Form(""), broll_pacing: str = Form(""),
    broll_style: str = Form(""),
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
            broll_pacing=broll_pacing,
            broll_style=broll_style,
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
    from .hero_context import get_hero_photo_files
    from .imagegen import generate_post_image_with_refs
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
    _img_tid = _img_tid or settings.default_tenant_id
    # Baseline every generated post image on the brand hero's uploaded photos
    # so the SAME person (James) shows up consistently. With no hero photos
    # uploaded, generate_post_image_with_refs transparently falls back to the
    # no-reference generate path.
    hero_refs = await get_hero_photo_files(tenant_id=_img_tid)
    png, meta, err = await generate_post_image_with_refs(
        topic=topic,
        references=hero_refs,
        platform=req.platform.strip() or "linkedin",
        brief=req.brief,
        aspect=req.aspect,
        style=req.style,
        tenant_id=_img_tid,
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
    # B-roll and hero assets get a description of WHAT THEY SHOW, written at
    # upload time. The reel placer matches a spoken moment against that text, so
    # an asset without one can never be cut in — describing on arrival is what
    # keeps the library matchable without anyone remembering to run a batch.
    if asset.get("role") in ("broll", "hero_photo", "hero_video"):
        from .reel_vision import describe_media_asset
        await describe_media_asset(media_id, tenant)
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


@app.post("/media/store", status_code=201)
async def media_store(file: UploadFile = File(...)) -> dict:
    """Persist an uploaded file to durable storage and hand back its public URL —
    nothing else.

    Unlike /media/upload this makes NO media_assets row, runs NO perception
    analysis, and does NOT content-dedup. It is the raw hosting seam for a caller
    that manages the asset itself (BM2.0's "schedule your own post": the owner
    uploads a photo/video for a post they compose by hand). The bytes land in the
    same Supabase bucket every generated image publishes from, so the returned URL
    is a stable, public link the social aggregator can fetch at post time. Because
    there's no dedup, re-using the same image across posts just works."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    # Cap the size so a runaway upload can't exhaust memory/storage (BM2.0 caps at
    # 100 MB too; this is the server-side backstop).
    _MAX_STORE_BYTES = 120 * 1024 * 1024
    if len(data) > _MAX_STORE_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 120 MB)")
    # Store under the REQUESTING brand's tenant (set from X-Tenant-Id by the auth
    # middleware) so each brand's hand-uploaded media stays in its own prefix —
    # matching how the rest of this API scopes storage.
    from .db import _request_tenant
    tenant = str(_request_tenant.get() or settings.default_tenant_id)
    # to_thread: the Supabase storage client is sync HTTP — a big upload pushed
    # inline would freeze the server for its duration (matches /media/upload).
    served_uri, file_path = await asyncio.to_thread(
        media_storage().save, tenant, data, file.filename or "upload.bin"
    )
    return {
        "url": served_uri,
        "path": file_path,
        "filename": file.filename or "",
        "content_type": file.content_type or "",
        "bytes": len(data),
    }


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
        source_type=req.source_type,
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
