"""The competitor refresh costs money every time it runs, so WHEN it runs is a
cost control, not a scheduling detail.

What went wrong: brands.py seeded competitor_refresh DAILY for every tenant,
sync_competitor had no idea when it last ran, and POST /competitors/refresh
minted a fresh chain on every call — the scheduler, BM2's Monday job and the
operator's button each paid for a full pull of every competitor, on content
that barely changes day to day. Pure-logic half; the DB half is
test_competitor_refresh_db.py.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from james_os import brands, competitor_sync, competitors_api
from james_os.db import set_request_tenant

pytestmark = pytest.mark.nodb


# ── clause 1: the seed, and the recency skip ──────────────────────────

def test_the_intake_seeds_competitor_refresh_weekly_not_daily():
    jobs = dict(brands.INTAKE_JOBS)
    assert jobs["competitor_refresh"] == 168, "daily was a few hundred paid runs a week"
    # It is still first: a job with no last_run_at runs immediately, so a
    # brand finishing intake still sees a shelf the same day.
    assert brands.INTAKE_JOBS[0][0] == "competitor_refresh"
    # The seed and the migration that moves old rows must agree.
    from pathlib import Path
    sql = Path(__file__).resolve().parents[1] / "migrations" / "070_competitor_media_attempts.sql"
    assert "SET cadence_hours = 168" in sql.read_text()


def test_fresh_means_within_six_days_and_unknown_means_not_fresh():
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.SYNC_FRESH_DAYS == 6
    assert competitor_sync.is_fresh(now - timedelta(days=1), now=now)
    assert competitor_sync.is_fresh(now - timedelta(days=5, hours=23), now=now)
    assert not competitor_sync.is_fresh(now - timedelta(days=6, minutes=1), now=now)
    # competitors._row hands the timestamp over as an ISO string.
    assert competitor_sync.is_fresh((now - timedelta(hours=3)).isoformat(), now=now)
    assert not competitor_sync.is_fresh((now - timedelta(days=30)).isoformat(), now=now)
    # Never synced, or undatable: must be synced, never silently skipped.
    assert not competitor_sync.is_fresh(None, now=now)
    assert not competitor_sync.is_fresh("", now=now)
    assert not competitor_sync.is_fresh("last tuesday", now=now)


async def test_a_recently_synced_competitor_costs_nothing_unless_forced(monkeypatch):
    """The skip sits BEFORE the profile re-read and the post pull — both are
    paid — and `force` is the only way past it."""
    async def _boom(*a, **k):
        raise AssertionError("a provider was called for a fresh competitor")
    monkeypatch.setattr(competitor_sync, "refresh_profile", _boom)
    # With force the call proceeds into the normal path; with no key that is
    # the configured() refusal, which proves the gate was passed without a
    # network call.
    monkeypatch.setattr(competitor_sync, "configured", lambda: False)

    fresh = {"id": str(uuid4()), "platform": "instagram", "handle": "@someone",
             "last_synced_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    r = await competitor_sync.sync_competitor(fresh)
    assert r["skipped"] == "synced within 6 days"
    assert r["error"] is None and r["fetched"] == 0 and r["items_fetched"] == 0

    r = await competitor_sync.sync_competitor(fresh, force=True)
    assert "skipped" not in r
    assert r["error"] == "No Xpoz API key configured."

    stale = {**fresh, "last_synced_at": (datetime.now(UTC) - timedelta(days=8)).isoformat()}
    r = await competitor_sync.sync_competitor(stale)
    assert "skipped" not in r


# ── clause 3: one refresh per tenant, one per half hour ───────────────
#
# The clock lives in competitor_sync, at full_refresh itself, not in the HTTP
# route: the first cut gated only the route, and the scheduler's tick never
# passes through a route, so a tick and BM2's Monday call in the same half
# hour still ran two chains.

@pytest.fixture
def unsynced(monkeypatch):
    """The tenant's tracked-but-never-synced competitor ids, as the gate reads
    them. A set the test mutates; nothing here touches a database."""
    ids: set[str] = set()

    async def _read(tenant_id=None):
        return set(ids)
    monkeypatch.setattr(competitor_sync, "unsynced_tracked_ids", _read)
    return ids


@pytest.fixture
def clean_refresh_state(unsynced):
    competitors_api._REFRESH_JOBS.clear()
    competitor_sync._REFRESH_STATE.clear()
    yield
    competitors_api._REFRESH_JOBS.clear()
    competitor_sync._REFRESH_STATE.clear()
    set_request_tenant(None)


@pytest.fixture
def quiet_chain(monkeypatch):
    """Every stage of the chain stubbed to do nothing and cost nothing;
    `calls` records what ran."""
    calls: dict = {"sync_all": 0, "synced": 0}

    async def _sync_all(**kw):
        calls["sync_all"] += 1
        hook = calls.get("during_sync")
        if hook:
            await hook()
        # `synced` is how many competitors the stubbed pull "got"; 0 is a
        # chain that pulled nothing, which must not start the cooldown.
        return {"posts_stored": 0, "synced": calls["synced"]}
    async def _nothing(*a, **kw):
        return {}
    from james_os import competitor_gap, competitor_media, competitor_profile, competitor_vision
    monkeypatch.setattr(competitor_sync, "sync_all", _sync_all)
    monkeypatch.setattr(competitor_media, "fetch_all_missing_media", _nothing)
    monkeypatch.setattr(competitor_vision, "analyze_all", _nothing)
    monkeypatch.setattr(competitor_profile, "build_all_profiles", _nothing)
    monkeypatch.setattr(competitor_gap, "content_gap", _nothing)
    monkeypatch.setattr(competitor_sync, "studio_status", _nothing)
    return calls


@pytest.fixture
def no_stages(monkeypatch):
    """Every paid stage of the chain raises if reached. A refresh the clock
    turns away must never get this far."""
    async def _boom(*a, **k):
        raise AssertionError("a chain stage ran for a refresh the clock should have refused")
    monkeypatch.setattr(competitor_sync, "sync_all", _boom)
    monkeypatch.setattr(competitor_sync, "studio_status", _boom)
    return _boom


def test_the_clock_claims_once_and_cools_down_for_half_an_hour(clean_refresh_state):
    tid = uuid4()
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.REFRESH_COOLDOWN == timedelta(minutes=30)
    assert competitor_sync.claim_refresh(tid, "a", now=t0) is None
    # Re-entering your own claim is a no-op (the route claims, then
    # full_refresh claims again under the same id).
    assert competitor_sync.claim_refresh(tid, "a", now=t0) is None
    # Anyone else gets the running job back — force included.
    r = competitor_sync.claim_refresh(tid, "b", now=t0 + timedelta(minutes=5))
    assert r == {"job_id": "a", "reused": "in_flight", "note": r["note"]}
    assert competitor_sync.claim_refresh(tid, "b", force=True, now=t0)["reused"] == "in_flight"
    # Another tenant is another shelf.
    assert competitor_sync.claim_refresh(uuid4(), "c", now=t0) is None

    # Only the holder can release; a stranger's release is ignored.
    competitor_sync.release_refresh(tid, "b", now=t0 + timedelta(minutes=9))
    assert competitor_sync.refresh_state(tid)["finished_at"] is None
    competitor_sync.release_refresh(tid, "a", now=t0 + timedelta(minutes=10))

    r = competitor_sync.claim_refresh(tid, "b", now=t0 + timedelta(minutes=20))
    assert r["reused"] == "cooldown" and r["job_id"] == "a" and "force=true" in r["note"]
    # The operator saying "again" is honoured ...
    assert competitor_sync.claim_refresh(tid, "b", force=True, now=t0 + timedelta(minutes=20)) is None
    assert competitor_sync.refresh_state(tid)["job_id"] == "b"
    competitor_sync.release_refresh(tid, "b", now=t0 + timedelta(minutes=21))
    # ... and so is the clock.
    assert competitor_sync.claim_refresh(tid, "d", now=t0 + timedelta(minutes=51)) is None


def test_a_claim_nobody_released_expires_instead_of_blocking_the_tenant(clean_refresh_state):
    tid = uuid4()
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.claim_refresh(tid, "killed-worker", now=t0) is None
    late = t0 + competitor_sync.REFRESH_MAX_RUNTIME + timedelta(minutes=1)
    assert competitor_sync.claim_refresh(tid, "next-week", now=late) is None
    assert competitor_sync.refresh_state(tid)["job_id"] == "next-week"


async def test_a_second_refresh_call_gets_the_running_job_back(clean_refresh_state):
    tid = uuid4()
    set_request_tenant(tid)
    first = await competitors_api.competitors_refresh(BackgroundTasks(), limit=30)
    assert first["status"] == "running" and "reused" not in first
    # The route claimed BEFORE answering, so the scheduler now sees it too.
    assert competitor_sync.refresh_state(tid)["job_id"] == first["job_id"]

    again = await competitors_api.competitors_refresh(BackgroundTasks(), limit=30)
    assert again["job_id"] == first["job_id"]
    assert again["reused"] == "in_flight" and again["status"] == "running"

    # force cannot stack a second chain on a running one.
    forced = await competitors_api.competitors_refresh(BackgroundTasks(), force=True)
    assert forced["job_id"] == first["job_id"] and forced["reused"] == "in_flight"

    # Another tenant is another shelf: its own job.
    set_request_tenant(uuid4())
    other = await competitors_api.competitors_refresh(BackgroundTasks())
    assert other["job_id"] != first["job_id"] and "reused" not in other


async def test_the_scheduler_tick_does_not_run_a_chain_the_route_already_has_in_flight(
    clean_refresh_state, no_stages,
):
    """The reviewer's case: BM2's Monday call (the route) and the BM1 tick
    (run_competitor_refresh) inside one half hour, one tenant."""
    tid = uuid4()
    set_request_tenant(tid)
    route = await competitors_api.competitors_refresh(BackgroundTasks())
    # The tick runs the scheduler entry directly — no route, no job store.
    await competitor_sync.run_competitor_refresh(tid, {})
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["skipped"] == "in_flight" and r["reused_job_id"] == route["job_id"]
    assert r["stages"] == {}
    # Not the other way round either: the tick's own claim turns the route away,
    # and the route says so with a job_id this process's store has never held.
    competitor_sync._REFRESH_STATE.clear()
    competitor_sync.claim_refresh(tid, "refresh-from-the-tick")
    r = await competitors_api.competitors_refresh(BackgroundTasks())
    assert r["job_id"] == "refresh-from-the-tick" and r["reused"] == "in_flight"
    assert r["status"] == "running"
    # ... and GET /competitors/refresh/{that id} answers with the data, not a 404.
    monkeypatch_status = {"posts": 0}
    async def _status(tenant_id=None):
        return dict(monkeypatch_status)
    competitor_sync.studio_status = _status   # restored by no_stages' monkeypatch teardown
    got = await competitors_api.competitors_refresh_job("refresh-from-the-tick")
    assert got["status"] == "unknown" and got["posts"] == 0


async def test_a_refresh_that_just_finished_is_reused_for_half_an_hour(
    clean_refresh_state, no_stages,
):
    tid = uuid4()
    set_request_tenant(tid)
    competitors_api._REFRESH_JOBS["j1"] = {"status": "done"}
    competitor_sync._REFRESH_STATE[str(tid)] = {
        "job_id": "j1", "started_at": datetime.now(UTC) - timedelta(minutes=20),
        "finished_at": datetime.now(UTC) - timedelta(minutes=10)}

    r = await competitors_api.competitors_refresh(BackgroundTasks())
    assert r["job_id"] == "j1" and r["reused"] == "cooldown" and r["status"] == "done"
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["skipped"] == "cooldown" and r["reused_job_id"] == "j1"
    # A job the store has already pruned still cools down: the stamp is what
    # counts, not the job record.
    competitors_api._REFRESH_JOBS.clear()
    r = await competitors_api.competitors_refresh(BackgroundTasks())
    assert r["reused"] == "cooldown" and r["status"] == "unknown"
    # force past the cooldown is honoured and takes the slot.
    r = await competitors_api.competitors_refresh(BackgroundTasks(), force=True)
    assert "reused" not in r and competitor_sync.refresh_state(tid)["job_id"] == r["job_id"]


async def test_the_chain_releases_the_slot_when_it_ends_so_the_cooldown_starts(
    clean_refresh_state, monkeypatch,
):
    """Every stage is best-effort and caught, but the release is in a finally
    on purpose: a chain that raised still spent its money."""
    seen: dict = {}

    async def _sync_all(**kw):
        seen.update(kw)
        return {"posts_stored": 0}
    async def _fail_status(tenant_id=None):
        raise RuntimeError("db went away")
    async def _nothing(**kw):
        return {}
    from james_os import competitor_gap, competitor_media, competitor_profile, competitor_vision
    monkeypatch.setattr(competitor_sync, "sync_all", _sync_all)
    monkeypatch.setattr(competitor_media, "fetch_all_missing_media", _nothing)
    monkeypatch.setattr(competitor_vision, "analyze_all", _nothing)
    monkeypatch.setattr(competitor_profile, "build_all_profiles", _nothing)
    monkeypatch.setattr(competitor_gap, "content_gap", _nothing)
    monkeypatch.setattr(competitor_sync, "studio_status", _fail_status)

    tid = uuid4()
    with pytest.raises(RuntimeError):
        await competitor_sync.full_refresh(tenant_id=tid, limit=12, force=True)
    assert seen["force"] is True and seen["limit"] == 12
    st = competitor_sync.refresh_state(tid)
    assert st["finished_at"] is not None
    assert datetime.now(UTC) - st["finished_at"] < timedelta(seconds=5)
    # Now it cools down rather than running again.
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["skipped"] == "cooldown"


async def test_force_reaches_the_media_fetcher_not_only_the_sync(
    clean_refresh_state, monkeypatch,
):
    """`force` on the route used to stop at sync_all, so an operator could
    not un-give-up the posts the media fetcher had abandoned."""
    seen: dict = {}

    async def _nothing(**kw):
        return {}
    async def _media(**kw):
        seen.update(kw)
        return {"stored": 1}
    async def _status(tenant_id=None):
        return {}
    from james_os import competitor_gap, competitor_media, competitor_profile, competitor_vision
    monkeypatch.setattr(competitor_sync, "sync_all", _nothing)
    monkeypatch.setattr(competitor_media, "fetch_all_missing_media", _media)
    monkeypatch.setattr(competitor_vision, "analyze_all", _nothing)
    monkeypatch.setattr(competitor_profile, "build_all_profiles", _nothing)
    monkeypatch.setattr(competitor_gap, "content_gap", _nothing)
    monkeypatch.setattr(competitor_sync, "studio_status", _status)

    tid = uuid4()
    set_request_tenant(tid)
    bg = BackgroundTasks()
    first = await competitors_api.competitors_refresh(bg, limit=12, force=True)
    assert len(bg.tasks) == 1
    await bg.tasks[0].func()
    assert seen["force"] is True and seen["tenant_id"] == tid
    assert competitors_api._REFRESH_JOBS[first["job_id"]]["status"] == "done"
    assert competitor_sync.refresh_state(tid)["finished_at"] is not None


# ── the cooldown must not turn away work nobody has bought ────────────
#
# What went wrong: onboarding's first tick runs within minutes of intake, with
# nothing tracked yet, and starts the 30-minute cooldown. BM2's chain then
# auto-tracks five peers and POSTs /competitors/refresh, gets
# reused="cooldown" back, records "refreshed" — and with a weekly cadence the
# peers' first pull was a week away. A peer approved while a chain was in
# flight was missed the same way: that chain had already listed its roster.

def test_a_never_synced_competitor_always_gets_past_the_cooldown_for_itself_only(
    clean_refresh_state,
):
    tid = uuid4()
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.claim_refresh(tid, "a", now=t0) is None
    assert competitor_sync.refresh_state(tid)["scope"] == "full"
    competitor_sync.release_refresh(tid, "a", now=t0 + timedelta(minutes=1))
    later = t0 + timedelta(minutes=10)
    # Nothing new: the cooldown holds.
    assert competitor_sync.claim_refresh(tid, "b", now=later)["reused"] == "cooldown"
    # A competitor nobody has pulled yet is never turned away — not even one
    # the last chain already tried and failed. Its run is narrow: the chain
    # for the never-synced competitors only, so a handle that always errors
    # costs one failed pull per call, not the roster.
    assert competitor_sync.claim_refresh(
        tid, "b", now=later, unsynced={"broken-handle"}) is None
    st = competitor_sync.refresh_state(tid)
    assert st["job_id"] == "b" and st["scope"] == "unsynced"
    # What it found waiting is kept, so a narrow run that pulls nothing can
    # put the earlier cooldown back instead of starting a new one.
    assert st["prev"] == {"job_id": "a", "finished_at": t0 + timedelta(minutes=1)}
    competitor_sync.release_refresh(tid, "b", now=later + timedelta(minutes=1),
                                    pulled=False)
    st = competitor_sync.refresh_state(tid)
    assert (st["job_id"], st["finished_at"]) == ("a", t0 + timedelta(minutes=1))
    # force is the whole roster, not a narrow run.
    assert competitor_sync.claim_refresh(
        tid, "c", force=True, now=later, unsynced={"broken-handle"}) is None
    assert competitor_sync.refresh_state(tid)["scope"] == "full"


def test_a_chain_that_pulled_nothing_does_not_start_the_cooldown(clean_refresh_state):
    tid = uuid4()
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.claim_refresh(tid, "empty-tick", now=t0) is None
    competitor_sync.release_refresh(tid, "empty-tick", now=t0 + timedelta(seconds=5),
                                    pulled=False)
    # Nothing was bought, so there is nothing for a cooldown to protect.
    assert competitor_sync.refresh_state(tid) is None
    assert competitor_sync.claim_refresh(tid, "bm2", now=t0 + timedelta(minutes=2)) is None


def test_the_route_and_the_scheduler_share_one_slot_for_the_default_tenant(
    clean_refresh_state,
):
    """Single-tenant mode: the route has no tenant on the request (None) while
    the scheduler passes the default tenant's uuid from scheduled_jobs. Keyed
    on the raw None they were two slots, and the gate never saw one from the
    other."""
    from james_os.config import settings
    set_request_tenant(None)
    assert competitor_sync.claim_refresh(None, "route") is None
    r = competitor_sync.claim_refresh(settings.default_tenant_id, "tick")
    assert r["reused"] == "in_flight" and r["job_id"] == "route"


def test_a_slow_chain_that_is_still_moving_is_not_stacked_on(clean_refresh_state):
    """REFRESH_MAX_RUNTIME counts from the last sign of life, not the start: a
    real chain over twenty peers can run past two hours."""
    tid = uuid4()
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert competitor_sync.claim_refresh(tid, "slow", now=t0) is None
    competitor_sync.touch_refresh(tid, "slow", now=t0 + timedelta(hours=1, minutes=50))
    competitor_sync.touch_refresh(tid, "stranger", now=t0 + timedelta(hours=3))  # ignored
    r = competitor_sync.claim_refresh(tid, "next", now=t0 + timedelta(hours=2, minutes=30))
    assert r["reused"] == "in_flight" and r["job_id"] == "slow"
    # A chain that stopped moving still expires.
    assert competitor_sync.claim_refresh(tid, "next", now=t0 + timedelta(hours=4)) is None


async def test_a_chain_that_found_nothing_tracked_does_not_block_the_first_real_pull(
    clean_refresh_state, quiet_chain, unsynced,
):
    """The onboarding collision, end to end: tick with nothing tracked, then
    peers tracked, then BM2's POST inside the window."""
    tid = uuid4()
    first = await competitor_sync.run_competitor_refresh(tid, {})
    assert first is None and quiet_chain["sync_all"] == 1
    # It pulled nothing, so it started no cooldown.
    assert competitor_sync.refresh_state(tid) is None

    unsynced.update({"peer-1", "peer-2"})
    quiet_chain["synced"] = 2
    set_request_tenant(tid)
    bg = BackgroundTasks()
    r = await competitors_api.competitors_refresh(bg)
    assert "reused" not in r and r["status"] == "running"
    await bg.tasks[0].func()
    assert quiet_chain["sync_all"] == 2
    assert competitors_api._REFRESH_JOBS[r["job_id"]]["cooldown_started"] is True

    # That one pulled, and with nothing new waiting the window is a window.
    unsynced.clear()
    r = await competitors_api.competitors_refresh(BackgroundTasks())
    assert r["reused"] == "cooldown"


