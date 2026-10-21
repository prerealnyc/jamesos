"""A brand with no photos builds its first posts on what it already HAS.

generate_template_samples used to DRAW two backdrops (gpt-image-1) for a brand
with an empty library, and the QA reviewer often threw the result out as plain
colour. Now the stock pool is used first, and a backdrop is drawn only for the
slots it leaves empty — so the number of generations is the same as before or
fewer, never more. The job also carries skipped_reason + assets on every emit
and in the result.

Never a backdrop: house-catalogue pictures (other accounts' posts the harvest
keeps for layout review only, not licensed for us to publish) and the brand's
logo (clone_post already puts it in the logo slot).
"""

from __future__ import annotations

import contextlib
import types
from uuid import uuid4

import pytest

from james_os import template_clone as tc

pytestmark = pytest.mark.nodb

TENANT = uuid4()


def _posts(k: int) -> list[dict]:
    return [{"id": f"p{i}", "handle": f"h{i}", "topic": f"topic {i}",
             "stored_media_url": f"https://x/{i}.png"} for i in range(1, k + 1)]


def _install(monkeypatch, *, logo: bytes | None, stock: list, photos: bool = False,
             house_bytes: bytes = b"HOUSE"):
    from james_os import (
        brand_identity,
        content,
        hero_context,
        house_layouts,
        media,
        render_reviewer,
    )

    calls = {"draw": 0, "clones": [], "fetched": [], "sql": []}

    async def _top_posts(tenant_id, limit, *, templates_only=False):
        return _posts(6) if templates_only else []

    async def _none_list(*a, **kw):
        return []

    async def _formats(tenant_id=None):
        return None

    async def _hero_files(tenant_id=None, limit=None, **kw):
        return [("lib", b"photo")] if photos else []

    async def _draw(tenant_id, topic, spec=None):
        calls["draw"] += 1
        return b"drawn", True, ""

    async def _logo(tenant_id):
        return logo

    async def _fetch_bytes(url):
        # Any image fetched by the batch would be a catalogue picture here: the
        # logo, stock and drawing paths are all faked above.
        calls["fetched"].append(url)
        return house_bytes

    async def _niches(conn, tenant_id):
        # A niche with an approved catalogue row: the old code would have used it.
        return ["golf resort"]

    async def _stock(topics, tenant_id=None):
        return list(stock)

    async def clone_post(post, tenant_id, *, hero_bytes=None, hero_generated=False,
                         hero_key="", stock_pool=None, allowed_formats=None):
        calls["clones"].append((post["id"], hero_key, hero_bytes, hero_generated))
        return {"png": b"png", "kind": "k", "content": {}, "topic": post["topic"],
                "generated_hero": hero_generated, "from": post["handle"],
                "hero_photo_key": hero_key, "spec": {}, "source_url": "",
                **tc._image_source(hero_key, hero_generated, hero_bytes)}

    async def review_post(png, *, exemplars=None, mime="image/png"):
        return {"status": "ok", "passed": True, "polish_score": 80.0}

    async def generate_content(brief, tenant_id=None):
        return types.SimpleNamespace(action_id=str(uuid4()), draft="cap")

    async def list_media(role="", tenant_id=None):
        return [{"id": 1}] if (role == "brand_logo" and logo) else []

    class _Storage:
        def save(self, tenant, png, name):
            return "https://cdn/x.png", "fp"

    payloads: list[str] = []

    class _Conn:
        async def execute(self, sql, action_id, payload):
            payloads.append(payload)

        async def fetch(self, sql, *args):
            calls["sql"].append(sql)
            return [{"id": "L1", "source_image_uri": "https://cdn/l1.png"}]

    @contextlib.asynccontextmanager
    async def acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(tc, "_top_posts", _top_posts)
    monkeypatch.setattr(tc, "_exemplars", _none_list)
    monkeypatch.setattr(tc, "_stock_pool", _stock)
    monkeypatch.setattr(tc, "_hero_or_placeholder", _draw)
    monkeypatch.setattr(tc, "_brand_logo", _logo)
    monkeypatch.setattr(tc, "_fetch_bytes", _fetch_bytes)
    monkeypatch.setattr(house_layouts, "tenant_niches", _niches)
    monkeypatch.setattr(tc, "clone_post", clone_post)
    monkeypatch.setattr(tc, "acquire", acquire)
    monkeypatch.setattr(brand_identity, "get_enabled_formats", _formats)
    monkeypatch.setattr(hero_context, "get_hero_photo_files", _hero_files)
    monkeypatch.setattr(render_reviewer, "review_post", review_post)
    monkeypatch.setattr(content, "generate_content", generate_content)
    monkeypatch.setattr(media, "storage", lambda: _Storage())
    monkeypatch.setattr(media, "list_media", list_media)
    calls["payloads"] = payloads
    return calls


