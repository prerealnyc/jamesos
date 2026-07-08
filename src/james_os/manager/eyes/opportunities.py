"""Content Opportunity Radar — the daily 'what should we make?' agent.

Mines Reddit for the recurring PROBLEMS in the brand's niche (SERP-level via
site:reddit.com, D9), optionally cross-references what the top tracked accounts
are posting about, and turns the recurring pain points into content
suggestions grounded in the Reddit threads as evidence ('this keeps coming up
across these links — we need a post/blog here'). Each opportunity is pushed as
a trackable content action item for the brand manager to approve; later the BM
will execute them itself. Ported from bm2.0 backend/app/agents/opportunities.py.

Reddit mining is the core and needs only the search provider. The
competitor-topic layer is best-effort — skipped cleanly when the profile has
no peer roster or no peer-data source is connected.
"""

from uuid import UUID

from ... import db
from .. import actions as action_service
from .. import profile, runs
from ..providers import get_providers
from . import niche_block, suggest

AGENT = "content_radar"
_PROBLEM_ANGLES = ("problem OR frustrated OR struggling", '"how do i" OR "help with" OR advice', "worst OR avoid OR mistake")
_MAX_PER_QUERY = 8

PROMPT_SYSTEM = (
    "You are a content strategist who finds recurring problems a niche audience "
    "keeps raising and turns the ones a brand is positioned to answer into "
    "specific content pieces — always citing the source threads as evidence. "
    "You only flag a problem as an opportunity when it recurs across multiple "
    "threads; one-off complaints are noise."
)
PROMPT = (
    "From the Reddit discussions below (real problems people in this niche "
    "raise) and what top accounts are posting about, identify the RECURRING "
    "problems this brand should make content about.\n\n"
    "BRAND NICHE / POSITIONING:\n{niche}\n\n"
    "REDDIT DISCUSSIONS (problem : thread url):\n{reddit}\n\n"
    "WHAT TOP ACCOUNTS ARE POSTING ABOUT:\n{competitor_topics}\n\n"
    "Return JSON only: {{\"opportunities\": [{{\"title\": str, \"problem\": "
    "str, \"content_type\": \"post|blog|video|thread|carousel|newsletter\", "
    "\"angle\": str, \"why\": str, \"recurring_count\": int (how many threads "
    "support it), \"reddit_links\": [url, ...]}}], \"competitor_themes\": "
    "[str, ...], \"note\": str}}. Only include opportunities whose "
    "recurring_count >= 2 and whose reddit_links come from the discussions "
    "above."
)


async def _competitor_topics(providers, fields: list[dict]) -> list[str]:
    """Best-effort: recent post topics from the profile's tracked peer roster
    (competitors.roster fields — the substrate has no PeerEntity table).
    Skipped cleanly when no roster or no peer-data source is connected."""
    peers = [
        f["value"].get("v")
        for f in fields
        if f["field_key"] == "competitors.roster" and isinstance(f["value"].get("v"), dict)
    ]
    topics: list[str] = []
    for p in peers[:5]:
        handle = str(p.get("handle") or "")
        platform = str(p.get("platform") or "")
        if not handle or platform in ("", "unknown"):
            continue
        try:
            posts = await providers.peers.recent_posts(platform, handle, limit=10)
        except Exception:
            continue  # no peer source / fetch failed — best-effort
        for post in posts[:5]:
            if post.text:
                topics.append(f"@{handle}: {post.text[:120]}")
    return topics


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(AGENT, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)

        niche = niche_block(fields)
        # a short niche phrase to seed reddit queries: brand name + top positioning
        brand_term = next(
            (str(f["value"].get("v"))
             for f in fields if f["field_key"] in ("positioning.niche", "identity.industry", "positioning.category")),
            None,
        )
        seed = brand_term or next(
            (str(f["value"].get("v"))
             for f in fields if f["field_key"] == "identity.positioning_line"),
            "the niche",
        )

        reddit_lines: list[str] = []
        for angle in _PROBLEM_ANGLES:
            try:
                results = await providers.search.search(f'site:reddit.com {seed} ({angle})', num=_MAX_PER_QUERY)
                for r in results[:_MAX_PER_QUERY]:
                    if "reddit.com" in r.url:
                        reddit_lines.append(f"- {r.title}: {r.snippet} ({r.url})")
            except Exception as exc:
                reddit_lines.append(f"(reddit search unavailable: {str(exc)[:80]})")

        competitor_topics = await _competitor_topics(providers, fields)

        # cap what the LLM sees — a wall of 20+ threads overwhelms the fallback
        # model and it returns nothing; the freshest ~14 are plenty to cluster.
        raw = await providers.llm.complete_json(
            "content", PROMPT_SYSTEM,
            PROMPT.format(
                niche=niche[:1500],
                reddit="\n".join(reddit_lines[:14]) or "(no reddit discussions found)",
                competitor_topics="\n".join(competitor_topics[:10]) or "(no competitor post source connected)",
            ),
        )
        opportunities = [o for o in (raw.get("opportunities") or []) if isinstance(o, dict)]

        # push each as a trackable content action item (dedupe by title)
        async with db.acquire(tenant_id) as conn:
            for o in opportunities:
                title = str(o.get("title") or "").strip()
                if not title:
                    continue
                links = [str(u) for u in (o.get("reddit_links") or []) if isinstance(u, str)][:5]
                detail = f"{o.get('problem', '')} — {o.get('angle', '')}"
                if links:
                    detail += "\nRecurring across: " + " · ".join(links)
                await suggest(
                    conn, source=AGENT,
                    title=title,
                    topic=str(o.get("angle") or title),
                    format="reel" if o.get("content_type") == "video" else "post",
                    why=str(o.get("why") or ""),
                    evidence=links,
                )
                await action_service.upsert_action(
                    conn,
                    kind="content",
                    title=f"{o.get('content_type', 'content')}: {title}"[:300],
                    detail=detail,
                    meta={
                        "source": "content_radar", "content_type": o.get("content_type"),
                        "recurring_count": o.get("recurring_count"), "reddit_links": links,
                        "why": o.get("why"),
                    },
                    dedupe_key=f"radar:{title[:80].lower()}",
                )
        report = {
            "opportunities": opportunities,
            "competitor_themes": [str(t) for t in (raw.get("competitor_themes") or [])],
            "reddit_threads_scanned": len([ln for ln in reddit_lines if "reddit.com" in ln]),
            "note": str(raw.get("note") or "").strip(),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"opportunity_count": len(opportunities)})
    return report
