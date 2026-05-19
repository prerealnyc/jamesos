"""Re-uploading a file replaces the previous version.

The bug this guards against: a changed file gets a new content hash, so
ingestion creates brand-new chunks while the OLD chunks linger un-retired
and keep getting retrieved — the brand manager then blends stale + current
guidelines. Fix: the new upload supersedes the prior version.
"""

import pytest

from james_os.db import acquire
from james_os.documents import document_to_events
from james_os.ingestion import ingest_many, supersede_prior_document_versions
from james_os.retrieval import search


async def _upload(text: str, filename: str, category: str) -> list:
    events = document_to_events(filename, text.encode(), category=category)
    stored = await ingest_many(events)
    superseded = await supersede_prior_document_versions(
        filename, category, [e.id for e in stored]
    )
    return stored, superseded


async def _live_ids() -> set:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM events WHERE superseded_by IS NULL"
        )
    return {r["id"] for r in rows}


@pytest.mark.asyncio
async def test_updated_file_supersedes_previous_version():
    v1, s1 = await _upload(
        "The brand voice is FORMAL and uses the word synergy often.",
        "guidelines.md",
        "guideline",
    )
    assert s1 == 0  # nothing prior to retire

    v2, s2 = await _upload(
        "UPDATED RULE: the brand voice is PUNCHY and bans the word synergy.",
        "guidelines.md",
        "guideline",
    )
    # Every old chunk got retired.
    assert s2 == len(v1)

    live = await _live_ids()
    assert all(e.id not in live for e in v1)  # v1 retired
    assert all(e.id in live for e in v2)      # v2 live

    # Retrieval (which filters superseded_by IS NULL) sees only the new rule.
    hits = await search("brand voice rule synergy")
    texts = " ".join((h.raw_content or "") for h in hits)
    assert "UPDATED RULE" in texts
    assert "FORMAL" not in texts


@pytest.mark.asyncio
async def test_identical_reupload_supersedes_nothing():
    text = "Stable guideline that does not change between uploads."
    v1, s1 = await _upload(text, "stable.md", "guideline")
    assert s1 == 0
    v2, s2 = await _upload(text, "stable.md", "guideline")
    # Same content hash → dedupes back to the same rows → nothing retired,
    # and the original chunks stay live.
    assert s2 == 0
    assert {e.id for e in v1} == {e.id for e in v2}
    live = await _live_ids()
    assert all(e.id in live for e in v1)


@pytest.mark.asyncio
async def test_supersede_is_scoped_to_category():
    await _upload("Reference copy of the doc.", "shared.md", "reference")
    v_guide, _ = await _upload("Guideline copy of the doc.", "shared.md", "guideline")
    # A new guideline upload must not retire the reference-category copy.
    v_guide2, superseded = await _upload(
        "Updated guideline copy.", "shared.md", "guideline"
    )
    assert superseded == len(v_guide)
    hits = await search("copy of the doc reference")
    assert any("Reference copy" in (h.raw_content or "") for h in hits)
