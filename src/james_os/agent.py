"""Agent — Claude tool-use loop over the JAMES OS feature set.

Turns natural-language commands into calls against the tools we've
already built. The agent IS the LLM; this module just defines the
tool spec, executes the calls, and persists the run.

Architecture:

    prompt → Claude with tools → tool_use blocks → execute → tool_result
                ↑                                              │
                └──────────────────────────────────────────────┘
                              loop until stop_reason == "end_turn"

Every tool here wraps an existing module / endpoint function. We don't
re-implement business logic — wrappers exist only to:
  * present a clean JSON-schema interface to the LLM
  * coerce types (LLM sometimes passes "30" instead of 30)
  * return a stable shape the LLM can read back

State is persisted to agent_runs as the loop progresses, so a long
run is visible in the UI while it's still in-flight.

Honest scope today:
  * Anthropic-only — uses claude-sonnet-4 by default. The wrapper
    in llm.py is JSON-oriented; this module uses the raw Anthropic
    SDK directly because tool-use needs a different message shape.
  * Tools are read-only OR write-with-immediate-confirmation. We
    don't currently gate destructive actions behind a separate
    approval step — instead, tools that spend money / kick renders
    are explicit about it in their description and the agent's
    summary tells the user what landed where.
  * Max 12 tool turns per run (cost ceiling). If the agent burns
    through 12 without ending, we mark the run failed with a clear
    "exceeded tool-turn budget" error.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from .config import settings
from .db import acquire


MAX_TOOL_TURNS = 12


# ── tool registry ────────────────────────────────────────────────────


# Each tool is registered with its Claude-facing schema + a Python
# coroutine that runs it. The coroutine receives a dict of kwargs and
# returns a JSON-serializable result.
ToolFn = Callable[..., Awaitable[Any]]


class Tool:
    __slots__ = ("name", "description", "input_schema", "fn", "writes")

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        fn: ToolFn,
        writes: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.fn = fn
        # `writes` flags tools that mutate state or spend external API
        # credits — used for the summary line so the user knows what
        # cost them money.
        self.writes = writes


_TOOLS: dict[str, Tool] = {}


def _register(t: Tool) -> Tool:
    _TOOLS[t.name] = t
    return t


def get_tool_specs() -> list[dict]:
    """Anthropic-shaped tool definitions for the API call."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _TOOLS.values()
    ]


# ── tool implementations ────────────────────────────────────────────


# Each wrapper imports inside the function so module load is cheap and
# circular-import safe (agent.py is imported by main.py, which imports
# basically everything else).


async def _t_ask_memory(question: str) -> dict:
    from .ask import ask
    from .models import AskRequest
    r = await ask(AskRequest(question=question))
    return {
        "answer": r.response,
        "refused": r.refused,
        "refusal_reason": r.refusal_reason,
        "citation_count": len(r.citations),
        "confidence": r.confidence,
    }


_register(Tool(
    name="ask_memory",
    description=(
        "Run a grounded Q&A against the events memory. Use this when "
        "the user is asking for FACTS (what did we decide, what's in "
        "our voice rules, what did peer X post). Returns a cited "
        "answer or a refusal if nothing in memory grounds it. Does "
        "NOT take actions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The user's question, verbatim."},
        },
        "required": ["question"],
    },
    fn=_t_ask_memory,
))


async def _t_list_long_sources() -> dict:
    from .long_form import list_sources
    sources = await list_sources()
    # Trim heavy fields the agent doesn't need.
    return {
        "sources": [
            {
                "id": s["id"],
                "title": s["title"],
                "status": s["status"],
                "duration_s": s["duration_s"],
                "drive_file_id": s.get("drive_file_id"),
                "created_at": s["created_at"],
            }
            for s in sources
        ],
    }


_register(Tool(
    name="list_long_sources",
    description=(
        "List long-form video sources (podcasts, interviews, IG Lives) "
        "that have been imported. Returns id, title, status, duration. "
        "Use this to find a source the user wants to render or "
        "reanalyze."
    ),
    input_schema={"type": "object", "properties": {}},
    fn=_t_list_long_sources,
))


async def _t_render_whole_source(
    source_id: str, caption_style: str = "", platform: str = "instagram",
) -> dict:
    from .long_form import (
        create_whole_source_candidate, get_source_with_candidates,
        link_candidate_to_production,
    )
    from .video_pipeline import run_production
    from .video import start_production
    src_uuid = UUID(source_id)
    cand = await create_whole_source_candidate(src_uuid)
    if cand is None:
        return {"error": "source not ready or has no duration_s"}
    src = await get_source_with_candidates(src_uuid)
    if src is None:
        return {"error": "source not found"}
    payload = [{
        "source_id": cand["source_id"],
        "candidate_id": cand["id"],
        "source_url": src["source_url"],
        "drive_file_id": src.get("drive_file_id") or "",
        "start_s": cand["start_s"],
        "end_s": cand["end_s"],
        "hook_quote": cand["hook_quote"],
        "summary": cand["summary"],
    }]
    prod = await start_production(
        (cand["hook_quote"] or src["title"])[:200],
        platform, "9:16",
        (cand["summary"] or src["title"] or "Reel")[:120],
        payload, "long_form_reel",
        caption_style, "",
    )
    await link_candidate_to_production(UUID(cand["id"]), UUID(prod["id"]))
    # Fire-and-forget — the user can poll /queue or /library for the
    # finished reel; we don't block the agent loop on a 5-min render.
    asyncio.create_task(run_production(UUID(prod["id"])))
    return {
        "production_id": prod["id"],
        "status": "queued",
        "caption_style": caption_style or "(auto)",
        "note": "Render kicked. Check /queue or /library in ~3-5 min.",
    }


