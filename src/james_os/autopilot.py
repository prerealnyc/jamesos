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
    # When on, each video reel in a bulk batch is produced in a DIFFERENT
    # style from the Style Template library (cycling). Empty library →
    # silently falls back to the standard engaging_avatar look.
    "use_style_templates": True,
    # B-roll animator for every reel in the batch: '' = system default,
    # 'runway', or 'higgsfield' (image→video). Lets daily batches run on a
    # chosen engine without touching the global provider.
    "broll_engine": "",
    # How batch reels choose their caption style:
    #   "rotate" — each video gets the next style in the showcase rotation
    #              (compare looks on real renders, finalise favourites);
    #   "smart"  — the LLM picks the best-fitting style PER VIDEO from the
    #              script (hook-driven → viral_hook, hot take →
    #              magenta_blocks, launch → editorial_serif, …);
    #   "template" — the replicated style template's analysed preset wins
    #              (auto-pick only when the template has none).
    "caption_mode": "rotate",
    "caption_rotation_offset": 0,
    "last_run_date": "",   # YYYY-MM-DD of the last completed run
}

# Server-maintained bookkeeping. Only the batch runners write these; a
# settings save from the UI must never overwrite them — echoing back a stale
# last_run_date would make the scheduler re-fire today's batch, and a stale
# caption_rotation_offset would restart the caption rotation.
_BOOKKEEPING_KEYS = frozenset({"last_run_date", "caption_rotation_offset"})

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


async def set_config(
    updates: dict, tenant_id: UUID | None = None, *, internal: bool = False
) -> dict:
    """Patch the autopilot config with only the submitted keys.

    Public saves (internal=False, the API route) silently drop the
    bookkeeping keys so a stale UI snapshot can never rewind last_run_date
    (duplicate daily batch) or reset caption_rotation_offset. The batch
    runners pass internal=True to advance them.

    The merge happens in SQL (`||` onto the existing autopilot object), so
    concurrent writers — scheduler, bulk batch, UI — only touch the keys
    they submit instead of clobbering each other with full stale copies.
    """
    allowed = set(DEFAULT_CONFIG) if internal else set(DEFAULT_CONFIG) - _BOOKKEEPING_KEYS
    patch = {k: v for k, v in updates.items() if k in allowed}
    async with acquire(tenant_id) as conn:
        merged = await conn.fetchval(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{autopilot}', "
            "coalesce(config->'autopilot','{}'::jsonb) || $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid "
            "RETURNING config->'autopilot'",
            json.dumps(patch),
        )
    if isinstance(merged, str):
        merged = json.loads(merged)
    return {**DEFAULT_CONFIG, **(merged or {})}


# ─────────────────────────────────────────────────────────── ideation ──

_IDEA_SYSTEM = """You are the content strategist for a personal brand.
You are given LIVE market research and trends describing what is working /
going viral RIGHT NOW, plus the brand voice. You may also be given a
CURATED COHORT — creators on the brand's speaking-targets watchlist whose
interests overlap the topic. When that cohort and its trend events are
present, bias ideas toward angles those creators are currently working;
they are the people the brand is trying to ride alongside, so an idea
they could plausibly amplify is more valuable than a generic global trend.

Invent {n} content ideas that RIDE these specific trends — each idea must be
traceable to something in the research/trends/cohort (a hook pattern, format,
or topic that's currently working). Each MUST be a SINGLE-ARC STORY angle (a
first-person moment, decision, or lesson) in the brand's voice — never a
listicle, never "N tips". Be specific; no generic platitudes.

Return STRICT JSON:
{{"ideas": [{{"title": str, "topic": str (a one-line story prompt the writer
will expand, phrased as a personal story), "pillar": str,
"trend_basis": str (the specific trend/insight this idea rides — name the
creator when riding a COHORT TREND)}}]}}"""


