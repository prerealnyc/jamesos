"""Strategist — the brain (bm2.0 spec §3.6, D5), ported onto the james-os
substrate. The only plan producer.

weekly_plan: ONE strategy-tier LLM call over gathered state (profile
positioning/goals/guardrails, account baselines, peer digest recomputed from
the watchlist's TRACKED entries + newest peer_snapshots, algorithm briefs
from platform_playbooks, the learning read-back, staleness notes); validated
to WeeklyPlan with one retry appending the validation error; D5 enforced
post-parse (every item: rationale + >=1 citation + predicted_metrics, crude
0.5x-peer-median fill noted in the rationale). Persists a prescriptions row
(052_strategy.sql) whose plan entries carry BOTH the table's documented shape
[{platform, format, per_week, topics[], why, evidence[]}] and the full donor
item fields, so activate() can materialize work orders without re-reading
anything else.

activate: partial acceptance (item_indices), idempotent re-activation
(dedupe on payload plan_id+item_index), and the D5 replan rule — activating
a newer plan supersedes older plans' still-queued work orders, except a
queued order whose (topic, platform, content_type) exactly matches an
activated item, which reattaches to the new plan instead. Activating a plan
that a newer prescriptions row has overtaken raises ValueError('superseded')
— the HTTP layer maps it to 409.

brief: deterministic assembly, no LLM (donor v0 semantics) — work orders due
today, follow-ups due, the recomputed peer digest, profile fields needing
attention, and the latest plan's status.

Donor: bm2.0 backend/app/agents/strategist.py. Constraints kept: LLM access
only through providers.llm (strategy tier), work-order status changes only
via the state machine, every run a job_runs row, tenancy via db.acquire/RLS.
"""

import json
from datetime import date, datetime, timedelta, timezone
from statistics import median
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from .. import db
from ..trends import get_watchlist
from . import actions as action_service
from . import profile, runs
from .contracts import Citation, PeerDigest, WeeklyPlan, WorkOrderStatus
from .providers import get_providers
from .state_machine import transition

AGENT = "strategist"

# D5 replan vocabulary: still-queued orders are replaceable by a newer plan;
# anything already moving (review and beyond) is in flight and survives.
_REPLACEABLE = (WorkOrderStatus.QUEUED,)
_IN_FLIGHT = (
    WorkOrderStatus.REVIEW,
    WorkOrderStatus.PENDING_APPROVAL,
    WorkOrderStatus.APPROVED,
    WorkOrderStatus.SCHEDULED,
)
_BRIEF_DUE_STATUSES = (WorkOrderStatus.QUEUED, WorkOrderStatus.GENERATING) + _IN_FLIGHT

_PLAN_SYSTEM = (
    "You are the Strategist, the brand's CMO. You produce evidence-cited weekly content plans. "
    "Respond with a single JSON object and nothing else. Every item needs: a rationale tied to a "
    "goal, baseline, or peer benchmark; at least one evidence citation (url or ref taken from the "
    "context); and predicted_metrics grounded in the peer benchmarks or account baselines. "
    "Respect the guardrails; never plan content touching off-limits topics."
)

_PLAN_FORMAT = (
    "Return JSON with exactly these keys:\n"
    '{"rationale": "<plan-level why, citing evidence>",\n'
    ' "goals_snapshot": {"<goal field_key>": "<target>"},\n'
    ' "items": [{"content_type": "<text_post|reel|image|blog>", "platform": "<platform>",\n'
    '   "topic": "<specific topic>", "count": <integer >= 1>, "format_spec": {},\n'
    '   "rationale": "<why this item this week>",\n'
    '   "evidence": [{"url": "<source url>", "ref": "<internal ref>", "note": "<what it shows>"}],\n'
    '   "predicted_metrics": {"engagement": <float>}}]}'
)


