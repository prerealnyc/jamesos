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
    "topic_hint": "",      # the space to research, e.g. "Staten Island commercial RE"
    "research_focus": "",  # optional override of the virality research angle
    "last_run_date": "",   # YYYY-MM-DD of the last completed run
}

# Autopilot is virality-first: it researches what's working RIGHT NOW before
# inventing anything. This is the default angle of that research.
_VIRALITY_FOCUS = (
    "what is going viral and trending RIGHT NOW in short-form video — the "
    "specific hooks, formats, angles, and topics that are working on Instagram "
    "Reels, TikTok, and YouTube Shorts. List concrete, currently-working patterns."
)


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
You are given LIVE market research and trends describing what is working /
going viral RIGHT NOW, plus the brand voice.

Invent {n} content ideas that RIDE these specific trends — each idea must be
traceable to something in the research/trends (a hook pattern, format, or
topic that's currently working). Each MUST be a SINGLE-ARC STORY angle (a
first-person moment, decision, or lesson) in the brand's voice — never a
listicle, never "N tips". Be specific; no generic platitudes.

Return STRICT JSON:
{{"ideas": [{{"title": str, "topic": str (a one-line story prompt the writer
will expand, phrased as a personal story), "pillar": str,
"trend_basis": str (the specific trend/insight this idea rides)}}]}}"""


async def _gather_intel(cfg: dict, tenant_id: UUID | None) -> dict | None:
    """Virality-first: run LIVE market research + pull trends BEFORE ideating.
    Returns None when no real intel is available — the caller then refuses to
    generate, so Autopilot never produces off-trend content."""
    from .ingestion import ingest_many
    from .research import get_research_provider, research_to_events

    provider = get_research_provider()
    if provider.name == "stub":
        return None  # no live research → no viral signal → don't ideate

    subject = (cfg.get("topic_hint") or "").strip() or "this brand's industry and niche"
    focus = (cfg.get("research_focus") or "").strip() or _VIRALITY_FOCUS
    try:
        result = await provider.research(subject, focus)
    except Exception:  # noqa: BLE001
        return None
    if result.is_empty():
        return None

    # Persist the research into memory (citable + reusable), best-effort.
    try:
        await ingest_many(research_to_events(result), tenant_id)
    except Exception:  # noqa: BLE001
        pass

    async with acquire(tenant_id) as conn:
        trend_rows = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category'='trend' AND superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 5"
        )
    trends = [(r["raw_content"] or "")[:200] for r in trend_rows]
    return {
        "provider": result.provider,
        "subject": subject,
        "summary": result.summary,
        "findings": result.findings,
        "sources": [s.url for s in result.sources],
        "trends": trends,
    }


async def _voice_for_ideation(tenant_id: UUID | None) -> str:
    async with acquire(tenant_id) as conn:
        voice = await conn.fetch(
            "SELECT raw_content FROM events "
            "WHERE payload->>'category'='voice_corpus' AND superseded_by IS NULL "
            "AND length(raw_content) > 200 ORDER BY random() LIMIT 3"
        )
    return "\n---\n".join((r["raw_content"] or "")[:500] for r in voice) or "(no voice corpus)"


async def generate_ideas(
    n: int, intel: dict, tenant_id: UUID | None = None
) -> list[dict]:
    """Ideas grounded in live research/trends (what's working) + voice (tone)."""
    n = max(1, min(n, 10))
    voice = await _voice_for_ideation(tenant_id)
    findings = "\n".join(f"- {f}" for f in (intel.get("findings") or [])[:12])
    trends = "\n".join(f"- {t}" for t in (intel.get("trends") or []))
    ctx = (
        f"LIVE MARKET RESEARCH (subject: {intel.get('subject')}, via "
        f"{intel.get('provider')}):\n{intel.get('summary','')}\n\n"
        f"KEY FINDINGS (what's working now):\n{findings or '(none)'}\n\n"
        f"SCRAPED TRENDS:\n{trends or '(none — research only)'}\n\n"
        f"BRAND VOICE (write in this voice):\n{voice}"
    )
    try:
        out = await get_llm().complete_json(
            system=_IDEA_SYSTEM.format(n=n),
            messages=[{"role": "user", "content": ctx}],
            max_tokens=1000, temperature=0.8,
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
                "trend_basis": str(it.get("trend_basis") or "").strip(),
            })
    return ideas


# ─────────────────────────────────────────────────────────── batch ──

def _run_row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    for k in ("ideas", "results", "research"):
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
        # ── 1) Virality-first: research what's working BEFORE ideating ──
        intel = await _gather_intel(cfg, tenant_id)
        if intel is None:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE autopilot_runs SET status='failed', error=$2, "
                    "completed_at=now() WHERE id=$1",
                    run_id,
                    "No live market research/trends available — Autopilot only "
                    "ideates after research to stay viral-aligned. Add a "
                    "Perplexity key (and Apify for scraped trends).",
                )
            return await get_run(run_id, tenant_id)

        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE autopilot_runs SET research=$2::jsonb WHERE id=$1",
                run_id, json.dumps(intel),
            )

        # ── 2) Ideate strictly from that intel ──
        ideas = await generate_ideas(count, intel, tenant_id)
        if not ideas:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE autopilot_runs SET status='failed', "
                    "error='ideation produced no ideas from the research', "
                    "completed_at=now() WHERE id=$1",
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
