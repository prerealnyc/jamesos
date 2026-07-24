"""Public /v1 service façade — the stable, versioned API another platform calls
to drive JAMES OS headlessly.

This is the "hands as a service" layer: a small, intent-level surface (create a
post/video, ask the memory, get the weekly plan, read/approve the queue) that a
separate experience layer (e.g. the 2.0 UI's backend) calls machine-to-machine.
The end user never sees JAMES OS.

Design decisions:
- AUTH is a service API key (Authorization: Bearer <SERVICE_API_KEY>), NOT the
  browser cookie. Unset key => 503 (disabled by default). The key is BOUND to a
  single configured tenant (settings.service_api_tenant_id or default_tenant_id);
  the caller cannot choose the tenant, so one shared key can never act as another
  brand. main.py whitelists "/v1/" so the cookie gate doesn't 401 us first; this
  router's require_service dependency enforces the key.
- TENANT is passed EXPLICITLY into every engine call + acquire(tenant_id) (never
  relies on the request contextvar, which is lost in background tasks).
- ASYNC job model: content generation is slow (video renders run detached), so
  POST /v1/generate returns a job_id immediately; poll GET /v1/jobs/{id} or receive
  a callback. The produced content is persistent (actions / video_productions), so
  results are also retrievable via /v1/queue.
- Approve/reject mirror the app's own gates exactly: the voice-QA hard gate on
  posts, the succeeded-render guard on videos, and BOTH halves of the learning
  loop (record_approval / record_rejection / record_video_feedback), so driving the
  system over /v1 teaches it the same as the built-in UI does.

NOTE: the job registry is in-memory (lost on restart, not shared across workers).
The persistent artifacts make that acceptable for v1; a durable jobs table is the
next hardening step.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import socket
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .db import acquire

router = APIRouter(prefix="/v1", tags=["v1"])


# ─────────────────────────────────────────────────────────────── auth ──

def service_key_tenant(authorization: str | None, x_tenant_id: str | None = None) -> UUID | None:
    """Resolve a service-key request to its tenant, or None if unauthenticated.
    Shared by /v1 AND the allowlisted internal API (via the auth middleware).

    - BRAND key   -> its one bound tenant (X-Tenant-Id ignored).
    - PLATFORM key -> the X-Tenant-Id header — a trusted multi-tenant control plane
      (e.g. the 2.0 backend) picks the tenant; RLS still isolates each tenant's data.

    The tenant is NEVER browser-chosen: only a holder of these server-side keys can
    set it, and only the platform key may cross tenants."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    brand = (settings.service_api_key or "").strip()
    if brand and hmac.compare_digest(token, brand):
        return settings.service_api_tenant_id or settings.default_tenant_id
    platform = (settings.service_api_platform_key or "").strip()
    if platform and hmac.compare_digest(token, platform):
        if not x_tenant_id:
            return None
        try:
            return UUID(x_tenant_id)
        except ValueError:
            return None
    return None


def is_platform_key(authorization: str | None) -> bool:
    """True iff the bearer is the platform key (gates tenant provisioning)."""
    platform = (settings.service_api_platform_key or "").strip()
    if not platform or not authorization or not authorization.lower().startswith("bearer "):
        return False
    token = authorization[7:].strip()
    return bool(token) and hmac.compare_digest(token, platform)


# Platform-key calls may name ANY tenant via X-Tenant-Id. Cache the ids we have
# already confirmed real so the existence guard costs a DB round-trip only the
# first time each tenant is seen (grow-only; unknown/typo'd ids are never cached,
# so they are always re-checked and rejected).
_KNOWN_TENANTS: set[str] = set()


async def tenant_is_real(tenant_id: UUID) -> bool:
    """True iff tenant_id is a provisioned tenant. Goes through the SECURITY
    DEFINER tenant_exists() because the app role only sees its own tenant row
    under the tenants RLS policy (a direct SELECT would reject every OTHER tenant)."""
    key = str(tenant_id)
    if key in _KNOWN_TENANTS:
        return True
    async with acquire() as conn:
        ok = bool(await conn.fetchval("SELECT tenant_exists($1)", tenant_id))
    if ok:
        _KNOWN_TENANTS.add(key)
    return ok


