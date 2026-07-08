"""The eyes — bm2.0's scanning agents, ported onto the substrate (P2).

Each eye is a scheduler-shaped job: `run(tenant_id, config) -> report`.
Findings land twice, by design: as a content_suggestions row (the accept/
dismiss rail the existing UI already reads) and as a deduped action_items
card (the follow-ups surface). Every finding carries its WHY and citations;
re-running an eye never duplicates a suggestion.
"""

import json

import asyncpg


async def suggest(
    conn: asyncpg.Connection,
    *,
    source: str,
    title: str,
    topic: str,
    format: str = "post",
    why: str = "",
    evidence: list[str] | None = None,
) -> str | None:
    """Insert a content suggestion unless the same (source, title) is already
    on the rail — dismissed/accepted included: the owner's decision stands."""
    existing = await conn.fetchval(
        "SELECT id FROM content_suggestions WHERE source = $1 AND title = $2 LIMIT 1",
        source, title[:300],
    )
    if existing is not None:
        return None
    row_id = await conn.fetchval(
        """INSERT INTO content_suggestions (source, title, topic, format, why, evidence)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING id""",
        source, title[:300], topic[:300], format, why,
        json.dumps([{"url": u} for u in (evidence or [])[:6]]),
    )
    return str(row_id)


def niche_block(fields: list[dict]) -> str:
    """The brand's niche/positioning lines an eye grounds its prompt on."""
    keep = ("positioning", "identity", "audience")
    lines = [
        f"- {f['field_key']}: {str(f['value'].get('v'))[:150]}"
        for f in fields
        if f["section"] in keep
    ]
    return "\n".join(lines[:20]) or "(niche not yet researched)"


def niche_terms(fields: list[dict]) -> list[str]:
    """Industry search terms from the profile — industry + pillar topics."""
    terms: list[str] = []
    for f in fields:
        v = f["value"].get("v")
        if f["field_key"] in ("identity.industry", "positioning.niche", "positioning.category") and v:
            terms.append(str(v))
        if f["field_key"].startswith("positioning.pillar") and v:
            terms.append(str(v))
    return terms[:3] or ["the industry"]
