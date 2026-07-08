"""P1 of the bm2.0 merge: the manager substrate.

Ported from bm2.0's test_profile_service / test_state_machine / test_actions
onto the asyncpg harness — same semantics, new substrate: append-only
envelope with computed confidence and contradiction states, action-item
dedupe lifecycle, the D5 edges on the actions queue, and job_runs
bookkeeping with token accounting.
"""

import pytest

from james_os.db import acquire
from james_os.manager import actions as actions_svc
from james_os.manager import profile, runs, state_machine
from james_os.manager.contracts import (
    Citation,
    FieldStatus,
    FieldWrite,
    Source,
    WorkOrderStatus,
    compute_confidence,
)


def fw(value, source=Source.RESEARCHED, key="identity.tagline", **kw):
    return FieldWrite(
        section=kw.pop("section", key.split(".")[0]),
        field_key=key,
        value=value,
        source=source,
        updated_by=kw.pop("updated_by", "test"),
        **kw,
    )


# ── D2 confidence rubric (deterministic, never model-reported) ─────────────


def test_confidence_rubric():
    assert compute_confidence(Source.USER_STATED, []) == 1.0
    assert compute_confidence(Source.NEGOTIATED, ["a", "b", "c"]) == 1.0
    assert compute_confidence(Source.AUDITED, []) == 0.95
    assert compute_confidence(Source.RESEARCHED, []) == 0.6
    assert compute_confidence(Source.RESEARCHED, [], primary=True) == 0.8
    assert compute_confidence(Source.RESEARCHED, ["a", "b"]) == 0.7  # multi-citation bonus
    assert compute_confidence(Source.AUDITED, ["a", "b"]) == 0.95  # non-human cap
    assert compute_confidence(Source.INFERRED, []) == 0.4


# ── profile envelope: append-only + supersede + contradiction (D1) ─────────


async def test_write_field_append_only_versioning():
    async with acquire() as conn:
        v1 = await profile.write_field(conn, fw("The calm brand"))
        assert v1["version"] == 1
        assert v1["status"] == FieldStatus.UNCONFIRMED.value
        assert v1["confidence"] == pytest.approx(0.6)

        v2 = await profile.write_field(conn, fw("The bold brand"))
        assert v2["version"] == 2

        current = await profile.current_fields(conn)
        tagline_rows = [r for r in current if r["field_key"] == "identity.tagline"]
        assert len(tagline_rows) == 1  # same-class refresh supersedes quietly
        assert tagline_rows[0]["value"]["v"] == "The bold brand"

        # the superseded row is still on record (append-only), pointing forward
        all_rows = await conn.fetch(
            "SELECT status, superseded_by FROM profile_fields WHERE field_key='identity.tagline' ORDER BY version"
        )
        assert all_rows[0]["status"] == "superseded"
        assert str(all_rows[0]["superseded_by"]) == v2["id"]


async def test_human_answer_wins_and_confidence_is_1():
    async with acquire() as conn:
        await profile.write_field(conn, fw("researched value"))
        human = await profile.write_field(conn, fw("the real tagline", source=Source.USER_STATED))
        assert human["confidence"] == 1.0
        assert human["status"] == FieldStatus.CONFIRMED.value

        current = await profile.current_fields(conn)
        rows = [r for r in current if r["field_key"] == "identity.tagline"]
        assert len(rows) == 1 and rows[0]["value"]["v"] == "the real tagline"


async def test_contradiction_flags_both_and_queues_question():
    async with acquire() as conn:
        await profile.write_field(conn, fw("human truth", source=Source.USER_STATED))
        contradicting = await profile.write_field(conn, fw("robot claim", source=Source.RESEARCHED))
        assert contradicting["status"] == FieldStatus.CONTRADICTED.value

        current = await profile.current_fields(conn)
        rows = [r for r in current if r["field_key"] == "identity.tagline"]
        assert len(rows) == 2  # both sides stay visible while the contradiction is open
        assert {r["status"] for r in rows} == {"contradicted"}

        q = await conn.fetch("SELECT field_key, status FROM brand_questions WHERE field_key = 'identity.tagline'")
        assert len(q) == 1 and q[0]["status"] == "open"

        # a second contradicting write must not duplicate the open question
        await profile.write_field(conn, fw("another robot claim", source=Source.RESEARCHED))
        q2 = await conn.fetch("SELECT id FROM brand_questions WHERE field_key = 'identity.tagline'")
        assert len(q2) == 1

        # the human answer resolves everything — no contradicted row survives
        await profile.write_field(conn, fw("final truth", source=Source.USER_STATED))
        rows = [r for r in await profile.current_fields(conn) if r["field_key"] == "identity.tagline"]
        assert len(rows) == 1 and rows[0]["status"] == FieldStatus.CONFIRMED.value


async def test_mark_stale_respects_ttl():
    async with acquire() as conn:
        await profile.write_field(
            conn, fw(1234, source=Source.AUDITED, key="performance.followers", section="performance")
        )
        await profile.write_field(conn, fw("evergreen", source=Source.AUDITED))
        # backdate the performance row past its 7-day TTL; identity TTL is 365d
        await conn.execute(
            "UPDATE profile_fields SET created_at = now() - interval '10 days' "
            "WHERE field_key = 'performance.followers'"
        )
        flipped = await profile.mark_stale(conn)
        assert flipped == 1
        rows = await profile.current_fields(conn, section="performance")
        assert rows[0]["status"] == FieldStatus.STALE.value


# ── action items: dedupe + lifecycle + digest ──────────────────────────────


