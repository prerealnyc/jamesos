"""Hybrid retrieval — vector + full-text + structured, fanned out in parallel.

Production RAG #1 lesson: pure vector search misses things; pure full-text
misses things. Run both, merge, rerank. Without this, retrieval quality is
mediocre and downstream cite-or-refuse will refuse a lot of answerable
questions.

Reranking lives in `rerank.py` and is called separately so retrieval can be
tested without LLM dependencies.
"""

import asyncio
import json
from datetime import datetime
from uuid import UUID

import asyncpg

from .config import settings
from .db import acquire
from .embedder import get_embedder
from .models import RetrievedEvent


async def search(
    query: str,
    *,
    tenant_id: UUID | None = None,
    event_types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    top_k_per_index: int | None = None,
) -> list[RetrievedEvent]:
    """Hybrid retrieval. Returns deduplicated, merged candidates.

    Reranking is the caller's responsibility (see rerank.py).
    """
    k = top_k_per_index or settings.retrieval_top_k_per_index
    embedder = get_embedder()
    query_vec = await embedder.embed_one(query)

    # asyncpg connections are single-statement; parallel fan-out needs
    # one connection per branch. RLS tenant scoping is set on each.
    async def vec():
        async with acquire(tenant_id) as conn:
            return await _vector_search(conn, query_vec, k, event_types, since, until)

    async def fts():
        async with acquire(tenant_id) as conn:
            return await _fts_search(conn, query, k, event_types, since, until)

    vector_hits, fts_hits = await asyncio.gather(vec(), fts())
    return _merge(vector_hits, fts_hits)


async def _vector_search(
    conn: asyncpg.Connection,
    query_vec: list[float],
    k: int,
    event_types: list[str] | None,
    since: datetime | None,
    until: datetime | None,
) -> list[RetrievedEvent]:
    where, params = _build_filters(event_types, since, until, start_idx=2)
    sql = f"""
        SELECT
            id, event_type, raw_content, payload, effective_at,
            1 - (embedding <=> $1::vector) AS score
        FROM events
        WHERE embedding IS NOT NULL
          AND superseded_by IS NULL
          {where}
        ORDER BY embedding <=> $1::vector
        LIMIT {k}
    """
    rows = await conn.fetch(sql, query_vec, *params)
    return [_row_to_retrieved(r, "vector") for r in rows]


async def _fts_search(
    conn: asyncpg.Connection,
    query: str,
    k: int,
    event_types: list[str] | None,
    since: datetime | None,
    until: datetime | None,
) -> list[RetrievedEvent]:
    where, params = _build_filters(event_types, since, until, start_idx=2)
    sql = f"""
        SELECT
            id, event_type, raw_content, payload, effective_at,
            ts_rank(to_tsvector('english', coalesce(raw_content, '')),
                    plainto_tsquery('english', $1)) AS score
        FROM events
        WHERE raw_content IS NOT NULL
          AND superseded_by IS NULL
          AND to_tsvector('english', raw_content) @@ plainto_tsquery('english', $1)
          {where}
        ORDER BY score DESC
        LIMIT {k}
    """
    rows = await conn.fetch(sql, query, *params)
    return [_row_to_retrieved(r, "fts") for r in rows]


def _build_filters(
    event_types: list[str] | None,
    since: datetime | None,
    until: datetime | None,
    start_idx: int,
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    idx = start_idx

    if event_types:
        clauses.append(f"AND event_type = ANY(${idx}::text[])")
        params.append(event_types)
        idx += 1
    if since:
        clauses.append(f"AND effective_at >= ${idx}")
        params.append(since)
        idx += 1
    if until:
        clauses.append(f"AND effective_at <= ${idx}")
        params.append(until)
        idx += 1

    return " ".join(clauses), params


def _row_to_retrieved(row: asyncpg.Record, signal: str) -> RetrievedEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return RetrievedEvent(
        event_id=row["id"],
        event_type=row["event_type"],
        raw_content=row["raw_content"],
        payload=payload,
        effective_at=row["effective_at"],
        score=float(row["score"] or 0.0),
        source_signal=[signal],
    )


def _merge(*hit_lists: list[RetrievedEvent]) -> list[RetrievedEvent]:
    """Deduplicate by event_id, combine source signals, keep max score per event."""
    by_id: dict[UUID, RetrievedEvent] = {}
    for hits in hit_lists:
        for hit in hits:
            existing = by_id.get(hit.event_id)
            if existing is None:
                by_id[hit.event_id] = hit
            else:
                existing.score = max(existing.score, hit.score)
                signals = list(set(existing.source_signal + hit.source_signal))
                existing.source_signal = signals
    return sorted(by_id.values(), key=lambda h: h.score, reverse=True)
