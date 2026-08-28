"""Describing assets so the placer can match them.

Two things must hold, because both are ways to quietly corrupt a library:

  * a failure writes NOTHING. An asset with no description is skipped by the
    placer; an invented one produces a cutaway that contradicts the voiceover,
    which is the failure that got automatic B-roll switched off in the first
    place.
  * a person's own description is never overwritten by ours.
"""

import uuid

import pytest

from james_os.media import create_media, list_media, update_media
from james_os.db import acquire
from james_os.reel_vision import (
    DESCRIBE_TAG,
    VisionError,
    describe_media_asset,
    describe_pending,
)

TITLE_PREFIX = "ZZVIS"


@pytest.fixture
async def asset():
    """A B-roll row with a real-looking file path, cleaned up after."""
    made = await create_media(
        role="broll", source_type="upload", uri="https://x/zzvis.mp4",
        file_path="/tmp/zzvis.mp4", title=f"{TITLE_PREFIX} clip",
        mime="video/mp4", tags=["sha256:deadbeef"],
    )
    yield made
    async with acquire() as conn:
        await conn.execute("DELETE FROM media_assets WHERE title LIKE $1",
                           f"{TITLE_PREFIX}%")


@pytest.fixture(autouse=True)
def _no_real_vision(monkeypatch):
    """Every test controls the vision result explicitly; none may reach OpenAI."""
    async def unset(*_a, **_k):
        raise AssertionError("describe_file was not stubbed in this test")
    monkeypatch.setattr("james_os.reel_vision.describe_file", unset)


def _returns(monkeypatch, description="a waterfront commercial lot from the air",
             tags=("waterfront", "lot")):
    async def fake(_path, _kind="video"):
        return {"description": description, "tags": list(tags)}
    monkeypatch.setattr("james_os.reel_vision.describe_file", fake)


def _raises(monkeypatch, msg="the frames don't show anything identifiable"):
    async def fake(_path, _kind="video"):
        raise VisionError(msg)
    monkeypatch.setattr("james_os.reel_vision.describe_file", fake)


def _local_file(monkeypatch, ok=True):
    async def fake(_file_path, _uri=""):
        return ("/tmp/zzvis.mp4", None) if ok else (None, None)
    monkeypatch.setattr("james_os.media.fetch_media_local", fake)


# ── writing a description ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_described_asset_becomes_matchable(monkeypatch, asset):
    _local_file(monkeypatch)
    _returns(monkeypatch)
    r = await describe_media_asset(uuid.UUID(asset["id"]))
    assert r["status"] == "described"

    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    # `notes` is exactly what reel_placer reads as the asset's description.
    assert rows[0]["notes"] == "a waterfront commercial lot from the air"
    assert "waterfront" in rows[0]["tags"]
    assert DESCRIBE_TAG in rows[0]["tags"]


@pytest.mark.asyncio
async def test_describing_keeps_the_content_hash_tag(monkeypatch, asset):
    # The dedup tag is load-bearing elsewhere; a description must not drop it.
    _local_file(monkeypatch)
    _returns(monkeypatch)
    await describe_media_asset(uuid.UUID(asset["id"]))
    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    assert "sha256:deadbeef" in rows[0]["tags"]


@pytest.mark.asyncio
async def test_re_describing_refreshes_our_own_text(monkeypatch, asset):
    _local_file(monkeypatch)
    _returns(monkeypatch, "first read", ("one",))
    await describe_media_asset(uuid.UUID(asset["id"]))
    _returns(monkeypatch, "second read", ("two",))
    r = await describe_media_asset(uuid.UUID(asset["id"]))
    assert r["status"] == "described"
    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    assert rows[0]["notes"] == "second read"
    assert rows[0]["tags"].count(DESCRIBE_TAG) == 1      # not appended twice


# ── the two safety rules ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_humans_description_is_never_overwritten(monkeypatch, asset):
    await update_media(uuid.UUID(asset["id"]),
                       notes="Shot this myself at the Bay Street site")
    _local_file(monkeypatch)
    _returns(monkeypatch, "something the model made up")
    r = await describe_media_asset(uuid.UUID(asset["id"]))
    assert r["status"] == "skipped"
    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    assert rows[0]["notes"] == "Shot this myself at the Bay Street site"


@pytest.mark.asyncio
async def test_force_overrides_a_human_note(monkeypatch, asset):
    await update_media(uuid.UUID(asset["id"]), notes="mine")
    _local_file(monkeypatch)
    _returns(monkeypatch, "the model's read")
    r = await describe_media_asset(uuid.UUID(asset["id"]), force=True)
    assert r["status"] == "described"


@pytest.mark.asyncio
async def test_a_vision_failure_writes_nothing(monkeypatch, asset):
    # The rule: no description beats a guessed one. An undescribed asset is
    # simply skipped by the placer.
    _local_file(monkeypatch)
    _raises(monkeypatch)
    r = await describe_media_asset(uuid.UUID(asset["id"]))
    assert r["status"] == "failed"
    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    assert not (rows[0]["notes"] or "").strip()
    assert DESCRIBE_TAG not in rows[0]["tags"]


@pytest.mark.asyncio
async def test_an_unfetchable_file_fails_without_writing(monkeypatch, asset):
    _local_file(monkeypatch, ok=False)
    _returns(monkeypatch)
    r = await describe_media_asset(uuid.UUID(asset["id"]))
    assert r["status"] == "failed"
    rows = [a for a in await list_media("broll") if a["id"] == asset["id"]]
    assert not (rows[0]["notes"] or "").strip()


@pytest.mark.asyncio
async def test_a_missing_asset_is_reported_not_raised(monkeypatch):
    r = await describe_media_asset(uuid.uuid4())
    assert r["status"] == "failed"


# ── the batch ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_batch_reports_what_it_did_not_attempt(monkeypatch, asset):
    # A capped pass must never read as a complete one.
    _local_file(monkeypatch)
    _returns(monkeypatch)
    extra = []
    for i in range(2):
        extra.append(await create_media(
            role="broll", source_type="upload", uri=f"https://x/zz{i}.mp4",
            file_path=f"/tmp/zz{i}.mp4", title=f"{TITLE_PREFIX} extra {i}",
            mime="video/mp4"))
    try:
        out = await describe_pending("broll", limit=1)
        assert len(out["described"]) == 1
        assert out["pending_total"] >= 3
        assert out["not_attempted"] >= 2
    finally:
        async with acquire() as conn:
            await conn.execute("DELETE FROM media_assets WHERE title LIKE $1",
                               f"{TITLE_PREFIX}%")


@pytest.mark.asyncio
async def test_the_batch_survives_one_bad_asset(monkeypatch, asset):
    _local_file(monkeypatch)
    _raises(monkeypatch)
    out = await describe_pending("broll", limit=5)
    assert out["described"] == []
    assert out["failed"]                       # reported, not swallowed
