"""Autopilot — autonomous daily content.

On a schedule (or on demand) it:
  1. invents fresh, STORY-framed ideas grounded in the brand voice, pillars,
     recent research/trends, and the learned rejection guardrails,
  2. drafts a piece for each idea through the normal content engine
     (voice-QA + guardrails),
  3. drops them in the approval queue.

Nothing publishes — a human still approves every piece. Story-framing is
baked into ideation because the voice data shows listicles score poorly and
single-arc stories score high.

Config lives per-tenant in tenants.config -> 'autopilot' (same pattern as
credentials). Run history is the autopilot_runs table.

Honest scope: the scheduler is in-process (asyncio), so scheduled runs only
fire while the server is up. It's restart-safe (it checks last_run_date in
config, not a fragile timer), but a 24/7 deployment needs a real worker.
"""

import json
from datetime import UTC, date, datetime
from uuid import UUID

from .content import generate_content
from .db import acquire
from .llm import get_llm
from .models import ContentBrief

DEFAULT_CONFIG = {
    "enabled": False,
    "daily_count": 3,
    "platforms": ["instagram"],
    "format": "reel_script",
    "hour": 9,             # local hour (server time) to run the daily batch
    "topic_hint": "",      # optional steer, e.g. "Staten Island commercial RE"
    "last_run_date": "",   # YYYY-MM-DD of the last completed run
}


# ─────────────────────────────────────────────────────────── config ──

async def get_config(tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    ap = (cfg or {}).get("autopilot", {}) or {}
    return {**DEFAULT_CONFIG, **ap}


async def set_config(updates: dict, tenant_id: UUID | None = None) -> dict:
    cur = await get_config(tenant_id)
    allowed = set(DEFAULT_CONFIG)
    merged = {**cur, **{k: v for k, v in updates.items() if k in allowed}}
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{autopilot}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(merged),
        )
    return merged


# ─────────────────────────────────────────────────────────── ideation ──

_IDEA_SYSTEM = """You are the content strategist for a personal brand.
Invent {n} fresh content ideas. Each MUST be a SINGLE-ARC STORY angle (a
first-person moment, decision, or lesson) — never a listicle, never "N tips".
Ground them in the brand voice and pillars shown. Be specific and concrete;
no generic platitudes.

Return STRICT JSON:
{{"ideas": [{{"title": str, "topic": str (a one-line story prompt the writer
will expand, phrased as a personal story), "pillar": str}}]}}"""


async def _ideation_context(hint: str, tenant_id: UUID | None) -> str:
    async with acquire(tenant_id) as conn:
        voice = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category'='voice_corpus' AND superseded_by IS NULL "
            "AND length(raw_content) > 200 ORDER BY random() LIMIT 3"
        )
        trends = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category'='trend' AND superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 3"
        )
    v = "\n---\n".join((r["raw_content"] or "")[:500] for r in voice) or "(no voice corpus)"
    t = "\n".join((r["raw_content"] or "")[:200] for r in trends)
    ctx = f"BRAND VOICE:\n{v}"
    if t:
        ctx += f"\n\nWHAT'S TRENDING (for inspiration):\n{t}"
    if hint:
        ctx += f"\n\nFOCUS HINT: {hint}"
    return ctx


async def generate_ideas(n: int, hint: str, tenant_id: UUID | None = None) -> list[dict]:
    n = max(1, min(n, 10))
    ctx = await _ideation_context(hint, tenant_id)
    try:
        out = await get_llm().complete_json(
            system=_IDEA_SYSTEM.format(n=n),
            messages=[{"role": "user", "content": ctx}],
            max_tokens=900, temperature=0.8,
        )
    except Exception:  # noqa: BLE001
        return []
    ideas = []
    for it in (out.get("ideas") or [])[:n]:
        topic = str(it.get("topic") or it.get("title") or "").strip()
        if topic:
            ideas.append({
                "title": str(it.get("title") or "").strip(),
                "topic": topic,
                "pillar": str(it.get("pillar") or "").strip(),
            })
    return ideas


# ─────────────────────────────────────────────────────────── batch ──

def _run_row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    for k in ("ideas", "results"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("created_at", "completed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def run_batch(
    trigger: str = "manual", tenant_id: UUID | None = None
) -> dict:
    """Generate today's batch: ideas → drafts → approval queue. Durable."""
    cfg = await get_config(tenant_id)
    count = int(cfg.get("daily_count", 3))
    platforms = cfg.get("platforms") or ["instagram"]
    fmt = cfg.get("format", "reel_script")

    async with acquire(tenant_id) as conn:
        run_id = await conn.fetchval(
            "INSERT INTO autopilot_runs (status, trigger, requested) "
            "VALUES ('running',$1,$2) RETURNING id",
            trigger, count,
        )

    try:
        ideas = await generate_ideas(count, cfg.get("topic_hint", ""), tenant_id)
        if not ideas:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE autopilot_runs SET status='failed', "
                    "error='ideation produced no ideas', completed_at=now() WHERE id=$1",
                    run_id,
                )
            return await get_run(run_id, tenant_id)

        results = []
        generated = queued = 0
        for idea in ideas:
            platform = platforms[0]
            draft = await generate_content(ContentBrief(
                platform=platform, format=fmt,
                pillar=idea.get("pillar", ""), topic=idea["topic"],
                extra_instructions="Write as a single-arc first-person story, "
                                   "not a list. Decisive ownership, concrete specifics.",
            ), tenant_id)
            generated += 1
            if draft.action_id:
                queued += 1
            results.append({
                "title": idea.get("title", ""),
                "platform": draft.platform,
                "voice_score": draft.voice_score,
                "status": draft.status,
                "action_id": str(draft.action_id) if draft.action_id else None,
            })

        async with acquire(tenant_id) as conn:
            await conn.execute(
                """UPDATE autopilot_runs SET status='succeeded', generated=$2,
                   queued=$3, ideas=$4::jsonb, results=$5::jsonb, completed_at=now()
                   WHERE id=$1""",
                run_id, generated, queued, json.dumps(ideas), json.dumps(results),
            )
        # mark today's date so the scheduler won't double-run
        await set_config({"last_run_date": date.today().isoformat()}, tenant_id)
        return await get_run(run_id, tenant_id)

    except Exception as e:  # noqa: BLE001
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE autopilot_runs SET status='failed', error=$2, "
                "completed_at=now() WHERE id=$1",
                run_id, str(e)[:500],
            )
        return await get_run(run_id, tenant_id)


async def get_run(run_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow("SELECT * FROM autopilot_runs WHERE id=$1", run_id)
    return _run_row(r) if r else None


async def list_runs(tenant_id: UUID | None = None, limit: int = 20) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM autopilot_runs ORDER BY created_at DESC LIMIT $1", limit
        )
    return [_run_row(r) for r in rows]


# ─────────────────────────────────────────────────────────── scheduler ──

def should_run_today(cfg: dict, now: datetime | None = None) -> bool:
    if not cfg.get("enabled"):
        return False
    now = now or datetime.now(UTC)
    if now.hour < int(cfg.get("hour", 9)):
        return False
    return cfg.get("last_run_date", "") != now.date().isoformat()


__all__ = [
    "get_config", "set_config", "generate_ideas", "run_batch",
    "get_run", "list_runs", "should_run_today", "DEFAULT_CONFIG",
]
