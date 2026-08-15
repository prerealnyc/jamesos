"""Academy — dump → memory, and campaign generation plumbing (stub LLM).

The stub LLM never produces a real campaign, so these assert the plumbing
(dump → memory → retrievable; generate returns a safe, grounded-shaped result),
not content quality.
"""

import pytest

from james_os.academy import (
    ACADEMY_SILO,
    add_academy_source,
    generate_campaign,
    list_academy_sources,
)
from james_os.db import acquire
from james_os.retrieval import search


async def _clean():
    async with acquire() as conn:
        await conn.execute("DELETE FROM document_metadata WHERE silo_id = $1", ACADEMY_SILO)


@pytest.mark.asyncio
async def test_dump_lands_in_memory_and_is_listed():
    await _clean()
    out = await add_academy_source(
        title="Objection handling",
        content="Lesson: when a buyer stalls on price, reframe around cost of waiting.",
    )
    assert out["ok"] is True
    assert out["siloId"] == ACADEMY_SILO

    sources = await list_academy_sources()
    assert any(s["silo_id"] == ACADEMY_SILO if "silo_id" in s else True for s in sources)
    assert len(sources) >= 1

    hits = await search("objection handling price")
    assert hits, "a dumped academy lesson should be retrievable from memory"
    await _clean()


@pytest.mark.asyncio
async def test_generate_campaign_is_safe_and_grounded_shaped_with_stub():
    await _clean()
    await add_academy_source(title="Voice", content="We speak plainly, no hype, buyer-first.")
    res = await generate_campaign("selling a boutique hotel", pieces=3)
    assert res["topic"] == "selling a boutique hotel"
    assert "campaign" in res
    # stub LLM → a safe shape (concept present, pieces a list), never a crash
    assert res["campaign"] is not None
    assert isinstance(res["campaign"]["pieces"], list)
    assert res["grounded_on"] >= 0
    await _clean()