async def run(
    tenant_id: UUID | None = None,
    config: dict | None = None,
    *,
    mode: str = "weekly_plan",
    trigger: str = "manual",
) -> dict:
    trigger = (config or {}).get("trigger") or trigger
    handle = await runs.start_run(AGENT, trigger=trigger, input={"mode": mode}, tenant_id=tenant_id)
    try:
        if mode == "weekly_plan":
            result = await _weekly_plan(tenant_id)
            output = {"plan_id": result["plan_id"], "item_count": len(result["plan"]["items"])}
        elif mode == "morning_brief":
            result = await brief(tenant_id)
            output = {"headline": result["headline"]}
        else:
            raise ValueError(f"unknown strategist mode: {mode!r}")
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output=output)
    return result


# ------------------------------------------------------------- weekly plan


async def _weekly_plan(tenant_id: UUID | None) -> dict:
    providers = get_providers()
    now = datetime.now(timezone.utc)
    period_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    period_end = period_start + timedelta(days=7)

    from . import learning

    async with db.acquire(tenant_id) as conn:
        positioning = _compact(await profile.current_fields(conn, "positioning"))
        goals = _compact(await profile.current_fields(conn, "goals"))
        guardrails = _compact(await profile.current_fields(conn, "guardrails"))
        baselines = _account_baselines(await profile.current_fields(conn, "performance"))
        what_worked = await learning.what_worked(conn)
        algorithm_briefs = await _algorithm_briefs(conn)
        open_contradictions = await _field_status_count(conn, "contradicted")
        stale_fields = await _field_status_count(conn, "stale")
    digest = await _recompute_peer_digest(tenant_id)

    context = {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "positioning": positioning,
        "goals": goals,
        "guardrails": guardrails,
        "account_baselines": baselines,
        "peer_digest": digest.model_dump(mode="json"),
        # the learning loop: measured results of past work + current platform
        # algorithm rules, so the plan reasons from what actually worked (R7/R2)
        "what_worked": what_worked,
        "algorithm_briefs": algorithm_briefs,
        "open_contradictions": open_contradictions,
        "profile_staleness": {
            "stale_fields": stale_fields,
            "note": "counts of profile field keys currently contradicted/stale (D2)",
        },
    }
    prompt = (
        f"Draft this brand's weekly plan for {period_start.date()} to {period_end.date()}.\n\n"
        f"Context (JSON):\n{json.dumps(context, separators=(',', ':'), default=str)}\n\n"
        f"{_PLAN_FORMAT}"
    )

    data = await providers.llm.complete_json("strategy", _PLAN_SYSTEM, prompt, max_tokens=4000)
    try:
        plan = _parse_plan(tenant_id, period_start, period_end, data)
    except (ValidationError, ValueError) as err:
        retry_prompt = (
            f"{prompt}\n\nYour previous response failed validation:\n{err}\n"
            "Return corrected JSON only."
        )
        data = await providers.llm.complete_json("strategy", _PLAN_SYSTEM, retry_prompt, max_tokens=4000)
        plan = _parse_plan(tenant_id, period_start, period_end, data)

    _enforce_item_contract(plan, digest, baselines)

    # Persist as a prescriptions row (052_strategy.sql). Each plan entry keeps
    # the table's documented shape AND the full donor item fields so
    # activate() has everything it needs to materialize work orders.
    plan_rows = []
    for item in plan.items:
        dumped = item.model_dump(mode="json")
        plan_rows.append(
            {
                "platform": item.platform,
                "format": item.content_type,
                "per_week": max(item.count, 1),
                "topics": [item.topic],
                "why": item.rationale,
                "evidence": dumped["evidence"],
                # full donor item fields (activation contract)
                "content_type": item.content_type,
                "topic": item.topic,
                "count": max(item.count, 1),
                "format_spec": item.format_spec,
                "rationale": item.rationale,
                "predicted_metrics": item.predicted_metrics,
            }
        )
    growth_actions = [g for g in (data.get("growth_actions") or []) if isinstance(g, dict)]
    week_of = date.today() - timedelta(days=date.today().weekday())  # Monday of the ISO week
    async with db.acquire(tenant_id) as conn:
        plan_id = await conn.fetchval(
            """INSERT INTO prescriptions (week_of, status, plan, growth_actions)
               VALUES ($1, 'proposed', $2::jsonb, $3::jsonb) RETURNING id""",
            week_of, json.dumps(plan_rows), json.dumps(growth_actions),
        )

    return {
        "plan_id": str(plan_id),
        "week_of": week_of.isoformat(),
        "status": "proposed",
        "plan": {
            "rationale": plan.rationale,
            "goals_snapshot": plan.goals_snapshot,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "items": [item.model_dump(mode="json") for item in plan.items],
        },
        "growth_actions": growth_actions,
    }


