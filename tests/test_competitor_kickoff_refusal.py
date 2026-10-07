"""A pick the engine refused keeps its verdict.

generate_content answers a voiceless tenant with a draft whose status is
'not_generated' — it does not raise. generate_first_posts caught exceptions
only, so the refusal fell through to the 'queued' mark, and picked_posts
excludes 'queued': one run before the voice landed consumed every post the
owner had picked, for a batch that made nothing, and no later run could see
them again.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from james_os import competitor_kickoff as kick

pytestmark = pytest.mark.nodb

PICK = {"id": "11111111-1111-1111-1111-111111111111", "handle": "rival",
        "replicate_status": "saved", "caption": "their post", "topic": "pricing",
        "media_type": "image"}
REFUSAL = ("No voice corpus or thesis in memory for this tenant — the engine will "
           "not fabricate a generic post and call it on-voice.")


def _wire(monkeypatch, draft):
    """One pick, no photos, a recording DB, and an engine that returns `draft`."""
    from james_os import content

    executed: list[str] = []

    async def picked(limit=10, include_queued=False, tenant_id=None):
        return [dict(PICK)]

    async def no_photos(tenant_id):
        return False

    async def generate(brief):
        return draft

    class _Conn:
        async def execute(self, sql, *args):
            executed.append(sql)

    @asynccontextmanager
    async def acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(kick, "picked_posts", picked)
    monkeypatch.setattr(kick, "_has_hero_photos", no_photos)
    monkeypatch.setattr(kick, "acquire", acquire)
    monkeypatch.setattr(content, "generate_content", generate)
    return executed


async def test_a_refused_pick_is_not_marked_queued(monkeypatch):
    refused = SimpleNamespace(status="not_generated", draft="", note=REFUSAL,
                              action_id=None, voice_score=0.0)
    executed = _wire(monkeypatch, refused)

    out = await kick.generate_first_posts(n=5, tenant_id=None)

    assert out["generated"] == 0
    assert not any("replicate_status = 'queued'" in sql for sql in executed), \
        "the owner's pick must survive a run the engine refused"
    assert out["failed"] and "voice" in out["failed"][0]["error"].lower(), \
        "the refusal is reported, with the engine's own reason"


async def test_a_drafted_pick_is_still_marked_queued(monkeypatch):
    """The fix is narrow: a real draft still retires its pick, so a second run
    does not redraft it."""
    made = SimpleNamespace(status="generated", draft="our take on pricing", note=None,
                           action_id=None, voice_score=0.9)
    executed = _wire(monkeypatch, made)

    out = await kick.generate_first_posts(n=5, tenant_id=None)

    assert out["generated"] == 1
    assert sum("replicate_status = 'queued'" in sql for sql in executed) == 1
