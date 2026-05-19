"""Durable video pipeline, proven with the stub provider.

Asserts the plumbing — persist-first, poll, land-in-queue-once, honest
failure — not a real render (the stub never produces an mp4).
"""

import pytest

from james_os import video as video_module
from james_os.config import settings
from james_os.db import acquire
from james_os.video import (
    RunwayVideoProvider,
    list_video_jobs,
    refresh_video_job,
    submit_video_job,
)


async def _action_count(kind: str = "video") -> int:
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type=$1", kind
        )


@pytest.mark.asyncio
async def test_job_is_persisted_before_provider_call():
    job = await submit_video_job("a slow dolly over a city at dusk")
    # Stub submit → durable row exists, marked submitted, has a job id.
    assert job["status"] == "submitted"
    assert job["provider"] == "stub"
    assert job["provider_job_id"]
    rows = await list_video_jobs()
    assert any(r["id"] == job["id"] for r in rows)


@pytest.mark.asyncio
async def test_poll_succeeds_and_lands_in_queue_once():
    before = await _action_count()
    job = await submit_video_job("neon rain on a quiet street")
    refreshed = await refresh_video_job(job["id"])

    assert refreshed["status"] == "succeeded"
    assert refreshed["result_url"].startswith("stub://")  # honest, not a fake mp4
    assert refreshed["queued_action_id"]
    assert await _action_count() == before + 1

    # Polling again must NOT double-queue.
    again = await refresh_video_job(job["id"])
    assert again["status"] == "succeeded"
    assert await _action_count() == before + 1

    # The queued item is a pending video the approval queue can render.
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, payload FROM actions WHERE id=$1",
            refreshed["queued_action_id"],
        )
    import json
    payload = row["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert row["status"] == "pending"
    assert payload["media_url"].startswith("stub://")
    assert payload["stub"] is True


@pytest.mark.asyncio
async def test_runway_without_image_fails_honestly_and_queues_nothing():
    """Runway's dev API is image-conditioned — we refuse, not fake it."""
    video_module._provider = RunwayVideoProvider(api_key="x", api_version="v")
    settings.video_provider = "runway"
    try:
        before = await _action_count()
        job = await submit_video_job("text only prompt, no still")
        assert job["status"] == "failed"
        assert "image-conditioned" in (job["error"] or "")
        assert await _action_count() == before  # nothing queued
    finally:
        settings.video_provider = "stub"
        video_module._provider = None