_register(Tool(
    name="render_whole_source",
    description=(
        "Render an ENTIRE long-form source as one reel (no candidate "
        "picking — for short talking clips where the whole clip IS the "
        "reel). Spends external API credits (gpt-image-1, Runway, "
        "Creatomate). Returns the new production id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "UUID of the long_sources row."},
            "caption_style": {
                "type": "string",
                "description": "tiktok_yellow | clean_white | bold_pop | subtle_minimal | branded_red | '' for auto-pick.",
                "default": "",
            },
            "platform": {"type": "string", "default": "instagram"},
        },
        "required": ["source_id"],
    },
    fn=_t_render_whole_source,
    writes=True,
))


async def _t_list_brand_accounts() -> dict:
    from .brand_accounts import get_brand_accounts
    return {"accounts": await get_brand_accounts()}


_register(Tool(
    name="list_brand_accounts",
    description="List the social handles configured as the brand's own (the analytics tab scopes to these).",
    input_schema={"type": "object", "properties": {}},
    fn=_t_list_brand_accounts,
))


async def _t_add_brand_account(
    platform: str, handle: str, name: str = "",
) -> dict:
    from .brand_accounts import get_brand_accounts, set_brand_accounts
    current = await get_brand_accounts()
    handle = handle.strip().lstrip("@").lower()
    platform = platform.strip().lower()
    if any(a["platform"] == platform and a["handle"] == handle for a in current):
        return {"ok": True, "note": "already tracked", "accounts": current}
    new_list = [*current, {"platform": platform, "handle": handle, "name": name}]
    cleaned = await set_brand_accounts(new_list)
    return {"ok": True, "added": f"@{handle} ({platform})", "accounts": cleaned}


_register(Tool(
    name="add_brand_account",
    description="Add a brand's own social handle for analytics tracking. Idempotent — adding an existing handle is a no-op.",
    input_schema={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "enum": ["instagram", "tiktok", "youtube"]},
            "handle": {"type": "string", "description": "Without @-prefix; case-insensitive."},
            "name": {"type": "string", "default": ""},
        },
        "required": ["platform", "handle"],
    },
    fn=_t_add_brand_account,
    writes=True,
))


async def _t_refresh_brand_analytics(limit: int = 30) -> dict:
    from .brand_accounts import brand_handles_by_platform
    from .trends import refresh_watchlist
    handles = await brand_handles_by_platform()
    if not handles:
        return {"error": "no brand accounts configured"}
    r = await refresh_watchlist(handles, max(1, min(int(limit), 50)))
    return {
        "scraped": r.get("found", 0),
        "stored": len(r.get("stored_event_ids") or []),
        "provider": r.get("provider"),
    }


_register(Tool(
    name="refresh_brand_analytics",
    description=(
        "Scrape recent posts for every configured brand account via "
        "Apify and ingest them. Spends Apify credits. Returns counts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 30, "description": "Posts per handle (1-50)."},
        },
    },
    fn=_t_refresh_brand_analytics,
    writes=True,
))


async def _t_analytics_summary(
    handle: str = "", platform: str = "", days: int = 30,
) -> dict:
    from .analytics import handle_summary
    s = await handle_summary(handle=handle, platform=platform, days=int(days))
    # Drop the best_post.thumbnail (big URL) — the agent doesn't need it.
    bp = s.get("best_post")
    if bp:
        bp = {k: v for k, v in bp.items() if k != "thumbnail"}
        s = {**s, "best_post": bp}
    return s


_register(Tool(
    name="analytics_summary",
    description="Aggregate stats for one brand handle (or all when blank). Window in days. Read-only.",
    input_schema={
        "type": "object",
        "properties": {
            "handle": {"type": "string", "default": ""},
            "platform": {"type": "string", "default": ""},
            "days": {"type": "integer", "default": 30},
        },
    },
    fn=_t_analytics_summary,
))


