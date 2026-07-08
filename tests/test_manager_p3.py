"""P3 of the bm2.0 merge: the brain and the learning loop.

Measure → insight memory → what-worked read-back; goal-pace math; promote
scan; rejection → permanent guardrail on the profile envelope; approval →
origin-tagged voice exemplar. All keyless on the mock providers.
"""

import json
from uuid import UUID

from james_os.db import acquire
from james_os.manager import learning

TENANT = UUID("00000000-0000-0000-0000-000000000001")


async def _work_order(conn, status="published", **payload) -> str:
    payload.setdefault("topic", "Zoning wins on Staten Island")
    payload.setdefault("platform", "instagram")
    payload.setdefault("content_type", "social_post")
    payload.setdefault("predicted_metrics", {"engagement": 50})
    return str(
        await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('strategist', 'work_order', $1::jsonb, $2) RETURNING id",
            json.dumps(payload), status,
        )
    )


# ── measure → insight → read-back (R7) ─────────────────────────────────────


async def test_measure_closes_the_loop_and_writes_insight():
    async with acquire() as conn:
        oid = await _work_order(conn)
        result = await learning.measure(conn, oid, {"engagement": 80, "likes": 60}, tenant_id=TENANT)
    assert result["status"] == "measured"
    assert result["delta"] == {"engagement": 30}  # predicted 50, actual 80
    assert "outperformed" in result["insight"]

    async with acquire() as conn:
        status = await conn.fetchval("SELECT status FROM actions WHERE id = $1::uuid", oid)
        insight = await conn.fetchval(
            "SELECT count(*) FROM events WHERE payload->>'category' = 'insight'"
        )
    assert status == "measured" and insight == 1
    # idempotent on the event side; measuring twice is a state-machine error
    import pytest

    from james_os.manager.state_machine import InvalidTransition

    async with acquire() as conn:
        with pytest.raises(InvalidTransition):
            await learning.measure(conn, oid, {"engagement": 81}, tenant_id=TENANT)


async def test_what_worked_reads_measured_results_back():
    async with acquire() as conn:
        oid = await _work_order(conn)
        await learning.measure(conn, oid, {"engagement": 80}, tenant_id=TENANT)
        block = await learning.what_worked(conn)
    assert block and block[0]["actual"] == {"engagement": 80}
    assert block[0]["predicted"] == {"engagement": 50}


# ── goal pace math ('it notices it's missing the goal') ────────────────────


async def test_goal_gap_notices_off_pace():
    from james_os.manager import profile
    from james_os.manager.contracts import FieldWrite, Source

    async with acquire() as conn:
        await profile.write_field(conn, FieldWrite(
            section="goals", field_key="goals.instagram.followers",
            value={"baseline": 1000, "target": 2000, "timeframe_days": 30},
            source=Source.NEGOTIATED, updated_by="test"))
        await profile.write_field(conn, FieldWrite(
            section="channels", field_key="channels.instagram.followers",
            value=1100, source=Source.AUDITED, updated_by="test"))
        # goal set 15 days ago -> 50% elapsed -> expected 1500; 1100 < 1200 (80%)
        await conn.execute(
            "UPDATE profile_fields SET created_at = now() - interval '15 days' "
            "WHERE field_key = 'goals.instagram.followers'"
        )
        misses = await learning.goal_gap(conn)
    assert len(misses) == 1
    m = misses[0]
    assert (m["platform"], m["metric"]) == ("instagram", "followers")
    assert m["current"] == 1100 and m["target"] == 2000


async def test_goal_gap_too_early_or_on_pace_is_quiet():
    from james_os.manager import profile
    from james_os.manager.contracts import FieldWrite, Source

    async with acquire() as conn:
        await profile.write_field(conn, FieldWrite(
            section="goals", field_key="goals.instagram.followers",
            value={"baseline": 1000, "target": 2000, "timeframe_days": 30},
            source=Source.NEGOTIATED, updated_by="test"))
        await profile.write_field(conn, FieldWrite(
            section="channels", field_key="channels.instagram.followers",
            value=1000, source=Source.AUDITED, updated_by="test"))
        # only 2 days elapsed (< quarter of horizon) -> too early to judge
        await conn.execute(
            "UPDATE profile_fields SET created_at = now() - interval '2 days' "
            "WHERE field_key = 'goals.instagram.followers'"
        )
        assert await learning.goal_gap(conn) == []


