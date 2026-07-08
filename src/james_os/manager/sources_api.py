"""Intelligence Sources endpoints (D12: one key, one job, additive
intelligence). Ported from bm2.0 backend/app/routers/sources.py.

GET /system/sources is the stream inventory, computed from the wired
providers — one row per insight STREAM, not per Protocol (reddit rides the
web_search key; the llm stream reports its availability chain). Status is
derived from the implementation's type name: the D8 mock convention prefixes
every keyless fixture with 'Mock', anything else is a live vendor adapter.

GET /manager/sources is the per-tenant contribution: the latest succeeded
researcher job_run's per-lane field counts (output.lane_stats, mapped
lane -> stream) plus current profile-field counts by section for context.
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..config import settings
from . import profile
from .providers import Providers, get_providers

router = APIRouter(tags=["manager-sources"])

# researcher lane key (agents/researcher.py lane names) -> inventory stream key
LANE_STREAMS = {
    "web": "web_search",
    "news": "news",
    "wikipedia": "wikipedia",
    "youtube": "youtube",
    "reddit": "reddit",
    "places": "places",
    "deep": "deep_research",
}

# live LLM router class -> the chain name live_providers logs it under
_LLM_CHAIN_NAMES = {"AnthropicRouter": "anthropic", "PerplexityRouter": "perplexity"}


async def require_manager_v2() -> None:
    """All manager surfaces sit behind the manager_v2 flag (unification plan:
    James's live tenant untouched until cutover). Global default from settings;
    a tenant opts in via tenants.config['manager_v2'] = true."""
    if settings.manager_v2:
        return
    async with db.acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    if not (cfg or {}).get("manager_v2"):
        raise HTTPException(status_code=404, detail="manager_v2 is not enabled for this tenant")


def provider_dep() -> Providers:
    return get_providers()


def _impl(obj: object) -> str:
    return type(obj).__name__


def _is_mock(obj: object) -> bool:
    return _impl(obj).startswith("Mock")


def _row(stream: str, obj: object, *, live_note: str = "", mock_note: str = "") -> dict:
    mock = _is_mock(obj)
    return {
        "stream": stream,
        "provider_impl": _impl(obj),
        "status": "mock" if mock else "live",
        "note": mock_note if mock else live_note,
    }


def stream_rows(providers: Providers) -> list[dict]:
    """The D12 inventory: every stream, its wired implementation, and whether
    adding its key would add intelligence ('more keys = more power' must be
    observable, not folklore)."""
    llm_impl = _impl(providers.llm)
    if llm_impl == "ChainLLMRouter":
        chain = [str(n) for n in getattr(providers.llm, "_names", [])]
    elif _is_mock(providers.llm):
        chain = []
    else:
        chain = [_LLM_CHAIN_NAMES.get(llm_impl, llm_impl)]

    llm_row = _row(
        "llm",
        providers.llm,
        live_note=f"chain: {' -> '.join(chain)}" if chain else "",
        mock_note="Anthropic — awaiting key",
    )
    llm_row["chain"] = chain

    reddit_row = _row(
        "reddit",
        providers.search,  # D9: SERP-level site:reddit.com — rides the search key
        live_note="rides the web_search key (site:reddit.com)",
        mock_note="rides the web_search key (site:reddit.com)",
    )

    transcription_row = _row(
        "transcription",
        providers.transcription,
        live_note="transcribes spoken sources for the Brand Voice harvester",
        mock_note="AssemblyAI — awaiting key",
    )

    return [
        _row("web_search", providers.search, mock_note="Serper — awaiting key"),
        _row("page_scraping", providers.scrape, mock_note="Firecrawl — awaiting key"),
        _row("news", providers.news, mock_note="GNews/Serper — awaiting key"),
        _row(
            "places",
            providers.places,
            live_note="rides the web_search key (Serper /places)",
            mock_note="rides the web_search key (Serper /places)",
        ),
        _row("wikipedia", providers.wiki, live_note="keyless REST API"),
        _row("youtube", providers.video, mock_note="YouTube Data API — awaiting key"),
        reddit_row,
        _row("deep_research", providers.deep, mock_note="Perplexity — awaiting key"),
        llm_row,
        _row("peer_tracking", providers.peers, mock_note="Apify/ScrapeCreators/YouTube — awaiting key"),
        _row("social_publishing", providers.social, mock_note="PostProxy/Ayrshare — awaiting key"),
        # execution 'hands': the D8 gap-fill surfaces james-os never built as
        # text hands — each shows live-vs-awaiting-key so "suggestions come
        # with API keys" is observable (which hand can actually do the work).
        _row("email_publishing", providers.email, mock_note="Resend — awaiting key"),
        _row("blog_publishing", providers.blog, mock_note="hosted blog/webhook — awaiting key"),
        transcription_row,
    ]


@router.get("/system/sources", dependencies=[Depends(require_manager_v2)])
async def system_sources(providers: Providers = Depends(provider_dep)) -> dict:
    return {"env": settings.manager_env, "sources": stream_rows(providers)}


@router.get("/manager/sources", dependencies=[Depends(require_manager_v2)])
async def tenant_sources() -> dict:
    """Per-stream contribution for the current tenant. The latest succeeded
    researcher run carrying lane_stats wins (discover-mode runs succeed with
    empty lane_stats and must not mask an earlier confirmed fan-out)."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT output, started_at, finished_at FROM job_runs
               WHERE agent = 'researcher' AND status = 'succeeded'
               ORDER BY started_at DESC"""
        )
        run = None
        for r in rows:
            output = r["output"]
            if isinstance(output, str):
                output = json.loads(output or "{}")
            if output.get("lane_stats"):
                run = {"output": output, "at": r["finished_at"] or r["started_at"]}
                break

        sections: dict[str, int] = {}
        for f in await profile.current_fields(conn):
            sections[f["section"]] = sections.get(f["section"], 0) + 1

    contributions = {
        LANE_STREAMS.get(str(lane), str(lane)): int(count)
        for lane, count in (run["output"].get("lane_stats", {}) if run else {}).items()
    }
    return {
        "last_run": run["at"].isoformat() if run else None,
        "contributions": contributions,
        "profile_sections": sections,
    }
