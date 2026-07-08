"""Next-steps checklist — ported from bm2.0 backend/app/routers/next_steps.py.

Ordered onboarding/operating checklist recomputed from live DB state on every
call — no stored progress flags that could drift. Tenancy comes from
db.acquire()'s RLS binding, so every count below is tenant-scoped without
per-query threading.

Donor checks mapped onto the james-os substrate:
  1. research_profile   -> current profile_fields count (running while a
                           researcher job_run is in flight)
  2. answer_questions   -> open brand_questions count (the continuous deep
                           interview; bm2.0's info_value-5 must-asks)
  3. connect_accounts   -> tenants.config['postproxy_profile_key'] present
                           and/or connections rows with status='connected'
  4. build_voice        -> voice_corpus exemplar count, broken down by
                           payload origin (harvested/uploaded/approved/audited)
  5. track_peers        -> tenants.config['watchlist'] entries with
                           status='tracked' (bm2.0's tracked PeerEntities)
  6. weekly_prescription-> a prescriptions row exists (bm2.0's active Plan)
  7. review_queue       -> work-order actions: pending_approval awaiting vs
                           published/measured shipped counts

Each step is {step, label, state, count, hint}: state is 'done' | 'running'
(research in flight) | 'pending'; count carries the number behind the state
where one is meaningful (else null); hints are human sentences the frontend
renders as-is (the donor contract, verbatim).
"""

from __future__ import annotations

import json

from .. import db

PEER_TRACKED_TARGET = 3  # donor PEER_SNAPSHOT_TARGET, applied to tracked peers
# Work-order statuses at or past human approval (D5 state-machine order)
PAST_APPROVAL_STATUSES = ("approved", "scheduled", "published", "measured")
PUBLISHED_STATUSES = ("published", "measured")