# ── promote scan (spend behind winners) ────────────────────────────────────


async def test_promote_scan_flags_clear_winner_once():
    from james_os.manager.heartbeat import _promote_scan

    async with acquire() as conn:
        for eng in (10, 12, 100):
            oid = await _work_order(conn, status="published", topic=f"post-{eng}")
            await learning.measure(conn, oid, {"engagement": eng}, tenant_id=TENANT)
        cands = await learning.promote_candidates(conn)
    assert len(cands) == 1 and cands[0]["engagement"] == 100

    assert await _promote_scan(TENANT) == 1
    assert await _promote_scan(TENANT) == 1  # dedupe: same suggestion, not a second card
    async with acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM action_items WHERE dedupe_key LIKE 'promote:%'"
        )
    assert count == 1


async def test_promote_needs_a_real_median():
    async with acquire() as conn:
        for eng in (10, 100):  # two data points are noise, not a benchmark
            oid = await _work_order(conn, status="published", topic=f"post-{eng}")
            await learning.measure(conn, oid, {"engagement": eng}, tenant_id=TENANT)
        assert await learning.promote_candidates(conn) == []


# ── rejection → permanent guardrail on the envelope (R6.1) ─────────────────


async def test_rejection_distills_into_learned_avoid():
    from james_os.learning import record_rejection

    async with acquire() as conn:
        aid = await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('content_engine', 'content', $1::jsonb, 'pending') RETURNING id",
            json.dumps({"content": "We guarantee 20% returns on every deal, always.",
                        "platform": "instagram", "format": "post", "topic": "returns"}),
        )
    event_id = await record_rejection(aid, "never promise guaranteed returns", TENANT)
    assert event_id is not None  # the frustration guardrail landed

    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT item_key, value, source, citations FROM profile_fields "
            "WHERE field_key = 'guardrails.learned_avoid' AND status <> 'superseded'"
        )
    assert rows, "the rejection must ALSO distill into the profile envelope"
    row = rows[0]
    assert row["source"] == "queue_signal"
    cits = row["citations"]
    if isinstance(cits, str):
        cits = json.loads(cits)
    assert any(str(aid) in c.get("ref", "") for c in cits)


async def test_approval_exemplar_carries_origin_tag():
    from james_os.learning import record_approval

    async with acquire() as conn:
        aid = await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('content_engine', 'content', $1::jsonb, 'approved') RETURNING id",
            json.dumps({"content": "Staten Island's waterfront is quietly becoming the best "
                                    "risk-adjusted development story in the five boroughs.",
                        "platform": "linkedin", "format": "post", "topic": "waterfront"}),
        )
    event_id = await record_approval(aid, TENANT)
    assert event_id is not None
    async with acquire() as conn:
        payload = await conn.fetchval("SELECT payload FROM events WHERE id = $1::uuid", event_id)
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["category"] == "voice_corpus"
    assert payload["origin"] == "approved"  # one corpus, tagged by origin
    assert payload["source"] == "approved_exemplar"


# ── the brain: goal negotiation, daily plan, growth, collaboration ─────────


async def _seed_brand(conn) -> None:
    from james_os.manager import profile
    from james_os.manager.contracts import FieldWrite, Source

    for key, val, src in [
        ("identity.industry", "real estate", Source.RESEARCHED),
        ("positioning.niche", "NYC commercial real estate", Source.RESEARCHED),
        ("channels.instagram.followers", 1200, Source.AUDITED),
    ]:
        await profile.write_field(conn, FieldWrite(
            section=key.split(".")[0], field_key=key, value=val, source=src, updated_by="test"))


