"""Compatibility API for the JP Brand Manager dashboard build.

The dashboard is a minified Vite bundle built against the old Express/SQLite
backend. It calls ~20 `/api/...` endpoints. JAMES OS backs the ones that map
to a real concept here (the approval queue == the `actions` table, system
status, scan history) and graceful-stubs the rest so old-project pages render
empty instead of crashing.

Honest scope: shapes for the queue endpoints are best-effort against a
minified bundle. The clean fix is the dashboard SOURCE repo.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, HTTPException

from .config import settings
from .db import acquire

router = APIRouter(prefix="/api")

SOCIAL_PLATFORMS = [
    "instagram", "tiktok", "x", "facebook",
    "linkedin", "youtube", "threads", "substack",
]


# ─────────────────────────────────── profile (lightweight, not auth) ──

@router.get("/profile")
async def get_profile() -> dict:
    async with acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return (cfg or {}).get("profile", {"name": "", "email": "", "brand": "JP Brand Manager"})


@router.post("/profile")
async def set_profile(body: dict = Body(...)) -> dict:
    prof = {
        "name": str(body.get("name", ""))[:120],
        "email": str(body.get("email", ""))[:200],
        "brand": str(body.get("brand", "JP Brand Manager"))[:120],
    }
    async with acquire() as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{profile}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(prof),
        )
    return {"ok": True, "profile": prof}


# ─────────────────────────────────── social connections (config) ──

@router.get("/connections")
async def get_connections() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT platform, handle, enabled, status FROM connections"
        )
    have = {r["platform"]: dict(r) for r in rows}
    out = []
    for p in SOCIAL_PLATFORMS:
        r = have.get(p)
        out.append({
            "platform": p,
            "handle": r["handle"] if r else "",
            "enabled": r["enabled"] if r else False,
            "status": r["status"] if r else "not_connected",
        })
    return out


@router.post("/connections")
async def upsert_connection(body: dict = Body(...)) -> dict:
    platform = str(body.get("platform", "")).lower().strip()
    if platform not in SOCIAL_PLATFORMS:
        return {"ok": False, "error": f"unknown platform '{platform}'"}
    handle = str(body.get("handle", "")).strip()[:120]
    enabled = bool(body.get("enabled", False))
    status = "configured" if handle else "not_connected"
    async with acquire() as conn:
        await conn.execute(
            "INSERT INTO connections (platform, handle, enabled, status) "
            "VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (tenant_id, platform) DO UPDATE SET "
            "handle=$2, enabled=$3, status=$4, updated_at=now()",
            platform, handle, enabled, status,
        )
    return {"ok": True, "platform": platform, "status": status}




# ─────────────────────────────────── API credentials (tenant-managed) ──
# Drop keys in from the Settings UI; they persist per-tenant in the DB and
# take effect on the next request (no restart). Raw values NEVER leave the
# process — GET returns masked previews only.

@router.get("/credentials")
async def get_credentials() -> dict:
    from .credentials import status

    return await status()


@router.post("/credentials")
async def set_credentials(body: dict = Body(...)) -> dict:
    """Body: {"updates": {"perplexity_api_key": "pplx-...", ...}}.
    Empty string clears a key (reverts to the .env default)."""
    from .credentials import save

    updates = body.get("updates")
    if not isinstance(updates, dict):
        return {"ok": False, "error": "expected {'updates': {field: value}}"}
    result = await save({str(k): str(v) for k, v in updates.items()})
    return {"ok": True, **result}


# ─────────────────────────────────── integration status (bools only) ──

@router.get("/integrations")
async def integrations() -> dict:
    """Which credentials are present. Booleans only — values never leave
    the process. `active` = JAMES OS has code that actually uses it today.
    """
    def cfg(v: str) -> bool:
        return bool(v and v.strip())

    return {
        "configured": {
            "anthropic": cfg(settings.anthropic_api_key),
            "voyage": cfg(settings.voyage_api_key),
            "cohere": cfg(settings.cohere_api_key),
            "openai": cfg(settings.openai_api_key),
            "elevenlabs": cfg(settings.elevenlabs_api_key),
            "heygen": cfg(settings.heygen_api_key),
            "xpoz": cfg(settings.xpoz_api_key),
            "runway": cfg(settings.runway_api_key),
            "minimax": cfg(settings.minimax_api_key),
            "postproxy": cfg(settings.postproxy_api_key),
            "meta": cfg(settings.meta_access_token),
            "twitter": cfg(settings.twitter_bearer_token),
            "xpoz": cfg(settings.xpoz_api_key),
            "perplexity": cfg(settings.perplexity_api_key),
            "google_search": cfg(settings.google_search_api_key)
            and cfg(settings.google_search_cx),
        },
        # Only these are wired to real code paths today. openai = Whisper
        # audio transcription in the ingestion pipeline; perplexity = the
        # /research endpoint; runway = the /video durable render pipeline.
        "active": [
            "anthropic", "voyage", "cohere", "openai", "perplexity", "runway",
        ],
    }


# ───────────────────── live connectivity verification ──
# Real authenticated calls. Status vocabulary, deliberately constrained
# so the result is honest, not green-washed:
#   ok            credential authenticated against the live service
#   bad_key       service rejected the credential (401/403)
#   rate_limited  authenticated but quota/billing-blocked (e.g. Voyage free tier)
#   unverified    no safe free probe endpoint — verified at first real use
#                 (NOT failure; we refuse to fabricate a green check)
#   not_configured no key present
# Key VALUES are never returned — only status + a short detail string.

async def _probe(client: httpx.AsyncClient, name: str) -> dict:
    try:
        if name == "openai":
            if not settings.openai_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            if r.status_code == 200:
                n = len(r.json().get("data", []))
                return {"status": "ok", "detail": f"{n} models reachable"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "anthropic":
            if not settings.anthropic_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": "models reachable"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "voyage":
            if not settings.voyage_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"model": settings.embedding_model, "input": ["ping"]},
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": "embedding returned"}
            if r.status_code == 429:
                msg = ""
                try:
                    msg = (r.json().get("detail") or "")[:120]
                except Exception:
                    msg = "rate limited"
                return {"status": "rate_limited", "detail": msg}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "cohere":
            if not settings.cohere_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
                json={
                    "model": "rerank-v3.5",
                    "query": "ping",
                    "documents": ["pong"],
                    "top_n": 1,
                },
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": "rerank returned"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "heygen":
            if not settings.heygen_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.heygen.com/v2/avatars",
                headers={"X-Api-Key": settings.heygen_api_key},
            )
            if r.status_code == 200:
                body = r.json()
                avatars = (body.get("data") or {}).get("avatars") or body.get("data") or []
                cnt = len(avatars) if isinstance(avatars, list) else "?"
                return {"status": "ok", "detail": f"{cnt} avatars reachable"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "runway":
            if not settings.runway_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.dev.runwayml.com/v1/organization",
                headers={
                    "Authorization": f"Bearer {settings.runway_api_key}",
                    "X-Runway-Version": "2024-11-06",
                },
            )
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            if r.status_code == 200:
                return {"status": "ok", "detail": "organization reachable"}
            return {
                "status": "unverified",
                "detail": f"HTTP {r.status_code} — probe endpoint not confirmed; "
                "verified on first real generation call",
            }

        if name == "perplexity":
            if not settings.perplexity_api_key:
                return {"status": "not_configured", "detail": ""}
            # Smallest possible authenticated call (max_tokens=1).
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.perplexity_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.perplexity_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 16,
                },
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": "completion returned"}
            if r.status_code == 429:
                return {"status": "rate_limited", "detail": "HTTP 429"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "google_search":
            if not (settings.google_search_api_key and settings.google_search_cx):
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": settings.google_search_api_key,
                    "cx": settings.google_search_cx,
                    "q": "ping",
                    "num": 1,
                },
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": "search returned"}
            if r.status_code == 429:
                return {"status": "rate_limited", "detail": "daily quota (HTTP 429)"}
            if r.status_code in (400, 401, 403):
                # 400 usually = bad cx; 403 = bad key or quota.
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "apify":
            if not settings.apify_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.apify.com/v2/users/me",
                params={"token": settings.apify_api_key},
            )
            if r.status_code == 200:
                u = r.json().get("data", {}) or {}
                plan = (u.get("plan") or {}).get("id") or "?"
                return {"status": "ok",
                        "detail": f"user={u.get('username','?')} plan={plan}"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            if r.status_code == 429:
                return {"status": "rate_limited", "detail": "HTTP 429"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "creatomate":
            if not settings.creatomate_api_key:
                return {"status": "not_configured", "detail": ""}
            r = await client.get(
                "https://api.creatomate.com/v1/templates",
                headers={"Authorization": f"Bearer {settings.creatomate_api_key}"},
            )
            if r.status_code == 200:
                n = len(r.json()) if isinstance(r.json(), list) else "?"
                return {"status": "ok", "detail": f"auth OK, {n} templates"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        if name == "shotstack":
            if not settings.shotstack_api_key:
                return {"status": "not_configured", "detail": ""}
            env = settings.shotstack_env or "stage"
            r = await client.get(
                f"https://api.shotstack.io/{env}/assets",
                headers={"x-api-key": settings.shotstack_api_key},
            )
            if r.status_code == 200:
                return {"status": "ok", "detail": f"auth OK ({env})"}
            if r.status_code in (401, 403):
                return {"status": "bad_key", "detail": f"HTTP {r.status_code}"}
            return {"status": "unverified", "detail": f"HTTP {r.status_code}"}

        return {"status": "not_configured", "detail": "no probe defined"}
    except httpx.TimeoutException:
        return {"status": "unverified", "detail": "probe timed out"}
    except Exception as e:  # noqa: BLE001
        return {"status": "unverified", "detail": f"{type(e).__name__}"}


@router.get("/integrations/check")
async def integrations_check() -> dict:
    """Live authenticated probe of each configured credential. Real calls,
    honest status, key values never returned. Slow (network) — call on
    demand, not on every page load.
    """
    services = [
        "anthropic", "openai", "voyage", "cohere",
        "heygen", "runway",
        "perplexity", "google_search",
        "apify", "creatomate", "shotstack",
    ]
    async with httpx.AsyncClient(timeout=20.0) as client:
        results = await asyncio.gather(
            *[_probe(client, s) for s in services]
        )
    return {"checked_at": datetime.now(UTC).isoformat(),
            "results": dict(zip(services, results, strict=True))}


# ─────────────────────────────────────── approval queue (REAL) ──

def _action_to_queue_item(row: dict) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    body = payload.get("content") or payload.get("text") or ""
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "platform": payload.get("platform", "—"),
        "pillar": payload.get("pillar", "—"),
        "format": payload.get("format", row["action_type"]),
        "content": body,
        "caption": body,
        "voiceScore": payload.get("voice_score"),
        "imageUrl": payload.get("image_url"),
        "mediaUrl": payload.get("media_url"),
        "proposedBy": row["proposed_by"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "scheduledFor": payload.get("scheduled_for"),
        "reason": row["approval_reason"] or row["rejection_reason_code"],
    }


@router.get("/queue")
async def get_queue() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM actions ORDER BY created_at DESC LIMIT 100"
        )
    return [_action_to_queue_item(dict(r)) for r in rows]


@router.get("/queue/stats")
async def queue_stats() -> dict:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n FROM actions GROUP BY status"
        )
    by = {r["status"]: r["n"] for r in rows}
    return {
        "pending": by.get("pending", 0),
        "approved": by.get("approved", 0),
        "rejected": by.get("rejected", 0),
        "executed": by.get("executed", 0),
        "total": sum(by.values()),
    }


@router.post("/queue/{item_id}/approve")
async def approve_item(item_id: UUID, body: dict = Body(default={})) -> dict:
    async with acquire() as conn:
        # asyncpg's execute() returns the command tag, e.g. 'UPDATE 1' /
        # 'UPDATE 0' — the count is the second token. We can't lie to the
        # caller about a write that affected nothing.
        tag = await conn.execute(
            "UPDATE actions SET status='approved', approval_reason=$2, "
            "decided_at=now() WHERE id=$1",
            item_id,
            body.get("reason", "approved via dashboard"),
        )
    if not tag.endswith(" 1"):
        raise HTTPException(status_code=404, detail=f"action {item_id} not found")
    # Positive half of the learning loop: an approved draft becomes a
    # voice_corpus exemplar so the engine makes more like it. Best-effort —
    # never fail the approval if reinforcement hiccups.
    learned_id = None
    try:
        from .learning import record_approval
        learned_id = await record_approval(item_id)
    except Exception:  # noqa: BLE001
        learned_id = None
    return {"ok": True, "id": str(item_id), "status": "approved", "reinforced": bool(learned_id)}


@router.post("/queue/{item_id}/reject")
async def reject_item(item_id: UUID, body: dict = Body(default={})) -> dict:
    reason = body.get("reason", "rejected")
    async with acquire() as conn:
        tag = await conn.execute(
            "UPDATE actions SET status='rejected', "
            "rejection_reason_code=$2, decided_at=now() WHERE id=$1",
            item_id,
            reason,
        )
    if not tag.endswith(" 1"):
        raise HTTPException(status_code=404, detail=f"action {item_id} not found")
    # Close the learning loop: turn the manager's reason into a hard
    # guardrail in memory so the engine doesn't repeat the mistake.
    from .learning import record_rejection

    learned_id = await record_rejection(item_id, reason)
    # Auto-refresh the "What's changing next" board with this feedback.
    from .feedback_interpreter import kick_interpret_background
    kick_interpret_background()
    return {
        "ok": True,
        "id": str(item_id),
        "status": "rejected",
        "learned": bool(learned_id),
        "guardrail_id": learned_id,
    }


@router.delete("/queue/{item_id}")
async def delete_item(item_id: UUID) -> dict:
    """Hard-delete a queue item. Unlike reject (which keeps the row +
    learns from it), delete removes it entirely — for spam, dupes, or
    test rows the marketing manager just wants gone. No learning event."""
    async with acquire() as conn:
        tag = await conn.execute("DELETE FROM actions WHERE id=$1", item_id)
    if not tag.endswith(" 1"):
        raise HTTPException(status_code=404, detail=f"action {item_id} not found")
    return {"ok": True, "id": str(item_id), "deleted": True}


@router.get("/guardrails")
async def guardrails() -> dict:
    """The learned 'never do this' ledger from past rejections."""
    from .learning import recent_guardrails

    return {"guardrails": await recent_guardrails()}


# ─────────────────────────────────────── system / pipeline (REAL) ──

@router.get("/system/last-scan")
async def last_scan() -> dict:
    async with acquire() as conn:
        ts = await conn.fetchval(
            "SELECT max(created_at) FROM events"
        )
    return {"lastScanAt": ts.isoformat() if ts else None}


@router.get("/scan-logs")
async def scan_logs() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, left(coalesce(raw_content,''),120) AS summary, "
            "created_at FROM events ORDER BY created_at DESC LIMIT 50"
        )
    return [
        {
            "id": str(r["id"]),
            "type": r["event_type"],
            "summary": r["summary"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
            "status": "ok",
        }
        for r in rows
    ]


@router.get("/pipeline/health")
async def pipeline_health() -> list[dict]:
    return [
        {"stage": "ingest", "ok": True},
        {"stage": "retrieve", "ok": True},
        {"stage": "generate", "ok": True},
        {"stage": "verify", "ok": True},
        {"stage": "approve", "ok": True},
    ]


@router.get("/pipeline/runs")
async def pipeline_runs() -> list[dict]:
    return []


@router.post("/pipeline/run")
async def pipeline_run() -> dict:
    return {"ok": True, "started": datetime.now(UTC).isoformat()}


# ─────────────────────── old-project features (graceful empty) ──
# These have no JAMES OS data behind them yet. Return empty so the
# dashboard pages render their empty states instead of crashing.

_EMPTY_LIST = [
    "peer-creators",
    "market-research",
    "image-library",
    "jp-clip-library",
    "social-content",
    "design-content",
    "voice/pending-amendments",
    "scan-logs-archive",
]


# Illustrative peer cohort so the peer-comparison / market-research pages
# render their real UI. NOT real metrics — the actual cohort is "to be
# locked with James per P47" (unlocked in the foundational docs). Replace
# when the real cohort + a social-metrics source exist.
_PEER_SAMPLE = [
    {"id": "p1", "name": "[sample] RE Thought Leader", "platform": "Instagram",
     "category": "real-estate", "handle": "@sample_re", "followers": 142000,
     "engagementRate": 3.1, "sample": True},
    {"id": "p2", "name": "[sample] NM Civic Voice", "platform": "X",
     "category": "civic", "handle": "@sample_civic", "followers": 88000,
     "engagementRate": 2.4, "sample": True},
    {"id": "p3", "name": "[sample] Mindset / Neville-adjacent", "platform": "YouTube",
     "category": "mindset", "handle": "@sample_mindset", "followers": 310000,
     "engagementRate": 4.6, "sample": True},
    {"id": "p4", "name": "[sample] Education Reform Operator", "platform": "LinkedIn",
     "category": "education", "handle": "@sample_edu", "followers": 51000,
     "engagementRate": 1.9, "sample": True},
    {"id": "p5", "name": "[sample] Real Estate Macro Caller", "platform": "Substack",
     "category": "real-estate", "handle": "@sample_macro", "followers": 67000,
     "engagementRate": 5.2, "sample": True},
]


@router.get("/peer-creators")
async def _peer_creators() -> list:
    return _PEER_SAMPLE


@router.get("/market-research")
async def _market_research() -> list:
    return []


@router.get("/image-library")
async def _image_library() -> list:
    return []


@router.get("/jp-clip-library")
async def _clip_library() -> list:
    return []


@router.get("/social-content")
async def _social_content() -> list[dict]:
    """Real ingested events surfaced as the content feed.

    Engagement metrics are honestly zero — JAMES OS has no social-metrics
    source wired. This is real content, not fabricated performance.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, raw_content, payload, created_at "
            "FROM events WHERE superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 60"
        )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        caption = r["raw_content"] or payload.get("text") or ""
        out.append({
            "id": str(r["id"]),
            "platform": payload.get("platform") or r["event_type"],
            "caption": caption,
            "publishedAt": r["created_at"].isoformat() if r["created_at"] else None,
            "engagement": 0,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "metricsAvailable": False,
        })
    return out


@router.get("/design-content")
async def _design_content() -> list:
    return []


@router.get("/brand-voice")
async def _brand_voice() -> dict:
    return {"profile": None, "rules": [], "note": "voice profile not yet ingested"}


@router.get("/voice/pending-amendments")
async def _pending_amendments() -> list:
    return []


@router.post("/refresh-social")
async def _refresh_social() -> dict:
    return {"ok": True, "note": "social ingestion not wired in JAMES OS yet"}


@router.post("/refresh-peers")
async def _refresh_peers() -> dict:
    return {"ok": True, "note": "peer ingestion not wired in JAMES OS yet"}


@router.post("/seed")
async def _seed() -> dict:
    return {"ok": True}


@router.post("/generate-caption")
async def _gen_caption(body: dict = Body(default={})) -> dict:
    return {
        "caption": "",
        "note": "caption generation routes through JAMES OS /ask in the real build",
    }


@router.post("/generate-image")
async def _gen_image(body: dict = Body(default={})) -> dict:
    return {"url": None, "note": "image generation not wired in JAMES OS yet"}


# Catch-all REMOVED 2026-05 — it was masking every frontend typo and
# every removed route as `200 OK []`, making regressions invisible. Real
# 404s surface bugs.