async def test_a_competitor_that_keeps_failing_costs_its_own_pull_not_the_rosters(
    clean_refresh_state, quiet_chain, unsynced, monkeypatch,
):
    """Never turned away (it has never been synced), but the run it gets is
    for it alone, and failing again does not move the cooldown the last real
    chain started."""
    tid = uuid4()
    quiet_chain["synced"] = 3
    unsynced.add("always-errors")      # never synced, before and after the chain
    await competitor_sync.full_refresh(tenant_id=tid)
    first = competitor_sync.refresh_state(tid)
    assert first["finished_at"] is not None

    tried: list[str] = []

    async def _list(status="", platform="", tenant_id=None):
        return [{"id": "old-peer", "handle": "old", "platform": "instagram"},
                {"id": "always-errors", "handle": "gone", "platform": "instagram"}]
    async def _sync_one(c, **kw):
        tried.append(c["id"])
        return {"handle": c["handle"], "items_fetched": 0, "rows_new": 0,
                "error": "profile not found"}
    from james_os import competitors
    monkeypatch.setattr(competitors, "list_competitors", _list)
    monkeypatch.setattr(competitor_sync, "sync_competitor", _sync_one)

    for _ in range(2):
        r = await competitor_sync.full_refresh(tenant_id=tid)
        assert "skipped" not in r and r["scope"] == "unsynced"
        assert r["catch_up"]["synced"] == 0 and r["cooldown_started"] is False
    assert tried == ["always-errors", "always-errors"]
    assert quiet_chain["sync_all"] == 1, "the roster was not re-bought"
    st = competitor_sync.refresh_state(tid)
    assert (st["job_id"], st["finished_at"]) == (first["job_id"], first["finished_at"])


