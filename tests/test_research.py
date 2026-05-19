"""Research → memory loop, proven with the stub provider.

The stub never fabricates intelligence, so these tests assert the
*plumbing* (provider → events → ingested → retrievable), not facts.
"""

import pytest

from james_os.ingestion import ingest_many
from james_os.research import (
    RESEARCH_CATEGORY,
    StubResearchProvider,
    research_to_events,
)
from james_os.retrieval import search


@pytest.mark.asyncio
async def test_stub_provider_is_labelled_not_fabricated():
    result = await StubResearchProvider().research("Acme Corp", focus="content style")
    assert result.provider == "stub"
    assert "STUB RESEARCH" in result.summary
    assert "Acme Corp" in result.summary
    # The stub must not pretend to have real sources.
    assert all(s.url.startswith("stub://") for s in result.sources)
    assert not result.is_empty()


def test_research_to_events_tags_category_and_provenance():
    result = StubResearchProvider.__new__(StubResearchProvider)
    from james_os.research import ResearchResult, ResearchSource

    result = ResearchResult(
        subject="Jane Doe",
        summary="Jane Doe is a fitness creator.",
        findings=["Posts daily reels", "Focuses on mobility"],
        sources=[ResearchSource(url="https://example.com/jane", title="Profile")],
        provider="perplexity",
    )
    events = research_to_events(result)

    assert len(events) == 3  # 1 summary + 2 findings
    for ev in events:
        assert ev.payload["category"] == RESEARCH_CATEGORY
        assert f"category:{RESEARCH_CATEGORY}" in ev.entities
        assert "subject:Jane Doe" in ev.entities
        assert "source:example.com" in ev.entities
        assert ev.source.raw_metadata["sources"] == ["https://example.com/jane"]
        assert ev.confidence == 0.6  # informative, not authoritative
        assert "https://example.com/jane" in ev.raw_content


@pytest.mark.asyncio
async def test_research_findings_land_in_memory_and_are_retrievable():
    """Full loop: research → ingest → the substrate can retrieve it."""
    result = await StubResearchProvider().research("Globex Inc")
    events = research_to_events(result)
    stored = await ingest_many(events)
    assert len(stored) == len(events) >= 1

    hits = await search("Globex Inc research")
    assert any("Globex Inc" in (h.raw_content or "") for h in hits)
    # Provenance survived into the stored event payload.
    assert any(h.payload.get("category") == RESEARCH_CATEGORY for h in hits)


@pytest.mark.asyncio
async def test_research_ingestion_is_idempotent():
    result = await StubResearchProvider().research("Initech")
    events = research_to_events(result)
    first = await ingest_many(events)
    second = await ingest_many(research_to_events(result))
    # Same dedupe_key → same event ids, no duplicate memory.
    assert {e.id for e in first} == {e.id for e in second}
