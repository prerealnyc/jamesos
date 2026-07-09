"""The daily brand-manager heartbeat — bm2.0's full cycle ported onto the
scheduled_jobs scheduler (P2 of the unification plan).

One registered job per tenant (kind='manager_daily_cycle') runs the
sense→think→act→learn loop, every step best-effort — one failing step never
blocks the rest:

  1. MEASURE     — auto-pull analytics for published work (R7), notice goal
                   misses and replan (rate-limited nag + corrective draft),
                   put spend behind measured winners (promote scan)
  2. HYGIENE     — nightly mark_stale on the profile envelope (D2 TTLs)
  3. ALGORITHM   — per-platform ranking briefs re-researched when stale (R2, 7d)
  4. THE EYES    — content radar, trends, press, questions, appearances
  5. AUTOPILOT   — opt-in: top fresh suggestions are drafted through the
                   EXISTING content engine (voice-QA'd, into the approval
                   queue). Nothing publishes; the human gate never moves.
  6. DIGEST      — the 'chunk for the day' assembled, and emailed when the
                   tenant opted in.

A second registered job (kind='manager_peer_snapshot', weekly) snapshots the
human-approved peers. The five pre-merge dormant intelligence kinds are
retired here — one brain, no duplicate spend.
"""

import json
import logging
from uuid import UUID

from .. import db
from ..config import settings
from . import actions
from . import profile as profile_svc
from .providers import get_providers

logger = logging.getLogger("manager.heartbeat")

AUTOPILOT_MAX_DRAFTS = 2  # per cycle — a steady drip, not a flood; gate stays human

# the pre-merge dormant intelligence kinds this heartbeat replaces
RETIRED_KINDS = (
    "daily_brand_research",
    "brand_interview",
    "playbook_refresh",
    "peer_snapshot",
    "weekly_prescription",
)
MANAGER_JOBS = (
    ("manager_daily_cycle", 24),
    ("manager_peer_snapshot", 24 * 7),
    # James's R2 rhythm: the strategist produces the weekly quantified
    # prescription ON THE CLOCK — draft only; activation stays human.
    ("manager_weekly_strategist", 24 * 7),
)


async def _tenant_config(tenant_id: UUID | None) -> dict:
    async with db.acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return cfg or {}


async def _onboarded(tcfg: dict) -> bool:
    """The heartbeat only beats for brands that finished onboarding — a fresh
    tenant must meet the interviewer before the eyes/strategist write anything
    (a startup-scheduled cycle on an empty tenant used to fake 'onboarded' to
    the front door). intake_agent sets onboarding_status='active'."""
    return (tcfg or {}).get("onboarding_status") == "active"