async def test_a_new_peer_after_a_real_chain_gets_a_narrow_run(
    clean_refresh_state, quiet_chain, unsynced, monkeypatch,
):
    tid = uuid4()
    quiet_chain["synced"] = 4
    await competitor_sync.full_refresh(tenant_id=tid)
    unsynced.add("new-peer")
    pulled: list[str] = []

    async def _list(status="", platform="", tenant_id=None):
        return [{"id": "old-peer", "handle": "old", "platform": "instagram"},
                {"id": "new-peer", "handle": "new", "platform": "instagram"}]
    async def _sync_one(c, **kw):
        pulled.append(c["id"])
        unsynced.discard(c["id"])
        return {"handle": c["handle"], "items_fetched": 9, "rows_new": 9, "error": None}
    async def _nothing(*a, **kw):
        return {}
    from james_os import competitor_media, competitor_profile, competitor_vision, competitors
    monkeypatch.setattr(competitors, "list_competitors", _list)
    monkeypatch.setattr(competitor_sync, "sync_competitor", _sync_one)
    monkeypatch.setattr(competitor_media, "fetch_media_for_competitor", _nothing)
    monkeypatch.setattr(competitor_vision, "analyze_competitor", _nothing)
    monkeypatch.setattr(competitor_profile, "build_profile", _nothing)

    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["scope"] == "unsynced" and pulled == ["new-peer"]
    assert r["catch_up"]["rows_new"] == 9 and r["cooldown_started"] is True
    assert quiet_chain["sync_all"] == 1
    # It pulled, so the cooldown is now from this run; nothing else waits.
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["skipped"] == "cooldown"