async def _t_list_outputs(limit: int = 12) -> dict:
    async with acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, mode, status, script, final_url, caption_style,
                      created_at, completed_at
                 FROM video_productions
                WHERE status = 'succeeded' AND final_url IS NOT NULL
                ORDER BY coalesce(completed_at, updated_at) DESC
                LIMIT $1""",
            int(limit),
        )
    return {
        "outputs": [
            {
                "id": str(r["id"]),
                "mode": r["mode"],
                "script": (r["script"] or "")[:160],
                "final_url": r["final_url"],
                "caption_style": r["caption_style"] or "(auto)",
                "completed_at": (r["completed_at"] or r["created_at"]).isoformat() if r["completed_at"] or r["created_at"] else None,
            }
            for r in rows
        ],
    }


_register(Tool(
    name="list_outputs",
    description="Recent finished video renders with their URLs and caption styles.",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 12}},
    },
    fn=_t_list_outputs,
))


async def _t_list_pending_approvals(kind: str = "all") -> dict:
    """kind = 'videos' | 'posts' | 'all'. Filters server-side so the
    agent doesn't have to do it."""
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM actions WHERE status='pending' "
            "ORDER BY created_at DESC LIMIT 50"
        )
    items = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        is_video = payload.get("format") == "video" and payload.get("media_url")
        if kind == "videos" and not is_video:
            continue
        if kind == "posts" and is_video:
            continue
        items.append({
            "id": str(r["id"]),
            "kind": "video" if is_video else "post",
            "platform": payload.get("platform", ""),
            "format": payload.get("format", ""),
            "content": (payload.get("content") or "")[:200],
            "media_url": payload.get("media_url"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return {"items": items, "count": len(items)}


_register(Tool(
    name="list_pending_approvals",
    description="Items in the approval queue waiting for marketing yes/no. kind = videos / posts / all.",
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["videos", "posts", "all"], "default": "all"},
        },
    },
    fn=_t_list_pending_approvals,
))


async def _t_approve_item(item_id: str, reason: str = "approved by agent") -> dict:
    async with acquire() as conn:
        tag = await conn.execute(
            "UPDATE actions SET status='approved', approval_reason=$2, "
            "decided_at=now() WHERE id=$1",
            UUID(item_id), reason,
        )
    if not tag.endswith(" 1"):
        return {"ok": False, "error": "item not found or already decided"}
    return {"ok": True, "id": item_id, "status": "approved"}


_register(Tool(
    name="approve_item",
    description="Approve a queue item by id. Use ONLY when the user has explicitly told you to approve something.",
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "reason": {"type": "string", "default": "approved by agent"},
        },
        "required": ["item_id"],
    },
    fn=_t_approve_item,
    writes=True,
))


async def _t_list_drive_videos(folder_id: str = "") -> dict:
    from .drive import list_drive_videos
    videos = await list_drive_videos(folder_id or None)
    return {
        "videos": [
            {
                "id": v["id"],
                "name": v.get("name", ""),
                "size_mb": round(int(v.get("size") or 0) / (1024 * 1024), 1) if v.get("size") else None,
                "modified": v.get("modifiedTime"),
            }
            for v in videos
        ],
    }


_register(Tool(
    name="list_drive_videos",
    description="List videos in a Google Drive folder. Blank folder_id uses the configured default folder.",
    input_schema={
        "type": "object",
        "properties": {
            "folder_id": {"type": "string", "default": ""},
        },
    },
    fn=_t_list_drive_videos,
))


async def _t_import_drive_video(
    file_id: str, title: str = "",
) -> dict:
    from .long_form import (
        create_source_placeholder, fetch_from_drive_then_ingest,
    )
    from .drive import _file_metadata_sync, DriveNotConfigured
    try:
        meta = await asyncio.to_thread(_file_metadata_sync, file_id)
    except DriveNotConfigured as e:
        return {"error": str(e)}
    name = str(meta.get("name") or f"drive-{file_id}.mp4")
    src = await create_source_placeholder(
        title=(title or name), drive_file_id=file_id,
    )
    asyncio.create_task(
        fetch_from_drive_then_ingest(UUID(src["id"]), file_id, name)
    )
    return {
        "source_id": src["id"],
        "title": src["title"],
        "status": src["status"],
        "note": "Import kicked. Status will move from uploading → transcribing → analyzing → ready.",
    }


_register(Tool(
    name="import_drive_video",
    description=(
        "Import a Google Drive video as a long-form source. Spends "
        "OpenAI Whisper credits for transcription. Returns the new "
        "source id; the ingest runs in the background."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Drive file ID (not the share URL)."},
            "title": {"type": "string", "default": ""},
        },
        "required": ["file_id"],
    },
    fn=_t_import_drive_video,
    writes=True,
))


async def _t_list_trends(platform: str = "", limit: int = 12) -> dict:
    from .trends import list_trends
    rows = await list_trends(platform=platform, limit=int(limit))
    return {
        "trends": [
            {
                "platform": r.get("platform"),
                "handle": r.get("handle"),
                "caption": (r.get("caption") or "")[:160],
                "views": r.get("views"),
                "outlier_score": r.get("outlier_score"),
                "url": r.get("url"),
            }
            for r in rows
        ],
    }


_register(Tool(
    name="list_trends",
    description="Recent scraped trend posts from the watchlist (peers/competitors). Read-only.",
    input_schema={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 12},
        },
    },
    fn=_t_list_trends,
))


# ── create content (initiate work) ───────────────────────────────────


async def _t_generate_post(topic: str, platform: str = "instagram") -> dict:
    """Generate one on-voice text+image post draft and queue it for review."""
    from .autopilot_bulk import _make_text_post
    idea = {"topic": topic, "title": (topic or "")[:60], "pillar": ""}
    return await _make_text_post(idea, platform or "instagram", None)


_register(Tool(
    name="generate_post",
    description=(
        "CREATE a new on-voice text post (with an AI hero image) about a "
        "topic and queue it in the Approval Queue for review. Spends LLM + "
        "image credits. Use when the user asks to write / create / draft a post."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "What the post is about."},
            "platform": {"type": "string", "default": "instagram"},
        },
        "required": ["topic"],
    },
    fn=_t_generate_post,
    writes=True,
))


