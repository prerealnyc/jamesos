"""Algorithm Agent — 'how does each platform reward content RIGHT NOW?'.

James's R2: prescriptions must be grounded in platform-algorithm research
refreshed on a cadence — 'it has to know where to look, how often to look'.
This agent researches each connected platform's current ranking behavior
(web + news via the provider layer, never the model's memory alone), distills
a per-platform brief (rules, recent changes, what to do about it), and lands
it twice: as a versioned platform_playbooks row (james-os's diff/change-
detected strategy input) and as a cited ProfileField
(performance.algorithm_brief · item per platform, source=researched) — so it
carries confidence, citations, and a last-refreshed date for free (D1/D2).
Ported from bm2.0 backend/app/agents/algorithm.py.
"""

import json
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from .. import db
from . import profile, runs
from .contracts import Citation, FieldWrite, Source
from .providers import get_providers

AGENT = "algorithm"
_FALLBACK_PLATFORMS = ("instagram", "tiktok", "linkedin")
_NON_PLATFORM_CHANNEL_KEYS = {"local", "website"}  # channels.* that aren't social platforms
_MAX_RESULTS_PER_PLATFORM = 6
STALE_AFTER_DAYS = 7  # PRD R2.5: refreshed on a cadence, re-checked when stale

PROMPT_SYSTEM = (
    "You are a platform algorithm analyst. From fresh reporting and platform "
    "announcements, you distill how each social platform's ranking currently "
    "works and what a brand should do about it. Only claims supported by the "
    "sources given — no folklore, no outdated rules."
)
PROMPT = (
    "Distill a current algorithm brief per platform from the results below.\n\n"
    "PLATFORMS: {platforms}\n\n"
    "RESULTS (platform :: title : snippet : url):\n{results}\n\n"
    "Return JSON only: {{\"briefs\": [{{\"platform\": str, \"summary\": str "
    "(2-3 sentences: how ranking works now), \"rules\": [str (concrete, "
    "actionable)], \"recent_changes\": [str], \"do_now\": [str (what THIS kind "
    "of brand should change)], \"links\": [url]}}], \"note\": str}}. One brief "
    "per platform; each brief cites at least one link from the results. Keep "
    "briefs TIGHT: max 4 rules, max 2 recent_changes, max 3 do_now, one line each."
)


def _platforms(fields: list[dict]) -> list[str]:
    """Connected platforms from the channels profile fields (the substrate has
    no connected_accounts table — the auditor/researcher write
    channels.<platform>.* keys instead), else the donor's fallback list."""
    seen: list[str] = []
    for f in fields:
        if f["section"] != "channels":
            continue
        parts = f["field_key"].split(".")
        if len(parts) < 3:  # e.g. channels.website — not a platform key
            continue
        p = parts[1].lower()
        if p not in _NON_PLATFORM_CHANNEL_KEYS and p not in seen:
            seen.append(p)
    return seen or list(_FALLBACK_PLATFORMS)


async def is_stale(conn: asyncpg.Connection, days: int = STALE_AFTER_DAYS) -> bool:
    """True when no playbook exists or the newest is past the refresh cadence."""
    newest = await conn.fetchval("SELECT max(refreshed_at) FROM platform_playbooks")
    if newest is None:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).days >= days


def _brief_md(b: dict) -> str:
    """Markdown rendering of the structured brief for platform_playbooks.brief_md."""
    lines = [str(b.get("summary") or "").strip()]
    for heading, key in (("Rules", "rules"), ("Recent changes", "recent_changes"), ("Do now", "do_now")):
        items = [str(x).strip() for x in (b.get(key) or []) if str(x).strip()]
        if items:
            lines.append(f"\n**{heading}**")
            lines.extend(f"- {x}" for x in items)
    return "\n".join(lines)[:6000]


