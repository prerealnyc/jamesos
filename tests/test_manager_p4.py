"""P4 of the bm2.0 merge: onboarding, voice, and the publish spine.

End-to-end on the deterministic mock providers: research lanes fill the
envelope, the interview confirms human truth, an opportunity becomes a
drafted work order that publishes through the (formerly dormant) outbox,
and the next-steps checklist reads it all live.
"""

import json
from uuid import UUID

from james_os.db import acquire
from james_os.manager import actions as action_service

TENANT = UUID("00000000-0000-0000-0000-000000000001")

SEED = {"name": "PreReal", "website": "https://prereal.com", "entity_type": "company"}


# ── D11 researcher: discover + confirmed fan-out ───────────────────────────


async def test_research_discover_then_confirmed_fanout():
    from james_os.manager import researcher

    report = await researcher.run(TENANT, seed=SEED, mode="discover")
    assert report.candidates, "discovery must surface 'is this your brand?' candidates"

    report = await researcher.run(
        TENANT, seed=SEED, mode="confirmed",
        confirmed_urls=[c.urls[0] for c in report.candidates[:1] if c.urls] or ["https://prereal.com"],
    )
    assert report.fields, "confirmed research must extract cited fields"

    async with acquire() as conn:
        sections = {r["section"] for r in await conn.fetch(
            "SELECT DISTINCT section FROM profile_fields WHERE status <> 'superseded'"
        )}
        wiki = await conn.fetchrow(
            "SELECT value, citations FROM profile_fields "
            "WHERE field_key = 'positioning.wikipedia_presence' AND status <> 'superseded'"
        )
        run_row = await conn.fetchrow(
            "SELECT output FROM job_runs WHERE agent = 'researcher' AND status = 'succeeded' "
            "ORDER BY started_at DESC LIMIT 1"
        )
    assert len(sections) >= 2, f"multi-lane fan-out should fill several sections, got {sections}"
    assert wiki is not None, "wikipedia absence/presence is always a finding"
    output = run_row["output"]
    if isinstance(output, str):
        output = json.loads(output)
    assert output.get("lane_stats"), "lane_stats must land on the job run (feeds /manager/sources)"

    # every non-human field must carry citations (the citation gate)
    async with acquire() as conn:
        uncited = await conn.fetchval(
            "SELECT count(*) FROM profile_fields WHERE source = 'researched' "
            "AND citations = '[]'::jsonb AND field_key <> 'positioning.wikipedia_presence'"
        )
    assert uncited == 0


# ── intake: materialize → ask → human answer wins ──────────────────────────


async def test_interview_materializes_and_confirms_human_truth():
    import james_os.intake_agent as intake

    n = await intake.generate_questions(TENANT)
    assert n > 0
    assert await intake.generate_questions(TENANT) == 0  # idempotent

    batch = await intake.interview_next(TENANT, session_budget=3)
    qs = batch.get("questions") or []
    assert qs, "the interviewer must surface questions"

    q = qs[0]
    result = await intake.answer_question(TENANT, question_id=q["id"], answer="We build calm brands.")
    assert result

    async with acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM brand_questions WHERE id = $1::uuid", q["id"]
        )
        human = await conn.fetchval(
            "SELECT count(*) FROM profile_fields WHERE source = 'user_stated' AND status <> 'superseded'"
        )
    assert status == "confirmed"
    assert human >= 1  # the answer became an authoritative envelope write


# ── the publish spine: opportunity → draft → approve → published ───────────


async def _seed_voice() -> None:
    """The engine honestly refuses to draft without a voice corpus. The insert
    must COMMIT (leave the acquire block) before record_approval reads it on
    its own connection."""
    from james_os.learning import record_approval

    async with acquire() as conn:
        aid = await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('content_engine', 'content', $1::jsonb, 'approved') RETURNING id",
            json.dumps({"content": "Staten Island development is a patience game — the zoning "
                                    "tells you where the next decade of value is going before "
                                    "the market does.",
                        "platform": "linkedin", "format": "post", "topic": "zoning"}),
        )
    assert await record_approval(aid, TENANT) is not None