async def test_goal_negotiation_writes_negotiated_fields():
    from james_os.manager import goal

    async with acquire() as conn:
        await _seed_brand(conn)
    report = await goal.run(TENANT)
    assert report

    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT field_key, source FROM profile_fields "
            "WHERE section = 'goals' AND status <> 'superseded'"
        )
    assert rows, "negotiation must land goals in the envelope"
    assert all(r["source"] == "negotiated" for r in rows)
    assert all(len(r["field_key"].split(".")) == 3 for r in rows)  # goals.{platform}.{metric}


async def test_daily_plan_creates_deduped_activities():
    from james_os.manager import daily_plan

    async with acquire() as conn:
        await _seed_brand(conn)
    await daily_plan.run(TENANT)
    async with acquire() as conn:
        first = await conn.fetchval(
            "SELECT count(*) FROM action_items WHERE dedupe_key LIKE 'daily:%'"
        )
    assert first > 0
    await daily_plan.run(TENANT)  # same mock plan, same day -> no duplicates
    async with acquire() as conn:
        second = await conn.fetchval(
            "SELECT count(*) FROM action_items WHERE dedupe_key LIKE 'daily:%'"
        )
    assert second == first


async def test_collaboration_never_dead_ends():
    from james_os.manager import collaboration

    # zero tracked peers -> the visibility floor still produces a move
    report = await collaboration.run(TENANT)
    assert report["visibility_plays"], "the floor guarantees a next move"
    async with acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM action_items WHERE dedupe_key LIKE 'visibility:%'"
        )
    assert count >= 1


async def test_growth_trajectories_over_snapshots():
    from james_os.manager import growth, peers

    async with acquire() as conn:
        await _seed_brand(conn)
    await peers.discover(TENANT)
    cands = await peers.candidates(TENANT)
    await peers.set_status(TENANT, cands[0]["handle"], "tracked")
    await peers.snapshot_all(TENANT)
    report = await growth.run(TENANT)
    assert len(report.get("competitors", [])) >= 1
    assert report.get("borrow"), "growth must say what to borrow"


# ── the weekly strategist: plan → prescriptions → activation (D5) ──────────


async def test_weekly_plan_enforces_contract_and_lands_in_prescriptions():
    from james_os.manager import strategist

    async with acquire() as conn:
        await _seed_brand(conn)
    result = await strategist.run(TENANT)
    plan_id = result["plan_id"]

    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, plan FROM prescriptions WHERE id = $1::uuid", plan_id
        )
    assert row["status"] == "proposed"
    plan = row["plan"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    assert plan, "a weekly plan must contain items"
    for item in plan:
        # D5 contract: every item guaranteed rationale + evidence + prediction
        assert item.get("rationale") or item.get("why")
        assert isinstance(item.get("evidence"), list)
        assert isinstance(item.get("predicted_metrics"), dict) and item["predicted_metrics"]


async def test_activation_is_partial_idempotent_and_409s_when_superseded():
    import pytest

    from james_os.manager import strategist

    async with acquire() as conn:
        await _seed_brand(conn)
    first = await strategist.run(TENANT)
    act = await strategist.activate(TENANT, first["plan_id"], item_indices=[0])

    async with acquire() as conn:
        orders = await conn.fetch(
            "SELECT id, payload FROM actions WHERE action_type = 'work_order' AND status = 'queued'"
        )
    assert len(orders) >= 1
    made = len(orders)

    # idempotent re-activation: same selection, no duplicates
    await strategist.activate(TENANT, first["plan_id"], item_indices=[0])
    async with acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type = 'work_order' AND status = 'queued'"
        ) == made

    # a newer plan supersedes the old one — activating the old plan 409s
    second = await strategist.run(TENANT)
    with pytest.raises(ValueError, match="supersede"):
        await strategist.activate(TENANT, first["plan_id"], item_indices=[0])
    assert second["plan_id"] != first["plan_id"]


async def test_brief_is_deterministic_assembly():
    from james_os.manager import strategist

    async with acquire() as conn:
        await _seed_brand(conn)
    brief = await strategist.brief(TENANT)
    assert brief.get("headline")
    assert isinstance(brief.get("sections"), list) and brief["sections"]