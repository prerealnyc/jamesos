from datetime import UTC, datetime

import pytest

from james_os.ingestion import ingest, ingest_many
from james_os.models import EventCreate, EventSource


@pytest.mark.asyncio
async def test_ingest_creates_event_with_embedding():
    ev = await ingest(
        EventCreate(
            event_type="note",
            payload={"text": "hello memory"},
            raw_content="hello memory",
            source=EventSource(adapter="manual", uri="test://1", dedupe_key="abc"),
        )
    )
    assert ev.event_type == "note"
    assert ev.embedding_model == "stub"
    assert ev.source["dedupe_key"] == "abc"


@pytest.mark.asyncio
async def test_ingest_is_idempotent_on_dedupe_key():
    spec = EventCreate(
        event_type="note",
        payload={"text": "duplicate"},
        raw_content="duplicate",
        source=EventSource(adapter="manual", uri="test://2", dedupe_key="dup-1"),
    )
    first = await ingest(spec)
    second = await ingest(spec)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_ingest_many_batches_efficiently():
    specs = [
        EventCreate(
            event_type="note",
            payload={"i": i},
            raw_content=f"item {i}",
            source=EventSource(adapter="manual", dedupe_key=f"batch-{i}"),
            effective_at=datetime.now(UTC),
        )
        for i in range(5)
    ]
    out = await ingest_many(specs)
    assert len(out) == 5
    assert len({e.id for e in out}) == 5