def _parse_plan(
    tenant_id: UUID | None, period_start: datetime, period_end: datetime, data: dict
) -> WeeklyPlan:
    items = data.get("items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if isinstance(evidence, str):
                item["evidence"] = [{"url": evidence}]
            elif isinstance(evidence, list):
                item["evidence"] = [{"url": e} if isinstance(e, str) else e for e in evidence]
    plan = WeeklyPlan.model_validate(
        {
            "brand_id": str(tenant_id or ""),
            "period_start": period_start,
            "period_end": period_end,
            "rationale": data.get("rationale", ""),
            "goals_snapshot": data.get("goals_snapshot") or {},
            "items": items,
        }
    )
    if not plan.items:
        raise ValueError("weekly plan contains no items")
    return plan


def _enforce_item_contract(plan: WeeklyPlan, digest: PeerDigest, baselines: list[dict]) -> None:
    """D5: every item leaves here with rationale, >=1 citation, predicted_metrics."""
    for item in plan.items:
        if not item.rationale.strip():
            item.rationale = plan.rationale or "See plan rationale."
        if not item.evidence:
            item.evidence = [_fallback_citation(digest, baselines)]
        if not item.predicted_metrics:
            item.predicted_metrics, note = _crude_prediction(digest, baselines)
            item.rationale = f"{item.rationale.rstrip('.')}. {note}"


def _fallback_citation(digest: PeerDigest, baselines: list[dict]) -> Citation:
    if digest.peers:
        return Citation(ref="peer_digest:latest_snapshots", note="recomputed from newest peer snapshots")
    if baselines:
        return Citation(
            ref=f"performance.baseline:{baselines[0]['platform']}", note="account baseline snapshot"
        )
    return Citation(ref="brand_profile", note="current profile fields")


def _crude_prediction(digest: PeerDigest, baselines: list[dict]) -> tuple[dict, str]:
    peer_median = digest.benchmarks.get("median_engagement")
    if isinstance(peer_median, (int, float)):
        return (
            {"engagement": round(peer_median * 0.5, 4), "basis": "peer_median_engagement_x0.5"},
            "Predicted engagement filled at 0.5x peer median engagement (crude v0 benchmark, D5).",
        )
    for account in baselines:
        own = (account.get("baseline") or {}).get("avg_engagement")
        if isinstance(own, (int, float)):
            return (
                {"engagement": round(own * 0.5, 4), "basis": f"own_{account['platform']}_baseline_x0.5"},
                f"Predicted engagement filled at 0.5x own {account['platform']} baseline"
                " (no peer benchmarks; crude v0, D5).",
            )
    return (
        {"engagement": 0.0, "basis": "no_benchmarks_available"},
        "No peer benchmarks or account baselines available; predicted engagement unknown (crude v0, D5).",
    )


# -------------------------------------------------------------- activation


def _json(value) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _sig(d: dict) -> tuple[str, str, str]:
    return (str(d.get("topic") or ""), str(d.get("platform") or ""), str(d.get("content_type") or ""))


async def activate(
    tenant_id: UUID | None, plan_id: str, item_indices: list[int] | None = None
) -> dict:
    """D5 replan on the actions queue: older plans' still-queued work orders
    are superseded — unless a queued order exactly matches an activated item
    of THIS plan on (topic, platform, content_type), in which case it
    reattaches (donor semantics: identical work in flight survives a replan).
    Materializes work orders from the plan entries (count>1 -> that many
    orders), due dates spread across the week in the payload.

    item_indices (PRD R2.3 partial acceptance): activate only the selected
    plan entries (0-based positions); None = all.

    Guards: idempotent re-activation — entries whose orders already exist
    (dedupe on payload plan_id+item_index) don't duplicate; activating a plan
    that a newer prescriptions row has overtaken raises
    ValueError('... superseded ...') which the route maps to 409."""
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT id, week_of, status, plan, accepted_items, created_at
               FROM prescriptions WHERE id = $1::uuid""",
            str(plan_id),
        )
        if row is None:
            raise ValueError(f"plan {plan_id} not found")
        newer = await conn.fetchval(
            "SELECT id FROM prescriptions WHERE created_at > $1 LIMIT 1", row["created_at"]
        )
        if newer is not None:
            raise ValueError(
                f"plan {plan_id} is superseded by a newer plan ({newer}); activate that instead"
            )

        items = [x for x in (_json(row["plan"]) or []) if isinstance(x, dict)]
        if item_indices is None:
            chosen = list(range(len(items)))
        else:
            chosen = sorted({i for i in item_indices if 0 <= i < len(items)})
            if not chosen:
                raise ValueError(
                    f"plan {plan_id}: item_indices {item_indices} select none of the {len(items)} items"
                )

        # already-materialized orders for THIS plan (idempotent re-activation)
        existing_count: dict[int, int] = {}
        for r in await conn.fetch(
            """SELECT payload FROM actions
               WHERE action_type = 'work_order' AND payload->>'plan_id' = $1""",
            str(row["id"]),
        ):
            p = _json(r["payload"]) or {}
            idx = p.get("item_index")
            if isinstance(idx, int):
                existing_count[idx] = existing_count.get(idx, 0) + 1

        # D5 replan: older plans' still-queued orders — reattach exact matches,
        # supersede the rest (via the validated state machine, never raw SQL).
        activated_sigs = {_sig(items[i]): i for i in chosen}
        superseded = 0
        reattached = 0
        for r in await conn.fetch(
            """SELECT id, status, payload FROM actions
               WHERE action_type = 'work_order' AND status = 'queued'
                 AND coalesce(payload->>'plan_id', '') <> $1""",
            str(row["id"]),
        ):
            payload = _json(r["payload"]) or {}
            match = activated_sigs.get(_sig(payload))
            if match is not None:
                payload["plan_id"] = str(row["id"])
                payload["item_index"] = match
                await conn.execute(
                    "UPDATE actions SET payload = $2::jsonb WHERE id = $1::uuid",
                    str(r["id"]), json.dumps(payload),
                )
                existing_count[match] = existing_count.get(match, 0) + 1
                reattached += 1
            else:
                await transition(
                    conn, str(r["id"]), set(_REPLACEABLE), WorkOrderStatus.SUPERSEDED
                )
                superseded += 1

        # older still-proposed prescriptions are overtaken by this activation
        await conn.execute(
            "UPDATE prescriptions SET status = 'expired' WHERE id <> $1::uuid AND status = 'proposed'",
            str(row["id"]),
        )

        # materialize: count>1 -> that many orders; due dates spread across
        # the week (donor spacing); slots already covered by existing/
        # reattached orders are skipped, keeping re-activation idempotent.
        week_start = datetime(
            row["week_of"].year, row["week_of"].month, row["week_of"].day, tzinfo=timezone.utc
        )
        span = timedelta(days=7)
        total = sum(max(int(items[i].get("count") or 1), 1) for i in chosen)
        created: list[str] = []
        slot = 0
        for i in chosen:
            it = items[i]
            have = existing_count.get(i, 0)
            for _ in range(max(int(it.get("count") or 1), 1)):
                slot += 1
                if have > 0:
                    have -= 1
                    continue
                payload = {
                    "plan_id": str(row["id"]),
                    "item_index": i,
                    "topic": str(it.get("topic") or ""),
                    "platform": str(it.get("platform") or ""),
                    "content_type": str(it.get("content_type") or it.get("format") or ""),
                    "format_spec": it.get("format_spec") or {},
                    "rationale": str(it.get("rationale") or it.get("why") or ""),
                    "evidence": it.get("evidence") or [],
                    "predicted_metrics": it.get("predicted_metrics") or {},
                    "due_at": (week_start + span * (slot / (total + 1))).isoformat(),
                }
                order_id = await conn.fetchval(
                    """INSERT INTO actions (proposed_by, action_type, payload, status)
                       VALUES ('strategist', 'work_order', $1::jsonb, 'queued') RETURNING id""",
                    json.dumps(payload),
                )
                created.append(str(order_id))

        # 'accepted' when every item has been activated (across activations),
        # else 'partial' (PRD R2.3)
        prev = {int(x) for x in (_json(row["accepted_items"]) or []) if isinstance(x, (int, float))}
        accepted_all = sorted(prev | set(chosen))
        status = "accepted" if len(accepted_all) >= len(items) else "partial"
        await conn.execute(
            "UPDATE prescriptions SET status = $2, accepted_items = $3::jsonb WHERE id = $1::uuid",
            str(row["id"]), status, json.dumps(accepted_all),
        )

        orders = [
            {"id": str(r["id"]), "status": r["status"], "payload": _json(r["payload"]) or {}}
            for r in await conn.fetch(
                """SELECT id, status, payload FROM actions
                   WHERE action_type = 'work_order' AND payload->>'plan_id' = $1
                   ORDER BY created_at""",
                str(row["id"]),
            )
        ]

    return {
        "plan_id": str(row["id"]),
        "plan_status": status,
        "activated_items": chosen,
        "accepted_items": accepted_all,
        "work_orders_created": len(created),
        "work_orders_reattached": reattached,
        "work_orders_superseded": superseded,
        "work_orders": orders,
    }


# ---------------------------------------------------------- morning brief


async def brief(tenant_id: UUID | None = None) -> dict:
    """Deterministic assembly, no LLM (donor v0): the work-order queue, due
    follow-ups, the recomputed peer digest, profile fields needing attention,
    and the latest plan's status."""
    now = datetime.now(timezone.utc)
    today_end = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)

    async with db.acquire(tenant_id) as conn:
        queue_counts: dict[str, int] = {}
        for r in await conn.fetch(
            "SELECT status, count(*) AS n FROM actions WHERE action_type = 'work_order' GROUP BY status"
        ):
            queue_counts[r["status"]] = int(r["n"])
        due = []
        for r in await conn.fetch(
            "SELECT id, status, payload FROM actions WHERE action_type = 'work_order' AND status = ANY($1)",
            [s.value for s in _BRIEF_DUE_STATUSES],
        ):
            p = _json(r["payload"]) or {}
            due_at = p.get("due_at")
            if not due_at:
                continue
            try:
                if datetime.fromisoformat(due_at) < today_end:
                    due.append({"id": str(r["id"]), "status": r["status"], **p})
            except ValueError:
                continue
        due.sort(key=lambda o: o.get("due_at") or "")
        followups = await action_service.due_followups(conn)
        contradicted = await _field_status_count(conn, "contradicted")
        stale = await _field_status_count(conn, "stale")
        plan_row = await conn.fetchrow(
            "SELECT id, week_of, status, plan FROM prescriptions ORDER BY created_at DESC LIMIT 1"
        )
    digest = await _recompute_peer_digest(tenant_id)

    attention = contradicted + stale
    sections = [
        {
            "title": "Due today",
            "body": "\n".join(
                f"{o.get('content_type', 'post')} on {o.get('platform', '?')}: "
                f"{o.get('topic', '')} [{o['status']}]"
                for o in due
            )
            or "Nothing due today.",
            "citations": [
                {"ref": f"work_order:{o['id']}", "note": o.get("topic", "")} for o in due
            ],
        },
        {
            "title": "Peer watch",
            "body": "\n".join(digest.observations) or "No peer snapshots yet.",
            "citations": [{"ref": "peer_digest:latest_snapshots", "note": digest.period}],
        },
        {
            "title": "Follow-ups due",
            "body": "\n".join(f"{it['title']} [{it['status']}]" for it in followups)
            or "No follow-ups due.",
            "citations": [
                {"ref": f"action_item:{it['id']}", "note": it["title"]} for it in followups
            ],
        },
    ]
    if attention:
        parts = []
        if contradicted:
            parts.append(f"{_plural(contradicted, 'contradicted field')}")
        if stale:
            parts.append(f"{_plural(stale, 'stale field')}")
        sections.append(
            {
                "title": "Needs your attention",
                "body": f"{' and '.join(parts)} in the profile.",
                "citations": [{"ref": "profile_fields", "note": "field status counts"}],
            }
        )
    plan_items = [x for x in (_json(plan_row["plan"]) if plan_row else []) or [] if isinstance(x, dict)]
    sections.append(
        {
            "title": "Weekly plan",
            "body": (
                f"Week of {plan_row['week_of'].isoformat()}: {plan_row['status']}, "
                f"{_plural(len(plan_items), 'item')}."
                if plan_row
                else "No weekly plan yet — run the strategist."
            ),
            "citations": [{"ref": f"prescription:{plan_row['id']}"}] if plan_row else [],
        }
    )

    actions: list[str] = []
    if due:
        actions.append(f"Move the {_plural(len(due), 'work order')} due today toward publish.")
    if followups:
        actions.append(f"Touch the {_plural(len(followups), 'follow-up')} waiting on an update.")
    if contradicted:
        actions.append(
            f"Resolve {_plural(contradicted, 'contradicted profile field')} with the Interviewer."
        )
    if stale:
        actions.append(f"Refresh {_plural(stale, 'stale field')} (re-run Researcher/Auditor).")
    if plan_row and plan_row["status"] == "proposed":
        actions.append("Review and activate the proposed weekly plan.")

    headline_parts = [
        f"{_plural(len(due), 'item')} due today",
        f"{_plural(len(followups), 'follow-up')} due",
    ]
    if attention:
        headline_parts.append(
            f"{_plural(attention, 'profile field')} need{'s' if attention == 1 else ''} attention"
        )
    headline = f"Morning brief: {', '.join(headline_parts)}."
    return {
        "date": now.isoformat(),
        "headline": headline,
        "sections": sections,
        "recommended_actions": actions,
        "queue_counts": queue_counts,
        "plan": (
            {
                "plan_id": str(plan_row["id"]),
                "week_of": plan_row["week_of"].isoformat(),
                "status": plan_row["status"],
                "item_count": len(plan_items),
            }
            if plan_row
            else None
        ),
    }


