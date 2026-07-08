"""Search-Questions Agent (AEO eye) — 'what is our audience actually asking,
and what authoritative answers should we own?'. Scans search for the questions
people ask in the niche and turns the recurring, high-intent ones into
answer-content opportunities (FAQ, blog, video) so the brand surfaces in
Google AND in AI/generative answers. Distinct from the Reddit eye: that finds
complaints/problems; this finds search intent. Pushes cited action items.
Ported from bm2.0 backend/app/agents/questions.py.
"""

from uuid import UUID

from ... import db
from .. import actions as action_service
from .. import profile, runs
from ..providers import get_providers
from . import niche_block, suggest

AGENT = "questions"
_SEEDS = ("how to", "what is OR why", "best OR vs OR guide")
_MAX_PER_QUERY = 8

PROMPT_SYSTEM = (
    "You are an answer-engine strategist. From what people search in a niche, "
    "you pick the high-intent questions a brand should own an authoritative "
    "answer for — the ones that recur and that this brand is credible to answer "
    "— so it ranks in search and gets cited in AI answers."
)
PROMPT = (
    "From the search results below, identify the questions this brand should "
    "publish authoritative answer content for.\n\n"
    "BRAND NICHE / POSITIONING:\n{niche}\n\n"
    "SEARCH RESULTS (title : snippet : url):\n{results}\n\n"
    "Return JSON only: {{\"questions\": [{{\"question\": str, \"content_type\": "
    "\"faq|blog|video|thread\", \"answer_angle\": str (the brand's credible "
    "take), \"why\": str (search/AEO value), \"links\": [url]}}], \"note\": "
    "str}}. Only high-intent questions the brand is credible to answer, each "
    "with a link from the results."
)


def _seed(fields: list[dict]) -> str:
    for f in fields:
        if f["field_key"] in ("positioning.niche", "identity.industry", "positioning.category"):
            v = f["value"].get("v")
            if v:
                return str(v)
    return "the niche"


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(AGENT, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)

        seed = _seed(fields)
        lines: list[str] = []
        for s in _SEEDS:
            try:
                for r in (await providers.search.search(f"{seed} {s}", num=_MAX_PER_QUERY))[:_MAX_PER_QUERY]:
                    lines.append(f"- {r.title}: {r.snippet[:110]} ({r.url})")
            except Exception as exc:
                lines.append(f"(search unavailable: {str(exc)[:80]})")

        raw = await providers.llm.complete_json(
            "content", PROMPT_SYSTEM,
            PROMPT.format(niche=niche_block(fields)[:1400], results="\n".join(lines[:14]) or "(no results)"),
        )
        questions = [q for q in (raw.get("questions") or []) if isinstance(q, dict)]
        async with db.acquire(tenant_id) as conn:
            for q in questions:
                title = str(q.get("question") or "").strip()
                if not title:
                    continue
                links = [str(u) for u in (q.get("links") or []) if isinstance(u, str)][:4]
                detail = str(q.get("answer_angle") or "")
                if links:
                    detail += "\nSearch context: " + " · ".join(links)
                await suggest(
                    conn, source=AGENT,
                    title=title,
                    topic=str(q.get("answer_angle") or title),
                    format="reel" if q.get("content_type") == "video" else "post",
                    why=str(q.get("why") or ""),
                    evidence=links,
                )
                await action_service.upsert_action(
                    conn,
                    kind="content",
                    title=f"answer · {q.get('content_type', 'blog')}: {title}"[:300],
                    detail=detail,
                    meta={"source": "questions", "content_type": q.get("content_type"), "links": links, "why": q.get("why")},
                    dedupe_key=f"question:{title[:80].lower()}",
                )
        report = {
            "questions": questions,
            "results_scanned": len([ln for ln in lines if "http" in ln]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"question_count": len(questions)})
    return report