def _backdrops(calls) -> list:
    return [c[2] for c in calls["clones"]]


@pytest.mark.asyncio
async def test_catalogue_images_are_never_backdrops(monkeypatch):
    """Even with a catalogue row and its picture available, the batch neither
    reads house_layouts nor fetches a catalogue image, and no post is built on
    one. The missing slots are drawn exactly as before."""
    calls = _install(monkeypatch, logo=None, stock=[])
    res = await tc.generate_template_samples(TENANT, n=4)
    assert res["count"] == 4
    assert not any("house_layouts" in q for q in calls["sql"])
    assert calls["fetched"] == []
    assert b"HOUSE" not in _backdrops(calls)
    assert not any(str(c[1]).startswith("house:") for c in calls["clones"])
    assert not any("house_catalogue" in p for p in calls["payloads"])
    assert calls["draw"] == tc._DRAWN_HEROES == 2
    assert not hasattr(tc, "_house_catalogue_images")


@pytest.mark.asyncio
async def test_found_heroes_never_returns_catalogue_or_logo(monkeypatch):
    calls = _install(monkeypatch, logo=b"LOGO", stock=[("https://unsplash/a", b"A"), ("k", b"")])
    got = await tc._found_heroes(TENANT, [("https://unsplash/a", b"A"), ("k", b"")])
    assert got == [("https://unsplash/a", b"A")]      # stock only, empty bytes dropped
    assert calls["fetched"] == [] and calls["sql"] == []


@pytest.mark.asyncio
async def test_logo_is_never_the_backdrop(monkeypatch):
    """The logo goes in the logo slot (clone_post), never under the whole post:
    with only a logo, both backdrop slots are drawn, as before this change."""
    calls = _install(monkeypatch, logo=b"LOGO", stock=[])
    emits: list[dict] = []
    res = await tc.generate_template_samples(TENANT, n=4, progress=emits.append)
    assert res["count"] == 4
    assert b"LOGO" not in _backdrops(calls)
    assert not any(str(c[1]).startswith("logo:") for c in calls["clones"])
    assert not any('"image_source": "logo"' in p for p in calls["payloads"])
    assert calls["draw"] == 2
    # The gap is stated from the first emit to the result.
    assert emits[0]["skipped_reason"] == "missing_photos"
    assert emits[0]["assets"] == {"logo_count": 1, "photo_count": 0, "photos_needed": 3}
    assert res["skipped_reason"] == "missing_photos" and res["assets"]["photo_count"] == 0


@pytest.mark.asyncio
async def test_logo_with_stock_builds_on_stock_only(monkeypatch):
    calls = _install(monkeypatch, logo=b"LOGO", stock=[("https://unsplash/a", b"A")])
    await tc.generate_template_samples(TENANT, n=4)
    assert calls["draw"] == 1                       # 2 slots - 1 stock photo
    keys = [c[1] for c in calls["clones"]]
    assert keys == ["https://unsplash/a", "", "https://unsplash/a", ""]
    assert [c[3] for c in calls["clones"]] == [False, True, False, True]
    assert b"LOGO" not in _backdrops(calls)


@pytest.mark.asyncio
async def test_stock_counts_as_found_and_keeps_its_credit_key(monkeypatch):
    calls = _install(monkeypatch, logo=None,
                     stock=[("https://unsplash/a", b"A"), ("https://unsplash/b", b"B")])
    res = await tc.generate_template_samples(TENANT, n=2)
    assert calls["draw"] == 0
    assert [c[1] for c in calls["clones"]] == ["https://unsplash/a", "https://unsplash/b"]
    assert all(c[3] is False for c in calls["clones"])   # nothing is "generated"
    assert {s["used_placeholder"] for s in res["samples"]} == {False}
    assert res["skipped_reason"] == "missing_logo_and_photos"


@pytest.mark.asyncio
async def test_nothing_found_draws_exactly_as_before(monkeypatch):
    calls = _install(monkeypatch, logo=None, stock=[])
    await tc.generate_template_samples(TENANT, n=6)
    assert calls["draw"] == tc._DRAWN_HEROES == 2
    assert all(c[1] == "" and c[3] is True for c in calls["clones"])


@pytest.mark.asyncio
async def test_a_brand_with_photos_is_left_alone(monkeypatch):
    calls = _install(monkeypatch, logo=b"LOGO", stock=[], photos=True)
    res = await tc.generate_template_samples(TENANT, n=3)
    assert calls["draw"] == 0
    assert all(c[1] == "" and c[2] is None for c in calls["clones"])   # clone_post picks
    assert res["skipped_reason"] == "missing_photos"     # one photo is still fewer than three
    assert res["assets"]["photo_count"] == 1


def test_image_source_names_drawn_and_missing_backdrops():
    assert tc._image_source("", True, b"x") == {"image_source": "generated"}
    assert tc._image_source("", False, None) == {"image_source": "none"}