async def _land_playbook(
    conn: asyncpg.Connection, platform: str, brief_md: str, key_points: list[str], sources: list[str]
) -> dict:
    """Insert the next platform_playbooks version, change-detected against the
    previous one (same drift rule as strategy.refresh_playbook: 'changed' means
    meaningful drift in the atomic claims, not cosmetic rewording)."""
    prev = await conn.fetchrow(
        "SELECT version, key_points FROM platform_playbooks "
        "WHERE platform=$1 ORDER BY version DESC LIMIT 1", platform)
    prev_points: set[str] = set()
    version = 1
    if prev:
        version = int(prev["version"]) + 1
        pp = prev["key_points"]
        if isinstance(pp, str):
            pp = json.loads(pp)
        prev_points = {str(p).strip().lower() for p in (pp or [])}
    new_points = {p.strip().lower() for p in key_points}
    overlap = len(prev_points & new_points)
    changed = bool(prev_points) and overlap < 0.6 * max(1, min(len(prev_points), len(new_points)))
    await conn.execute(
        """INSERT INTO platform_playbooks
             (platform, version, brief_md, key_points, sources, changed, refreshed_at)
           VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, now())""",
        platform, version, brief_md, json.dumps(key_points), json.dumps(sources), changed)
    return {"platform": platform, "version": version, "changed": changed}


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(AGENT, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn, "channels")
        platforms = _platforms(fields)

        year = datetime.now(timezone.utc).year
        lines: list[str] = []
        for p in platforms:
            try:
                results = await providers.search.search(
                    f"{p} algorithm ranking changes {year}", num=_MAX_RESULTS_PER_PLATFORM
                )
                for r in results[:_MAX_RESULTS_PER_PLATFORM]:
                    lines.append(f"- {p} :: {r.title}: {r.snippet[:100]} ({r.url})")
            except Exception as exc:
                lines.append(f"- {p} :: (search unavailable: {str(exc)[:60]})")
            try:
                for n in (await providers.news.search(f"{p} algorithm update", days=60))[:3]:
                    lines.append(f"- {p} :: {n.title}: {n.snippet[:100]} ({n.url})")
            except Exception:
                pass  # news lane is additive; search alone is enough

        raw = await providers.llm.complete_json(
            "content", PROMPT_SYSTEM,
            PROMPT.format(platforms=", ".join(platforms), results="\n".join(lines[:30]) or "(no results)"),
            max_tokens=8000,  # 5 platforms x structured brief overflows the 4k default
        )
        briefs = [b for b in (raw.get("briefs") or []) if isinstance(b, dict) and b.get("platform")]
        playbooks: list[dict] = []
        async with db.acquire(tenant_id) as conn:
            for b in briefs:
                platform = str(b["platform"]).lower().strip()
                links = [str(u) for u in (b.get("links") or []) if isinstance(u, str)][:5]
                key_points = [
                    str(x)[:300]
                    for x in list(b.get("rules") or []) + list(b.get("recent_changes") or [])
                    + list(b.get("do_now") or [])
                ][:12]
                playbooks.append(await _land_playbook(conn, platform, _brief_md(b), key_points, links))
                # the donor's ProfileField write, preserved: the envelope keeps
                # the cited, confidence-scored copy the interviewer/strategist read
                await profile.write_field(
                    conn,
                    FieldWrite(
                        section="performance",
                        field_key="performance.algorithm_brief",
                        item_key=platform,
                        value={
                            "summary": b.get("summary", ""),
                            "rules": b.get("rules") or [],
                            "recent_changes": b.get("recent_changes") or [],
                            "do_now": b.get("do_now") or [],
                            "refreshed": datetime.now(timezone.utc).date().isoformat(),
                        },
                        source=Source.RESEARCHED,
                        citations=[Citation(url=u) for u in links],
                        updated_by=AGENT,
                    ),
                )
        report = {
            "platforms": platforms,
            "briefs": briefs,
            "playbooks": playbooks,
            "results_scanned": len([ln for ln in lines if "http" in ln]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle,
        output={
            "briefs_written": len(playbooks),
            "changed": [p["platform"] for p in playbooks if p["changed"]],
        },
    )
    return report
