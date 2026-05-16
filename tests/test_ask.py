import pytest

from james_os.ask import ask
from james_os.ingestion import ingest_many
from james_os.models import AskRequest, EventCreate, EventSource


def _spec(text: str, dedupe: str) -> EventCreate:
    return EventCreate(
        event_type="note",
        payload={"text": text},
        raw_content=text,
        source=EventSource(adapter="manual", dedupe_key=dedupe),
    )


@pytest.mark.asyncio
async def test_ask_refuses_when_memory_is_empty():
    resp = await ask(AskRequest(question="What is X?"))
    assert resp.refused is True
    assert resp.refusal_reason == "no_relevant_events_found"
    assert resp.citations == []


@pytest.mark.asyncio
async def test_ask_refuses_under_stub_llm_even_with_events():
    """Stub LLM always refuses — proves cite-or-refuse default behavior."""
    await ingest_many([_spec("The pilot price is $500K", "p-1")])
    resp = await ask(AskRequest(question="What's the pilot price?"))
    assert resp.refused is True
    # retrieved_event_ids should still be populated even on refusal
    assert len(resp.retrieved_event_ids) >= 1