async def _t_generate_reel(topic: str, platform: str = "instagram") -> dict:
    """Write a short on-voice reel script and kick an engaging_avatar render."""
    from .autopilot_bulk import _make_video
    idea = {"topic": topic, "title": (topic or "")[:60], "pillar": ""}
    return await _make_video(idea, platform or "instagram", None)


_register(Tool(
    name="generate_reel",
    description=(
        "CREATE a new short video reel about a topic: writes an on-voice "
        "script, then kicks a real avatar + B-roll render (HeyGen/Runway/"
        "Creatomate). Spends render credits and takes a few minutes; it lands "
        "in the Approval Queue when finished. Use when the user asks to make / "
        "create a video or reel."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "What the reel is about."},
            "platform": {"type": "string", "default": "instagram"},
        },
        "required": ["topic"],
    },
    fn=_t_generate_reel,
    writes=True,
))


async def _t_run_autopilot(count: int = 6) -> dict:
    """Generate a batch of content (≈50/50 text+image posts and video reels)."""
    from .autopilot_bulk import generate_bulk
    n = max(1, min(int(count or 6), 12))
    return await generate_bulk(n, 0, None)


_register(Tool(
    name="run_autopilot",
    description=(
        "Generate a BATCH of content in one go (split ~50/50 between "
        "text+image posts and video reels), ideated from live trends + voice. "
        "Spends significant LLM / image / render credits — confirm the count "
        "with the user first. Capped at 12 pieces per call. Results appear in "
        "the Approval Queue."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "default": 6,
                "description": "How many pieces to make (max 12).",
            },
        },
    },
    fn=_t_run_autopilot,
    writes=True,
))


# ── analyze performance (read) ───────────────────────────────────────


def _slim_post(p: dict) -> dict:
    return {
        k: p.get(k)
        for k in (
            "platform", "handle", "url", "caption", "views", "likes",
            "comments", "shares", "engagement_rate", "outlier_score",
            "velocity", "posted_at",
        )
    }


async def _t_get_post_performance_summary(
    handle: str = "", platform: str = "", days: int = 30, limit_top: int = 3
) -> dict:
    """Own aggregates + best AND worst posts in one call — the default
    analysis read."""
    from .analytics import handle_summary, list_posts
    days = int(days)
    n = max(1, min(int(limit_top), 5))
    summary = await handle_summary(handle=handle, platform=platform, days=days)
    top = await list_posts(
        handle=handle, platform=platform, days=days,
        sort="engagement_rate", limit=n,
    )
    pool = await list_posts(
        handle=handle, platform=platform, days=days,
        sort="engagement_rate", limit=200,
    )
    bottom = list(reversed([p for p in pool if int(p.get("views") or 0) > 0]))[:n]
    keep = (
        "post_count", "views", "likes", "comments", "shares", "engagement",
        "engagement_rate", "median_outlier", "by_platform", "best_post",
    )
    return {
        "window_days": days,
        "summary": {k: summary.get(k) for k in keep},
        "top_posts": [_slim_post(p) for p in top],
        "bottom_posts": [_slim_post(p) for p in bottom],
    }


_register(Tool(
    name="get_post_performance_summary",
    description=(
        "DEFAULT analytics read: the brand's own aggregate stats PLUS its top "
        "and bottom posts for a window, so you can see what worked, what "
        "flopped, and the patterns behind both. Use this first for any "
        "'analyze my performance' request. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "handle": {"type": "string", "default": ""},
            "platform": {"type": "string", "default": ""},
            "days": {"type": "integer", "default": 30},
            "limit_top": {"type": "integer", "default": 3},
        },
    },
    fn=_t_get_post_performance_summary,
))


async def _t_get_top_posts(
    sort: str = "engagement_rate", platform: str = "",
    handle: str = "", days: int = 30, limit: int = 5,
) -> dict:
    from .analytics import list_posts
    valid = {
        "views", "likes", "comments", "engagement", "engagement_rate",
        "outlier", "velocity", "recent",
    }
    s = sort if sort in valid else "engagement_rate"
    rows = await list_posts(
        handle=handle, platform=platform, days=int(days),
        sort=s, limit=max(1, min(int(limit), 20)),
    )
    return {"sort": s, "posts": [_slim_post(p) for p in rows]}


_register(Tool(
    name="get_top_posts",
    description=(
        "The brand's own posts ranked by a chosen metric. sort ∈ "
        "engagement_rate | engagement | outlier (viral) | views | velocity | "
        "recent. Use to name concrete winners/losers. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sort": {"type": "string", "default": "engagement_rate"},
            "platform": {"type": "string", "default": ""},
            "handle": {"type": "string", "default": ""},
            "days": {"type": "integer", "default": 30},
            "limit": {"type": "integer", "default": 5},
        },
    },
    fn=_t_get_top_posts,
))


async def _t_get_platform_performance(days: int = 30) -> dict:
    from .analytics import platform_performance
    return await platform_performance(days=int(days))


_register(Tool(
    name="get_platform_performance",
    description=(
        "Per-platform breakdown across the brand's own posts (post count, "
        "views, avg/median engagement rate, best post per platform) so you "
        "can say which CHANNEL is winning. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {"days": {"type": "integer", "default": 30}},
    },
    fn=_t_get_platform_performance,
))


