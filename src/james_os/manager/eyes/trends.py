"""Trend Agent — 'what's happening in our industry right now that we should
ride?'. One eye of the brand: scans news for what's trending in the brand's
industry/niche and turns the relevant moments into timely post opportunities,
each cited to the article. Ported from bm2.0 backend/app/agents/trends.py.
"""

from uuid import UUID

from ... import db
from .. import actions as action_service
from .. import profile, runs
from ..providers import get_providers
from . import niche_block, niche_terms, suggest

AGENT = "trends"
_NEWS_DAYS = 14
_MAX_ITEMS = 16

PROMPT_SYSTEM = (
    "You are a brand's trend-watcher. From recent industry news you spot the "
    "moments worth posting about — a shift, a story, a debate the brand's "
    "audience cares about — and propose a timely angle for each. You only flag "
    "news that is genuinely relevant to this brand's niche and audience."
)
PROMPT = (
    "From the recent industry news below, pick the trends this brand should "
    "post about NOW and give each a timely angle tied to its positioning.\n\n"
    "BRAND NICHE / POSITIONING:\n{niche}\n\n"
    "RECENT INDUSTRY NEWS (headline : url):\n{news}\n\n"
    "Return JSON only: {{\"trends\": [{{\"title\": str, \"trend\": str (what's "
    "happening), \"angle\": str (the brand's take), \"content_type\": "
    "\"post|thread|video|blog|newsletter\", \"why\": str, \"links\": [url]}}], "
    "\"note\": str}}. Only include trends genuinely relevant to the niche, "
    "each with at least one link from the news above."
)


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(AGENT, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)

        news_lines: list[str] = []
        for term in niche_terms(fields):
            try:
                for n in (await providers.news.search(f"{term} trends", days=_NEWS_DAYS))[:_MAX_ITEMS]:
                    news_lines.append(f"- {n.title}: {n.snippet[:120]} ({n.url})")
            except Exception as exc:
                news_lines.append(f"(news search unavailable: {str(exc)[:80]})")

        raw = await providers.llm.complete_json(
            "content", PROMPT_SYSTEM,
            PROMPT.format(
                niche=niche_block(fields)[:1500],
                news="\n".join(news_lines[:_MAX_ITEMS]) or "(no news found)",
            ),
        )
        trends = [t for t in (raw.get("trends") or []) if isinstance(t, dict)]
        async with db.acquire(tenant_id) as conn:
            for t in trends:
                title = str(t.get("title") or "").strip()
                if not title:
                    continue
                links = [str(u) for u in (t.get("links") or []) if isinstance(u, str)][:4]
                detail = f"{t.get('trend', '')} — {t.get('angle', '')}"
                if links:
                    detail += "\nSources: " + " · ".join(links)
                await suggest(
                    conn, source=AGENT,
                    title=title,
                    topic=str(t.get("angle") or title),
                    format="reel" if t.get("content_type") == "video" else "post",
                    why=str(t.get("why") or ""),
                    evidence=links,
                )
                await action_service.upsert_action(
                    conn,
                    kind="content",
                    title=f"trend · {t.get('content_type', 'post')}: {title}"[:300],
                    detail=detail,
                    meta={"source": "trends", "content_type": t.get("content_type"),
                          "links": links, "why": t.get("why")},
                    dedupe_key=f"trend:{title[:80].lower()}",
                )
        report = {
            "trends": trends,
            "news_scanned": len([ln for ln in news_lines if "http" in ln]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"trend_count": len(trends)})
    return report
