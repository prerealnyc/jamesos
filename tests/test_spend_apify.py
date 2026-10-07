"""The competitor scrapes on the spend ledger, and the spend gate in front of
them.

What went wrong: every competitor sync and media fetch is paid — Xpoz
credits for Instagram/TikTok/X, a pay-per-result Apify actor for
YouTube/LinkedIn and for the media files — and none of it was recorded. The
ledger could say what an image cost but not what re-pulling an unchanged
shelf every day cost, and a brand over its cap (or PAUSE_SPEND) still bought
a full chain from the operator's button or BM2's Monday call.

Pinned here, against the local docker Postgres (:5433), with fake actor
runners so nothing reaches a provider:
  * one row per Apify actor run: units = items the actor returned (what Apify
    bills), est_usd from the per-result table, meta {items_fetched, rows_new}
    so the waste ratio is a query;
  * one row per Xpoz call, in requests, unpriced (the SDK reports no credits);
  * full_refresh / sync_all / fetch_all_missing_media and the refresh route
    skip — and never raise — over the cap or under PAUSE_SPEND.
Tenants are ZZTEST-sa and every DELETE names the tenant.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import BackgroundTasks

from james_os import (
    competitor_apify,
    competitor_media,
    competitor_sync,
    competitors,
    competitors_api,
    spend,
    trends,
    xpoz_api,
    xpoz_intel,
)
from james_os.apify import TrendItem
from james_os.config import settings
from james_os.db import acquire, set_request_tenant

# ── fixtures ──────────────────────────────────────────────────────────


async def _new_tenant() -> UUID:
    async with acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            "ZZTEST-sa", f"zztest-sa-{datetime.now(UTC).timestamp()}")


async def _drop_tenant(tid: UUID) -> None:
    # Named tenant on every DELETE: the docker role is a superuser, RLS is off.
    async with acquire(tid) as conn:
        for table in ("provider_spend", "competitor_posts", "competitors", "events"):
            await conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tid)
    async with acquire() as conn:
        await conn.execute("DELETE FROM scheduled_jobs WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tid)


@pytest.fixture
async def tenant(monkeypatch):
    monkeypatch.setattr(settings, "pause_spend", False)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 5.0)
    monkeypatch.setattr(settings, "apify_api_key", "zz-test-not-a-real-key")
    competitor_sync._REFRESH_STATE.clear()
    tid = await _new_tenant()
    yield tid
    set_request_tenant(None)
    competitor_sync._REFRESH_STATE.clear()
    await _drop_tenant(tid)


async def _competitor(tid: UUID, platform: str = "instagram",
                      handle: str = "zztest-peer") -> dict:
    async with acquire(tid) as conn:
        cid = await conn.fetchval(
            "INSERT INTO competitors (platform, handle, status) "
            "VALUES ($1, $2, 'tracked') RETURNING id", platform, handle)
    return {"id": str(cid), "platform": platform, "handle": handle}


async def _post(tid: UUID, cid: str, code: str) -> None:
    async with acquire(tid) as conn:
        await conn.execute(
            "INSERT INTO competitor_posts (competitor_id, platform, post_id, url, media_type) "
            "VALUES ($1::uuid, 'instagram', $2, $3, 'image')",
            cid, code, f"https://www.instagram.com/p/{code}/")


async def _rows(tid: UUID) -> list[dict]:
    async with acquire(tid) as conn:
        rows = await conn.fetch(
            "SELECT provider, model, units, unit_kind, est_usd, agent_or_job, meta "
            "FROM provider_spend WHERE tenant_id = $1 ORDER BY created_at, id", tid)
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["meta"], str):
            d["meta"] = json.loads(d["meta"])
        out.append(d)
    return out


async def _over_cap(tid: UUID) -> None:
    """Put the brand over its own cap: $0.01 a day, $0.05 already spent."""
    async with acquire(tid) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config, '{}'::jsonb) || "
            "'{\"spend\": {\"daily_cap_usd\": 0.01}}'::jsonb WHERE id = $1", tid)
    await spend.record("openai", "gpt-4o", 1, "tokens", 0.05, "zz.test", tenant_id=tid)


def _ig_item(code: str) -> dict:
    return {"shortCode": code, "type": "Image",
            "url": f"https://www.instagram.com/p/{code}/",
            "displayUrl": f"https://cdn.example/{code}.jpg"}


@pytest.fixture
def fake_media_actor(monkeypatch):
    state = {"runs": 0, "items": [], "err": ""}

    async def _run_actor(actor, run_input):
        state["runs"] += 1
        return list(state["items"]), state["err"]

    async def _store_media(url, tenant, kind, label):
        return f"https://durable.example/{label}.jpg", ""

    monkeypatch.setattr(competitor_media, "_run_actor", _run_actor)
    monkeypatch.setattr(competitor_sync, "_store_media", _store_media)
    return state


# ── the price table ───────────────────────────────────────────────────

@pytest.mark.nodb
def test_per_result_prices_are_estimates_and_unknown_actors_are_unpriced():
    assert spend.estimate_results_usd("apify~instagram-scraper", 1000) == pytest.approx(2.7)
    # The `user/name` spelling resolves to the same actor.
    assert spend.estimate_results_usd("streamers/youtube-scraper", 10) == pytest.approx(0.04)
    assert spend.estimate_results_usd("apify~instagram-scraper", 0) == 0
    assert spend.estimate_results_usd("someone~unknown-actor", 50) is None


# ── Apify: one row per actor run ──────────────────────────────────────

async def test_a_media_actor_run_is_one_row_counted_in_results_returned(
    tenant, fake_media_actor,
):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    await _post(tenant, comp["id"], "BBB222")
    # Three results paid for; one of them filled one of our rows.
    fake_media_actor["items"] = [_ig_item("AAA111"), _ig_item("ZZZ999"), _ig_item("YYY888")]

    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["stored"] == 1 and r["items_fetched"] == 3

    (row,) = await _rows(tenant)
    assert row["provider"] == "apify" and row["model"] == "apify~instagram-scraper"
    assert float(row["units"]) == 3 and row["unit_kind"] == "results"
    assert float(row["est_usd"]) == pytest.approx(3 * 0.0027)
    assert row["agent_or_job"] == "competitor_media.fetch"
    m = row["meta"]
    assert (m["items_fetched"], m["rows_new"]) == (3, 1)
    assert m["estimate"] is True and m["price_basis"] == "per_result_estimate"
    assert m["competitor_id"] == comp["id"]
    assert "tenant_fallback" not in m


async def test_a_media_run_that_failed_is_still_on_the_ledger(tenant, fake_media_actor):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    fake_media_actor["err"] = "actor timed out (aborted)"

    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["error"] == "actor timed out (aborted)"
    (row,) = await _rows(tenant)
    assert float(row["units"]) == 0 and float(row["est_usd"]) == 0
    assert row["meta"]["error"] == "actor timed out (aborted)"


async def test_a_youtube_sync_books_its_run_and_a_repull_shows_the_waste(tenant, monkeypatch):
    comp = await _competitor(tenant, "youtube", "zztest-yt")
    now = datetime.now(UTC).isoformat()
    videos = [{"id": f"zzvid{i:06d}", "title": f"v{i}", "date": now, "viewCount": 10}
              for i in range(4)]
    calls: list[str] = []

    async def _run_call(actor, key, payload):
        calls.append(actor)
        return list(videos), ""

    monkeypatch.setattr(competitor_apify, "_run_call", _run_call)

    r = await competitor_sync.sync_competitor(comp, store_media=False, tenant_id=tenant)
    assert r["error"] is None and r["rows_new"] == 4
    # The same four posts again: paid for, nothing new.
    r = await competitor_sync.sync_competitor(comp, store_media=False, tenant_id=tenant,
                                              force=True)
    assert r["rows_new"] == 0

    rows = await _rows(tenant)
    assert calls == ["streamers~youtube-scraper"] * 2
    assert [(r["provider"], r["model"], float(r["units"]), r["unit_kind"]) for r in rows] == [
        ("apify", "streamers~youtube-scraper", 4.0, "results")] * 2
    assert [(r["meta"]["items_fetched"], r["meta"]["rows_new"]) for r in rows] == [
        (4, 4), (4, 0)]
    assert all(float(r["est_usd"]) == pytest.approx(0.016) for r in rows)
    assert all(r["agent_or_job"] == "competitor_sync.sync" for r in rows)


async def test_a_linkedin_sync_that_ran_two_actors_writes_two_rows(tenant, monkeypatch):
    comp = await _competitor(tenant, "linkedin", "zztest-li")
    posts = [{"full_urn": f"urn:li:activity:zz{i}", "post_url": f"https://li.example/{i}",
              "text": "x", "stats": {"total_reactions": 3}} for i in range(2)]

    async def _run_call(actor, key, payload):
        # The company page has nothing; the person does.
        return ([] if actor.endswith("company-posts") else list(posts)), ""

    monkeypatch.setattr(competitor_apify, "_run_call", _run_call)

    r = await competitor_sync.sync_competitor(comp, store_media=False, tenant_id=tenant)
    assert r["error"] is None and r["rows_new"] == 2
    rows = await _rows(tenant)
    assert [(r["model"], float(r["units"]), r["meta"]["rows_new"]) for r in rows] == [
        ("apimaestro~linkedin-company-posts", 0.0, 0),
        ("apimaestro~linkedin-profile-posts", 2.0, 2),
    ]


# ── Xpoz: one row per call, in requests ───────────────────────────────

async def test_an_xpoz_sync_books_the_profile_read_and_the_posts_call(tenant, monkeypatch):
    import xpoz

    comp = await _competitor(tenant, "instagram", "zztest-ig")

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _verify(accounts):
        return [{"followers": 1000, "handle": "zztest-ig"}], []

    async def _fetch(client, platform, handle, limit, days):
        return [{"post_id": f"zzig{i}", "url": f"https://www.instagram.com/p/zzig{i}/",
                 "caption": "", "media_type": "image", "media_url": "",
                 "thumbnail_url": "", "duration": 0, "likes": 5, "comments": 1,
                 "shares": 0, "views": 0, "posted_at": None} for i in range(3)]

    monkeypatch.setattr(competitor_sync, "configured", lambda: True)
    monkeypatch.setattr(competitors, "verify_handles", _verify)
    monkeypatch.setattr(xpoz, "AsyncXpozClient", _Client)
    monkeypatch.setattr(competitor_sync, "_fetch_posts", _fetch)

    r = await competitor_sync.sync_competitor(comp, store_media=False, tenant_id=tenant)
    assert r["error"] is None and r["rows_new"] == 3

    rows = await _rows(tenant)
    assert [(r["provider"], r["model"], float(r["units"]), r["unit_kind"]) for r in rows] == [
        ("xpoz", "instagram.get_user", 1.0, "requests"),
        ("xpoz", "instagram.get_posts_by_user", 1.0, "requests"),
    ]
    posts_row = rows[1]
    assert (posts_row["meta"]["items_fetched"], posts_row["meta"]["rows_new"]) == (3, 3)
    # No credit figure comes back per call: recorded, not priced by a guess.
    assert all(float(r["est_usd"]) == 0 and r["meta"]["priced"] is False for r in rows)
    assert all(r["meta"]["credits_reported"] is False for r in rows)


async def test_an_xpoz_search_is_one_row_of_requests_per_platform(tenant, monkeypatch):
    async def _search(query, **kw):
        return {"query": query, "results": [], "count": 2, "filtered_out": 5, "errors": {}}

    monkeypatch.setattr(xpoz_intel, "search_social", _search)
    set_request_tenant(tenant)
    await xpoz_api.xpoz_search(xpoz_api.SocialSearchRequest(
        query="golf", platforms=["instagram", "tiktok", "not-a-platform"]))
    (row,) = await _rows(tenant)
    assert (row["provider"], row["model"], float(row["units"]), row["unit_kind"]) == (
        "xpoz", "search_posts", 2.0, "requests")
    assert row["meta"]["items_fetched"] == 7

    # A search that never reached Xpoz (no key) costs nothing and says nothing.
    async def _refused(query, **kw):
        return {"error": "No Xpoz API key configured."}

    monkeypatch.setattr(xpoz_intel, "search_social", _refused)
    await xpoz_api.xpoz_search(xpoz_api.SocialSearchRequest(query="golf"))
    assert len(await _rows(tenant)) == 1


# ── the watchlist scrape ──────────────────────────────────────────────

async def test_the_watchlist_scrape_books_one_row_per_actor_and_what_was_new(
    tenant, monkeypatch,
):
    now = datetime.now(UTC).isoformat()

    class _Provider:
        name = "apify"

        async def scrape_handles(self, handles, limit):
            return [TrendItem(platform="instagram", handle="zzc", url=f"https://ig.example/{i}",
                              views=100 * (i + 1), posted_at=now) for i in range(2)]

    monkeypatch.setattr(trends, "get_trend_provider", lambda: _Provider())
    handles = {"instagram": ["zzc"], "youtube": ["zzyt"], "twitter": ["zzx"]}

    await trends.refresh_watchlist(handles, 5, tenant_id=tenant)
    await trends.refresh_watchlist(handles, 5, tenant_id=tenant)

    rows = await _rows(tenant)
    # twitter has no actor: no run, no row. youtube ran and returned nothing.
    got = [(r["model"], float(r["units"]), r["meta"]["rows_new"]) for r in rows]
    assert got == [
        ("apify~instagram-scraper", 2.0, 2), ("streamers~youtube-scraper", 0.0, 0),
        ("apify~instagram-scraper", 2.0, 0), ("streamers~youtube-scraper", 0.0, 0),
    ]
    assert all(r["agent_or_job"] == "trends.refresh_watchlist" for r in rows)


# ── the gate ──────────────────────────────────────────────────────────

async def test_over_the_cap_the_paid_stages_skip_and_buy_nothing(
    tenant, fake_media_actor, monkeypatch,
):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    fake_media_actor["items"] = [_ig_item("AAA111")]
    await _over_cap(tenant)

    synced: list = []

    async def _sync_one(*a, **kw):
        synced.append(a)
        return {}

    monkeypatch.setattr(competitor_sync, "sync_competitor", _sync_one)

    media = await competitor_media.fetch_all_missing_media(tenant_id=tenant)
    assert "daily spend cap reached" in media["spend_blocked"]
    assert media["actor_runs"] == 0 and fake_media_actor["runs"] == 0

    sync = await competitor_sync.sync_all(tenant_id=tenant)
    assert "daily spend cap reached" in sync["spend_blocked"]
    assert sync["synced"] == 0 and synced == []

    chain = await competitor_sync.full_refresh(tenant_id=tenant)
    assert chain["skipped"] == "spend" and "daily spend cap" in chain["reason"]
    # Refused before the claim: no slot held, no cooldown started.
    assert competitor_sync.refresh_state(tenant) is None
    # The only row is the one the test wrote to go over the cap.
    assert [r["provider"] for r in await _rows(tenant)] == ["openai"]


async def test_pause_spend_refuses_the_refresh_route_in_its_reply(tenant, monkeypatch):
    monkeypatch.setattr(settings, "pause_spend", True)
    set_request_tenant(tenant)

    bg = BackgroundTasks()
    r = await competitors_api.competitors_refresh(bg, limit=30, force=True)
    assert r["status"] == "skipped" and r["skipped"] == "spend"
    assert r["reason"] == "PAUSE_SPEND is set" and r["job_id"] is None
    assert bg.tasks == [] and competitor_sync.refresh_state(tenant) is None

    # And the scheduler's way in, which never passes through the route.
    chain = await competitor_sync.full_refresh(tenant_id=tenant, force=True)
    assert chain["skipped"] == "spend"
    assert competitor_sync.refresh_state(tenant) is None


async def test_under_the_cap_nothing_is_skipped(tenant, fake_media_actor, monkeypatch):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    fake_media_actor["items"] = [_ig_item("AAA111")]

    # list_competitors relies on RLS, which the superuser test role skips: left
    # alone it would hand this tenant every session's tracked competitors.
    async def _mine(status="", tenant_id=None, **kw):
        return [comp]

    monkeypatch.setattr(competitors, "list_competitors", _mine)
    r = await competitor_media.fetch_all_missing_media(tenant_id=tenant)
    assert "spend_blocked" not in r and r["actor_runs"] == 1 and r["stored"] == 1
