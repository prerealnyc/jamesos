"""Ingestion service.

Takes EventCreate payloads, embeds them, writes to the events table.
Idempotent on source.dedupe_key — re-ingesting the same content returns the
existing event id rather than creating a duplicate.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from .db import acquire
from .embedder import get_embedder
from .models import Event, EventCreate


async def ingest(event: EventCreate, tenant_id: UUID | None = None) -> Event:
    """Persist a single event. Embeds raw_content if present."""
    return (await ingest_many([event], tenant_id=tenant_id))[0]


async def ingest_many(
    events: list[EventCreate], tenant_id: UUID | None = None
) -> list[Event]:
    """Persist a batch of events with one round-trip to the embedder."""
    if not events:
        return []

    embedder = get_embedder()
    texts_to_embed: list[str] = []
    text_indexes: list[int] = []
    for i, ev in enumerate(events):
        if ev.raw_content:
            texts_to_embed.append(ev.raw_content)
            text_indexes.append(i)

    embeddings: dict[int, list[float]] = {}
    if texts_to_embed:
        vectors = await embedder.embed(texts_to_embed)
        for idx, vec in zip(text_indexes, vectors, strict=True):
            embeddings[idx] = vec

    inserted: list[Event] = []
    async with acquire(tenant_id) as conn:
        # acquire() already opens a transaction so set_config persists.
        for i, ev in enumerate(events):
            row = await _insert_one(conn, ev, embeddings.get(i), embedder.model_name)
            inserted.append(_row_to_event(row))
    return inserted


async def _insert_one(
    conn: asyncpg.Connection,
    event: EventCreate,
    embedding: list[float] | None,
    embedding_model: str,
) -> asyncpg.Record:
    dedupe = event.source.dedupe_key
    if dedupe:
        existing = await conn.fetchrow(
            "SELECT * FROM events WHERE source ->> 'dedupe_key' = $1 LIMIT 1",
            dedupe,
        )
        if existing:
            return existing

    effective = event.effective_at or datetime.now(UTC)
    row = await conn.fetchrow(
        """
        INSERT INTO events (
            event_type, payload, raw_content,
            embedding, embedding_model,
            source, participants, entities,
            parent_event_id, effective_at, expires_at,
            confidence, metadata
        ) VALUES (
            $1, $2::jsonb, $3,
            $4, $5,
            $6::jsonb, $7::uuid[], $8::text[],
            $9, $10, $11,
            $12, $13::jsonb
        )
        RETURNING *
        """,
        event.event_type,
        json.dumps(event.payload),
        event.raw_content,
        embedding,
        embedding_model if embedding else None,
        event.source.model_dump_json(),
        event.participants,
        event.entities,
        event.parent_event_id,
        effective,
        event.expires_at,
        event.confidence,
        json.dumps(event.metadata),
    )
    return row


def _row_to_event(row: asyncpg.Record) -> Event:
    d = dict(row)
    # asyncpg returns jsonb as already-decoded objects in most setups, but
    # be defensive in case it returns strings.
    for k in ("payload", "source", "metadata"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d.pop("embedding", None)  # don't ship raw vectors over the API
    return Event(**d)