async def test_execute_opportunity_refuses_honestly_without_real_llm():
    """Their engine's stub LLM refuses to fabricate (by design). The spine must
    surface that as a failed run on record — never a fake draft in the queue."""
    import pytest

    from james_os.manager import execution

    await _seed_voice()
    async with acquire() as conn:
        opp = await action_service.upsert_action(
            conn, kind="content", title="Write the zoning explainer",
            detail="Answer the recurring Reddit question about SI zoning",
            meta={"source": "content_radar", "content_type": "blog"},
            dedupe_key="radar:zoning-explainer",
        )
    with pytest.raises(RuntimeError):
        await execution.execute_opportunity(TENANT, opp["id"], fmt="blog")
    async with acquire() as conn:
        run_status = await conn.fetchval(
            "SELECT status FROM job_runs WHERE agent = 'hands' ORDER BY started_at DESC LIMIT 1"
        )
        queued_drafts = await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type = 'work_order' AND status = 'pending_approval'"
        )
    assert run_status == "failed"  # honest failure, on record
    assert queued_drafts == 0  # nothing fabricated


async def test_approved_order_publishes_via_outbox():
    from james_os.manager import publish
    from james_os.manager.contracts import WorkOrderStatus
    from james_os.manager.state_machine import APPROVE_FROM, transition

    async with acquire() as conn:
        opp = await action_service.upsert_action(
            conn, kind="content", title="Write the zoning explainer",
            meta={"source": "content_radar"}, dedupe_key="radar:zoning-explainer",
        )
        order_id = str(await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status) "
            "VALUES ('hands', 'work_order', $1::jsonb, 'pending_approval') RETURNING id",
            json.dumps({
                "source": "opportunity", "source_action_id": opp["id"],
                "topic": "Zoning explainer", "platform": "website", "content_type": "blog",
                "predicted_metrics": {"note": "v0"},
                "artifacts": [{"version": 1, "kind": "blog",
                                "content": "# SI zoning, explained\n\nThe patient investor's map.",
                                "media": {"title": "SI zoning, explained", "slug": "si-zoning"}}],
            }),
        ))
        await transition(conn, order_id, APPROVE_FROM, WorkOrderStatus.APPROVED)

    pub = await publish.publish_work_order(TENANT, order_id)
    assert pub.get("ok") is True, f"mock blog publish must succeed: {pub}"

    async with acquire() as conn:
        status = await conn.fetchval("SELECT status FROM actions WHERE id = $1::uuid", order_id)
        outbox = await conn.fetchrow(
            "SELECT status FROM outbox WHERE task_type = 'execute_action' ORDER BY created_at DESC LIMIT 1"
        )
        opp_after = await conn.fetchrow(
            "SELECT status FROM action_items WHERE id = $1::uuid", opp["id"]
        )
    assert status == "published"
    assert outbox is not None and outbox["status"] == "done"  # the dormant outbox is alive
    assert opp_after["status"] == "done"  # loop closed back on the opportunity card


# ── voice harvest: auto-pull into the shared corpus ────────────────────────


async def test_voice_harvest_fills_corpus_and_profile():
    from james_os.manager import profile, voice_harvester
    from james_os.manager.contracts import FieldWrite, Source

    async with acquire() as conn:
        await profile.write_field(conn, FieldWrite(
            section="channels", field_key="channels.youtube",
            value={"platform": "youtube", "url": "https://youtube.com/@prereal", "handle": "@prereal"},
            source=Source.RESEARCHED, updated_by="test"))

    report = await voice_harvester.run(TENANT)
    assert report

    async with acquire() as conn:
        harvested = await conn.fetchval(
            "SELECT count(*) FROM events WHERE payload->>'category' = 'voice_corpus' "
            "AND payload->>'origin' = 'harvested'"
        )
        voice_fields = await conn.fetch(
            "SELECT field_key FROM profile_fields WHERE section = 'voice' AND status <> 'superseded'"
        )
    assert harvested > 0, "own-channel transcripts must become cited exemplars"
    assert voice_fields, "the voice profile must be distilled into voice.* fields"


# ── next-steps: computed live, no stored flags ─────────────────────────────


async def test_next_steps_reflect_live_state():
    from james_os.manager import next_steps

    steps = await next_steps.compute()
    assert len(steps) >= 6
    by_name = {s["step"]: s for s in steps}
    assert "connect_accounts" in by_name
    assert all(s["state"] in ("done", "running", "pending") for s in steps)