async def _t_get_accounts_leaderboard(platform: str = "", days: int = 30) -> dict:
    from .analytics import accounts_leaderboard
    rows = await accounts_leaderboard(platform=platform, days=int(days))
    return {"accounts": rows}


_register(Tool(
    name="get_accounts_leaderboard",
    description=(
        "Rank the brand's OWN accounts side-by-side (e.g. personal IG vs brand "
        "IG vs TikTok) by views/engagement. Accounts with zero stats are "
        "configured-but-unscraped (needs refresh). Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "default": ""},
            "days": {"type": "integer", "default": 30},
        },
    },
    fn=_t_get_accounts_leaderboard,
))


# ── research / market intel (write — spends API credits) ─────────────


async def _t_research_topic(subject: str, focus: str = "") -> dict:
    from .ingestion import ingest_many
    from .research import get_research_provider, research_to_events
    prov = get_research_provider()
    if prov.name == "stub":
        return {
            "error": "No live research provider connected. Set "
            "RESEARCH_PROVIDER=perplexity and PERPLEXITY_API_KEY."
        }
    res = await prov.research(subject, focus or "")
    if res.is_empty():
        return {"subject": subject, "summary": "", "findings": [], "sources": []}
    stored: list[str] = []
    try:
        rows = await ingest_many(research_to_events(res))
        stored = [str(r.id) for r in rows]
    except Exception:  # noqa: BLE001 — research still returns even if not stored
        pass
    return {
        "subject": subject,
        "provider": res.provider,
        "summary": res.summary,
        "findings": res.findings[:12],
        "sources": [s.url for s in res.sources][:10],
        "stored_event_ids": stored,
    }


_register(Tool(
    name="research_topic",
    description=(
        "Live WEB research on any subject (a competitor, a market shift, a "
        "content angle) via Perplexity. Returns a cited summary + findings and "
        "saves them to memory so future answers/content can cite them. Spends "
        "research credits."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "focus": {"type": "string", "default": ""},
        },
        "required": ["subject"],
    },
    fn=_t_research_topic,
    writes=True,
))


async def _t_scan_market(
    topic: str, focus: str = "", platforms: str = "instagram,tiktok,youtube"
) -> dict:
    """One-call market context: web research + fresh competitor posts + the
    brand's curated peer cohort. The entry point for 'analyze the market'."""
    out: dict = {"topic": topic}
    out["research"] = await _t_research_topic(topic, focus)
    try:
        from .trends import discover_and_ingest
        pls = [p.strip() for p in platforms.split(",") if p.strip()]
        disc = await discover_and_ingest(topic, pls, 15)
        out["fresh_competitor_posts"] = {
            "found": disc.get("stored") or disc.get("found") or 0,
            "trends": (disc.get("trends") or [])[:10],
        }
    except Exception as e:  # noqa: BLE001
        out["fresh_competitor_posts"] = {"error": str(e)}
    try:
        from .trends import list_cohort_trends
        coh = await list_cohort_trends(topic, limit=8)
        out["peer_cohort"] = {
            "creators": coh.get("creators") or [],
            "trends": coh.get("trends") or [],
        }
    except Exception as e:  # noqa: BLE001
        out["peer_cohort"] = {"error": str(e)}
    return out


_register(Tool(
    name="scan_market",
    description=(
        "COMPOSITE market scan in one call: live web research + fresh "
        "competitor/peer posts (scraped by topic) + the brand's curated peer "
        "cohort — all saved to memory. Start here for 'analyze the market / "
        "what should we do next'. Spends research + scrape credits, so confirm "
        "intent for tight-budget contexts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "focus": {"type": "string", "default": ""},
            "platforms": {"type": "string", "default": "instagram,tiktok,youtube"},
        },
        "required": ["topic"],
    },
    fn=_t_scan_market,
    writes=True,
))


# ── close the loop: reject + track ───────────────────────────────────


async def _t_reject_item(item_id: str, reason: str) -> dict:
    """Reject a queued item WITH a reason — the reason becomes a memory
    guardrail so the engine stops repeating the mistake (learning loop)."""
    from .db import acquire
    from .learning import record_rejection
    iid = UUID(item_id)
    async with acquire() as conn:
        tag = await conn.execute(
            "UPDATE actions SET status='rejected', "
            "rejection_reason_code=$2, decided_at=now() WHERE id=$1",
            iid, reason,
        )
    if not tag.endswith(" 1"):
        return {"error": f"action {item_id} not found"}
    learned = await record_rejection(iid, reason)
    return {
        "ok": True, "id": item_id, "status": "rejected",
        "learned": bool(learned), "guardrail_id": learned,
    }


_register(Tool(
    name="reject_item",
    description=(
        "Reject a queue item by id WITH a real reason. Unlike approve, this "
        "TEACHES the content engine: the reason is saved as a guardrail so it "
        "stops repeating the mistake. Use when the user wants a draft killed "
        "and the system to learn from it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "reason": {"type": "string", "description": "Why it's wrong — this is what the engine learns from."},
        },
        "required": ["item_id", "reason"],
    },
    fn=_t_reject_item,
    writes=True,
))