async def require_service(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> UUID:
    """FastAPI dependency for /v1 routes: validate the key, return the target tenant."""
    if not ((settings.service_api_key or "").strip()
            or (settings.service_api_platform_key or "").strip()):
        raise HTTPException(503, "service API is disabled (no SERVICE_API_KEY set)")
    tid = service_key_tenant(authorization, x_tenant_id)
    if tid is None:
        raise HTTPException(
            401, "invalid key, or platform key missing/invalid X-Tenant-Id header")
    # A platform key may name any tenant; reject a bad/stale X-Tenant-Id cleanly
    # instead of running the request under a nonexistent (ghost) tenant scope.
    if is_platform_key(authorization) and not await tenant_is_real(tid):
        raise HTTPException(
            404, "unknown tenant: X-Tenant-Id does not match any provisioned tenant")
    return tid


# Injected into every /v1 route: validates the key and yields the bound tenant.
TenantDep = Annotated[UUID, Depends(require_service)]


# ──────────────────────────────────────────────── in-memory job store ──

_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TASKS: set[asyncio.Task] = set()
_MAX_JOBS = 500


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _put_job(job: dict[str, Any]) -> None:
    # Bound memory, but ONLY evict jobs that have finished — never drop a job whose
    # background task is still running (its result would become unpollable).
    if len(_JOBS) >= _MAX_JOBS:
        done = sorted(
            (j for j in _JOBS.values() if j["status"] in ("done", "failed")),
            key=lambda x: x["created_at"],
        )
        for j in done[: len(_JOBS) - _MAX_JOBS + 1]:
            _JOBS.pop(j["id"], None)
    _JOBS[job["id"]] = job


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _JOB_TASKS.add(task)
    task.add_done_callback(_JOB_TASKS.discard)


async def _callback_allowed(url: str) -> bool:
    """SSRF guard: only https, and only if every resolved IP is public."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def _fire_callback(url: str, job: dict[str, Any]) -> None:
    if not await _callback_allowed(url):
        print(f"[v1] callback rejected (not https / resolves to a private IP): {url}")
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, json={
                "job_id": job["id"],
                "status": job["status"],
                "result": job.get("result"),
                "error": job.get("error"),
            })
    except Exception as e:  # noqa: BLE001 — a bad webhook must not crash the job
        print(f"[v1] callback POST to {url} failed: {e}")


# ─────────────────────────────────────────────────────── models ──

class GenerateRequest(BaseModel):
    type: Literal["post", "video"] = "post"
    brief: str = Field(..., min_length=1, description="topic (post) or topic/script (video)")
    platform: str = "instagram"
    title: str | None = None
    image_kind: Literal["james", "designed"] = "james"   # post only
    video_template: str = ""                              # video only
    # video only: force a real avatar render even if the brand's avatar_videos
    # default is off (costs render credits; needs HeyGen configured). Default
    # false → a reel SCRIPT draft, matching the safe product default.
    render: bool = False
    callback_url: str | None = None


class BatchRequest(BaseModel):
    count: int = Field(3, ge=1, le=50)
    mix: Literal["mixed", "video", "text"] = "mixed"


class AskBody(BaseModel):
    question: str = Field(..., min_length=1)
    audience: Literal["internal", "public"] = "internal"
    history: list[dict[str, str]] = []


class Decision(BaseModel):
    reason: str = ""
    override: bool = False   # approve past the voice-QA gate (posts)


# ─────────────────────────────────────────────────────── health ──

@router.get("/ping")
async def v1_ping(tenant_id: TenantDep) -> dict[str, Any]:
    """Cheap auth + connectivity check. Echoes the resolved tenant so a platform-key
    caller can confirm its X-Tenant-Id routed correctly."""
    return {"ok": True, "tenant_id": str(tenant_id)}


# ─────────────────────────────────────────────── tenant provisioning ──

class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: str | None = None


@router.post("/tenants", status_code=201)
async def v1_create_tenant(
    body: TenantCreate, authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Provision a new client tenant (fully isolated). PLATFORM KEY ONLY — a brand
    key cannot create tenants. Returns the tenant_id to send as X-Tenant-Id on that
    client's subsequent calls. The client then uploads their own hero/knowledge/voice
    into this tenant and all their content is produced from it.

    The tenants table is RLS-scoped (id = app.current_tenant), so the app role
    cannot INSERT a new-id row directly; provisioning goes through the
    SECURITY DEFINER `provision_tenant()` function (slug + uniqueness handled
    server-side)."""
    if not is_platform_key(authorization):
        raise HTTPException(403, "tenant provisioning requires the platform API key")
    async with acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT id, name, slug FROM provision_tenant($1, $2)",
                body.name, body.slug)
        except Exception as exc:  # noqa: BLE001 — log detail server-side, don't leak DB internals
            print(f"[v1] provision_tenant failed: {exc}")
            raise HTTPException(500, "tenant provisioning failed") from exc
    if row is None:
        raise HTTPException(500, "tenant provisioning returned no row")
    return {"tenant_id": str(row["id"]), "name": row["name"], "slug": row["slug"]}


