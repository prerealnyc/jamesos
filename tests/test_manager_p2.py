"""P2 of the bm2.0 merge: the eyes and the heartbeat.

Everything runs keyless against the deterministic mock providers (D8) — the
same fixtures that backed these agents' tests in bm2.0.
"""

from uuid import UUID

from james_os.config import settings
from james_os.db import acquire
from james_os.manager.eyes import trends

TENANT = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_niche(conn) -> None:
    from james_os.manager import profile
    from james_os.manager.contracts import FieldWrite, Source

    for key, val in [
        ("identity.industry", "real estate"),
        ("positioning.niche", "NYC commercial real estate"),
        ("audience.primary", "property investors"),
    ]:
        await profile.write_field(
            conn,
            FieldWrite(section=key.split(".")[0], field_key=key, value=val,
                       source=Source.RESEARCHED, updated_by="test"),
        )


# ── the trends eye ──────────────────────────────────────────────────────────


async def test_trends_eye_files_suggestions_and_actions():
    async with acquire() as conn:
        await _seed_niche(conn)

    report = await trends.run(TENANT)
    assert report["trends"], "mock news + mock LLM must yield at least one trend"

    async with acquire() as conn:
        suggestions = await conn.fetch(
            "SELECT title, why, evidence FROM content_suggestions WHERE source = 'trends'"
        )
        items = await conn.fetch(
            "SELECT dedupe_key, kind FROM action_items WHERE dedupe_key LIKE 'trend:%'"
        )
        run_row = await conn.fetchrow(
            "SELECT status, output FROM job_runs WHERE agent = 'trends' ORDER BY started_at DESC LIMIT 1"
        )
    assert len(suggestions) >= 1
    assert all(s["why"] is not None for s in suggestions)  # every finding carries its WHY
    assert len(items) == len(report["trends"])
    assert all(i["kind"] == "content" for i in items)
    assert run_row["status"] == "succeeded"


async def test_trends_eye_rerun_never_duplicates():
    async with acquire() as conn:
        await _seed_niche(conn)
    await trends.run(TENANT)
    async with acquire() as conn:
        first_s = await conn.fetchval("SELECT count(*) FROM content_suggestions")
        first_a = await conn.fetchval("SELECT count(*) FROM action_items")
    await trends.run(TENANT)  # the mock providers return the same findings
    async with acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM content_suggestions") == first_s
        assert await conn.fetchval("SELECT count(*) FROM action_items") == first_a


# ── heartbeat job registration / retirement ─────────────────────────────────


async def test_ensure_manager_jobs_retires_dormant_and_registers_manager():
    from james_os.manager.heartbeat import MANAGER_JOBS, RETIRED_KINDS, ensure_manager_jobs

    async with acquire() as conn:
        # a lingering dormant job from the pre-merge world
        await conn.execute(
            "INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours) VALUES ($1, $2, 24)",
            TENANT, RETIRED_KINDS[0],
        )
        # the tenant opts into the manager
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{manager_v2}', 'true') WHERE id = $1", TENANT,
        )
    n = await ensure_manager_jobs()
    assert n == 1
    async with acquire() as conn:
        kinds = {r["kind"] for r in await conn.fetch("SELECT kind FROM scheduled_jobs WHERE tenant_id = $1", TENANT)}
    assert RETIRED_KINDS[0] not in kinds  # one brain: the dormant kind is gone
    assert kinds == {k for k, _ in MANAGER_JOBS}
    # idempotent
    await ensure_manager_jobs()
    async with acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM scheduled_jobs WHERE tenant_id = $1", TENANT)
    assert count == len(MANAGER_JOBS)


async def test_ensure_manager_jobs_skips_unopted_tenants():
    from james_os.manager.heartbeat import ensure_manager_jobs

    prior = settings.manager_v2
    settings.manager_v2 = False
    try:
        assert await ensure_manager_jobs() == 0
        async with acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM scheduled_jobs") == 0
    finally:
        settings.manager_v2 = prior


# ── the full daily cycle ────────────────────────────────────────────────────


async def test_scheduled_cycle_skips_unonboarded_brand():
    """A fresh tenant must meet the interviewer before the heartbeat writes
    anything — otherwise a startup-scheduled cycle fakes 'onboarded' to the
    front door (found live on a factory-fresh install)."""
    from james_os.manager import heartbeat

    report = await heartbeat.run_daily_cycle(TENANT)  # onboarding_status unset
    assert "skipped" in report
    async with acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM content_suggestions") == 0
        assert await conn.fetchval("SELECT count(*) FROM prescriptions") == 0
    # a human pressing the button still works pre-onboarding (manual override)
    manual = await heartbeat.run_daily_cycle(TENANT, {"trigger": "manual"})
    assert "skipped" not in manual


