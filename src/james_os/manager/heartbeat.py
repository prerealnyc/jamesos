"""The daily brand-manager heartbeat — bm2.0's full cycle ported onto the
scheduled_jobs scheduler (P2 of the unification plan).

One registered job per tenant (kind='manager_daily_cycle') runs the
sense→think→act→learn loop, every step best-effort — one failing step never
blocks the rest:

  0. HYGIENE     — nightly mark_stale on the profile envelope (D2 TTLs)
  1. ALGORITHM   — per-platform ranking briefs re-researched when stale (R2, 7d)
  2. THE EYES    — content radar, trends, press, questions, appearances
  3. AUTOPILOT   — opt-in: top fresh suggestions are drafted through the
                   EXISTING content engine (voice-QA'd, into the approval
                   queue). Nothing publishes; the human gate never moves.
  4. DIGEST      — the 'chunk for the day' assembled, and emailed when the
                   tenant opted in.

The P3 steps (auto-measure, goal-check, promote scan) slot in at the top of
this cycle when the learning port lands — the step list is the contract.

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
)


async def _tenant_config(tenant_id: UUID | None) -> dict:
    async with db.acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return cfg or {}


async def run_daily_cycle(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """The scheduler handler. Returns a step→outcome report (also useful for
    the manual trigger endpoint)."""
    report: dict = {}
    tcfg = await _tenant_config(tenant_id)

    try:  # 0 — envelope hygiene (D2 staleness TTLs, the 'nightly mark_stale')
        async with db.acquire(tenant_id) as conn:
            report["stale_marked"] = await profile_svc.mark_stale(conn)
    except Exception:
        logger.exception("mark_stale failed for tenant %s", tenant_id)
        report["stale_marked"] = "failed"

    try:  # 1 — weekly algorithm cadence (R2.5): refresh only when stale
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


async def run_peer_snapshot(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """Weekly: snapshot the human-approved peers (the P2 peers module)."""
    from . import peers

    return await peers.snapshot_all(tenant_id, config)


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