async def test_a_run_where_nothing_moved_does_not_re_buy_the_rollup(
    clean_refresh_state, quiet_chain, monkeypatch,
):
    """BM2's Monday call landing after the BM1 tick: every competitor fresh,
    no media stored, nothing analysed. The profile rollup is an LLM call per
    competitor and the gap is recomputed — on a shelf that did not move."""
    async def _boom(*a, **k):
        raise AssertionError("re-synthesised a shelf that did not change")
    from james_os import competitor_gap, competitor_profile
    monkeypatch.setattr(competitor_profile, "build_all_profiles", _boom)
    monkeypatch.setattr(competitor_gap, "content_gap", _boom)

    tid = uuid4()
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert r["stages"]["profiles"] == {"skipped": "nothing new on their side"}
    assert r["stages"]["gap"] == {"skipped": "nothing new on their side"}
    assert r["cooldown_started"] is False

    # force, or anything pulled, and they run.
    ran: list[str] = []

    async def _built(**kw):
        ran.append("profiles")
        return {"built": 1}
    async def _gap(**kw):
        ran.append("gap")
        return {}
    monkeypatch.setattr(competitor_profile, "build_all_profiles", _built)
    monkeypatch.setattr(competitor_gap, "content_gap", _gap)
    quiet_chain["synced"] = 1
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert ran == ["profiles", "gap"] and r["stages"]["profiles"] == {"built": 1}