async def test_full_daily_cycle_on_mocks():
    from james_os.manager import heartbeat

    async with acquire() as conn:
        await _seed_niche(conn)
        # the heartbeat only beats for onboarded brands (the gate a fresh
        # install exposed); scheduled cycles skip pre-onboarding tenants
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{onboarding_status}', '\"active\"') WHERE id = $1", TENANT,
        )
    report = await heartbeat.run_daily_cycle(TENANT)

    # every step reported, none hard-failed the cycle
    for step in ("stale_marked", "algorithm", "content_radar", "trends", "press",
                 "questions", "appearances", "autopilot_drafted", "digest", "digest_emailed"):
        assert step in report, f"cycle report missing step {step!r}"
    assert report["algorithm"] == "refreshed"  # no briefs existed -> stale -> refreshed

    async with acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM content_suggestions") > 0
        assert await conn.fetchval("SELECT count(*) FROM daily_digests") == 1
        assert await conn.fetchval("SELECT count(*) FROM platform_playbooks") > 0
        # a second cycle the same day: algorithm now fresh, digest still one row
    report2 = await heartbeat.run_daily_cycle(TENANT)
    assert report2["algorithm"] == "fresh"
    async with acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM daily_digests") == 1


# ── peers: discover → approve → track (the human gate) ─────────────────────


async def test_peer_lifecycle_gate():
    from james_os.manager import peers

    async with acquire() as conn:
        await _seed_niche(conn)

    await peers.discover(TENANT)
    cands = await peers.candidates(TENANT)
    assert cands, "mock discovery must propose candidates"
    assert all(c.get("status") == "candidate" for c in cands)

    # nothing is snapshotted before a human approves — the gate
    await peers.snapshot_all(TENANT)
    async with acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM peer_snapshots") == 0

    handle = cands[0]["handle"]
    approved = await peers.set_status(TENANT, handle, "tracked")
    assert approved["status"] == "tracked"
    rejected = await peers.set_status(TENANT, cands[-1]["handle"], "rejected")
    assert rejected["status"] == "rejected"

    await peers.snapshot_all(TENANT)
    async with acquire() as conn:
        snaps = await conn.fetch("SELECT peer FROM peer_snapshots")
        derived = await conn.fetch(
            "SELECT field_key, source FROM profile_fields WHERE section = 'competitors'"
        )
    assert len(snaps) >= 1
    assert all(s["peer"].lstrip("@").lower() != rejected["handle"].lstrip("@").lower() for s in snaps)
    assert derived and all(d["source"] == "derived" for d in derived)


# ── auditor: honest public-baseline fallback, additive memory import ────────


async def test_auditor_public_fallback_and_memory_import():
    from james_os.manager import auditor, profile
    from james_os.manager.contracts import FieldWrite, Source

    async with acquire() as conn:
        await _seed_niche(conn)
        # the brand's own handle on record; no postproxy_profile_key configured
        await profile.write_field(
            conn,
            FieldWrite(section="channels", field_key="channels.instagram",
                       value={"platform": "instagram", "handle": "@testbrand", "url": ""},
                       source=Source.RESEARCHED, updated_by="test"),
        )

    report = await auditor.run(TENANT)
    assert report.get("notes"), "no aggregator key -> degraded mode must be NOTED, not silent"
    assert report.get("platforms"), "public fallback must still produce a baseline"

    async with acquire() as conn:
        audited_fields = await conn.fetch(
            "SELECT field_key, source, value FROM profile_fields "
            "WHERE section = 'channels' AND status <> 'superseded' AND updated_by <> 'test'"
        )
        posts = await conn.fetchval(
            "SELECT count(*) FROM events WHERE payload->>'category' = 'post'"
        )
        exemplars = await conn.fetch(
            "SELECT payload FROM events WHERE payload->>'category' = 'voice_corpus'"
        )
    assert audited_fields, "baseline metrics must land in the profile envelope"
    assert posts > 0, "post history must import into the events substrate"
    for row in exemplars:
        payload = row["payload"]
        if isinstance(payload, str):
            import json as _json

            payload = _json.loads(payload)
        assert payload.get("origin") == "audited"  # additive, tagged, never impersonating

    run_status = None
    async with acquire() as conn:
        run_status = await conn.fetchval(
            "SELECT status FROM job_runs WHERE agent = 'auditor' ORDER BY started_at DESC LIMIT 1"
        )
    assert run_status == "succeeded"
