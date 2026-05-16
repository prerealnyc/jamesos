"""FastAPI app — the public surface of JAMES OS."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from .ask import ask
from .db import acquire, close_pool, init_pool
from .ingestion import ingest
from .models import (
    AskRequest,
    AskResponse,
    Event,
    EventCreate,
    PlugIn,
    PlugInCreate,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(
    title="JAMES OS",
    description="Memory substrate for AI-native operations.",
    version="0.1.0",
    lifespan=lifespan,
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


# ──────────────────────────────────────────────────────────────────── ask ──

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    return await ask(req)


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
