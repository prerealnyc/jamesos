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


# ── run lifecycle ────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are the JAMES OS agent.

You operate the brand-manager system: you take natural-language commands from a
marketing operator and run them by calling tools. Every tool maps to a real
feature the system already has (importing Drive videos, rendering reels,
refreshing analytics, approving queue items, etc.).

Rules:
  * If the user is asking a FACTUAL question (what / who / when / why /
    what's our voice rule about hashtags?), call `ask_memory` first.
  * If the user wants something DONE (render, refresh, add, approve,
    import), call the matching tool. You may chain — e.g. to render a
    podcast that's not yet imported, first import_drive_video, then
    once it's ready (status='ready'), render it.
  * Tools that mutate state or spend money (render_*, refresh_*,
    import_*, approve_*) — be explicit in your final summary about
    what you kicked, how much it'll cost roughly, and how the user
    sees the result (queue page, library, analytics).
  * If the user's request is ambiguous, ask back IN your final reply
    instead of guessing. One clarifying question beats a wrong action.
  * When you're done, write a short plain-English summary of what you
    did so the user reads one line and knows the state.

Be terse. Don't narrate every step — just call tools, then summarise."""


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
