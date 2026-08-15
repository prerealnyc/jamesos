"""Press monitoring — scan → memory loop, proven with the stub.

The stub never fabricates coverage, so these assert the plumbing
(scan → events → ingested → retrievable), not facts.
"""

import pytest

from james_os.ingestion import ingest_many
from james_os.press import (
    PRESS_CATEGORY,
    PressMention,
    PressScan,
    StubPressProvider,
    scan_to_events,
)
from james_os.retrieval import search


@pytest.mark.asyncio
async def test_stub_press_provider_is_labelled_not_fabricated():
    scan = await StubPressProvider().scan("Spaceport America", focus="hotel")
    assert scan.provider == "stub"
    assert "STUB PRESS" in scan.summary
    assert "Spaceport America" in scan.summary
    assert scan.mentions == []          # the stub must not invent mentions
    assert not scan.is_empty()


def test_scan_to_events_tags_press_and_provenance():
    scan = PressScan(
        brand="Turtleback",
        summary="Turtleback drew local coverage this month.",
        mentions=[
            PressMention(url="https://example.com/a", title="Turtleback opens"),
            PressMention(url="https://news.test/b", title="Golf resort news"),
        ],
        provider="perplexity",
    )
    events = scan_to_events(scan)
    assert len(events) == 3  # 1 overview + 2 mentions
    for ev in events:
        assert ev.payload["category"] == PRESS_CATEGORY
        assert f"category:{PRESS_CATEGORY}" in ev.entities
        assert "subject:Turtleback" in ev.entities
        assert ev.confidence == 0.6
    # the mention events carry their source URL
    urls = [ev.payload.get("url") for ev in events if ev.payload.get("kind") == "mention"]
    assert "https://example.com/a" in urls and "https://news.test/b" in urls


@pytest.mark.asyncio
async def test_press_mentions_land_in_memory_and_retrievable():
    scan = PressScan(
        brand="Spaceport America",
        summary="Coverage of Spaceport America's tourism push.",
        mentions=[PressMention(url="https://example.com/sp", title="Spaceport tourism grows")],
        provider="perplexity",
    )
    stored = await ingest_many(scan_to_events(scan))
    assert len(stored) == 2
    hits = await search("Spaceport America tourism")
    assert hits, "filed press mentions should be retrievable from memory"
