"""Content engine — plumbing + the honesty guarantees.

The stub LLM never writes content, so these prove: category bucketing is
correct, the engine refuses (and queues NOTHING) when it can't ground a
voice, and a stub LLM produces no fake post in the approval queue. Real
generation quality is verified live against Anthropic, not here.
"""

from datetime import UTC, datetime

import pytest

from james_os.content import _bucket_of, assemble_memory, generate_content
from james_os.db import acquire
from james_os.ingestion import ingest_many
from james_os.models import ContentBrief, EventCreate, EventSource, RetrievedEvent


def _ev(text: str, dedupe: str, category: str, etype: str = "document") -> EventCreate:
    return EventCreate(
        event_type=etype,
        payload={"text": text, "category": category},
        raw_content=text,
        source=EventSource(adapter="test", dedupe_key=dedupe),
        entities=[f"category:{category}"],
    )


def _retrieved(category: str, etype: str = "document") -> RetrievedEvent:
    from uuid import uuid4

    return RetrievedEvent(
        event_id=uuid4(),
        event_type=etype,
        raw_content="x",
        payload={"category": category},
        effective_at=datetime.now(UTC),
        score=0.5,
        source_signal=["vector"],
    )


async def _action_count() -> int:
    async with acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM actions")


def test_bucketing_is_category_driven():
    assert _bucket_of(_retrieved("voice_corpus")) == "voice"
    assert _bucket_of(_retrieved("guideline")) == "voice"
    assert _bucket_of(_retrieved("anything", "voice_memo")) == "voice"
    assert _bucket_of(_retrieved("thesis")) == "thesis"
    assert _bucket_of(_retrieved("frustration")) == "frustration"
    assert _bucket_of(_retrieved("research")) == "facts"
    assert _bucket_of(_retrieved("reference")) == "facts"
    assert _bucket_of(_retrieved("")) == "facts"  # uncategorised → context


@pytest.mark.asyncio
async def test_assemble_memory_buckets_by_category():
    await ingest_many([
        _ev("James always speaks in short punchy declaratives.", "v1", "voice_corpus"),
        _ev("The thesis: operators win by owning the memory layer.", "t1", "thesis"),
        _ev("Acme Corp raised $40M in 2026 per their press release.", "r1", "research"),
        _ev("Never use the word 'synergy'. Never hedge.", "f1", "frustration"),
    ])
    brief = ContentBrief(topic="owning the memory layer", pillar="moat")
    buckets, used = await assemble_memory(brief, None)

    assert used["voice"] >= 1
    assert used["thesis"] >= 1
    # research/uncategorised land in facts; frustration is its own bucket.
    assert used["facts"] >= 1
    assert used["frustration"] >= 1


@pytest.mark.asyncio
async def test_refuses_and_queues_nothing_without_voice_grounding():
    """No voice/thesis in memory → must refuse, not fabricate, not queue."""
    await ingest_many([_ev("Acme raised money.", "r1", "research")])
    before = await _action_count()
    draft = await generate_content(ContentBrief(topic="Acme"))

    assert draft.status == "not_generated"
    assert draft.draft == ""
    assert draft.action_id is None
    assert draft.qa is None
    assert "voice" in (draft.note or "").lower()
    assert await _action_count() == before  # nothing queued


@pytest.mark.asyncio
async def test_stub_llm_produces_no_fake_post_in_queue():
    """Even with voice grounding present, a stub LLM must not put a
    fabricated draft into the approval queue."""
    await ingest_many([
        _ev("Punchy declaratives. No hedging. This is the voice.", "v1", "voice_corpus"),
        _ev("Thesis: own the memory layer.", "t1", "thesis"),
    ])
    before = await _action_count()
    draft = await generate_content(ContentBrief(topic="the memory moat"))

    # Stub LLM refuses → engine surfaces that honestly, queues nothing.
    assert draft.status == "not_generated"
    assert draft.action_id is None
    assert await _action_count() == before
    # Memory WAS assembled (proves the pipeline ran up to generation).
    assert draft.memory_used["voice"] >= 1
    assert draft.memory_used["thesis"] >= 1