def _step(step: str, label: str, state: str, hint: str, count: int | None = None) -> dict:
    return {"step": step, "label": label, "state": state, "count": count, "hint": hint}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def compute() -> list[dict]:
    """The checklist, freshly computed. One connection, seven cheap counts."""
    async with db.acquire() as conn:
        field_count = await conn.fetchval(
            "SELECT count(*) FROM profile_fields WHERE status <> 'superseded'"
        )
        researching = bool(
            await conn.fetchval(
                "SELECT 1 FROM job_runs WHERE agent = 'researcher' AND status = 'running' "
                "AND started_at > now() - interval '30 minutes' LIMIT 1"
            )
        )
        open_questions = await conn.fetchval(
            "SELECT count(*) FROM brand_questions WHERE status = 'open'"
        )
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
        if isinstance(cfg, str):
            cfg = json.loads(cfg or "{}")
        cfg = cfg or {}
        profile_key = str(cfg.get("postproxy_profile_key") or "")
        tracked_peers = len(
            [
                e for e in (cfg.get("watchlist") or [])
                # entries that pre-date the merge (no status) count as tracked
                if str(e.get("status") or "tracked") == "tracked"
            ]
        )
        connected_rows = await conn.fetchval(
            "SELECT count(*) FROM connections WHERE status = 'connected'"
        )
        exemplar_rows = await conn.fetch(
            "SELECT coalesce(payload->>'origin', 'uploaded') AS origin, count(*) AS n "
            "FROM events WHERE payload->>'category' = 'voice_corpus' "
            "AND superseded_by IS NULL GROUP BY 1 ORDER BY n DESC"
        )
        prescription_count = await conn.fetchval("SELECT count(*) FROM prescriptions")
        awaiting = await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type = 'work_order' "
            "AND status = 'pending_approval'"
        )
        shipped = await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type = 'work_order' "
            "AND status = ANY($1::text[])",
            list(PAST_APPROVAL_STATUSES),
        )
        published = await conn.fetchval(
            "SELECT count(*) FROM actions WHERE action_type = 'work_order' "
            "AND status = ANY($1::text[])",
            list(PUBLISHED_STATUSES),
        )

    steps: list[dict] = []

    # 1. research_profile — any current envelope field means research (or an
    # answer) has landed; an in-flight researcher run reads as running.
    if field_count:
        steps.append(
            _step(
                "research_profile",
                "Research the brand profile",
                "done",
                f"{_plural(field_count, 'profile field')} drafted so far.",
                count=field_count,
            )
        )
    elif researching:
        steps.append(
            _step(
                "research_profile",
                "Research the brand profile",
                "running",
                "The Researcher is reading public sources now — results land in the profile shortly.",
            )
        )
    else:
        steps.append(
            _step(
                "research_profile",
                "Research the brand profile",
                "pending",
                "Confirm the brand's sources so the Researcher can draft the profile from public data.",
            )
        )

    # 2. answer_questions — open brand_questions still waiting on the human.
    steps.append(
        _step(
            "answer_questions",
            "Answer the Interviewer's questions",
            "pending" if open_questions else "done",
            f"The Interviewer has {_plural(open_questions, 'question')} for you."
            if open_questions
            else "All open questions are settled.",
            count=open_questions,
        )
    )

    # 3. connect_accounts — an aggregator profile key is bound and/or synced
    # connection rows exist, so the Auditor can see the channels.
    accounts_done = bool(profile_key) or connected_rows > 0
    if accounts_done:
        hint = (
            f"{_plural(connected_rows, 'account')} connected."
            if connected_rows
            else "Aggregator profile bound — sync to pull the connected accounts in."
        )
    else:
        hint = "Connect at least one social account so the Auditor can see your channels."
    steps.append(
        _step(
            "connect_accounts",
            "Connect social accounts",
            "done" if accounts_done else "pending",
            hint,
            count=connected_rows,
        )
    )

    # 4. build_voice — voice_corpus exemplars ground every draft; the origin
    # breakdown shows where they came from (harvested/uploaded/approved/audited).
    exemplar_total = sum(int(r["n"]) for r in exemplar_rows)
    by_origin = ", ".join(f"{r['origin']} {r['n']}" for r in exemplar_rows)
    steps.append(
        _step(
            "build_voice",
            "Build the voice corpus",
            "done" if exemplar_total else "pending",
            f"{_plural(exemplar_total, 'voice exemplar')} on file ({by_origin})."
            if exemplar_total
            else "Harvest or upload voice samples so drafts sound like the brand.",
            count=exemplar_total,
        )
    )

    # 5. track_peers — enough approved (tracked) watchlist peers to benchmark.
    steps.append(
        _step(
            "track_peers",
            "Track competitor peers",
            "done" if tracked_peers >= PEER_TRACKED_TARGET else "pending",
            f"{_plural(tracked_peers, 'tracked peer')} on the watchlist."
            if tracked_peers >= PEER_TRACKED_TARGET
            else f"Approve peers until at least {PEER_TRACKED_TARGET} are tracked "
            f"({tracked_peers} so far).",
            count=tracked_peers,
        )
    )

    # 6. weekly_prescription — the strategy loop produced a Prescription.
    steps.append(
        _step(
            "weekly_prescription",
            "Get a weekly prescription",
            "done" if prescription_count else "pending",
            "A prescription exists — the plan is feeding the queue."
            if prescription_count
            else "Run the strategy engine to get your first weekly prescription.",
        )
    )

    # 7. review_queue — done once at least one work order made it past approval.
    if shipped:
        hint = (
            f"{_plural(awaiting, 'draft')} still waiting for review."
            if awaiting
            else f"Approved content is moving through the queue "
            f"({_plural(published, 'piece')} published)."
        )
        state = "done"
    elif awaiting:
        state = "pending"
        hint = f"{_plural(awaiting, 'draft')} waiting for your approval in the queue."
    else:
        state = "pending"
        hint = "Draft and approve your first piece of content."
    steps.append(_step("review_queue", "Review the content queue", state, hint, count=awaiting))

    return steps


__all__ = ["compute", "PEER_TRACKED_TARGET", "PAST_APPROVAL_STATUSES", "PUBLISHED_STATUSES"]
