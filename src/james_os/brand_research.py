"""The daily research job — the brand manager doing its homework.

James: "research spaceports, come up with five videos a day that are the
most compelling things about space exploration based on publicly available
data, plus whatever data we give it."

Per tenant, once a day (scheduled_jobs kind='daily_brand_research'):
  1. read the brand profile (identity, goals, pillars, taboos)
  2. one fresh web-research pass on the brand's domain (what's new/
     compelling TODAY) — filed into memory like every research brief
  3. ideation: N concrete content suggestions (mix of posts + reels),
     each with a WHY, deduped against the last 14 days of suggestions
  4. rows land in content_suggestions → the Suggestions rail; accepting
     one routes through the normal production machinery into the queue.

Costs are bounded: one research call + one ideation call per tenant per
day; nothing renders until a human accepts.
"""

from __future__ import annotations

import json
from uuid import UUID

from .db import acquire
from .llm import get_llm

_IDEATE_SYSTEM = """You are the brand manager for the brand described in
<brand_profile>. Using TODAY'S research briefing and the brand's own memory
themes, propose the {n} strongest content pieces to make TODAY.

Rules:
* Each suggestion is ONE specific piece: a punchy internal title (≤ 90
  chars), the content topic a writer works from (the specific claim/story/
  fact, ≤ 220 chars), a format ("post" or "reel" — lean reel for visual/
  story material, post for takes/analysis), and WHY it will perform for
  THIS brand's goals (≤ 140 chars, cite the briefing when it's the source).
* Serve the brand's goals and topic pillars; NEVER touch the taboos.
* No two suggestions may cover the same angle, and none may repeat the
  <recent_suggestions> list.
* Concrete beats clever: numbers, named places, real events.

Return STRICT JSON:
{{"suggestions": [{{"title": str, "topic": str, "format": "post"|"reel",
                    "why": str}}, ...]}}
"""


async def run_daily_brand_research(
    tenant_id: UUID, config: dict | None = None,
) -> int:
    config = config or {}
    n = max(3, min(10, int(config.get("n") or 6)))

    from .brands import brand_profile_block, get_brand_profile
    profile = await get_brand_profile(tenant_id)
    if not profile or not profile.get("intake_done"):
        # No identity yet — nothing responsible to suggest.
        return 0
    block = await brand_profile_block(tenant_id)

    # ── fresh domain research (filed into memory like any brief) ──
    ident = profile.get("identity") or {}
    domain = (ident.get("positioning") or ident.get("mission")
              or ident.get("name") or "the brand's field")
    briefing = ""
    try:
        from .ingestion import ingest_many
        from .research import get_research_provider, research_to_events
        res = await get_research_provider().research(
            subject=str(domain)[:200],
            focus="what is new, compelling, or newsworthy TODAY — concrete "
                  "stories, numbers, and developments a content creator "
                  "could publish about",
        )
        if not res.is_empty():
            briefing = (res.summary + "\n" + "\n".join(
                f"- {f}" for f in res.findings[:12]))[:6000]
            # File the briefing into memory like every research pass — the
            # brand's intelligence compounds even when no suggestion is taken.
            try:
                await ingest_many(research_to_events(res), tenant_id=tenant_id)
            except Exception as e:  # noqa: BLE001 — filing is best-effort
                print(f"[brand_research] briefing not filed: {e}")
    except Exception as e:  # noqa: BLE001 — research down ≠ no suggestions
        print(f"[brand_research] research pass skipped: {e}")

    # ── dedupe context: what we already suggested recently ──
    async with acquire(tenant_id) as conn:
        recent = await conn.fetch(
            """SELECT title FROM content_suggestions
                WHERE created_at > now() - interval '14 days'
                ORDER BY created_at DESC LIMIT 60""")
    recent_titles = [r["title"] for r in recent]

    out = await get_llm().complete_json(
        system=_IDEATE_SYSTEM.format(n=n),
        messages=[{"role": "user", "content":
                   f"{block}\n\n<todays_briefing>\n{briefing or '(no fresh briefing available)'}\n"
                   f"</todays_briefing>\n\n<recent_suggestions>\n"
                   + "\n".join(f"- {t}" for t in recent_titles[:40])
                   + "\n</recent_suggestions>"}],
        max_tokens=1600, temperature=0.6,
    )
    raw = (out or {}).get("suggestions") or []
    made = 0
    async with acquire(tenant_id) as conn:
        for s in raw[:n]:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "").strip()[:140]
            topic = str(s.get("topic") or "").strip()[:280]
            if not title or not topic:
                continue
            if any(title.lower() == t.lower() for t in recent_titles):
                continue
            fmt = "reel" if str(s.get("format")) == "reel" else "post"
            await conn.execute(
                """INSERT INTO content_suggestions
                     (source, title, topic, format, why, evidence)
                   VALUES ('daily_research', $1, $2, $3, $4, $5::jsonb)""",
                title, topic, fmt, str(s.get("why") or "")[:200],
                json.dumps([{"kind": "briefing"}] if briefing else []),
            )
            made += 1
    print(f"[brand_research] tenant {tenant_id}: {made} suggestion(s)")
    return made


__all__ = ["run_daily_brand_research"]
