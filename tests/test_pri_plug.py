"""PRI plug — pull PreReal Intelligence into memory, proven with the stub.

The stub never fabricates intelligence, so these tests assert the *plumbing*
(fetch → render → ingest → retrievable, idempotent on re-pull), not facts.
"""

import pytest

from james_os.adapters.pri_plug import StubPriPlugProvider
from james_os.db import acquire
from james_os.pri_plug_ingest import (
    _render_brief,
    _render_doc,
    pull_silo_into_memory,
)
from james_os.retrieval import search
from james_os.silos import list_silos

TEST_SILO = "ztestpri"


async def _clean_test_silo():
    """pri_pull_log + document_metadata aren't in conftest's TRUNCATE set, so
    scrub this test's silo up front for a deterministic start."""
    async with acquire() as conn:
        await conn.execute("DELETE FROM pri_pull_log WHERE silo_id = $1", TEST_SILO)
        await conn.execute("DELETE FROM document_metadata WHERE silo_id = $1", TEST_SILO)


@pytest.mark.asyncio
async def test_stub_provider_is_labelled_not_fabricated():
    intel = await StubPriPlugProvider().fetch_intelligence("spaceport")
    assert intel.provider == "stub"
    assert intel.brief is not None
    assert "STUB PRI PLUG" in (intel.brief.narrative or "")
    assert intel.docs and "STUB PRI PLUG" in intel.docs[0].text
    # The stub must not pretend to have retrieved real passages.
    assert await StubPriPlugProvider().retrieve("spaceport", "anything") == []


def test_renderers_shape():
    from james_os.adapters.pri_plug import PlugBrief, PlugDoc

    brief = PlugBrief(
        goal="Win the RFP", status="On track",
        narrative="A concise situation.", blockers=[{"text": "budget", "needs": "sign-off"}],
        next_actions=[{"text": "send deck", "owner": "James", "due": "2026-08-20"}],
    )
    md = _render_brief("spaceport", brief)
    assert "# PRI living brief — spaceport" in md
    assert "Win the RFP" in md and "budget" in md and "send deck" in md

    doc = PlugDoc(id="1", filename="hotel_pro_forma.pdf", doc_type="Financials",
                  file_date="2026-08-01", sensitivity="Restricted", text="EBITDA table…")
    dmd = _render_doc(doc)
    assert "hotel_pro_forma.pdf" in dmd and "Restricted" in dmd and "EBITDA" in dmd


@pytest.mark.asyncio
async def test_pull_into_memory_idempotent_and_retrievable():
    await _clean_test_silo()
    stub = StubPriPlugProvider()

    first = await pull_silo_into_memory(TEST_SILO, provider=stub)
    assert first["provider"] == "stub"
    assert first["ingested"] >= 2      # brief + at least one stub doc
    assert first["updated"] == 0 and first["skipped"] == 0

    # The silo was auto-created, and the content is now retrievable memory.
    assert any(s["id"] == TEST_SILO for s in await list_silos())
    hits = await search("STUB PRI PLUG")
    assert hits, "pulled PRI content should be retrievable from memory"

    # Re-pull: identical content → everything skipped, nothing duplicated.
    second = await pull_silo_into_memory(TEST_SILO, provider=stub)
    assert second["ingested"] == 0 and second["updated"] == 0
    assert second["skipped"] == first["ingested"]

    await _clean_test_silo()