async def run_daily_cycle(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """The scheduler handler. Returns a step→outcome report (also useful for
    the manual trigger endpoint)."""
    report: dict = {}
    tcfg = await _tenant_config(tenant_id)
    if not _is_manual(config) and not await _onboarded(tcfg):
        return {"skipped": "onboarding not complete — the cycle starts after Brand Setup"}

    try:  # 1 — learn from what's out there before planning more (R7)
        from . import learning

        report["auto_measure"] = await learning.auto_measure(tenant_id)
    except Exception:
        logger.exception("auto-measure failed for tenant %s", tenant_id)
        report["auto_measure"] = "failed"

    try:  # 1b — notice a goal miss and replan (the deepest "it thinks" move)
        report["goal_misses"] = await _goal_check(tenant_id)
    except Exception:
        logger.exception("goal check failed for tenant %s", tenant_id)
        report["goal_misses"] = "failed"

    try:  # 1c — put spend behind measured winners (promote suggestions)
        report["promote_candidates"] = await _promote_scan(tenant_id)
    except Exception:
        logger.exception("promote scan failed for tenant %s", tenant_id)
        report["promote_candidates"] = "failed"

    try:  # 2 — envelope hygiene (D2 staleness TTLs, the 'nightly mark_stale')
        async with db.acquire(tenant_id) as conn:
            report["stale_marked"] = await profile_svc.mark_stale(conn)
    except Exception:
        logger.exception("mark_stale failed for tenant %s", tenant_id)
        report["stale_marked"] = "failed"

    try:  # 3 — weekly algorithm cadence (R2.5): refresh only when stale
        from . import algorithm

        async with db.acquire(tenant_id) as conn:
            stale = await algorithm.is_stale(conn)
        if stale:
            await algorithm.run(tenant_id, config)
            report["algorithm"] = "refreshed"
        else:
            report["algorithm"] = "fresh"
    except Exception:
        logger.exception("algorithm refresh failed for tenant %s", tenant_id)
        report["algorithm"] = "failed"

    # 2 — the eyes scan
    from .eyes import appearances, opportunities, press, questions, trends

    eyes = (
        ("content_radar", opportunities), ("trends", trends), ("press", press),
        ("questions", questions), ("appearances", appearances),
    )
    for name, eye in eyes:
        try:
            out = await eye.run(tenant_id, config)
            report[name] = {k: v for k, v in out.items() if isinstance(v, (int, str))} or "ok"
        except Exception:
            logger.exception("%s scan failed for tenant %s", name, tenant_id)
            report[name] = "failed"

    try:  # 3 — autopilot: suggestions become waiting drafts (opt-in per tenant)
        report["autopilot_drafted"] = await _autopilot(tenant_id, tcfg)
    except Exception:
        logger.exception("autopilot failed for tenant %s", tenant_id)
        report["autopilot_drafted"] = "failed"

    # 4 — the chunk for the day
    async with db.acquire(tenant_id) as conn:
        digest = await actions.run_daily_cycle(conn)
    report["digest"] = digest["summary"]

    try:  # 4b — deliver it to the owner (opt-in per tenant)
        report["digest_emailed"] = await _deliver_digest(tenant_id, tcfg, digest)
    except Exception:
        logger.exception("digest delivery failed for tenant %s", tenant_id)
        report["digest_emailed"] = False

    return report


async def _autopilot(tenant_id: UUID | None, tcfg: dict) -> int:
    """James's stated later-phase: 'later on we will make the BM do them by
    itself.' When tenants.config.manager_autopilot is on, the cycle drafts the
    top fresh content suggestions through the EXISTING james-os content engine
    (grounded, voice-QA'd, honest refusal without a voice corpus) so drafts
    are waiting in the approval queue. Nothing publishes."""
    if not tcfg.get("manager_autopilot"):
        return 0
    from ..content import ContentBrief, generate_content

    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, title, topic, format, why FROM content_suggestions
               WHERE status = 'suggested' AND action_ref IS NULL
               ORDER BY created_at DESC LIMIT 6"""
        )
    platform = str(tcfg.get("primary_platform") or "instagram")
    drafted = 0
    for row in rows:
        if drafted >= AUTOPILOT_MAX_DRAFTS:
            break
        try:
            draft = await generate_content(
                ContentBrief(
                    platform=platform,
                    format="reel_script" if row["format"] == "reel" else "post",
                    topic=f"{row['topic']} — {row['why']}"[:400],
                ),
                tenant_id,
            )
            if getattr(draft, "action_id", None):
                async with db.acquire(tenant_id) as conn:
                    await conn.execute(
                        "UPDATE content_suggestions SET action_ref = $2::uuid WHERE id = $1::uuid",
                        str(row["id"]), str(draft.action_id),
                    )
                drafted += 1
        except Exception:
            logger.exception("autopilot draft failed for suggestion %s", row["id"])
    if drafted:
        logger.info("autopilot drafted %d suggestion(s) for tenant %s", drafted, tenant_id)
    return drafted


async def _deliver_digest(tenant_id: UUID | None, tcfg: dict, digest: dict) -> bool:
    """Email the daily chunk to the owner when tenants.config.digest_email is
    set — the heartbeat should reach the owner, not wait in a dashboard."""
    to = tcfg.get("digest_email")
    if not to:
        return False
    name = tcfg.get("brand_name") or "Your brand"
    items = "".join(
        f"<li><b>{i.get('title', '')}</b> — {i.get('why', '')}</li>" for i in (digest.get("items") or [])
    ) or "<li>All clear today.</li>"
    html = (
        f"<div><p>{digest['summary']}</p><ul>{items}</ul>"
        f"<p>Open the dashboard to act on any of these.</p></div>"
    )
    result = await get_providers().email.send(
        to=[to],
        subject=f"{name} — your brand manager's chunk for {digest['date']}",
        html=html,
    )
    return bool(result.ok)


async def _goal_check(tenant_id: UUID | None) -> int:
    """PRD 'it has to think': when a north-star goal is tracking off pace, the
    manager NOTICES and replans by itself — a flagged action item for the owner
    plus a fresh draft weekly plan (activation stays human). Replans at most
    once per ISO week so a persistent miss nags, not spams."""
    from datetime import datetime, timedelta, timezone

    from . import learning

    async with db.acquire(tenant_id) as conn:
        misses = await learning.goal_gap(conn)
        if not misses:
            return 0
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        for m in misses:
            await actions.upsert_action(
                conn,
                kind="general",
                title=f"Off pace: {m['platform']} {m['metric'].replace('_', ' ')}",
                detail=(
                    f"At {m['elapsed_pct']}% of the goal horizon you're at {m['current']} but should be "
                    f"near {m['expected_now']} to hit {m['target']}. I've drafted a corrective weekly "
                    "plan — review and activate it."
                ),
                meta={"source": "goal_check", **m},
                dedupe_key=f"goalmiss:{week}:{m['platform']}.{m['metric']}",
            )
        newest = await conn.fetchval(
            "SELECT max(created_at) FROM prescriptions"
        )
    # one corrective draft plan per week, and only if this week hasn't produced one
    now = datetime.now(timezone.utc)
    if newest is None or (now - (newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc))) > timedelta(days=6):
        from . import strategist

        await strategist.run(tenant_id, {"trigger": "goal_miss"})
        logger.info("goal miss -> corrective weekly plan drafted for tenant %s", tenant_id)
    return len(misses)


async def _promote_scan(tenant_id: UUID | None) -> int:
    """Put spend behind measured winners: every clear over-performer becomes a
    visibility action item (dedupe on the work order, so it suggests once)."""
    from . import learning

    async with db.acquire(tenant_id) as conn:
        cands = await learning.promote_candidates(conn)
        for cand in cands:
            await actions.upsert_action(
                conn,
                kind="visibility",
                title=f"Boost the winner: {cand['topic'][:120]}",
                detail=(
                    f"This piece did {cand['ratio']}x your median engagement "
                    f"({cand['engagement']:.0f} vs median {cand['median']}). Put promote spend "
                    f"behind it while it's warm.{' ' + cand['url'] if cand['url'] else ''}"
                ),
                meta={"source": "promote", "work_order_id": cand["work_order_id"],
                      "platform": cand["platform"], "ratio": cand["ratio"]},
                dedupe_key=f"promote:{cand['work_order_id']}",
            )
    return len(cands)


def _is_manual(config: dict | None) -> bool:
    """A human pressing the button overrides the onboarding gate — the
    scheduled heartbeat does not."""
    return (config or {}).get("trigger") == "manual"


async def run_peer_snapshot(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """Weekly: snapshot the human-approved peers (the P2 peers module)."""
    from . import peers

    if not _is_manual(config) and not await _onboarded(await _tenant_config(tenant_id)):
        return {"skipped": "onboarding not complete"}
    return await peers.snapshot_all(tenant_id, config)


async def run_weekly_strategist(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """Weekly: the strategist drafts the quantified prescription on the clock.
    Draft only — activation stays human (D5)."""
    from . import strategist

    if not _is_manual(config) and not await _onboarded(await _tenant_config(tenant_id)):
        return {"skipped": "onboarding not complete"}
    return await strategist.run(tenant_id, {**(config or {}), "trigger": "schedule"})


async def ensure_manager_jobs() -> int:
    """Idempotent registration, called when the scheduler loop starts:
    retire the five dormant pre-merge kinds everywhere, and register the
    manager jobs for every tenant that has opted into manager_v2 (or all
    tenants when the global settings.manager_v2 flag is on)."""
    if not settings.manager_scheduler_enabled:
        return 0
    registered = 0
    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM scheduled_jobs WHERE kind = ANY($1::text[])", list(RETIRED_KINDS)
        )
        tenants = await conn.fetch("SELECT id, config FROM tenants")
    for t in tenants:
        cfg = t["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg or "{}")
        if not (settings.manager_v2 or (cfg or {}).get("manager_v2")):
            continue
        async with db.acquire(t["id"]) as conn:
            for kind, cadence in MANAGER_JOBS:
                await conn.execute(
                    """INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (tenant_id, kind) DO NOTHING""",
                    t["id"], kind, cadence,
                )
        registered += 1
    if registered:
        logger.info("manager jobs ensured for %d tenant(s)", registered)
    return registered
