"""When one parallel first-post attempt raises, no action row is left half-made.

generate_content creates the action row; the image and sample are written to it
a moment later. With three attempts in flight, cancelling every sibling the
instant one raised could stop one between those two steps and leave an action
with no image and no sample. Now an attempt already past the QA gate is left to
finish; one still before it is cancelled (it has no row yet and no paid call
worth finishing). The job still ends with the original error.

Every AI / image / network / DB step is mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import types
from uuid import uuid4

import pytest

from james_os import template_clone as tc

pytestmark = pytest.mark.nodb

TENANT = uuid4()


def _install(monkeypatch):
    from james_os import (
        brand_identity,
        content,
        hero_context,
        media,
        render_reviewer,
    )

    st = {"created": [], "stored": [], "content_for": [], "cancelled": []}
    captioning = asyncio.Event()
    never = asyncio.Event()

    posts = [{"id": f"p{i}", "handle": f"h{i}", "topic": f"topic {i}",
              "stored_media_url": f"https://x/{i}.png"} for i in range(1, 7)]

    async def _top_posts(tenant_id, limit, *, templates_only=False):
        return posts if templates_only else []

    async def _empty(*a, **kw):
        return []

    async def _none(*a, **kw):
        return None

    async def _hero_files(tenant_id=None, limit=None, **kw):
        return [("lib", b"photo")]

    async def clone_post(post, tenant_id, *, hero_bytes=None, hero_generated=False,
                         hero_key="", stock_pool=None, allowed_formats=None):
        if post["id"] == "p1":
            await captioning.wait()          # p2 is mid-caption when p1 blows up
            raise RuntimeError("boom")
        if post["id"] == "p3":
            try:
                await never.wait()           # still cloning when p1 blows up
            except asyncio.CancelledError:
                st["cancelled"].append("p3")
                raise
        return {"png": b"png-" + post["id"].encode(), "kind": "k", "content": {},
                "topic": post["topic"], "generated_hero": False, "from": post["handle"],
                "hero_photo_key": "", "spec": {}, "source_url": ""}

    async def review_post(png, *, exemplars=None, mime="image/png"):
        return {"status": "ok", "passed": True, "polish_score": 80.0}

    async def generate_content(brief, tenant_id=None):
        action_id = str(uuid4())
        st["created"].append(action_id)      # the row exists from here
        st["content_for"].append(brief.topic)
        captioning.set()
        await asyncio.sleep(0.01)            # the rest of the caption call
        return types.SimpleNamespace(action_id=action_id, draft="cap")

    class _Storage:
        def save(self, tenant, png, name):
            return "https://cdn/x.png", "fp"

    class _Conn:
        async def execute(self, sql, action_id, payload):
            st["stored"].append((action_id, payload))

    @contextlib.asynccontextmanager
    async def acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(tc, "_top_posts", _top_posts)
    monkeypatch.setattr(tc, "_exemplars", _empty)
    monkeypatch.setattr(tc, "_stock_pool", _empty)
    monkeypatch.setattr(tc, "_brand_logo", _none)
    monkeypatch.setattr(tc, "clone_post", clone_post)
    monkeypatch.setattr(tc, "acquire", acquire)
    monkeypatch.setattr(brand_identity, "get_enabled_formats", _none)
    monkeypatch.setattr(hero_context, "get_hero_photo_files", _hero_files)
    monkeypatch.setattr(render_reviewer, "review_post", review_post)
    monkeypatch.setattr(content, "generate_content", generate_content)
    monkeypatch.setattr(media, "storage", lambda: _Storage())
    monkeypatch.setattr(media, "list_media", _empty)
    return st


@pytest.mark.asyncio
async def test_a_failing_attempt_leaves_no_action_without_its_image(monkeypatch):
    st = _install(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        await tc.generate_template_samples(TENANT, n=6)
    # p2 had made its row when p1 raised: it was allowed to finish storing it.
    assert st["content_for"] == ["topic 2"]
    stored_ids = [a for a, _p in st["stored"]]
    assert st["created"] == stored_ids                # no orphan action
    assert all('"image_url"' in p and '"sample": "true"' in p for _a, p in st["stored"])
    # p3 had no row yet: it was cancelled, and never reached the caption call.
    assert st["cancelled"] == ["p3"]
