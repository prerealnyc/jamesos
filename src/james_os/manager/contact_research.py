"""Contact-research agent — the 'do more research to get their contact
details' action. Ported from bm2.0 backend/app/agents/contact_research.py.
Focused web research for a peer's real, PUBLIC outreach paths
(press/booking/business-inquiry pages, verified socials). Honest by design:
returns paths to VERIFY with citations, never a scraped private email
presented as fact. Findings append to the target action_items row as an
update note plus meta.contact_paths.
"""

import json
from uuid import UUID

from .. import db
from . import actions as action_service
from . import runs
from .providers import get_providers

AGENT = "contact_research"
_MAX_SEARCHES = 5

PROMPT_SYSTEM = (
    "You find PUBLIC, professional ways to reach a brand or creator — press "
    "pages, media-inquiry forms, booking/speaker pages, business emails listed "
    "on official sites, verified official social accounts. You never fabricate "
    "a private email or phone; if you only find a general contact page, say so."
)
PROMPT = (
    "From the search results, list the best PUBLIC outreach paths to reach "
    "'{name}'. Return JSON only: {{\"paths\": [{{\"type\": str (e.g. press_form|"
    "booking_page|business_email|official_dm|contact_page), \"value\": str, "
    "\"confidence\": \"low|medium|high\", \"source\": str (a url from the "
    "results)}}], \"note\": str}}. Include ONLY paths supported by a result "
    "url; omit anything you cannot source.\n\nSEARCH RESULTS:\n{results}"
)


async def run(
    tenant_id: UUID | None = None, action_id: str = "", config: dict | None = None
) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT,
        trigger=(config or {}).get("trigger", "manual"),
        input={"action_id": action_id},
        tenant_id=tenant_id,
    )
    try:
        if not action_id:
            raise ValueError("action_id is required")
        async with db.acquire(tenant_id) as conn:
            item = await conn.fetchrow(
                "SELECT id, title, related_peer FROM action_items WHERE id = $1::uuid",
                action_id,
            )
        if item is None:
            raise ValueError(f"action {action_id} not found")
        # resolve the target name: the related peer, else the action title
        name = (item["related_peer"] or "").strip() or item["title"]

        lines: list[str] = []
        for q in (f"{name} press contact media inquiries", f"{name} booking OR speaking OR partnership contact"):
            try:
                for r in (await providers.search.search(q, num=_MAX_SEARCHES))[:_MAX_SEARCHES]:
                    lines.append(f"- {r.title}: {r.snippet} ({r.url})")
            except Exception as exc:
                lines.append(f"(search unavailable: {str(exc)[:100]})")

        raw = await providers.llm.complete_json(
            "extract", PROMPT_SYSTEM, PROMPT.format(name=name, results="\n".join(lines) or "(none)")
        )
        paths = [p for p in (raw.get("paths") or []) if isinstance(p, dict)]
        note = str(raw.get("note") or "").strip() or (
            f"Found {len(paths)} public outreach path(s) for {name} — verify before use."
        )
        # record on the action item as an update + stash paths in meta
        summary = "Contact research: " + (
            "; ".join(f"{p.get('type')}: {p.get('value')} ({p.get('confidence')})" for p in paths[:4])
            or "no public path found — try their website contact page"
        )
        async with db.acquire(tenant_id) as conn:
            await action_service.add_note(conn, action_id, summary, actor=AGENT)
            await conn.execute(
                "UPDATE action_items SET meta = meta || $2::jsonb WHERE id = $1::uuid",
                action_id,
                json.dumps({"contact_paths": paths}),
            )
        result = {"action_id": action_id, "name": name, "paths": paths, "note": note}
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"path_count": len(paths)})
    return result