# --------------------------------------------------------------- gathering


def _plural(n: int, noun: str, plural: str | None = None) -> str:
    return f"{n} {noun if n == 1 else (plural or noun + 's')}"


def _compact(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for row in rows:
        key = f"{row['field_key']}[{row['item_key']}]" if row["item_key"] else row["field_key"]
        value = row["value"].get("v") if isinstance(row["value"], dict) else row["value"]
        out[key] = {"value": value, "status": row["status"], "confidence": row["confidence"]}
    return out


def _account_baselines(performance_rows: list[dict]) -> list[dict]:
    """Donor's ConnectedAccount.baseline equivalent on the substrate: the
    audited performance.baseline · item per platform (newest-first rows)."""
    out: list[dict] = []
    seen: set[str] = set()
    for f in performance_rows:
        if f["field_key"] != "performance.baseline" or not f["item_key"] or f["item_key"] in seen:
            continue
        value = f["value"].get("v") if isinstance(f["value"], dict) else f["value"]
        if isinstance(value, dict):
            seen.add(f["item_key"])
            out.append({"platform": f["item_key"], "baseline": value})
    return out


async def _algorithm_briefs(conn: asyncpg.Connection) -> list[dict]:
    """Latest platform_playbooks version per platform (the Algorithm Agent's
    versioned briefs — R2 grounding for the plan)."""
    rows = await conn.fetch(
        """SELECT DISTINCT ON (platform) platform, version, brief_md, key_points, refreshed_at
           FROM platform_playbooks ORDER BY platform, version DESC"""
    )
    out: list[dict] = []
    for r in rows:
        key_points = _json(r["key_points"]) or []
        out.append(
            {
                "platform": r["platform"],
                "version": r["version"],
                "key_points": key_points,
                "brief": (r["brief_md"] or "")[:800],
                "refreshed_at": r["refreshed_at"].isoformat(),
            }
        )
    return out


async def _recompute_peer_digest(tenant_id: UUID | None) -> PeerDigest:
    """PeerDigest-equivalent from each TRACKED watchlist peer's newest
    peer_snapshots row. Only approved peers feed strategy (the human-approval
    gate) — a rejected peer's stale snapshot must never leak into the plan
    rationale (rejected entries are excluded at read time). Entries that
    pre-date the merge (no status key) count as tracked (peers.py rule)."""
    now = datetime.now(timezone.utc)
    tracked = [
        e for e in await get_watchlist(tenant_id) if str(e.get("status") or "tracked") == "tracked"
    ]
    peers: list[dict] = []
    cadences: list[float] = []
    engagements: list[float] = []
    observations: list[str] = []
    async with db.acquire(tenant_id) as conn:
        for entry in tracked:
            peer_handle = str(entry.get("handle") or "")
            if not peer_handle:
                continue
            snap = await conn.fetchrow(
                """SELECT stats, captured_at FROM peer_snapshots
                   WHERE peer = $1 ORDER BY captured_at DESC LIMIT 1""",
                peer_handle,
            )
            if snap is None:
                continue
            metrics = _json(snap["stats"]) or {}
            posts_30d = metrics.get("posts_last_30d")
            engagement = metrics.get("avg_engagement")
            if isinstance(posts_30d, (int, float)):
                cadences.append(float(posts_30d))
            if isinstance(engagement, (int, float)):
                engagements.append(float(engagement))
            peers.append(
                {
                    "handle": peer_handle,
                    "platform": entry.get("platform", ""),
                    "kind": entry.get("kind", ""),
                    "followers": metrics.get("followers"),
                    "posts_last_30d": posts_30d,
                    "avg_engagement": engagement,
                    "top_posts": (metrics.get("top_posts") or [])[:3],
                    "captured_at": snap["captured_at"].isoformat(),
                }
            )
            observations.append(
                f"@{peer_handle} ({entry.get('platform', '')}, {entry.get('kind', '')}): "
                f"{metrics.get('followers', '?')} followers, {posts_30d if posts_30d is not None else '?'} "
                f"posts/30d, avg engagement {engagement if engagement is not None else '?'}"
            )
    benchmarks: dict = {}
    if cadences:
        benchmarks["cadence_per_week"] = round(median(cadences) / 30 * 7, 2)
    if engagements:
        benchmarks["median_engagement"] = round(median(engagements), 4)
    return PeerDigest(
        brand_id=str(tenant_id or ""),
        period=f"latest_snapshots_as_of_{now.date().isoformat()}",
        peers=peers,
        benchmarks=benchmarks,
        observations=observations,
    )


async def _field_status_count(conn: asyncpg.Connection, status: str) -> int:
    return (
        await conn.fetchval(
            "SELECT count(DISTINCT field_key) FROM profile_fields WHERE status = $1", status
        )
        or 0
    )


__all__ = ["run", "activate", "brief"]
