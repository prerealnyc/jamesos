"""The learning read-back loop — 'it learns from the work and improves'.
Ported from bm2.0 backend/app/services/learning.py (+ the measure step from
services/execution.py) onto the substrate: work orders are actions-queue rows
(action_type='work_order', lifecycle in payload), insights live in the events
substrate (embedded, Ask-retrievable).

This is James's R7 loop: MEASURE published work automatically from the
connected accounts, and READ the results back so the daily and weekly
strategists plan from what actually worked for THIS brand — not just goals
and competitor tactics. No fabricated numbers — a post we can't find
analytics for stays unmeasured, honestly.
"""

import json
import logging
from datetime import datetime, timezone
from statistics import median
from uuid import UUID

import asyncpg

from .. import db
from ..models import EventCreate, EventSource
from .contracts import WorkOrderStatus
from .providers import get_providers
from .state_machine import MEASURE_FROM, transition

logger = logging.getLogger("manager.learning")

_INSIGHT_LIMIT = 8
_MEASURE_AFTER_HOURS = 24  # let a post breathe before reading its numbers

# goal-pace rules: judge only after a quarter of the horizon has elapsed, and
# call a miss when tracking below 80% of the on-pace expectation.
_GOAL_MIN_ELAPSED = 0.25
_GOAL_PACE_TOLERANCE = 0.8
_PROMOTE_MIN_RATIO = 1.5  # a measured post this far above the median is a boost candidate


def _engagement(metrics: dict) -> float:
    return float(
        sum(v for k, v in metrics.items() if k in ("likes", "comments", "shares", "saves", "views") and isinstance(v, (int, float)))
    )


def _payload(row: asyncpg.Record | dict) -> dict:
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else (p or {})


async def _work_orders(conn: asyncpg.Connection, status: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, status, payload FROM actions WHERE action_type = 'work_order' AND status = $1",
        status,
    )
    return [{"id": str(r["id"]), "status": r["status"], **_payload(r)} for r in rows]


