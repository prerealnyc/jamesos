"""The starter-post jobs say WHY the brand's own assets were skipped over.

GET /v1/samples/generate/{job_id} (the store behind /v1/samples/clone and
/v1/samples/generate) and /competitors/first-posts/{job_id} carry
`skipped_reason` and `assets` from the first progress emit to the final result.
Counts come from two media_assets reads — no bytes, no AI.
"""

from __future__ import annotations

import pytest

from james_os import starter_assets as sa

pytestmark = pytest.mark.nodb


@pytest.mark.parametrize("logos,photos,reason", [
    (0, 0, "missing_logo_and_photos"),
    (0, 2, "missing_logo_and_photos"),
    (0, 3, "missing_logo"),
    (1, 0, "missing_photos"),
    (1, 2, "missing_photos"),
    (1, 3, None),
    (2, 10, None),
])
def test_skipped_reason(logos, photos, reason):
    assert sa.skipped_reason(logos, photos) == reason
    fields = sa.asset_fields(logos, photos)
    assert fields["skipped_reason"] == reason
    assert fields["assets"] == {"logo_count": logos, "photo_count": photos, "photos_needed": 3}


@pytest.mark.asyncio
async def test_asset_status_counts_media_rows_and_takes_the_larger_photo_count(monkeypatch):
    from james_os import media

    async def list_media(role="", tenant_id=None):
        return {"brand_logo": [{"id": 1}], "hero_photo": [{"id": 2}]}.get(role, [])

    monkeypatch.setattr(media, "list_media", list_media)
    assert await sa.asset_status("t") == {
        "skipped_reason": "missing_photos",
        "assets": {"logo_count": 1, "photo_count": 1, "photos_needed": 3}}
    # The render pool knew of more photos (scraped own posts) than the shelf lists.
    assert (await sa.asset_status("t", photo_count=4))["skipped_reason"] is None


@pytest.mark.asyncio
async def test_a_failed_read_counts_as_zero_not_a_crash(monkeypatch):
    from james_os import media

    async def boom(role="", tenant_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(media, "list_media", boom)
    assert (await sa.asset_status("t"))["skipped_reason"] == "missing_logo_and_photos"


@pytest.mark.asyncio
async def test_photo_samples_job_carries_the_fields_even_when_it_builds_nothing(monkeypatch):
    from james_os import brand_identity
    from james_os import hero_templatize as ht

    async def classify(tenant_id, force=False):
        return {}

    async def allowed(tenant_id=None):
        return None

    async def pool(tenant_id, limit=30):
        return []

    async def status(tenant_id, *, photo_count=None):
        return sa.asset_fields(0, 1)

    monkeypatch.setattr(ht, "classify_brand_photos", classify)
    monkeypatch.setattr(ht, "templatizable_photos", pool)
    monkeypatch.setattr(brand_identity, "get_enabled_formats", allowed)
    monkeypatch.setattr(sa, "asset_status", status)
    emits: list[dict] = []
    res = await ht.generate_samples("t", n=3, progress=emits.append)
    assert res["count"] == 0
    assert res["skipped_reason"] == "missing_logo_and_photos"
    assert res["assets"] == {"logo_count": 0, "photo_count": 1, "photos_needed": 3}
    assert emits and all(e["skipped_reason"] == "missing_logo_and_photos" for e in emits)


@pytest.mark.asyncio
async def test_first_posts_job_carries_the_fields(monkeypatch):
    import contextlib
    import types

    from james_os import competitor_kickoff as ck
    from james_os import content

    async def picks(limit=20, include_queued=False, tenant_id=None):
        return [{"id": "p1", "handle": "h", "topic": "t", "replicate_status": "saved",
                 "media_type": "image"}]

    async def no_photos(tenant_id):
        return False

    async def status(tenant_id, *, photo_count=None):
        return sa.asset_fields(1, 0)

    async def draft(brief, tenant_id=None):
        return types.SimpleNamespace(status="ok", action_id=None, draft="words", voice_score=1)

    class _Conn:
        async def execute(self, *a):
            return None

    @contextlib.asynccontextmanager
    async def acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(ck, "picked_posts", picks)
    monkeypatch.setattr(ck, "_has_hero_photos", no_photos)
    monkeypatch.setattr(ck, "acquire", acquire)
    monkeypatch.setattr(content, "generate_content", draft)
    monkeypatch.setattr(sa, "asset_status", status)
    emits: list[dict] = []
    res = await ck.generate_first_posts(n=1, tenant_id=None, progress=emits.append)
    assert res["generated"] == 1
    assert res["skipped_reason"] == "missing_photos"
    assert res["assets"]["photo_count"] == 0 and res["assets"]["photos_needed"] == 3
    assert emits and emits[0]["skipped_reason"] == "missing_photos"