async def test_a_peer_approved_mid_chain_is_pulled_by_that_chain(
    clean_refresh_state, quiet_chain, unsynced, monkeypatch,
):
    """BM2's competitor_chain gets reused="in_flight", records "refreshed" and
    never calls back, so the chain in flight has to pick the peer up."""
    tid = uuid4()
    set_request_tenant(tid)
    replies: list[dict] = []

    async def _approve_a_peer_meanwhile():
        unsynced.add("peer-approved-mid-chain")
        replies.append(await competitors_api.competitors_refresh(BackgroundTasks()))
    quiet_chain["during_sync"] = _approve_a_peer_meanwhile

    pulled: list[str] = []

    async def _list(status="", platform="", tenant_id=None):
        return [{"id": "old-peer", "handle": "old", "platform": "instagram"},
                {"id": "peer-approved-mid-chain", "handle": "new", "platform": "instagram"}]
    async def _sync_one(c, **kw):
        pulled.append(c["id"])
        unsynced.discard(c["id"])
        return {"handle": c["handle"], "items_fetched": 12, "rows_new": 12, "error": None}
    async def _nothing(*a, **kw):
        return {}
    from james_os import competitor_media, competitor_profile, competitor_vision, competitors
    monkeypatch.setattr(competitors, "list_competitors", _list)
    monkeypatch.setattr(competitor_sync, "sync_competitor", _sync_one)
    monkeypatch.setattr(competitor_media, "fetch_media_for_competitor", _nothing)
    monkeypatch.setattr(competitor_vision, "analyze_competitor", _nothing)
    monkeypatch.setattr(competitor_profile, "build_profile", _nothing)

    out = await competitor_sync.full_refresh(tenant_id=tid)
    assert replies[0]["reused"] == "in_flight"
    # Only the peer tracked mid-chain — the rest of the roster was just done.
    assert pulled == ["peer-approved-mid-chain"]
    assert out["catch_up"]["synced"] == 1 and out["catch_up"]["items_fetched"] == 12
    # The catch-up pulled, so this chain starts the cooldown.
    st = competitor_sync.refresh_state(tid)
    assert "rerun" not in st and st["finished_at"] is not None
    assert out["cooldown_started"] is True


