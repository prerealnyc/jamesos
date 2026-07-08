"""Appearances Agent — 'where could we show up to borrow a bigger audience?'.
Scans for guest/podcast/speaking/event opportunities in the niche — the
guest-appearance ladder (get on the bigger show/stage to reach up a tier).
Proposes which to pursue, why they fit, and how to approach, as trackable
outreach action items cited to the source.
Ported from bm2.0 backend/app/agents/appearances.py.
"""

import json
from uuid import UUID

from ... import db
from .. import actions as action_service
from .. import profile, runs
from ..providers import get_providers
from . import suggest

AGENT = "appearances"
_SEEDS = ("podcast guests", "conference OR summit 2026 speakers", "interview OR keynote")
_MAX_PER_QUERY = 8

PROMPT_SYSTEM = (
    "You are a brand's booking strategist. From what's out there — podcasts, "
    "conferences, shows, panels in the niche — you pick the appearance "
    "opportunities that would put this brand in front of the right, bigger "
    "audience, and suggest how to approach each. You never fabricate a booking "
    "contact; you suggest a path to verify."
)
PROMPT = (
    "From the results below, pick the appearance opportunities (podcasts, "
    "events, shows, panels) this brand should pursue to reach a bigger, "
    "relevant audience.\n\n"
    "BRAND:\n{brand}\n\n"
    "OPPORTUNITIES (title : snippet : url):\n{results}\n\n"
    "Return JSON only: {{\"appearances\": [{{\"title\": str, \"kind\": "
    "\"podcast|conference|show|panel|interview\", \"fit\": str (why the "
    "audience matches), \"approach\": str (how to pitch — a path to verify, "
    "not a fabricated contact), \"links\": [url]}}], \"note\": str}}. Only "
    "genuinely relevant opportunities, each with a link from the results."
)


def _brand_ctx(name: str, entity_type: str, fields: list[dict]) -> tuple[str, str]:
    lines = [f"- name: {name}", f"- type: {entity_type}"]
    seed = name
    for f in fields:
        v = f["value"].get("v")
        if f["field_key"] in ("positioning.niche", "identity.industry", "positioning.pillar_topics") and v:
            lines.append(f"- {f['field_key']}: {str(v)[:150]}")
            if f["field_key"] in ("positioning.niche", "identity.industry"):
                seed = str(v)
    return "\n".join(lines[:12]), seed


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(AGENT, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)
            brand = await conn.fetchrow("SELECT kind, identity FROM brand_profiles")

        # brand identity lives on the tenant's brand_profiles row here (no
        # Brand model); fall back to the researched display_name field.
        ident = brand["identity"] if brand else {}
        if isinstance(ident, str):
            ident = json.loads(ident)
        name = str(
            ident.get("name")
            or next((f["value"].get("v") for f in fields if f["field_key"] == "identity.display_name"), None)
            or "the brand"
        )
        ctx, seed = _brand_ctx(name, str(brand["kind"] if brand else "person"), fields)

        lines: list[str] = []
        for s in _SEEDS:
            try:
                for r in (await providers.search.search(f"{seed} {s}", num=_MAX_PER_QUERY))[:_MAX_PER_QUERY]:
                    lines.append(f"- {r.title}: {r.snippet[:110]} ({r.url})")
            except Exception as exc:
                lines.append(f"(search unavailable: {str(exc)[:80]})")

        raw = await providers.llm.complete_json(
            "content", PROMPT_SYSTEM,
            PROMPT.format(brand=ctx, results="\n".join(lines[:14]) or "(no results)"),
        )
        appearances = [a for a in (raw.get("appearances") or []) if isinstance(a, dict)]
        async with db.acquire(tenant_id) as conn:
            for a in appearances:
                title = str(a.get("title") or "").strip()
                if not title:
                    continue
                links = [str(u) for u in (a.get("links") or []) if isinstance(u, str)][:4]
                detail = f"{a.get('fit', '')} — Approach: {a.get('approach', '')}"
                if links:
                    detail += "\n" + " · ".join(links)
                await suggest(
                    conn, source=AGENT,
                    title=title,
                    topic=str(a.get("approach") or title),
                    format="post",
                    why=str(a.get("fit") or ""),
                    evidence=links,
                )
                await action_service.upsert_action(
                    conn,
                    kind="general",
                    title=f"appearance · {a.get('kind', 'podcast')}: {title}"[:300],
                    detail=detail,
                    meta={"source": "appearances", "kind": a.get("kind"), "links": links},
                    dedupe_key=f"appearance:{title[:80].lower()}",
                )
        report = {
            "appearances": appearances,
            "results_scanned": len([ln for ln in lines if "http" in ln]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"appearance_count": len(appearances)})
    return report
