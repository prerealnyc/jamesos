"""The competitor refresh cost controls that need a database to prove.

  * migration 070 moves the rows that were seeded daily to weekly, once, and
    leaves a hand-set cadence alone;
  * a post whose media will not store stops buying Apify runs after
    MAX_MEDIA_FETCH_ATTEMPTS — a failed download counts, and so does a post the
    actor did not return, because the run was paid for either way — but a run
    that saw no post at all is an outage, and `force` resets the counter;
  * sync_all reports a fresh competitor as skipped, not synced.

Runs against the local docker Postgres (:5433). Rows are ZZTEST- prefixed and
removed afterwards; nothing here touches a provider.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from james_os import competitor_media, competitor_sync
from james_os.config import settings
from james_os.db import acquire

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "070_competitor_media_attempts.sql"


@pytest.fixture
async def tenant():
    async with acquire() as conn:
        tid = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            "ZZTEST-competitor-refresh", f"zztest-cr-{datetime.now(UTC).timestamp()}")
    yield tid
    async with acquire(tid) as conn:
        await conn.execute("DELETE FROM competitor_posts")
        await conn.execute("DELETE FROM competitors")
    async with acquire() as conn:
        await conn.execute("DELETE FROM scheduled_jobs WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tid)


async def _competitor(tid: UUID, handle: str = "zztest-peer") -> dict:
    async with acquire(tid) as conn:
        cid = await conn.fetchval(
            "INSERT INTO competitors (platform, handle, status) "
            "VALUES ('instagram', $1, 'tracked') RETURNING id", handle)
    return {"id": str(cid), "platform": "instagram", "handle": handle}


async def _post(tid: UUID, cid: str, code: str, attempts: int = 0) -> str:
    async with acquire(tid) as conn:
        return str(await conn.fetchval(
            "INSERT INTO competitor_posts (competitor_id, platform, post_id, url, "
            " media_type, media_fetch_attempts) "
            "VALUES ($1::uuid, 'instagram', $2, $3, 'image', $4) RETURNING id",
            cid, code, f"https://www.instagram.com/p/{code}/", attempts))


async def _attempts(tid: UUID) -> dict[str, tuple[int, str]]:
    async with acquire(tid) as conn:
        rows = await conn.fetch(
            "SELECT post_id, media_fetch_attempts, stored_media_url FROM competitor_posts")
    return {r["post_id"]: (r["media_fetch_attempts"], r["stored_media_url"]) for r in rows}


# ── clause 1: the one-off move from 24 to 168 ─────────────────────────

async def test_migration_070_moves_daily_refresh_rows_to_weekly_once(tenant):
    sql = MIGRATION.read_text()
    async with acquire() as conn:
        await conn.executemany(
            "INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours) VALUES ($1, $2, $3)",
            [(tenant, "competitor_refresh", 24),
             (tenant, "daily_brand_research", 24),   # other daily jobs stay daily
             (tenant, "competitor_sync", 48)])        # a hand-set cadence stays
        await conn.execute(sql)
        await conn.execute(sql)   # idempotent: migrate.py re-applies every file
        rows = await conn.fetch(
            "SELECT kind, cadence_hours FROM scheduled_jobs WHERE tenant_id = $1", tenant)
    got = {r["kind"]: r["cadence_hours"] for r in rows}
    assert got == {"competitor_refresh": 168, "daily_brand_research": 24,
                   "competitor_sync": 48}


async def test_finishing_the_intake_seeds_a_weekly_competitor_refresh(tenant):
    from james_os.brands import upsert_brand_profile
    await upsert_brand_profile({"intake_done": True}, tenant_id=tenant)
    async with acquire() as conn:
        cadence = await conn.fetchval(
            "SELECT cadence_hours FROM scheduled_jobs WHERE tenant_id = $1 "
            "AND kind = 'competitor_refresh'", tenant)
    assert cadence == 168
    async with acquire(tenant) as conn:
        await conn.execute("DELETE FROM brand_profiles")


# ── clause 2: give up after three paid runs ───────────────────────────

@pytest.fixture
def fake_apify(monkeypatch):
    """A scrape that costs nothing: the actor 'returns' whatever the test
    says, and the download fails or succeeds on command."""
    state = {"runs": [], "items": [], "store_ok": False}

    async def _run_actor(actor, run_input):
        state["runs"].append(run_input)
        return list(state["items"]), ""

    async def _store_media(url, tenant, kind, label):
        return (f"https://durable.example/{label}.jpg", "") if state["store_ok"] \
            else ("", "HTTPStatusError")

    monkeypatch.setattr(settings, "apify_api_key", "zz-test-not-a-real-key")
    monkeypatch.setattr(competitor_media, "_run_actor", _run_actor)
    monkeypatch.setattr(competitor_sync, "_store_media", _store_media)
    return state


def _item(code: str) -> dict:
    return {"shortCode": code, "type": "Image",
            "url": f"https://www.instagram.com/p/{code}/",
            "displayUrl": f"https://cdn.example/{code}.jpg"}


async def test_a_failed_download_and_an_unreturned_post_both_count_as_an_attempt(
    tenant, fake_apify,
):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")              # returned, download fails
    await _post(tenant, comp["id"], "CCC333")              # never returned
    await _post(tenant, comp["id"], "BBB222", attempts=3)  # already given up
    fake_apify["items"] = [_item("AAA111")]

    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["error"] is None
    assert (r["stored"], r["failed"], r["items_fetched"], r["rows_new"]) == (0, 1, 1, 0)
    # The given-up post was not asked for: two missing, so the actor was asked
    # for the floor of 30, not 3.
    assert r["was_missing"] == 2 and fake_apify["runs"][-1]["resultsLimit"] == 30
    got = await _attempts(tenant)
    assert got["AAA111"] == (1, "")
    assert got["CCC333"] == (1, "")
    assert got["BBB222"] == (3, ""), "a post we gave up on is left alone"

    # A download that succeeds fills the row and stops counting against it.
    fake_apify["store_ok"] = True
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert (r["stored"], r["rows_new"]) == (1, 1)
    got = await _attempts(tenant)
    assert got["AAA111"][0] == 1 and got["AAA111"][1].startswith("https://durable.example/")
    assert got["CCC333"] == (2, "")

    # Third strike for the post the actor never returns ...
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert (await _attempts(tenant))["CCC333"] == (3, "")
    # ... and after it, nothing is missing and NO actor run is bought.
    runs_before = len(fake_apify["runs"])
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["note"] == "nothing missing media" and r["items_fetched"] == 0
    assert len(fake_apify["runs"]) == runs_before


async def test_an_actor_that_did_not_run_is_an_outage_not_an_attempt(
    tenant, fake_apify, monkeypatch,
):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")

    async def _dead_actor(actor, run_input):
        return [], "actor finished FAILED"
    monkeypatch.setattr(competitor_media, "_run_actor", _dead_actor)

    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["error"] == "actor finished FAILED" and r["items_fetched"] == 0
    assert (await _attempts(tenant))["AAA111"] == (0, "")


async def test_a_run_that_saw_no_post_at_all_is_an_outage_not_an_attempt(
    tenant, fake_apify,
):
    """A login wall, a rate limit or a profile gone private all come back
    SUCCEEDED with [] or with error items that carry no shortCode. Three of
    those (three weekly refreshes) used to abandon every missing post for the
    competitor, and nothing ever reset the counter."""
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    await _post(tenant, comp["id"], "CCC333")

    fake_apify["items"] = []
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert "no usable posts" in r["error"] and r["items_fetched"] == 0
    assert (await _attempts(tenant))["AAA111"] == (0, "")

    fake_apify["items"] = [{"error": "no_items", "errorDescription": "login required"},
                           {"shortCode": "", "displayUrl": ""}]
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert "no usable posts" in r["error"] and r["items_fetched"] == 2
    assert (await _attempts(tenant))["AAA111"] == (0, "")
    assert (await _attempts(tenant))["CCC333"] == (0, "")

    # Once the actor shows it can read the profile — one real post back — a
    # post it did not return IS charged: the profile is readable and that post
    # is simply not among what a scrape reaches.
    fake_apify["items"] = [_item("ZZZ999")]   # real, but not one we are missing
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["error"] is None and r["items_fetched"] == 1
    assert (await _attempts(tenant))["AAA111"] == (1, "")
    assert (await _attempts(tenant))["CCC333"] == (1, "")


async def test_force_gives_the_given_up_posts_another_three_tries(tenant, fake_apify):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111", attempts=3)
    await _post(tenant, comp["id"], "BBB222", attempts=3)
    other = await _competitor(tenant, handle="zztest-other")
    await _post(tenant, other["id"], "DDD444", attempts=3)   # not this competitor's
    fake_apify["items"] = [_item("AAA111")]
    fake_apify["store_ok"] = True

    # Without force: nothing eligible, no run bought.
    r = await competitor_media.fetch_media_for_competitor(comp, limit=30, tenant_id=tenant)
    assert r["note"] == "nothing missing media" and fake_apify["runs"] == []

    r = await competitor_media.fetch_media_for_competitor(
        comp, limit=30, tenant_id=tenant, force=True)
    assert r["reset"] == 2 and len(fake_apify["runs"]) == 1
    assert (r["stored"], r["rows_new"]) == (1, 1)
    got = await _attempts(tenant)
    assert got["AAA111"][0] == 0 and got["AAA111"][1].startswith("https://durable.example/")
    assert got["BBB222"] == (1, ""), "reset to 0, then charged once by this run"
    assert got["DDD444"] == (3, ""), "another competitor's posts are not reset"

    # The batch form threads it through too.
    fake_apify["items"] = []
    r = await competitor_media.fetch_all_missing_media(limit=30, tenant_id=tenant, force=True)
    assert r["reset"] == 2   # DDD444, and BBB222's single strike
    got = await _attempts(tenant)
    assert got["DDD444"] == (0, "") and got["BBB222"] == (0, "")


async def test_the_status_counter_does_not_call_a_given_up_post_pending(tenant):
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    await _post(tenant, comp["id"], "BBB222", attempts=competitor_media.MAX_MEDIA_FETCH_ATTEMPTS)
    s = await competitor_sync.studio_status(tenant_id=tenant)
    assert (s["posts"], s["with_media"], s["media_given_up"]) == (2, 0, 1)
    assert s["media_pending"] == 1, "'12 still to fetch' forever was the bug"


# ── clause 1 again, at the batch level ────────────────────────────────

async def test_sync_all_reports_a_fresh_competitor_as_skipped_not_synced(tenant, monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("a provider was called for a fresh competitor")
    monkeypatch.setattr(competitor_sync, "refresh_profile", _boom)

    comp = await _competitor(tenant)
    async with acquire(tenant) as conn:
        await conn.execute(
            "UPDATE competitors SET last_synced_at = now() - interval '2 days' "
            "WHERE id = $1::uuid", comp["id"])

    r = await competitor_sync.sync_all(tenant_id=tenant)
    assert (r["synced"], r["skipped"]) == (0, 1)
    assert (r["items_fetched"], r["rows_new"], r["posts_stored"]) == (0, 0, 0)
    assert r["results"][0]["skipped"] == "synced within 6 days"


# ── the seam the spend ledger reads: fetched vs genuinely new ─────────

async def test_an_upsert_says_whether_the_post_was_new_or_already_held(tenant):
    """`rows_new` is what separates "30 items fetched" from "0 of them new" —
    the waste ratio the ledger exists to show. (xmax = 0) is true only for a
    row the statement inserted."""
    comp = await _competitor(tenant)
    post = {"post_id": "NEW001", "url": "https://www.instagram.com/p/NEW001/",
            "caption": "x", "media_type": "image", "media_url": "", "thumbnail_url": "",
            "duration": 0, "likes": 1, "comments": 0, "shares": 0, "views": 0,
            "posted_at": None}
    async with acquire(tenant) as conn:
        first = await competitor_sync._upsert_post(conn, comp["id"], "instagram", post, 100)
        again = await competitor_sync._upsert_post(conn, comp["id"], "instagram",
                                                   {**post, "likes": 2}, 100)
    assert first["is_new"] is True
    assert again["is_new"] is False and again["likes"] == 2


# ── clause 3: what reopens the cooldown, read from the real roster ────

async def test_only_a_tracked_never_synced_competitor_reopens_the_cooldown(tenant):
    """The query behind try_claim_refresh: tracked AND never synced, inside
    this tenant. A candidate is not work the chain would do; a synced peer
    was already bought; another tenant's peer is not this tenant's work."""
    fresh = await _competitor(tenant, "zztest-never-synced")
    synced = await _competitor(tenant, "zztest-synced")
    async with acquire(tenant) as conn:
        await conn.execute("UPDATE competitors SET last_synced_at = now() "
                           "WHERE id = $1::uuid", synced["id"])
        await conn.execute("INSERT INTO competitors (platform, handle, status) "
                           "VALUES ('instagram', 'zztest-candidate', 'candidate')")
    assert await competitor_sync.unsynced_tracked_ids(tenant) == {fresh["id"]}

    competitor_sync._REFRESH_STATE.clear()
    try:
        assert competitor_sync.claim_refresh(tenant, "tick") is None
        competitor_sync.release_refresh(tenant, "tick")       # left nothing unsynced
        # The peer was tracked after that chain: the BM2 call goes through.
        assert await competitor_sync.try_claim_refresh(tenant, "bm2") is None
        competitor_sync.release_refresh(tenant, "bm2", left_unsynced={fresh["id"]})
        # The chain tried it and it is still unsynced (a dead handle): the
        # cooldown holds, or that handle would make every call a chain.
        r = await competitor_sync.try_claim_refresh(tenant, "again")
        assert r["reused"] == "cooldown" and r["job_id"] == "bm2"
    finally:
        competitor_sync._REFRESH_STATE.clear()
