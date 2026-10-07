"""The competitor refresh cost controls that need a database to prove.

  * migration 070 moves the rows that were seeded daily to weekly the first
    time it is applied and never again (migrate.py re-applies every file on
    every deploy), and leaves a hand-set cadence alone;
  * a post whose media will not store stops buying Apify runs after
    MAX_MEDIA_FETCH_ATTEMPTS — a failed download counts, and so does a post the
    actor did not return, because the run was paid for either way — but a run
    that saw no post at all is an outage, and `force` resets the counter;
  * a competitor whose runs keep seeing no post stops buying them after
    MAX_MEDIA_OUTAGE_RUNS, until a run or a sync sees it again, or force;
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


async def _new_tenant(label: str) -> UUID:
    async with acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            f"ZZTEST-{label}", f"zztest-{label}-{datetime.now(UTC).timestamp()}")


async def _drop_tenant(tid: UUID) -> None:
    # Every DELETE names the tenant. RLS is not a scope here: the docker
    # test role is a superuser, superusers skip even FORCE'd policies, and an
    # unscoped DELETE under acquire(tid) emptied every tenant's competitors,
    # posts and brand profiles in a DB other sessions share.
    async with acquire(tid) as conn:
        # The media fetcher meters every actor run into provider_spend, whose
        # tenant_id is a foreign key — it has to go before the tenant does.
        await conn.execute("DELETE FROM provider_spend WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM competitor_posts WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM competitors WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM brand_profiles WHERE tenant_id = $1", tid)
    async with acquire() as conn:
        await conn.execute("DELETE FROM scheduled_jobs WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tid)


@pytest.fixture
async def tenant():
    tid = await _new_tenant("cr")
    yield tid
    await _drop_tenant(tid)


@pytest.fixture
async def other_tenant():
    tid = await _new_tenant("cr-other")
    yield tid
    await _drop_tenant(tid)


def _only_this_tenant(monkeypatch, tenant) -> None:
    """The roster read leaves tenancy to RLS, which the superuser test role
    skips, so without this the batch functions reset and re-rank OTHER
    sessions' competitors in the shared docker DB. Production runs as the
    non-BYPASSRLS james_app role, where RLS does this for real."""
    from james_os import competitors as competitors_mod

    real_list = competitors_mod.list_competitors

    async def scoped_list(*a, **k):
        return [c for c in await real_list(*a, **k)
                if str(c.get("tenant_id") or "") == str(tenant)]

    async def no_rerank(*a, **k):
        return []

    monkeypatch.setattr(competitors_mod, "list_competitors", scoped_list)
    monkeypatch.setattr(competitors_mod, "recompute_ranks", no_rerank)


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
            "SELECT post_id, media_fetch_attempts, stored_media_url FROM competitor_posts "
            "WHERE tenant_id = $1", tid)
    return {r["post_id"]: (r["media_fetch_attempts"], r["stored_media_url"]) for r in rows}


# ── clause 1: the one-off move from 24 to 168 ─────────────────────────

class _Undo(Exception):
    pass


async def test_migration_070_moves_daily_refresh_rows_to_weekly_once(tenant):
    """migrate.py re-applies every file on every run, and the deploy runs it.
    A bare UPDATE would revert an operator's deliberate 24 on every deploy.
    The first application is simulated by dropping the column inside a
    savepoint that is rolled back, so the shared test DB is left as it was."""
    sql = MIGRATION.read_text()

    async def _cadences(conn) -> dict:
        rows = await conn.fetch(
            "SELECT kind, cadence_hours FROM scheduled_jobs WHERE tenant_id = $1", tenant)
        return {r["kind"]: r["cadence_hours"] for r in rows}

    async with acquire() as conn:
        await conn.executemany(
            "INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours) VALUES ($1, $2, $3)",
            [(tenant, "competitor_refresh", 24),
             (tenant, "daily_brand_research", 24),   # other daily jobs stay daily
             (tenant, "competitor_sync", 48)])        # a hand-set cadence stays
        # Already applied here: re-running moves nothing.
        await conn.execute(sql)
        assert (await _cadences(conn))["competitor_refresh"] == 24
        try:
            async with conn.transaction():
                await conn.execute(
                    "ALTER TABLE competitor_posts DROP COLUMN media_fetch_attempts")
                await conn.execute(sql)              # the first application
                first = await _cadences(conn)
                # An operator then sets it back to daily on purpose ...
                await conn.execute(
                    "UPDATE scheduled_jobs SET cadence_hours = 24 "
                    "WHERE tenant_id = $1 AND kind = 'competitor_refresh'", tenant)
                await conn.execute(sql)              # ... and the next deploy
                await conn.execute(sql)
                after = await _cadences(conn)
                raise _Undo
        except _Undo:
            pass
    assert first == {"competitor_refresh": 168, "daily_brand_research": 24,
                     "competitor_sync": 48}
    assert after["competitor_refresh"] == 24, "a deploy reverted a hand-set cadence"


async def test_finishing_the_intake_seeds_a_weekly_competitor_refresh(tenant):
    from james_os.brands import upsert_brand_profile
    await upsert_brand_profile({"intake_done": True}, tenant_id=tenant)
    async with acquire() as conn:
        cadence = await conn.fetchval(
            "SELECT cadence_hours FROM scheduled_jobs WHERE tenant_id = $1 "
            "AND kind = 'competitor_refresh'", tenant)
    assert cadence == 168   # the profile row goes with the tenant fixture


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


async def test_force_gives_the_given_up_posts_another_three_tries(tenant, fake_apify, monkeypatch):
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
    _only_this_tenant(monkeypatch, tenant)
    r = await competitor_media.fetch_all_missing_media(limit=30, tenant_id=tenant, force=True)
    assert r["reset"] == 2   # DDD444, and BBB222's single strike
    got = await _attempts(tenant)
    assert got["DDD444"] == (0, "") and got["BBB222"] == (0, "")


async def test_the_status_counter_does_not_call_a_given_up_post_pending(tenant):
    """Read as a difference: studio_status leaves tenancy to RLS (production
    runs as the non-BYPASSRLS james_app role), and the docker test role is a
    superuser, so its absolute counts include whatever other sessions hold."""
    comp = await _competitor(tenant)
    before = await competitor_sync.studio_status(tenant_id=tenant)
    await _post(tenant, comp["id"], "AAA111")
    await _post(tenant, comp["id"], "BBB222", attempts=competitor_media.MAX_MEDIA_FETCH_ATTEMPTS)
    s = await competitor_sync.studio_status(tenant_id=tenant)

    def delta(k: str) -> int:
        return s[k] - before[k]
    assert (delta("posts"), delta("with_media"), delta("media_given_up")) == (2, 0, 1)
    assert delta("media_pending") == 1, "'12 still to fetch' forever was the bug"


# ── clause 1 again, at the batch level ────────────────────────────────

async def test_sync_all_reports_a_fresh_competitor_as_skipped_not_synced(tenant, monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("a provider was called for a fresh competitor")
    monkeypatch.setattr(competitor_sync, "refresh_profile", _boom)

    comp = await _competitor(tenant, "zztest-fresh-skip")
    async with acquire(tenant) as conn:
        await conn.execute(
            "UPDATE competitors SET last_synced_at = now() - interval '2 days' "
            "WHERE id = $1::uuid", comp["id"])

    _only_this_tenant(monkeypatch, tenant)
    r = await competitor_sync.sync_all(tenant_id=tenant)
    # Only this competitor's entry is asserted: sync_all's roster read leaves
    # tenancy to RLS, which the superuser test role skips, so another
    # session's competitors can appear here (and hit _boom, not a provider).
    mine = [x for x in r["results"] if x.get("handle") == "zztest-fresh-skip"]
    assert len(mine) == 1
    assert mine[0]["skipped"] == "synced within 6 days" and not mine[0].get("error")
    assert (mine[0].get("items_fetched", 0), mine[0].get("rows_new", 0),
            mine[0].get("stored", 0)) == (0, 0, 0)
    assert r["skipped"] >= 1


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


# ── a profile that never answers stops buying runs ────────────────────

async def _outages(tid: UUID, cid: str) -> int:
    async with acquire(tid) as conn:
        return await conn.fetchval(
            "SELECT media_outage_runs FROM competitors WHERE id = $1::uuid", cid)


async def _fresh_row(tid: UUID, cid: str) -> dict:
    from james_os.competitors import get_competitor
    return await get_competitor(cid, tenant_id=tid)


async def test_a_profile_that_never_answers_stops_buying_actor_runs(tenant, fake_apify):
    """A run that sees no post is an outage and never charges the posts — so a
    profile gone private used to buy one actor run per refresh, forever."""
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    fake_apify["items"] = []
    for n in range(1, competitor_media.MAX_MEDIA_OUTAGE_RUNS + 1):
        r = await competitor_media.fetch_media_for_competitor(
            await _fresh_row(tenant, comp["id"]), limit=30, tenant_id=tenant)
        assert "no usable posts" in r["error"] and r["actor_runs"] == 1
        assert await _outages(tenant, comp["id"]) == n
    runs = len(fake_apify["runs"])
    r = await competitor_media.fetch_media_for_competitor(
        await _fresh_row(tenant, comp["id"]), limit=30, tenant_id=tenant)
    assert r["actor_runs"] == 0 and "skipped" in r["note"]
    assert len(fake_apify["runs"]) == runs, "a capped competitor bought a run"
    # The posts were never charged for any of it.
    assert (await _attempts(tenant))["AAA111"] == (0, "")

    # force is the operator's way back, and a run that sees a post resets it.
    fake_apify["items"] = [_item("AAA111")]
    fake_apify["store_ok"] = True
    r = await competitor_media.fetch_media_for_competitor(
        await _fresh_row(tenant, comp["id"]), limit=30, tenant_id=tenant, force=True)
    assert r["stored"] == 1 and await _outages(tenant, comp["id"]) == 0


async def test_a_sync_that_brings_new_posts_lets_the_media_fetcher_try_again(
    tenant, monkeypatch,
):
    from james_os import competitor_apify
    comp = await _competitor(tenant, "zztest-yt")
    async with acquire(tenant) as conn:
        await conn.execute(
            "UPDATE competitors SET platform = 'youtube', media_outage_runs = $2 "
            "WHERE id = $1::uuid", comp["id"], competitor_media.MAX_MEDIA_OUTAGE_RUNS)

    async def _fetch_posts(platform, handle, limit, days):
        return [{"post_id": "YT0001", "url": "https://www.youtube.com/watch?v=YT0001",
                 "caption": "x", "media_type": "video", "media_url": "",
                 "thumbnail_url": "", "duration": 0, "likes": 1, "comments": 0,
                 "shares": 0, "views": 10, "posted_at": None}], ""
    monkeypatch.setattr(competitor_apify, "configured", lambda: True)
    monkeypatch.setattr(competitor_apify, "fetch_posts", _fetch_posts)

    r = await competitor_sync.sync_competitor(
        await _fresh_row(tenant, comp["id"]), store_media=False, tenant_id=tenant)
    assert r["error"] is None and r["rows_new"] == 1
    assert await _outages(tenant, comp["id"]) == 0


# ── clause 3: what reopens the cooldown, read from the real roster ────

async def test_only_a_tracked_never_synced_competitor_reopens_the_cooldown(
        tenant, other_tenant):
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
    # Tracked and never synced, but in another tenant. On a role that skips
    # RLS only the query's own tenant predicate keeps it out.
    foreign = await _competitor(other_tenant, "zztest-foreign-never-synced")
    assert await competitor_sync.unsynced_tracked_ids(tenant) == {fresh["id"]}
    assert await competitor_sync.unsynced_tracked_ids(other_tenant) == {foreign["id"]}

    competitor_sync._REFRESH_STATE.clear()
    try:
        assert competitor_sync.claim_refresh(tenant, "tick") is None
        competitor_sync.release_refresh(tenant, "tick")       # pulled: cooldown on
        # The peer was tracked after that chain: the BM2 call goes through,
        # narrowed to the never-synced peer.
        assert await competitor_sync.try_claim_refresh(tenant, "bm2") is None
        assert competitor_sync.refresh_state(tenant)["scope"] == "unsynced"
        # That run failed to pull it (a dead handle): the tick's cooldown is
        # put back, not restarted ...
        competitor_sync.release_refresh(tenant, "bm2", pulled=False)
        assert competitor_sync.refresh_state(tenant)["job_id"] == "tick"
        # ... and the peer, still never synced, is still never turned away.
        assert await competitor_sync.try_claim_refresh(tenant, "again") is None
        competitor_sync.release_refresh(tenant, "again", pulled=False)
        # Once it is synced, the cooldown holds again.
        async with acquire(tenant) as conn:
            await conn.execute("UPDATE competitors SET last_synced_at = now() "
                               "WHERE id = $1::uuid", fresh["id"])
        r = await competitor_sync.try_claim_refresh(tenant, "later")
        assert r["reused"] == "cooldown" and r["job_id"] == "tick"
    finally:
        competitor_sync._REFRESH_STATE.clear()


# ── a paid run that failed still counts toward the outage cap ─────────

@pytest.mark.parametrize("err, counts", [
    ("actor timed out (aborted)", True),
    ("actor finished FAILED", True),
    ("dataset HTTP 403", True),
    ("actor start HTTP 402: payment required", False),
    ("Apify is not configured (APIFY_API_KEY)", False),
])
async def test_a_started_run_that_failed_counts_as_an_outage(tenant, monkeypatch, err, counts):
    """A run that started and then timed out or failed was paid for; a
    profile whose actor kept timing out used to be bought again every refresh.
    A run Apify never started cost nothing and does not count."""
    async def _run_actor(actor, run_input):
        return [], err

    monkeypatch.setattr(settings, "apify_api_key", "zz-test-not-a-real-key")
    monkeypatch.setattr(competitor_media, "_run_actor", _run_actor)
    comp = await _competitor(tenant)
    await _post(tenant, comp["id"], "AAA111")
    row = await _fresh_row(tenant, comp["id"])
    r = await competitor_media.fetch_media_for_competitor(row, limit=30, tenant_id=tenant)
    assert r["error"] == err
    assert await _outages(tenant, comp["id"]) == (1 if counts else 0)