async def test_action_upsert_dedupes_and_never_resurrects():
    async with acquire() as conn:
        a = await actions_svc.upsert_action(
            conn, kind="radar", title="Answer the pricing question", dedupe_key="radar:pricing"
        )
        b = await actions_svc.upsert_action(
            conn, kind="radar", title="Answer the pricing question (rerun)", dedupe_key="radar:pricing"
        )
        assert a["id"] == b["id"]  # re-running an eye never duplicates

        await actions_svc.set_status(conn, a["id"], "dismissed")
        c = await actions_svc.upsert_action(
            conn, kind="radar", title="Answer the pricing question", dedupe_key="radar:pricing"
        )
        assert c["id"] == a["id"] and c["status"] == "dismissed"  # decision stands


async def test_action_note_activates_and_snooze_comes_due():
    async with acquire() as conn:
        a = await actions_svc.upsert_action(conn, kind="collab", title="Pitch the podcast")
        noted = await actions_svc.add_note(conn, a["id"], "emailed the booker")
        assert noted["status"] == "active"
        assert noted["updates"][-1]["note"] == "emailed the booker"

        snoozed = await actions_svc.set_status(conn, a["id"], "snoozed", snooze_days=3)
        assert snoozed["snooze_until"] is not None
        # bring the snooze due
        await conn.execute(
            "UPDATE action_items SET snooze_until = now() - interval '1 hour' WHERE id = $1::uuid", a["id"]
        )
        due = await actions_svc.due_followups(conn)
        assert [d["id"] for d in due] == [a["id"]]


async def test_daily_cycle_upserts_one_digest_per_date():
    async with acquire() as conn:
        await actions_svc.upsert_action(conn, kind="trend", title="Ride the zoning story")
        d1 = await actions_svc.run_daily_cycle(conn)
        d2 = await actions_svc.run_daily_cycle(conn)
        assert d1["date"] == d2["date"]
        count = await conn.fetchval("SELECT count(*) FROM daily_digests")
        assert count == 1
        assert "1 new suggestion" in d2["summary"]


# ── D5 state machine on the actions queue ──────────────────────────────────


async def _new_order(conn, status="queued") -> str:
    return str(
        await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('strategist', 'work_order', '{}'::jsonb, $1) RETURNING id",
            status,
        )
    )


async def test_full_d5_lifecycle_walk():
    async with acquire() as conn:
        oid = await _new_order(conn)
        for allowed, target in [
            (state_machine.GENERATE_FROM, WorkOrderStatus.GENERATING),
            (state_machine.REVIEW_FROM, WorkOrderStatus.REVIEW),
            (state_machine.PASS_REVIEW_FROM, WorkOrderStatus.PENDING_APPROVAL),
            (state_machine.APPROVE_FROM, WorkOrderStatus.APPROVED),
            (state_machine.PUBLISH_FROM, WorkOrderStatus.PUBLISHED),
            (state_machine.MEASURE_FROM, WorkOrderStatus.MEASURED),
        ]:
            row = await state_machine.transition(conn, oid, allowed, target)
            assert row["status"] == target.value
        assert row["executed_at"] is not None  # publish stamps executed_at


async def test_invalid_edge_409s_and_leaves_order_untouched():
    async with acquire() as conn:
        oid = await _new_order(conn)  # queued
        with pytest.raises(state_machine.InvalidTransition):
            await state_machine.transition(conn, oid, state_machine.APPROVE_FROM, WorkOrderStatus.APPROVED)
    async with acquire() as conn:
        status = await conn.fetchval("SELECT status FROM actions WHERE id = $1::uuid", oid)
        assert status == "queued"


async def test_legacy_statuses_are_not_d5_orders():
    assert not state_machine.can_transition("pending", state_machine.APPROVE_FROM)
    assert state_machine.can_transition("pending_approval", state_machine.APPROVE_FROM)


# ── job_runs bookkeeping + token accounting ────────────────────────────────


async def test_job_run_records_usage_and_outcome():
    run = await runs.start_run("researcher", trigger="manual", input={"q": "who is the brand"})
    assert runs.active_run() is run
    runs.add_usage(tokens_in=100, tokens_out=40)
    runs.add_usage(tokens_out=10)
    await runs.finish_run(run, output={"lane_stats": {"web": 3}})
    assert runs.active_run() is None

    async with acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM job_runs WHERE id = $1::uuid", run.id)
    assert row["status"] == "succeeded"
    assert row["tokens_in"] == 100 and row["tokens_out"] == 50
    assert row["finished_at"] is not None


async def test_failed_run_survives_with_error():
    run = await runs.start_run("auditor", trigger="scheduled")
    await runs.finish_run(run, error="aggregator 500")
    async with acquire() as conn:
        row = await conn.fetchrow("SELECT status, error FROM job_runs WHERE id = $1::uuid", run.id)
    assert row["status"] == "failed" and "500" in row["error"]


# ── D12 stream inventory (mock mode: every stream present, honestly mock) ──


def test_stream_inventory_mock_mode():
    from james_os.manager.providers import get_providers
    from james_os.manager.sources_api import stream_rows

    rows = stream_rows(get_providers())  # manager_env defaults to mock
    streams = {r["stream"] for r in rows}
    assert {
        "web_search", "page_scraping", "news", "places", "wikipedia", "youtube",
        "reddit", "deep_research", "llm", "peer_tracking", "social_publishing",
        "email_publishing", "blog_publishing", "transcription",
    } <= streams
    assert all(r["status"] == "mock" for r in rows)  # zero keys -> honest mocks
    llm = next(r for r in rows if r["stream"] == "llm")
    assert llm["chain"] == []  # no availability chain without keys