async def _t_get_production(production_id: str) -> dict:
    from .video_pipeline import get_production
    r = await get_production(UUID(production_id))
    if not r:
        return {"error": "production not found"}
    return {
        k: r.get(k)
        for k in ("id", "status", "mode", "title", "final_url", "error", "completed_at")
    }


_register(Tool(
    name="get_production",
    description=(
        "Check the status of one video production by id (queued | planning | "
        "rendering_clips | assembling | succeeded | failed) and get its "
        "final_url when done. Use to answer 'is my video ready?'. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {"production_id": {"type": "string"}},
        "required": ["production_id"],
    },
    fn=_t_get_production,
))


# ── run lifecycle ────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are the JAMES OS agent — the operator of a brand-content
system. You take a marketing operator's natural-language command, pick the right
tools, and START AND FINISH the work. Don't just describe what could be done — do it.

WHAT YOU CAN DO (map the user's request to these):

• Answer factual questions about the brand → ask_memory (grounded, cited: voice
  rules, past decisions, what peers said).

• Analyze the brand's OWN performance:
  - get_post_performance_summary — DEFAULT: aggregates + top AND bottom posts.
  - get_top_posts — own posts ranked (engagement_rate | outlier | views | velocity).
  - get_platform_performance — which CHANNEL wins (IG vs TikTok vs YouTube …).
  - get_accounts_leaderboard — which of the brand's accounts wins.
  - analytics_summary — thin legacy summary (prefer get_post_performance_summary).
  - refresh_brand_analytics — re-scrape recent posts if data looks stale, then re-read.

• Analyze the MARKET / competitors / outside world:
  - scan_market — ONE call: live web research + fresh competitor posts + peer
    cohort. Start here for "analyze the market / what should we do".
  - research_topic — deep live web research on one subject (a named competitor,
    a market shift), cited + saved to memory.
  - list_trends — cached peer/competitor posts (free, no scrape).

• Create content (all land in the Approval Queue):
  - generate_post — on-voice text + image draft.
  - generate_reel — script + kicks a real avatar/B-roll render (engaging_avatar).
  - run_autopilot — a whole batch (~50/50 posts+reels); confirm the count first.

• Long-form & media: list_long_sources, render_whole_source, import_drive_video,
  list_drive_videos.

• Manage the queue: list_pending_approvals, approve_item, reject_item (rejects AND
  teaches the engine via a memory guardrail — use when killing a weak draft).

• Track + ship: get_production (is a render done?), list_outputs (finished reels),
  list_brand_accounts / add_brand_account.

HOW TO HANDLE "ANALYZE / WHAT SHOULD I DO" REQUESTS (do REAL work, never one tool):
  1. OWN baseline → get_post_performance_summary (days=30); add
     get_platform_performance and/or get_accounts_leaderboard when relevant. If
     data is thin/stale, refresh_brand_analytics first, then re-read.
  2. OUTSIDE context → scan_market(topic) for web research + fresh competitor
     posts + peer cohort in one shot (use research_topic for a deeper single
     subject). If a research tool reports it's not live-connected (stub), say so
     and lean on list_trends instead — never present stub text as real research.
  3. ON-BRAND context → ask_memory for voice rules / pillars so advice stays on-voice.
  4. COMPARE in NUMBERS → own top vs bottom (what format/hook/pillar/platform
     works), and own engagement_rate vs peer outlier scores / what's winning now.
  5. RECOMMEND → 3–6 concrete, ranked actions, each tied to evidence
     ("TikTok 5.3% ER vs IG 2.1%, peers using narrative hooks → make 3 narrative
     reels for TikTok").
  6. FINISH → when the user wants action, actually create it (generate_post /
     generate_reel / run_autopilot) and report exactly what landed in the queue.

RULES:
  * Factual question → ask_memory first. Action → call the matching tool; you may
    chain (e.g. import_drive_video → wait for status='ready' → render_whole_source).
  * Tools that SPEND money: scan_market / research_topic (research $),
    refresh_brand_analytics (scrape $), generate_reel / run_autopilot /
    render_* (render $). For batches/renders, confirm the count, then report
    roughly what it cost and where the result shows up (Approval Queue / Library).
  * If truly ambiguous, ask ONE clarifying question in your final reply instead of
    guessing. Otherwise proceed.
  * Cite tool outputs; never invent numbers. Be honest about data gaps and stubs.
  * Finish with a short plain-English summary of what you did and the state.

Be efficient with tool calls (budget is limited). Prefer the bundled tools
(get_post_performance_summary, scan_market). Then summarise."""


def _trim_for_log(obj: Any, max_chars: int = 800) -> Any:
    """Truncate string values in a result so the run log stays
    readable. Lists and dicts are recursed; primitives unchanged."""
    if isinstance(obj, str):
        return obj if len(obj) <= max_chars else obj[:max_chars] + f"…(+{len(obj)-max_chars} chars)"
    if isinstance(obj, dict):
        return {k: _trim_for_log(v, max_chars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_trim_for_log(v, max_chars) for v in obj[:30]]
    return obj


async def create_run(prompt: str, tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "INSERT INTO agent_runs (prompt) VALUES ($1) RETURNING *",
            prompt,
        )
    return _row(row)


async def list_runs(
    tenant_id: UUID | None = None, limit: int = 30,
) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, prompt, status, summary, answer, "
            "       jsonb_array_length(tool_calls) AS n_calls, "
            "       created_at, completed_at "
            "  FROM agent_runs "
            " ORDER BY created_at DESC LIMIT $1",
            int(limit),
        )
    return [
        {
            "id": str(r["id"]),
            "prompt": r["prompt"],
            "status": r["status"],
            "summary": r["summary"],
            "answer": r["answer"],
            "tool_call_count": int(r["n_calls"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
        }
        for r in rows
    ]


async def get_run(run_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT * FROM agent_runs WHERE id = $1", run_id,
        )
    return _row(r) if r else None


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    for k in ("tool_calls", "citations"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("created_at", "updated_at", "completed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def _append_tool_call(run_id: UUID, entry: dict, tenant_id: UUID | None) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE agent_runs SET tool_calls = tool_calls || $2::jsonb, "
            "updated_at = now() WHERE id = $1",
            run_id, json.dumps([entry]),
        )


async def _finish(
    run_id: UUID, *, status: str, summary: str = "", answer: str = "",
    citations: list | None = None, error: str = "",
    tenant_id: UUID | None = None,
) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """UPDATE agent_runs
                  SET status = $2, summary = $3, answer = $4,
                      citations = $5::jsonb, error = $6,
                      updated_at = now(), completed_at = now()
                WHERE id = $1""",
            run_id, status, summary, answer,
            json.dumps(citations or []), error,
        )


async def run_agent(run_id: UUID, tenant_id: UUID | None = None) -> None:
    """Execute the tool-use loop for an already-created run. Runs to
    completion; persists state along the way. Catches all exceptions
    so the run row always ends in a terminal state, never orphaned.

    Provider-aware: dispatches to OpenAI function-calling when
    LLM_PROVIDER=openai and Anthropic tool-use when LLM_PROVIDER=anthropic —
    the same provider the rest of the app runs on (see llm.get_llm)."""
    run = await get_run(run_id, tenant_id)
    if run is None:
        return
    provider = (settings.llm_provider or "").lower()
    if provider == "openai":
        await _run_loop_openai(run_id, run, tenant_id)
    elif provider == "anthropic":
        await _run_loop_anthropic(run_id, run, tenant_id)
    else:
        await _finish(
            run_id, status="failed",
            error=(
                "Agent 'Do' mode needs an OpenAI or Anthropic provider, but "
                f"LLM_PROVIDER={provider or 'unset'}. Set it in the backend env."
            ),
            tenant_id=tenant_id,
        )


async def _run_loop_anthropic(
    run_id: UUID, run: dict, tenant_id: UUID | None
) -> None:
    """Claude tool-use loop (raw Anthropic SDK)."""
    try:
        import anthropic
    except ImportError as e:
        await _finish(run_id, status="failed", error=f"anthropic SDK missing: {e}", tenant_id=tenant_id)
        return

    api_key = (settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        await _finish(run_id, status="failed", error="ANTHROPIC_API_KEY not set", tenant_id=tenant_id)
        return

    client = anthropic.AsyncAnthropic(api_key=api_key)
    # settings.llm_model may be a non-Anthropic model name (e.g. OpenAI
    # "gpt-4o-mini") when the pipeline runs on another provider; only honor
    # it when it's actually a Claude model, else use a known Claude default.
    _DEFAULT_AGENT_MODEL = "claude-sonnet-4-5-20250929"
    model = (
        settings.llm_model
        if settings.llm_model.startswith("claude")
        else _DEFAULT_AGENT_MODEL
    )
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": run["prompt"]},
    ]
    tools = get_tool_specs()
    writes_done: list[str] = []

    for turn in range(MAX_TOOL_TURNS):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            balance = (
                "credit balance is too low" in low
                or "billing" in low
                or "insufficient" in low
                or "quota" in low
            )
            okey = (settings.openai_api_key or os.getenv("OPENAI_API_KEY") or "").strip()
            if balance and okey:
                # Operator request: fall back to OpenAI when Claude is out of
                # credits. A balance failure happens on the first call before
                # any tool runs, so restarting fresh on OpenAI from the
                # original prompt is clean.
                await _run_loop_openai(run_id, run, tenant_id)
                return
            if balance:
                msg = (
                    "Anthropic API has no credits and no OpenAI fallback key is "
                    "set. Add credits at console.anthropic.com, or set "
                    "OPENAI_API_KEY. "
                    f"(raw: {msg[:160]})"
                )
            await _finish(run_id, status="failed", error=f"agent LLM call failed: {msg}", tenant_id=tenant_id)
            return

        # Collect any tool_use blocks; capture text too in case the
        # model ends here.
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        text_parts = [b.text for b in resp.content if b.type == "text"]
        text = "\n".join(text_parts).strip()

        if not tool_uses:
            # Final reply from Claude. Persist + done.
            summary_line = text if text else "Done."
            if writes_done:
                summary_line = (
                    f"{summary_line}\n\nActions taken: " + ", ".join(writes_done)
                )
            await _finish(
                run_id, status="succeeded",
                summary=summary_line, answer=text,
                tenant_id=tenant_id,
            )
            return

        # Re-attach the assistant message in full (text + tool_use)
        # because Anthropic requires it before tool_results.
        messages.append({
            "role": "assistant",
            "content": [b.model_dump() for b in resp.content],
        })

        # Execute each tool_use, append results.
        tool_results = []
        for tu in tool_uses:
            tool = _TOOLS.get(tu.name)
            t0 = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            ok = True
            error = ""
            if tool is None:
                result: Any = {"error": f"unknown tool {tu.name}"}
                ok = False
                error = "unknown tool"
            else:
                try:
                    raw_args = tu.input if isinstance(tu.input, dict) else {}
                    # Drop None values so defaults apply.
                    args = {k: v for k, v in raw_args.items() if v is not None}
                    result = await tool.fn(**args)
                    if tool.writes:
                        writes_done.append(tool.name)
                except Exception as e:  # noqa: BLE001
                    result = {"error": f"{type(e).__name__}: {e}"}
                    ok = False
                    error = str(e)

            await _append_tool_call(
                run_id,
                {
                    "name": tu.name,
                    "args": (tu.input if isinstance(tu.input, dict) else {}),
                    "result": _trim_for_log(result),
                    "ok": ok,
                    "error": error,
                    "started_at": started_at,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                },
                tenant_id,
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result),
                "is_error": not ok,
            })

        messages.append({"role": "user", "content": tool_results})

    # Out of turn budget.
    await _finish(
        run_id, status="failed",
        error=f"exceeded {MAX_TOOL_TURNS} tool turns without finishing",
        tenant_id=tenant_id,
    )


def get_openai_tool_specs() -> list[dict]:
    """Same tool registry, in OpenAI function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in _TOOLS.values()
    ]


async def _execute_tool(
    run_id: UUID,
    name: str,
    args: dict,
    writes_done: list[str],
    tenant_id: UUID | None,
) -> tuple[Any, bool]:
    """Run one tool call, log it to the run row, return (result, ok)."""
    tool = _TOOLS.get(name)
    t0 = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    ok = True
    error = ""
    if tool is None:
        result: Any = {"error": f"unknown tool {name}"}
        ok = False
        error = "unknown tool"
    else:
        try:
            clean = {k: v for k, v in (args or {}).items() if v is not None}
            result = await tool.fn(**clean)
            if tool.writes:
                writes_done.append(tool.name)
        except Exception as e:  # noqa: BLE001
            result = {"error": f"{type(e).__name__}: {e}"}
            ok = False
            error = str(e)
    await _append_tool_call(
        run_id,
        {
            "name": name,
            "args": args or {},
            "result": _trim_for_log(result),
            "ok": ok,
            "error": error,
            "started_at": started_at,
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        },
        tenant_id,
    )
    return result, ok


async def _run_loop_openai(
    run_id: UUID, run: dict, tenant_id: UUID | None
) -> None:
    """OpenAI function-calling loop — mirrors the Claude loop using the
    chat.completions tool shape (system message, tools as {type:function},
    tool outputs as role='tool' messages keyed on tool_call_id)."""
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        await _finish(run_id, status="failed", error=f"openai SDK missing: {e}", tenant_id=tenant_id)
        return

    api_key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        await _finish(run_id, status="failed", error="OPENAI_API_KEY not set", tenant_id=tenant_id)
        return

    client = AsyncOpenAI(api_key=api_key)
    # settings.llm_model is the OpenAI pipeline model (e.g. gpt-4o-mini). If it
    # somehow points at a Claude model, fall back to a known OpenAI default.
    model = (
        settings.llm_model
        if not settings.llm_model.startswith("claude")
        else "gpt-4o-mini"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": run["prompt"]},
    ]
    tools = get_openai_tool_specs()
    writes_done: list[str] = []

    for _turn in range(MAX_TOOL_TURNS):
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=messages,
                tools=tools,
            )
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            if "quota" in low or "billing" in low or "insufficient" in low:
                msg = (
                    "OpenAI API quota/credits exhausted. Add credits at "
                    "platform.openai.com → Billing. "
                    f"(raw: {msg[:160]})"
                )
            await _finish(run_id, status="failed", error=f"agent LLM call failed: {msg}", tenant_id=tenant_id)
            return

        m = resp.choices[0].message
        tool_calls = m.tool_calls or []
        text = (m.content or "").strip()

        if not tool_calls:
            summary_line = text if text else "Done."
            if writes_done:
                summary_line = (
                    f"{summary_line}\n\nActions taken: " + ", ".join(writes_done)
                )
            await _finish(
                run_id, status="succeeded",
                summary=summary_line, answer=text,
                tenant_id=tenant_id,
            )
            return

        # Re-attach the assistant message (with its tool_calls) before the
        # tool outputs — OpenAI requires every tool_call answered by a
        # role='tool' message keyed on tool_call_id, in order.
        messages.append({
            "role": "assistant",
            "content": m.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result, _ok = await _execute_tool(
                run_id, tc.function.name, args, writes_done, tenant_id
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    await _finish(
        run_id, status="failed",
        error=f"exceeded {MAX_TOOL_TURNS} tool turns without finishing",
        tenant_id=tenant_id,
    )


__all__ = [
    "create_run", "list_runs", "get_run", "run_agent",
    "get_tool_specs", "get_openai_tool_specs", "_TOOLS",
]
