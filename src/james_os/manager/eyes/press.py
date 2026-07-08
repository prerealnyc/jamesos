"""Press Agent — 'what's being said about US, and what should we amplify?'.
One eye of the brand: scans news/web for mentions of the brand and its key
people, classifies which are content-worthy (a feature, award, appointment,
notable coverage), and turns those into celebration/authority content
suggestions cited to the coverage. Pushed as trackable action items.
Ported from bm2.0 backend/app/agents/press.py.
"""

import json
from uuid import UUID

from ... import db
from .. import actions as action_service
from .. import profile, runs
from ..providers import get_providers
from . import suggest

AGENT = "press"
_NEWS_DAYS = 30
_MAX_ITEMS = 16

PROMPT_SYSTEM = (
    "You are a brand's press-watcher. You separate content-worthy coverage "
    "(features, awards, appointments, notable mentions that build authority) "
    "from routine noise, and for each content-worthy item propose a "
    "celebration or authority post that amplifies it — always citing the "
    "coverage. You never invent coverage that isn't in the results."
)
PROMPT = (
    "From the mentions below, identify the CONTENT-WORTHY press about this "
    "brand and its people, and propose an amplification post for each.\n\n"
    "BRAND:\n{brand}\n\n"
    "MENTIONS (headline : url):\n{mentions}\n\n"
    "Return JSON only: {{\"items\": [{{\"title\": str, \"signal\": str (why "
    "it's content-worthy — award|feature|appointment|coverage|milestone), "
    "\"post\": str (the amplification angle), \"content_type\": \"post|thread|"
    "video\", \"links\": [url]}}], \"note\": str}}. Only include genuinely "
    "content-worthy items, each with a link from the mentions above."
)


def _brand_ctx(name: str, entity_type: str, fields: list[dict]) -> tuple[str, list[str]]:
    """Brand description + the search terms (name + key people/holdings)."""
    terms = [name]
    lines = [f"- name: {name}", f"- type: {entity_type}"]
    for f in fields:
        v = f["value"].get("v")
        if f["field_key"] in ("identity.key_people", "identity.holdings", "identity.people") and v:
            lines.append(f"- {f['field_key']}: {str(v)[:150]}")
            if isinstance(v, str):
                terms.append(v.split(",")[0].strip())
    return "\n".join(lines[:12]), [t for t in terms if t][:3]


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
        ctx, terms = _brand_ctx(name, str(brand["kind"] if brand else "person"), fields)

        mentions: list[str] = []
        for term in terms:
            try:
                for n in (await providers.news.search(f'"{term}"', days=_NEWS_DAYS))[:_MAX_ITEMS]:
                    mentions.append(f"- {n.title}: {n.snippet[:120]} ({n.url})")
            except Exception as exc:
                mentions.append(f"(news search unavailable: {str(exc)[:80]})")

        raw = await providers.llm.complete_json(
            "extract", PROMPT_SYSTEM,
            PROMPT.format(brand=ctx, mentions="\n".join(mentions[:_MAX_ITEMS]) or "(no mentions found)"),
        )
        items = [i for i in (raw.get("items") or []) if isinstance(i, dict)]
        async with db.acquire(tenant_id) as conn:
            for it in items:
                title = str(it.get("title") or "").strip()
                if not title:
                    continue
                links = [str(u) for u in (it.get("links") or []) if isinstance(u, str)][:4]
                detail = f"{it.get('signal', '')}: {it.get('post', '')}"
                if links:
                    detail += "\nCoverage: " + " · ".join(links)
                await suggest(
                    conn, source=AGENT,
                    title=title,
                    topic=str(it.get("post") or title),
                    format="reel" if it.get("content_type") == "video" else "post",
                    why=str(it.get("signal") or ""),
                    evidence=links,
                )
                await action_service.upsert_action(
                    conn,
                    kind="press",
                    title=f"press · {title}"[:300],
                    detail=detail,
                    meta={"source": "press", "signal": it.get("signal"), "content_type": it.get("content_type"), "links": links},
                    dedupe_key=f"press:{title[:80].lower()}",
                )
        report = {
            "items": items,
            "mentions_scanned": len([m for m in mentions if "http" in m]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"item_count": len(items)})
    return report