# ─────────────────────────────────────────────────── generation ──

async def _run_generate(job_id: str, tenant_id: UUID, req: GenerateRequest) -> None:
    from .autopilot import get_config
    from .autopilot_bulk import _make_reel_script, _make_text_post, _make_video

    job = _JOBS.get(job_id)
    if job is None:
        return
    job["status"] = "running"
    job["updated_at"] = _now()
    try:
        idea = {
            "title": (req.title or f"{req.type}: {req.brief[:70]}"),
            "topic": req.brief,
            "pillar": "",
        }
        if req.type == "video":
            # Render only when the caller explicitly asks (render=true) OR the
            # brand's avatar_videos policy is on. Otherwise a reel SCRIPT draft.
            cfg = await get_config(tenant_id)
            if req.render or bool(cfg.get("avatar_videos", False)):
                made = await _make_video(
                    idea, req.platform, tenant_id, video_template=req.video_template)
                job["result"] = {
                    "kind": "video",
                    "production_id": made.get("production_id"),
                    "status": made.get("status"),
                }
            else:
                made = await _make_reel_script(idea, req.platform, tenant_id)
                job["result"] = {
                    "kind": "reel_script",
                    "action_id": made.get("action_id"),
                    "status": made.get("status"),
                    "note": "avatar videos are off for this brand — produced a reel "
                            "script draft instead (enable avatar_videos to render).",
                }
        else:
            made = await _make_text_post(
                idea, req.platform, tenant_id, image_kind=req.image_kind)
            job["result"] = {
                "kind": "post",
                "action_id": made.get("action_id"),
                "status": made.get("status"),
                "voice_score": made.get("voice_score"),
            }
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 — surface the failure on the job, don't crash
        job["status"] = "failed"
        job["error"] = str(e)
    job["updated_at"] = _now()
    if req.callback_url:
        await _fire_callback(req.callback_url, job)


@router.post("/generate", status_code=202)
async def v1_generate(req: GenerateRequest, tenant_id: TenantDep) -> dict[str, Any]:
    """Create one post or video. Returns a job_id immediately; poll /v1/jobs/{id}
    (or supply callback_url to be notified on completion)."""
    if req.callback_url and urlparse(req.callback_url).scheme != "https":
        raise HTTPException(400, "callback_url must be an https URL")
    job = {
        "id": str(uuid.uuid4()),
        "type": req.type,
        "tenant_id": str(tenant_id),
        "status": "queued",
        "result": None,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _put_job(job)
    _spawn(_run_generate(job["id"], tenant_id, req))
    return {"job_id": job["id"], "status": "queued"}


@router.get("/jobs/{job_id}")
async def v1_job(job_id: str, tenant_id: TenantDep) -> dict[str, Any]:
    """Poll a generate job. For videos, the render runs detached — this re-reads
    the production's live render status + output URL from the DB each call."""
    job = _JOBS.get(job_id)
    if not job or job["tenant_id"] != str(tenant_id):
        raise HTTPException(404, "job not found (unknown, expired, or wrong tenant)")
    out = {
        "job_id": job["id"], "type": job["type"], "status": job["status"],
        "result": job.get("result"), "error": job.get("error"),
        "created_at": job["created_at"], "updated_at": job["updated_at"],
    }
    res = job.get("result") or {}
    if res.get("kind") == "video" and res.get("production_id"):
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT status, final_url, review_status FROM video_productions "
                "WHERE id=$1", UUID(res["production_id"]))
        if row:
            out["result"] = {
                **res,
                "render_status": row["status"],
                "url": row["final_url"],
                "review_status": row["review_status"],
            }
    return out


