import pytest

from james_os.ingestion import ingest_many
from james_os.models import EventCreate, EventSource
from james_os.retrieval import search


def _spec(text: str, dedupe: str) -> EventCreate:
    return EventCreate(
        event_type="note",
        payload={"text": text},
        raw_content=text,
        source=EventSource(adapter="manual", dedupe_key=dedupe),
    )


@pytest.mark.asyncio
async def test_search_returns_matching_events():
    await ingest_many(
        [
            _spec("Spaceport pilot pricing decision: $500K", "evt-spaceport-1"),
            _spec("Brand voice rule: do not use exclamation marks", "evt-voice-1"),
            _spec("Standup notes from Tuesday", "evt-standup-1"),
        ]
    )
    hits = await search("Spaceport pricing", top_k_per_index=10)
    assert len(hits) >= 1
    # full-text should always pull the exact match
    assert any("Spaceport" in (h.raw_content or "") for h in hits)


@pytest.mark.asyncio
async def test_search_returns_empty_for_empty_db():
    hits = await search("anything")
    assert hits == []


@pytest.mark.asyncio
async def test_search_filters_by_event_type():
    await ingest_many([_spec("a note", "n1"), _spec("another", "n2")])
    hits_note = await search("note", event_types=["note"])
    hits_decision = await search("note", event_types=["decision"])
    assert len(hits_note) >= 1
    assert hits_decision == []