async def _gather_intel(cfg: dict, tenant_id: UUID | None) -> dict | None:
    """Virality-first: run LIVE market research + pull trends BEFORE ideating.
    Returns None when no real intel is available — the caller then refuses to
    generate, so Autopilot never produces off-trend content.

    Cohort bias: when topic_hint matches the interests of any watchlist
    creator (e.g. 'Real Estate' on Shawn Ryan when topic_hint is 'Staten
    Island commercial RE'), pulls that creator's recent trend events as a
    second feed for ideation. The cohort is the speaking-targets list — so
    autopilot will preferentially ride angles those creators are working,
    not just whatever happens to be the most-recent global trend.
    """
    from . import xpoz_intel
    from .ingestion import ingest_many
    from .research import get_research_provider, research_to_events
    from .trends import list_cohort_trends

    subject = (cfg.get("topic_hint") or "").strip() or "this brand's industry and niche"

    # Xpoz live-trend feed — real high-engagement posts in the niche RIGHT
    # NOW across X/IG/TikTok/Reddit. Its own viral signal, so it also lets
    # autopilot ideate even WITHOUT a Perplexity key.
    xpoz_posts: list[dict] = []
    if xpoz_intel.configured():
        try:
            tr = await xpoz_intel.trending_in_niche(subject, limit=6, days=7)
            xpoz_posts = tr.get("results") or []
        except Exception:  # noqa: BLE001 — a research feed must never break a batch
            xpoz_posts = []

    provider = get_research_provider()
    if provider.name == "stub":
        # No Perplexity — but Xpoz alone is a valid viral signal.
        if not xpoz_posts:
            return None
        return {
            "provider": "xpoz", "subject": subject, "summary": "", "findings": [],
            "sources": [p["url"] for p in xpoz_posts if p.get("url")],
            "trends": [], "xpoz_trending": xpoz_posts,
            "cohort_creators": [], "cohort_trends": [],
        }

    focus = (cfg.get("research_focus") or "").strip() or _VIRALITY_FOCUS
    try:
        result = await provider.research(subject, focus)
    except Exception:  # noqa: BLE001
        return None
    if result.is_empty() and not xpoz_posts:
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

    # Curated-cohort trends — only present when the topic_hint matches the
    # interests of a watchlist creator AND that creator has been scraped at
    # least once. Empty list is honest, not a failure.
    cohort = await list_cohort_trends(subject, limit=8, tenant_id=tenant_id)

    return {
        "provider": result.provider if not result.is_empty() else "xpoz",
        "subject": subject,
        "summary": result.summary,
        "findings": result.findings,
        "sources": [s.url for s in result.sources] + [p["url"] for p in xpoz_posts if p.get("url")],
        "trends": trends,
        "xpoz_trending": xpoz_posts,
        "cohort_creators": cohort["creators"],
        "cohort_trends": cohort["trends"],
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
    """Ideas grounded in live research/trends (what's working) + voice (tone).

    Two trend feeds when both are available:
      * SCRAPED TRENDS: most-recent global trend events
      * COHORT TRENDS: events from watchlist creators whose interests
        overlap with the autopilot topic_hint — the speaking-targets list
        the brand is trying to ride alongside. Prompt asks the LLM to bias
        ideas toward this feed when it's populated.
    """
    n = max(1, min(n, 10))
    voice = await _voice_for_ideation(tenant_id)
    findings = "\n".join(f"- {f}" for f in (intel.get("findings") or [])[:12])
    trends = "\n".join(f"- {t}" for t in (intel.get("trends") or []))

    cohort_creators = intel.get("cohort_creators") or []
    cohort_trends = intel.get("cohort_trends") or []
    if cohort_creators:
        cohort_label = ", ".join(
            f"{c['name']} (@{c['handle']} · {c['platform']})"
            + (f" interested in {'/'.join(c['interests'][:3])}" if c.get('interests') else "")
            for c in cohort_creators[:6]
        )
    else:
        cohort_label = ""
    if cohort_trends:
        cohort_block = "\n".join(
            f"- [{t['platform']} @{t['handle']} score={t['outlier_score']:.2f}] "
            f"{t['caption']}"
            for t in cohort_trends
        )
    else:
        cohort_block = ""

    cohort_section = ""
    if cohort_creators or cohort_trends:
        # Even if scrape hasn't run yet we show the matched creators so
        # ideation knows whose lane we're in. The "no trend events yet"
        # note is honest, not a fake signal.
        cohort_section = (
            f"\nCURATED COHORT (creators on the brand's speaking-targets "
            f"watchlist whose interests overlap '{intel.get('subject','')}'):"
            f"\n{cohort_label or '(no name labels yet)'}\n"
            f"\nCOHORT TRENDS (events from those creators — bias ideas "
            f"toward these angles when present):\n"
            f"{cohort_block or '(no scraped trend events yet — refresh the watchlist on Social Companion to populate)'}\n"
        )

    # Xpoz live viral posts in the niche — the strongest "what's working
    # RIGHT NOW" signal: real posts ranked by engagement across X/IG/TikTok/
    # Reddit. Ideas should ride these hooks/angles (NOT copy the words).
    from . import xpoz_intel
    xpoz_lines = xpoz_intel.trending_lines(intel.get("xpoz_trending") or [])
    xpoz_section = (
        "\nLIVE VIRAL POSTS IN YOUR NICHE (Xpoz — real high-engagement posts "
        "right now; ride these hooks/angles, do NOT copy their words):\n"
        + "\n".join(f"- {ln}" for ln in xpoz_lines) + "\n"
    ) if xpoz_lines else ""

    ctx = (
        f"LIVE MARKET RESEARCH (subject: {intel.get('subject')}, via "
        f"{intel.get('provider')}):\n{intel.get('summary','')}\n\n"
        f"KEY FINDINGS (what's working now):\n{findings or '(none)'}\n\n"
        f"SCRAPED TRENDS:\n{trends or '(none — research only)'}\n"
        f"{xpoz_section}"
        f"{cohort_section}\n"
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


async def reap_orphaned_runs(tenant_id: UUID | None = None) -> int:
    """Mark interrupted runs as failed. Called at startup so a process
    restart doesn't leave 'running' rows orphaned forever."""
    async with acquire(tenant_id) as conn:
        n = await conn.fetchval(
            """UPDATE autopilot_runs
               SET status='failed', stage='reaped',
                   error='interrupted — server restarted before this run finished',
                   completed_at=now()
               WHERE status='running'
               RETURNING (SELECT count(*)::int FROM autopilot_runs WHERE stage='reaped')"""
        )
    return int(n or 0)


async def _stage(run_id, stage: str, tenant_id: UUID | None = None) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE autopilot_runs SET stage=$2 WHERE id=$1", run_id, stage
        )


async def run_batch(
    trigger: str = "manual", tenant_id: UUID | None = None
) -> dict:
    """Generate today's batch: ideas → drafts → approval queue. Durable.
    Writes its current `stage` to autopilot_runs so the UI sees progress."""
    cfg = await get_config(tenant_id)
    count = int(cfg.get("daily_count", 3))
    platforms = cfg.get("platforms") or ["instagram"]
    fmt = cfg.get("format", "reel_script")

    async with acquire(tenant_id) as conn:
        run_id = await conn.fetchval(
            "INSERT INTO autopilot_runs (status, trigger, requested, stage) "
            "VALUES ('running',$1,$2,'starting') RETURNING id",
            trigger, count,
        )

    try:
        # ── 1) Virality-first: research what's working BEFORE ideating ──
        await _stage(run_id, "researching trends", tenant_id)
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
        await _stage(run_id, "ideating from trends", tenant_id)
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
        total = len(ideas)
        for i, idea in enumerate(ideas, 1):
            await _stage(run_id, f"drafting {i}/{total}: {idea.get('title','')[:50]}", tenant_id)
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

        await _stage(run_id, "saving", tenant_id)
        async with acquire(tenant_id) as conn:
            await conn.execute(
                """UPDATE autopilot_runs SET status='succeeded', stage='done',
                   generated=$2, queued=$3, ideas=$4::jsonb, results=$5::jsonb,
                   completed_at=now() WHERE id=$1""",
                run_id, generated, queued, json.dumps(ideas), json.dumps(results),
            )
        # mark today's date so the scheduler won't double-run
        await set_config(
            {"last_run_date": date.today().isoformat()}, tenant_id, internal=True
        )
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