async def test_no_one_asking_mid_chain_means_no_catch_up(
    clean_refresh_state, quiet_chain, unsynced, monkeypatch,
):
    """The catch-up is for a caller who was told "in flight"; a peer tracked
    without anyone asking waits for the next call, which the cooldown lets
    through — for that peer alone."""
    tid = uuid4()
    quiet_chain["synced"] = 2

    async def _tracked_quietly():
        unsynced.add("quiet-peer")
    quiet_chain["during_sync"] = _tracked_quietly
    out = await competitor_sync.full_refresh(tenant_id=tid)
    assert "catch_up" not in out and out["cooldown_started"] is True
    quiet_chain["during_sync"] = None

    pulled: list[str] = []

    async def _list(status="", platform="", tenant_id=None):
        return [{"id": "quiet-peer", "handle": "q", "platform": "instagram"}]
    async def _sync_one(c, **kw):
        pulled.append(c["id"])
        return {"handle": c["handle"], "error": "timed out"}
    from james_os import competitors
    monkeypatch.setattr(competitors, "list_competitors", _list)
    monkeypatch.setattr(competitor_sync, "sync_competitor", _sync_one)
    r = await competitor_sync.full_refresh(tenant_id=tid)
    assert "skipped" not in r and r["scope"] == "unsynced"
    assert pulled == ["quiet-peer"] and quiet_chain["sync_all"] == 1


