"""FastAPI app — the public surface of JAMES OS."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ask import ask
from .config import settings
from .dashboard_api import router as dashboard_api_router
from .db import acquire, close_pool, init_pool
from .documents import document_to_events_async
from .ingestion import ingest, ingest_many, supersede_prior_document_versions
from .models import (
    AskRequest,
    AskResponse,
    Event,
    EventCreate,
    ContentBrief,
    ContentDraft,
    PlugIn,
    PlugInCreate,
    ResearchRequest,
    ResearchResponse,
    ResearchSourceOut,
    ScriptRequest,
    TrendDiscoverRequest,
    VideoGenerateRequest,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    # Overlay any tenant-saved API keys from the DB onto the .env baseline
    # so UI-entered credentials are live from the first request.
    from .credentials import load_into_settings

    await load_into_settings()
    yield
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
    creators = [{"platform": c.platform, "handle": c.handle} for c in req.creators]
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