async def what_worked(conn: asyncpg.Connection, limit: int = _INSIGHT_LIMIT) -> list[dict]:
    """The compact 'measured results of past work' block for planning prompts.
    Measured work orders first (hard numbers), then narrative insights."""
    out: list[dict] = []
    measured = await conn.fetch(
        """SELECT payload FROM actions WHERE action_type = 'work_order' AND status = 'measured'
           ORDER BY (payload->>'measured_at') DESC NULLS LAST LIMIT $1""",
        limit,
    )
    for r in measured:
        p = _payload(r)
        actual = {k: v for k, v in (p.get("actual_metrics") or {}).items() if isinstance(v, (int, float))}
        if not actual:
            continue
        out.append(
            {
                "topic": str(p.get("topic", ""))[:120],
                "format": p.get("content_type"),
                "platform": p.get("platform"),
                "actual": actual,
                "predicted": {k: v for k, v in (p.get("predicted_metrics") or {}).items() if isinstance(v, (int, float))},
            }
        )
    insights = await conn.fetch(
        """SELECT payload FROM events
           WHERE payload->>'category' = 'insight' AND superseded_by IS NULL
           ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    seen_topics = {r["topic"] for r in out}
    for row in insights:
        if len(out) >= limit:
            break
        p = _payload(row)
        if str(p.get("topic", "")) in seen_topics:
            continue
        out.append({"insight": str(p.get("text", p.get("content", "")))[:220], "format": p.get("channel")})
    return out


async def measure(
    conn: asyncpg.Connection, work_order_id: str, actual_metrics: dict | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Close the loop: record actual impact against the prediction, move the
    order published→measured (D5), and write a learnable insight into the
    events substrate."""
    row = await conn.fetchrow(
        "SELECT id, status, payload FROM actions WHERE id = $1::uuid AND action_type = 'work_order'",
        work_order_id,
    )
    if row is None:
        raise ValueError(f"work order {work_order_id} not found")
    payload = _payload(row)
    payload["actual_metrics"] = actual_metrics or {
        "note": "per-post analytics not yet wired — capture is manual in v0 (PRD R7)"
    }
    payload["measured_at"] = datetime.now(timezone.utc).isoformat()
    await transition(conn, work_order_id, MEASURE_FROM, WorkOrderStatus.MEASURED)
    await conn.execute(
        "UPDATE actions SET payload = $2::jsonb WHERE id = $1::uuid",
        work_order_id, json.dumps(payload),
    )

    insight_text, delta = _impact_insight(payload)
    from ..ingestion import ingest

    await ingest(
        EventCreate(
            event_type="note",
            payload={
                "category": "insight", "text": insight_text, "topic": payload.get("topic", ""),
                "channel": payload.get("content_type"), "work_order_id": work_order_id,
                "predicted": payload.get("predicted_metrics"), "actual": payload.get("actual_metrics"),
                "delta": delta,
            },
            raw_content=insight_text,
            source=EventSource(adapter="manager_measure", dedupe_key=f"insight-{work_order_id}"),
        ),
        tenant_id=tenant_id,
    )
    return {"work_order_id": work_order_id, "status": "measured", "insight": insight_text, "delta": delta}


def _impact_insight(payload: dict) -> tuple[str, dict]:
    """Compare predicted vs actual on any shared numeric keys; produce a plain
    sentence the strategist can later read back (category='insight' memory)."""
    predicted, actual = payload.get("predicted_metrics") or {}, payload.get("actual_metrics") or {}
    delta: dict = {}
    for key in set(predicted) & set(actual):
        p, a = predicted.get(key), actual.get(key)
        if isinstance(p, (int, float)) and isinstance(a, (int, float)):
            delta[key] = round(a - p, 4)
    topic = payload.get("topic", "")
    kind = payload.get("content_type", "post")
    if delta:
        parts = ", ".join(f"{k}: predicted {predicted[k]}, actual {actual[k]} (Δ{delta[k]:+})" for k in delta)
        verdict = "outperformed" if all(v >= 0 for v in delta.values()) else "underperformed vs plan"
        text = f"'{topic}' ({kind}) {verdict}. {parts}."
    else:
        text = (
            f"'{topic}' ({kind}) was published; impact capture is pending "
            "(no per-post metrics yet). Log actuals to learn what worked for this brand."
        )
    return text, delta


async def auto_measure(tenant_id: UUID | None = None) -> dict:
    """Close the loop automatically: for published social posts, find their real
    numbers in the aggregator's post history and record them as actual_metrics
    (work order -> measured + an insight in memory). Blog/email have no
    analytics source yet — they stay manual (an honest gap, not a guess)."""
    providers = get_providers()
    async with db.acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
        if isinstance(cfg, str):
            cfg = json.loads(cfg or "{}")
        profile_key = (cfg or {}).get("postproxy_profile_key", "")
        published = [
            wo for wo in await _work_orders(conn, "published")
            if wo.get("content_type") in ("social_post", "text_post")
        ]
    if not published:
        return {"measured": 0, "note": "nothing published awaiting measurement"}
    if not profile_key:
        return {"measured": 0, "note": "no connected social profile — cannot pull post analytics"}

    now = datetime.now(timezone.utc)
    measured = 0
    notes: list[str] = []
    history_cache: dict[str, dict[str, dict]] = {}
    for wo in published:
        ref = wo.get("published_ref") or {}
        published_at = ref.get("at")
        if published_at:
            try:
                age_h = (now - datetime.fromisoformat(published_at)).total_seconds() / 3600
                if age_h < _MEASURE_AFTER_HOURS:
                    notes.append(f"{wo['id']}: published {age_h:.0f}h ago — measuring after {_MEASURE_AFTER_HOURS}h")
                    continue
            except ValueError:
                pass
        post_ids = {
            str(p.get("id")) for p in (ref.get("detail") or {}).get("post_ids", []) if isinstance(p, dict) and p.get("id")
        }
        if not post_ids:
            notes.append(f"{wo['id']}: no platform post ids recorded — measure manually")
            continue
        platform = wo.get("platform", "")
        if platform not in history_cache:
            try:
                posts = await providers.social.post_history(profile_key, platform, months=1)
                history_cache[platform] = {p.post_id: p.metrics for p in posts}
            except Exception as exc:
                notes.append(f"{platform}: post history unavailable ({str(exc)[:80]})")
                history_cache[platform] = {}
        match = next((history_cache[platform][pid] for pid in post_ids if pid in history_cache[platform]), None)
        if match is None:
            notes.append(f"{wo['id']}: post not found in {platform} history yet")
            continue
        actual = {k: v for k, v in match.items() if isinstance(v, (int, float))}
        actual["engagement"] = _engagement(match)
        async with db.acquire(tenant_id) as conn:
            await measure(conn, wo["id"], actual, tenant_id=tenant_id)
        measured += 1
    return {"measured": measured, "notes": notes[:6]}


async def _current_baselines(conn: asyncpg.Connection) -> dict[str, dict]:
    """{platform: {metric: value}} from the audited channels.* envelope fields
    (the substrate's stand-in for bm2.0's ConnectedAccount.baseline)."""
    from . import profile

    out: dict[str, dict] = {}
    for f in await profile.current_fields(conn, "channels"):
        parts = f["field_key"].split(".")
        if len(parts) != 3:
            continue
        _, platform, metric = parts
        v = f["value"].get("v")
        if isinstance(v, (int, float)):
            out.setdefault(platform, {})[metric] = v
    return out


async def goal_gap(conn: asyncpg.Connection) -> list[dict]:
    """'It notices it's missing the goal' — compare each numeric north-star
    goal (goals.{platform}.{metric}: baseline/target/timeframe_days) against
    the account's current audited baseline. A goal is a MISS when, after at
    least a quarter of its horizon, the current value tracks below 80% of the
    on-pace expectation. Non-numeric goals (narrative) are skipped honestly."""
    from . import profile

    baselines = await _current_baselines(conn)
    now = datetime.now(timezone.utc)
    misses: list[dict] = []
    for f in await profile.current_fields(conn, "goals"):
        parts = f["field_key"].split(".")
        if len(parts) != 3:
            continue  # narrative goals (business_primary, success_90d) have no pace math
        _, platform, metric = parts
        v = f["value"].get("v")
        if not isinstance(v, dict):
            continue
        baseline, target, days = v.get("baseline"), v.get("target"), v.get("timeframe_days")
        if not all(isinstance(x, (int, float)) for x in (baseline, target, days)) or not days:
            continue
        set_at = f["created_at"] if f["created_at"].tzinfo else f["created_at"].replace(tzinfo=timezone.utc)
        elapsed = min(max((now - set_at).days / float(days), 0.0), 1.0)
        if elapsed < _GOAL_MIN_ELAPSED:
            continue  # too early to judge
        current = baselines.get(platform, {}).get(metric)
        if not isinstance(current, (int, float)):
            continue  # no fresh audit for this platform/metric — nothing honest to say
        expected = baseline + (target - baseline) * elapsed
        if target > baseline and current < expected * _GOAL_PACE_TOLERANCE:
            misses.append(
                {
                    "platform": platform, "metric": metric,
                    "current": round(float(current), 3), "expected_now": round(expected, 3),
                    "target": target, "elapsed_pct": round(elapsed * 100),
                }
            )
    return misses


async def promote_candidates(conn: asyncpg.Connection) -> list[dict]:
    """The money move James asked for: find measured posts that clearly beat
    the brand's own median and suggest putting spend behind them. Returns the
    candidates; the caller persists them as action items."""
    measured = await _work_orders(conn, "measured")
    scored = [
        (float((wo.get("actual_metrics") or {}).get("engagement", 0)), wo)
        for wo in measured
        if isinstance((wo.get("actual_metrics") or {}).get("engagement"), (int, float))
    ]
    if len(scored) < 3:
        return []  # a median of one or two is noise, not a benchmark
    mid = median(s for s, _ in scored)
    if mid <= 0:
        return []
    return [
        {
            "work_order_id": wo["id"], "topic": wo.get("topic", ""), "platform": wo.get("platform", ""),
            "engagement": s, "median": round(mid, 2), "ratio": round(s / mid, 2),
            "url": (wo.get("published_ref") or {}).get("url", ""),
        }
        for s, wo in scored
        if s >= mid * _PROMOTE_MIN_RATIO
    ]