async def test_the_status_poll_survives_code_deployed_before_migration_070(monkeypatch):
    """Migrations are applied by hand; BM2 polls GET /competitors/status, and
    a 42703 there on every poll was the deploy-order failure."""
    import asyncpg
    calls: list[str] = []

    async def _row(tenant_id, given_up_sql, *args):
        calls.append(given_up_sql)
        if "media_fetch_attempts" in given_up_sql:
            raise asyncpg.exceptions.UndefinedColumnError(
                'column "media_fetch_attempts" does not exist')
        return {"competitors": 1, "posts": 5, "with_media": 2, "media_given_up": 0,
                "analysed": 1, "analysed_ok": 1, "picked": 0,
                "last_sync": None, "last_analysis": None}
    monkeypatch.setattr(competitor_sync, "_status_row", _row)
    d = await competitor_sync.studio_status(uuid4())
    assert len(calls) == 2 and d["media_given_up"] == 0 and d["media_pending"] == 3


async def test_the_pull_button_says_who_it_skipped_and_how_to_override(monkeypatch):
    """POST /competitors/sync is the operator's explicit button; a run that
    skipped every fresh competitor read as "Posts pulled." with no way to
    know why nothing changed."""
    async def _sync_all(**kw):
        return {"synced": 1, "skipped": 2, "results": [
            {"handle": "fresh1", "skipped": "synced within 6 days"},
            {"handle": "fresh2", "skipped": "synced within 6 days"},
            {"handle": "stale", "error": None}]}
    monkeypatch.setattr(competitor_sync, "sync_all", _sync_all)
    competitors_api._SYNC_JOBS.clear()
    bg = BackgroundTasks()
    r = await competitors_api.competitors_sync(competitors_api.SyncRequest(), bg)
    await bg.tasks[0].func()
    job = competitors_api._SYNC_JOBS[r["job_id"]]
    assert job["status"] == "done"
    assert "@fresh1, @fresh2" in job["note"] and "force=true" in job["note"]
    assert "@stale" not in job["note"]
    competitors_api._SYNC_JOBS.clear()