@router.post("/generate/batch", status_code=202)
async def v1_batch(req: BatchRequest, tenant_id: TenantDep) -> dict[str, Any]:
    """Bulk-generate a mix of posts/videos into the approval queue (fire-and-forget)."""
    from .autopilot_bulk import generate_bulk

    _spawn(_safe(generate_bulk, req.count, 0, tenant_id, req.mix))
    return {"started": True, "requested": req.count, "mix": req.mix}


# ─────────────────────────────────────────────────────── ask ──

@router.post("/ask")
async def v1_ask(body: AskBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Query the memory. Returns a grounded, cited answer (or a refusal)."""
    from .ask import ask
    from .models import AskRequest, AskTurn

    turns: list[AskTurn] = []
    for t in body.history:
        role = t.get("role")
        if role in ("user", "assistant") and t.get("content"):
            turns.append(AskTurn(role=role, content=str(t["content"])))
    res = await ask(
        AskRequest(question=body.question, audience=body.audience, history=turns),
        tenant_id=tenant_id,
    )
    return {
        "answer": res.response,
        "citations": [
            {"event_id": str(c.event_id), "span": c.span, "confidence": c.confidence}
            for c in res.citations
        ],
        "refused": res.refused,
        "refusal_reason": res.refusal_reason,
        "confidence": res.confidence,
    }


# ─────────────────────────────────────────────────── strategy ──

async def _safe(fn, *args) -> None:
    try:
        await fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"[v1] background {getattr(fn, '__name__', fn)} failed: {e}")


@router.post("/strategy/prescribe", status_code=202)
async def v1_prescribe(tenant_id: TenantDep) -> dict[str, Any]:
    """Compose a fresh weekly Prescription (fire-and-forget). Read it back from
    /v1/strategy/prescription once composed."""
    from .strategy import compose_prescription

    _spawn(_safe(compose_prescription, tenant_id))
    return {"started": True}


@router.get("/strategy/prescription")
async def v1_prescription(tenant_id: TenantDep) -> dict[str, Any]:
    """The latest weekly Prescription (evidence-backed plan + growth actions)."""
    from .strategy import latest_prescription

    return {"prescription": await latest_prescription(tenant_id)}


# ─────────────────────────────────────────────────────── queue ──

@router.get("/queue")
async def v1_queue(tenant_id: TenantDep, limit: int = 50) -> dict[str, Any]:
    """The approval queue: pending posts/scripts + SUCCEEDED, un-reviewed videos.
    Nothing here has shipped — approve/reject via the endpoints below."""
    lim = max(1, min(200, limit))
    async with acquire(tenant_id) as conn:
        posts = await conn.fetch(
            "SELECT id, status, payload->>'platform' AS platform, "
            "payload->>'format' AS format, payload->>'caption' AS caption, "
            "payload->>'image_url' AS image_url, created_at FROM actions "
            "WHERE action_type='content' AND status='pending' "
            "ORDER BY created_at DESC LIMIT $1", lim)
        # Only SUCCEEDED renders are approvable; queued/rendering/failed are not.
        vids = await conn.fetch(
            "SELECT id, status, review_status, title, platform, final_url, "
            "created_at FROM video_productions "
            "WHERE review_status IS NULL AND status='succeeded' "
            "ORDER BY created_at DESC LIMIT $1", lim)
    return {
        "posts": [{
            "id": str(r["id"]), "status": r["status"], "platform": r["platform"],
            "format": r["format"], "caption": r["caption"], "image_url": r["image_url"],
            "created_at": r["created_at"].isoformat(),
        } for r in posts],
        "videos": [{
            "id": str(r["id"]), "render_status": r["status"],
            "review_status": r["review_status"], "title": r["title"],
            "platform": r["platform"], "url": r["final_url"],
            "created_at": r["created_at"].isoformat(),
        } for r in vids],
    }


@router.get("/queue/rejected")
async def v1_queue_rejected(tenant_id: TenantDep, limit: int = 50) -> dict[str, Any]:
    """Posts that were turned down — what was rejected, why, and what replaced it.

    v1_queue only ever returns status='pending', so a rejected post disappeared
    the moment it was rejected: the row stayed in `actions` with the owner's
    words in rejection_reason_code, and nothing in either product read them
    back. This is that missing read side.

    Each entry carries its `replacements` — regenerated posts whose payload
    names it in `regen_of` — so a redo appears under the original it fixes
    rather than as an unrelated new card in the main queue."""
    lim = max(1, min(200, limit))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, payload->>'platform' AS platform, "
            "payload->>'format' AS format, payload->>'caption' AS caption, "
            "payload->>'image_url' AS image_url, "
            "rejection_reason_code AS reason, created_at, decided_at "
            "FROM actions WHERE action_type='content' AND status='rejected' "
            "ORDER BY decided_at DESC NULLS LAST, created_at DESC LIMIT $1", lim)
        ids = [str(r["id"]) for r in rows]
        # Regenerations pointing back at any of them. Deliberately NOT limited to
        # pending: a redo that was itself approved or rejected still belongs in
        # this history, or the trail goes cold exactly where it matters most.
        regens = await conn.fetch(
            "SELECT id, status, payload->>'platform' AS platform, "
            "payload->>'caption' AS caption, payload->>'image_url' AS image_url, "
            "payload->>'regen_of' AS regen_of, payload->>'version' AS version, "
            "payload->>'regen_feedback' AS regen_feedback, created_at "
            "FROM actions WHERE action_type='content' "
            "  AND payload->>'regen_of' = ANY($1::text[]) "
            "ORDER BY created_at ASC", ids,
        ) if ids else []

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for r in regens:
        ver = str(r["version"] or "")
        by_parent.setdefault(str(r["regen_of"]), []).append({
            "id": str(r["id"]), "status": r["status"], "platform": r["platform"],
            "caption": r["caption"], "image_url": r["image_url"],
            "version": int(ver) if ver.isdigit() else 2,
            "regen_feedback": r["regen_feedback"],
            "created_at": r["created_at"].isoformat(),
        })

    return {
        "posts": [{
            "id": str(r["id"]), "status": "rejected", "platform": r["platform"],
            "format": r["format"], "caption": r["caption"],
            "image_url": r["image_url"], "reason": r["reason"],
            "created_at": r["created_at"].isoformat(),
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "replacements": by_parent.get(str(r["id"]), []),
        } for r in rows],
    }


@router.post("/queue/post/{action_id}/approve")
async def v1_post_approve(
    action_id: UUID, body: Decision, tenant_id: TenantDep,
) -> dict[str, Any]:
    res = await _do_approve_post(action_id, tenant_id, override=body.override, reason=body.reason)
    if res["outcome"] == "qa_flagged":
        raise HTTPException(409, "qa_flagged: this draft failed voice-QA — approve "
                                 "again with override=true to publish it anyway.")
    if res["outcome"] != "approved":
        raise HTTPException(404, "post not found or not pending")
    return {"ok": True, "id": res["id"], "status": "approved",
            "reinforced": res.get("reinforced", False)}


@router.post("/queue/post/{action_id}/reject")
async def v1_post_reject(
    action_id: UUID, body: Decision, tenant_id: TenantDep,
) -> dict[str, Any]:
    res = await _do_reject_post(action_id, tenant_id, reason=body.reason)
    if res["outcome"] != "rejected":
        raise HTTPException(404, "post not found or not pending")
    return {"ok": True, "id": res["id"], "status": "rejected",
            "learned": res.get("learned", False)}


@router.post("/queue/video/{production_id}/approve")
async def v1_video_approve(
    production_id: UUID, body: Decision, tenant_id: TenantDep,
) -> dict[str, Any]:
    res = await _do_approve_video(production_id, tenant_id, reason=body.reason)
    if res["outcome"] == "not_ready":
        raise HTTPException(409, f"production not ready to approve ({res['detail']})")
    if res["outcome"] != "approved":
        raise HTTPException(404, "production not found")
    return {"ok": True, "id": res["id"], "status": res.get("status", "approved")}


@router.post("/queue/video/{production_id}/reject")
async def v1_video_reject(
    production_id: UUID, body: Decision, tenant_id: TenantDep,
) -> dict[str, Any]:
    res = await _do_reject_video(production_id, tenant_id, reason=body.reason)
    if res["outcome"] != "rejected":
        raise HTTPException(404, "production not found")
    return {"ok": True, "id": res["id"], "status": "rejected"}


# ── shared approve/reject core (used by the single-item AND bulk endpoints) ──
# These never raise for business conditions — they return an outcome dict so the
# bulk path can collect per-item results instead of failing the whole batch.

async def _do_approve_post(action_id: UUID, tenant_id: UUID, *,
                           override: bool, reason: str) -> dict[str, Any]:
    async with acquire(tenant_id) as conn:
        gate = await conn.fetchrow(
            "SELECT payload FROM actions WHERE id=$1 AND status='pending'", action_id)
        if gate is None:
            return {"id": str(action_id), "outcome": "skipped", "detail": "not found or not pending"}
        if not override:
            gp = gate["payload"]
            if isinstance(gp, str):
                gp = json.loads(gp)
            gp = gp or {}
            if gp.get("flagged") is True or gp.get("qa_passed") is False:
                return {"id": str(action_id), "outcome": "qa_flagged",
                        "detail": "failed voice-QA; pass override=true to approve anyway"}
        r = (f"[QA-OVERRIDE] {reason or 'approved via /v1'}"
             if override else (reason or "approved via /v1"))
        tag = await conn.execute(
            "UPDATE actions SET status='approved', approval_reason=$2, "
            "decided_at=now() WHERE id=$1 AND status='pending'", action_id, r)
    if not tag.endswith(" 1"):
        return {"id": str(action_id), "outcome": "skipped", "detail": "not pending"}
    reinforced = False
    try:
        from .learning import record_approval
        reinforced = bool(await record_approval(action_id, tenant_id))
    except Exception as e:  # noqa: BLE001
        print(f"[v1] record_approval failed: {e}")
    return {"id": str(action_id), "outcome": "approved", "reinforced": reinforced}


async def _do_reject_post(action_id: UUID, tenant_id: UUID, *, reason: str) -> dict[str, Any]:
    r = reason or "rejected via /v1"
    async with acquire(tenant_id) as conn:
        tag = await conn.execute(
            "UPDATE actions SET status='rejected', rejection_reason_code=$2, "
            "decided_at=now() WHERE id=$1 AND status='pending'", action_id, r)
    if not tag.endswith(" 1"):
        return {"id": str(action_id), "outcome": "skipped", "detail": "not found or not pending"}
    learned = False
    try:
        from .learning import record_rejection
        learned = bool(await record_rejection(action_id, r, tenant_id))
        from .video_feedback import record_video_feedback
        async with acquire(tenant_id) as conn:
            prod_id = await conn.fetchval(
                "SELECT id FROM video_productions WHERE queued_action_id=$1", action_id)
        if prod_id:
            await record_video_feedback(prod_id, r, status="rejected", tenant_id=tenant_id)
        from .feedback_interpreter import kick_interpret_background
        kick_interpret_background(tenant_id)
    except Exception as e:  # noqa: BLE001
        print(f"[v1] reject learning failed: {e}")
    return {"id": str(action_id), "outcome": "rejected", "learned": learned}


async def _do_approve_video(production_id: UUID, tenant_id: UUID, *, reason: str) -> dict[str, Any]:
    from .video_feedback import set_production_review
    async with acquire(tenant_id) as conn:
        render_status = await conn.fetchval(
            "SELECT status FROM video_productions WHERE id=$1", production_id)
    if render_status is None:
        return {"id": str(production_id), "outcome": "skipped", "detail": "production not found"}
    if render_status != "succeeded":
        return {"id": str(production_id), "outcome": "not_ready",
                "detail": f"render status={render_status}"}
    note = (reason or "").strip()
    status = "approved_with_notes" if note else "approved"
    ok = await set_production_review(production_id, status, note, tenant_id=tenant_id)
    if not ok:
        return {"id": str(production_id), "outcome": "skipped", "detail": "production not found"}
    if note:
        try:
            from .video_feedback import record_video_feedback
            await record_video_feedback(
                production_id, note, status="approved_with_notes", tenant_id=tenant_id)
        except Exception as e:  # noqa: BLE001
            print(f"[v1] record_video_feedback (approve) failed: {e}")
    return {"id": str(production_id), "outcome": "approved", "status": status}


async def _do_reject_video(production_id: UUID, tenant_id: UUID, *, reason: str) -> dict[str, Any]:
    from .video_feedback import record_video_feedback, set_production_review
    r = (reason or "").strip()
    ok = await set_production_review(production_id, "rejected", r, tenant_id=tenant_id)
    if not ok:
        return {"id": str(production_id), "outcome": "skipped", "detail": "production not found"}
    try:
        await record_video_feedback(production_id, r, status="rejected", tenant_id=tenant_id)
        from .feedback_interpreter import kick_interpret_background
        kick_interpret_background(tenant_id)
    except Exception as e:  # noqa: BLE001
        print(f"[v1] video reject learning failed: {e}")
    return {"id": str(production_id), "outcome": "rejected"}


# ── bulk approve / reject ──

class BulkDecision(BaseModel):
    posts: list[UUID] = []          # action ids
    videos: list[UUID] = []         # production ids
    all: bool = False               # act on EVERYTHING currently pending/reviewable
    override: bool = False          # approve past the voice-QA gate (posts)
    reason: str = ""


_BULK_CAP = 500   # safety ceiling per bulk call


async def _bulk_ids(tenant_id: UUID, body: BulkDecision, *,
                    only_succeeded_videos: bool) -> tuple[list, list]:
    post_ids = list(body.posts)
    video_ids = list(body.videos)
    if body.all:
        vq = ("SELECT id FROM video_productions WHERE review_status IS NULL "
              + ("AND status='succeeded' " if only_succeeded_videos else "")
              + "ORDER BY created_at DESC LIMIT $1")
        async with acquire(tenant_id) as conn:
            prows = await conn.fetch(
                "SELECT id FROM actions WHERE action_type='content' AND status='pending' "
                "ORDER BY created_at DESC LIMIT $1", _BULK_CAP)
            vrows = await conn.fetch(vq, _BULK_CAP)
        post_ids += [r["id"] for r in prows]
        video_ids += [r["id"] for r in vrows]
    return post_ids[:_BULK_CAP], video_ids[:_BULK_CAP]


@router.post("/queue/approve")
async def v1_bulk_approve(body: BulkDecision, tenant_id: TenantDep) -> dict[str, Any]:
    """Approve many items at once: pass posts[]/videos[] ids, or all=true to approve
    every pending post + succeeded video. QA-flagged posts need override=true.
    Runs inline (approval is fast — no rendering), capped at 500 items/call."""
    post_ids, video_ids = await _bulk_ids(tenant_id, body, only_succeeded_videos=True)
    results = [await _do_approve_post(a, tenant_id, override=body.override, reason=body.reason)
               for a in post_ids]
    results += [await _do_approve_video(v, tenant_id, reason=body.reason) for v in video_ids]
    approved = sum(1 for r in results if r["outcome"] == "approved")
    return {"approved": approved, "requested": len(results),
            "skipped": [r for r in results if r["outcome"] != "approved"]}


@router.post("/queue/reject")
async def v1_bulk_reject(body: BulkDecision, tenant_id: TenantDep) -> dict[str, Any]:
    """Reject many items at once: posts[]/videos[] ids, or all=true (any un-reviewed)."""
    post_ids, video_ids = await _bulk_ids(tenant_id, body, only_succeeded_videos=False)
    results = [await _do_reject_post(a, tenant_id, reason=body.reason) for a in post_ids]
    results += [await _do_reject_video(v, tenant_id, reason=body.reason) for v in video_ids]
    rejected = sum(1 for r in results if r["outcome"] == "rejected")
    return {"rejected": rejected, "requested": len(results),
            "skipped": [r for r in results if r["outcome"] != "rejected"]}
