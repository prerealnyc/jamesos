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
import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import caption_backfill, caption_burn
from .config import settings
from .db import acquire

# This module had no logger: the background regenerate job and the caption-edit
# learning leg both swallow exceptions, and without one a failure there would be
# silent (or, worse, raise NameError inside the handler that was meant to
# recover from it).
_log = logging.getLogger(__name__)

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


def _new_job(job_type: str, tenant_id: Any) -> dict[str, Any]:
    """A job in the shape `GET /v1/jobs/{id}` reads back.

    That poller dereferences "tenant_id" (to refuse another tenant's job) and
    "type" directly, so a job missing either is a 500 on every poll rather than
    a job that merely looks odd. Build them here so the shape stays one thing.
    """
    return {"id": uuid.uuid4().hex, "type": job_type, "tenant_id": str(tenant_id),
            "status": "running", "result": None, "error": None,
            "created_at": _now(), "updated_at": _now()}


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
    image_kind: Literal["james", "designed", "scene"] = "james"   # post only; scene = a fresh gpt-image-1 draw from the brief
    # post/designed only: pin the layout for an explicit build (e.g. 'carousel').
    # An explicit force is honored even when the design switch is off.
    force_format: str = ""
    # post/designed only, with force_format="learned": the built-in format BM2's
    # rotation would have chosen for this order, drawn when the learned layout
    # misses (thin library, design QA, error) and the brand allows it — instead
    # of an art-director free pick. Empty = today's behaviour.
    fallback_format: str = ""
    # post/designed only, with force_format="learned": the learned layout the
    # brand's last two image posts were both drawn from. A learned render that
    # comes back as that layout again is redrawn once. Empty = today's behaviour.
    avoid_template_id: str = ""
    # post/designed only: render the image at THIS size instead of the 4:5
    # default — the destination platform's best shape (1600x900 for X,
    # 1080x1920 for a Reel, 1000x1500 for a Pin). Both must be > 0 to take
    # effect; anything else keeps the default, so an old caller is unaffected.
    image_width: int = 0
    image_height: int = 0
    # post/designed only: ALSO render the same design at each of these sizes, so
    # one post can go out on every network at that network's shape. The primary
    # image is still image_width x image_height; these are the extras, returned
    # as image_urls_by_size on the queued post. Each is [width, height].
    sizes: list[list[int]] = Field(default_factory=list)
    # The owner's standing rejection notes — steers the draft + designed image away
    # from what's been rejected (off-brand style/claims). Applies to posts,
    # reel scripts and rendered videos alike.
    feedback: str = ""
    video_template: str = ""                              # video only
    # video only: force a real avatar render even if the brand's avatar_videos
    # default is off (costs render credits; needs HeyGen configured). Default
    # false → a reel SCRIPT draft, matching the safe product default.
    render: bool = False
    callback_url: str | None = None
    # post/designed/learned only: draw THIS catalogue layout (a house_layouts id)
    # instead of letting design_templates.pick() choose — BM2's showcase of the
    # newest house layouts. Forked into the brand like any pick; a row that is
    # missing, unapproved or undrawable falls back to the nine, as any failed
    # learned render does. Empty = today's behaviour.
    house_layout_id: str = ""

    @field_validator("house_layout_id")
    @classmethod
    def _house_layout_id_is_a_uuid(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        try:
            return str(UUID(v))
        except ValueError as exc:
            raise ValueError("house_layout_id must be empty or a UUID") from exc


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


@router.get("/house-knowledge/status")
async def v1_house_knowledge_status(tenant_id: TenantDep) -> dict[str, Any]:
    """How much shared marketing canon is loaded (chunks by layer). The canon is
    global — the same for every brand — so tenant_id is only the auth gate."""
    from .house_knowledge import status
    return await status()


@router.post("/house-knowledge/ingest")
async def v1_house_knowledge_ingest(tenant_id: TenantDep, force: bool = False
                                    ) -> dict[str, Any]:
    """Load (or force re-load) the shared marketing canon. Idempotent; startup
    also runs this, so it's here for manual (re)ingest without a redeploy."""
    from .house_knowledge import ingest_corpus
    return await ingest_corpus(force=force)


class MediaRehost(BaseModel):
    url: str = Field(..., min_length=1)
    label: str = "clip"


# The stored NAME carries the extension, and the extension is what every later
# reader uses to decide what the file IS. It was hardcoded ".mp4" — right for the
# clip library this endpoint was built for, wrong the moment anything else used
# it. A brand's own post STILL now comes through here, so the single durable copy
# of the picture was announcing itself as a video to everything downstream.
_REHOST_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
}


def _rehost_ext(content_type: str, url: str) -> str:
    """What this actually is, best evidence first: the server's own
    Content-Type, then the extension on the url, then the historical .mp4
    assumption so the clip library behaves exactly as it always has."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _REHOST_EXT:
        return _REHOST_EXT[ct]
    tail = urlparse(url).path.rsplit(".", 1)
    if len(tail) == 2 and tail[1].isalnum() and 1 <= len(tail[1]) <= 5:
        return f".{tail[1].lower()}"
    return ".mp4"


@router.post("/media/rehost")
async def v1_media_rehost(body: MediaRehost, tenant_id: TenantDep) -> dict[str, Any]:
    """Download a third-party, time-limited media URL (e.g. an OpusClip signed mp4
    that expires ~30 days) and re-host it to OUR durable storage for this tenant,
    returning the permanent public URL. Lets BM2.0 stop its clip library from
    rotting. No-op (returns the URL) when it's already on our public storage."""
    url = (body.url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(400, "url must be http(s)")
    if "supabase.co/storage/v1/object/public" in url:
        return {"url": url, "durable": True, "rehosted": False}
    from .media import storage as media_storage
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as c:
            r = await c.get(url, follow_redirects=True)
            r.raise_for_status()
            data = r.content
            content_type = str(r.headers.get("content-type", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not fetch source media ({type(exc).__name__})") from exc
    if not data:
        raise HTTPException(502, "source media was empty")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (body.label or "clip"))[:60] or "clip"
    try:
        durable, _ = await asyncio.to_thread(
            media_storage().save, str(tenant_id), data, f"{safe}{_rehost_ext(content_type, url)}"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"durable storage save failed ({type(exc).__name__})") from exc
    return {"url": durable, "durable": True, "rehosted": True, "bytes": len(data)}


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
                    idea, req.platform, tenant_id, video_template=req.video_template,
                    feedback=req.feedback)
                job["result"] = {
                    "kind": "video",
                    "production_id": made.get("production_id"),
                    "status": made.get("status"),
                }
            else:
                made = await _make_reel_script(idea, req.platform, tenant_id, feedback=req.feedback)
                job["result"] = {
                    "kind": "reel_script",
                    "action_id": made.get("action_id"),
                    "status": made.get("status"),
                    "note": "avatar videos are off for this brand — produced a reel "
                            "script draft instead (enable avatar_videos to render).",
                }
        else:
            made = await _make_text_post(
                idea, req.platform, tenant_id, image_kind=req.image_kind,
                force_format=req.force_format, feedback=req.feedback,
                house_layout_id=req.house_layout_id,
                # Sent only when set, so a caller that never heard of it is untouched.
                **({"fallback_format": req.fallback_format} if req.fallback_format else {}),
                **({"avoid_template_id": req.avoid_template_id} if req.avoid_template_id else {}),
                canvas=((req.image_width, req.image_height)
                        if req.image_width > 0 and req.image_height > 0 else None),
                extra_sizes=tuple(
                    (int(w), int(h)) for w, h in (
                        pair for pair in req.sizes if isinstance(pair, list) and len(pair) == 2
                    ) if int(w) > 0 and int(h) > 0
                    and (int(w), int(h)) != (req.image_width, req.image_height)
                ))
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
            "payload->>'image_url' AS image_url, payload->'media_urls' AS media_urls, "
            "payload->>'image_format' AS image_format, "
            # The hero photo this post was composed on. Surfaced so a caller can
            # PROVE a photo swap actually changed the picture (a fresh render
            # always yields a new image_url, so image_url alone can't tell a real
            # swap from a same-photo re-render) and can detect a one-photo no-op.
            "payload->>'hero_photo_key' AS hero_photo_key, "
            # The same design at each other network's shape (see
            # _generate_designed_post_image). Without it the caller gets one
            # image and has to crop it for every other platform.
            "payload->'image_urls_by_size' AS image_urls_by_size, "
            # Which learned layout the post was drawn from, and the post it was
            # learned from — so an approval can teach the library and the
            # approval card can say where the layout came from.
            "payload->>'design_template_id' AS design_template_id, "
            "payload->'design_template_source' AS design_template_source, "
            # True when the caller pinned a catalogue layout (house_layout_id on
            # /v1/generate) — BM2 tells its showcase pieces apart by it.
            "coalesce(payload->'house_layout_pinned' = 'true'::jsonb, false) "
            "AS house_layout_pinned, "
            # A regenerated post already records the original it replaces (see
            # the regenerate endpoint), but the queue never returned it — so a
            # redo arrived in the approval board looking like an unrelated new
            # draft, and the owner had no way to tell which card was the answer
            # to the feedback they had just given.
            "payload->>'regen_of' AS regen_of, payload->>'version' AS version, "
            "payload->>'regen_feedback' AS regen_feedback, "
            "created_at FROM actions "
            # EXPLICIT tenant filter, not RLS alone: if the connecting role isn't
            # FORCE-bound by RLS (or a read runs under a default tenant), an unscoped
            # SELECT leaks every brand's pending posts into this queue — the
            # Turtleback/Spaceport-in-James's-queue cross-brand leak.
            "WHERE action_type='content' AND status='pending' AND tenant_id = $2 "
            "ORDER BY created_at DESC LIMIT $1", lim, tenant_id)
        # Only SUCCEEDED renders are approvable; queued/rendering/failed are not.
        vids = await conn.fetch(
            "SELECT id, status, review_status, title, platform, final_url, "
            "created_at FROM video_productions "
            # Same explicit tenant scoping — an unscoped read put foreign brands'
            # finished videos into this brand's approval queue.
            "WHERE review_status IS NULL AND status='succeeded' AND tenant_id = $2 "
            "ORDER BY created_at DESC LIMIT $1", lim, tenant_id)
    def _arr(v):
        # payload->'media_urls' comes back as jsonb text under asyncpg — parse it
        # so a carousel surfaces its full ordered slide list to the adopter.
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return None
        return v

    return {
        "posts": [{
            "id": str(r["id"]), "status": r["status"], "platform": r["platform"],
            "format": r["format"], "caption": r["caption"], "image_url": r["image_url"],
            "media_urls": _arr(r["media_urls"]) or None,
            "image_format": r["image_format"],
            # The composed-on hero photo key (None for text-only cards) — lets a
            # caller confirm a swap replaced the picture, not just re-rendered it.
            "hero_photo_key": r["hero_photo_key"],
            # {"1600x900": url, "1080x1920": url, ...} — the same post at every
            # extra size that was asked for. Absent when none were.
            "image_urls_by_size": _arr(r["image_urls_by_size"]) or None,
            "design_template_id": r["design_template_id"] or None,
            "design_template_source": _arr(r["design_template_source"]) or None,
            "house_layout_pinned": bool(r["house_layout_pinned"]),
            # Present only on a redo: which post it replaces, which version it is,
            # and the feedback it was rebuilt from.
            "regen_of": r["regen_of"], "version": r["version"],
            "regen_feedback": r["regen_feedback"],
            "created_at": r["created_at"].isoformat(),
        } for r in posts],
        "videos": [{
            "id": str(r["id"]), "render_status": r["status"],
            "review_status": r["review_status"], "title": r["title"],
            "platform": r["platform"], "url": r["final_url"],
            "created_at": r["created_at"].isoformat(),
        } for r in vids],
    }


# ───────────────────────────────────────────────── social listening ──

class SocialSearchBody(BaseModel):
    query: str
    platforms: list[str] | None = None   # subset of twitter/instagram/tiktok/reddit
    limit: int = Field(default=12, ge=1, le=50)
    days: int = Field(default=14, ge=1, le=90)
    min_likes: int = Field(default=200, ge=0, le=10_000_000)


@router.get("/social/account")
async def v1_social_account(tenant_id: TenantDep) -> dict[str, Any]:
    """Xpoz account status (plan + remaining credits), or {configured:false} when
    XPOZ_API_KEY is unset. The tenant only authorizes the caller — Xpoz data is
    global, not tenant-scoped."""
    from . import xpoz_intel
    return await xpoz_intel.account_info()


@router.post("/social/search")
async def v1_social_search(body: SocialSearchBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Cross-platform niche listening (X / Instagram / TikTok / Reddit) via Xpoz:
    recent, engagement-ranked posts a brand could comment on, each with a real
    openable URL. Powers 2.0's 'Engage today' social lane. Returns {error:…}
    (never raises) when XPOZ_API_KEY is unset, so the caller degrades cleanly."""
    from datetime import date, timedelta

    from . import xpoz_intel
    start = (date.today() - timedelta(days=max(1, body.days))).isoformat()
    return await xpoz_intel.search_social(
        body.query, platforms=body.platforms, limit=body.limit,
        start_date=start, min_likes=body.min_likes,
    )


@router.get("/queue/find-by-image")
async def v1_queue_find_by_image(url: str, tenant_id: TenantDep) -> dict[str, Any]:
    """The content draft whose picture is this URL.

    How BM2 finds the draft behind one of its own pieces made before it recorded
    the draft's id — the card editor opens a piece through its draft. /v1/queue
    returns only the newest 200, and a brand with more drafts than that (Turtleback
    had 200+) left its older pieces unreachable: 5 of its 17 pictures awaiting
    approval could not be opened in the editor."""
    u = (url or "").strip()
    if not u:
        raise HTTPException(422, "url is required")
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM actions WHERE action_type='content' "
            "AND (payload->>'image_url' = $1 OR payload->>'media_url' = $1) "
            "AND tenant_id = $2 "
            "ORDER BY created_at DESC LIMIT 1", u, tenant_id)
    if row is None:
        raise HTTPException(404, "no draft has that picture")
    return {"id": str(row["id"]), "status": row["status"]}


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
            "AND tenant_id = $2 "
            "ORDER BY decided_at DESC NULLS LAST, created_at DESC LIMIT $1", lim, tenant_id)
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
        # What the feedback interpreter did with each rejection reason: applied a
        # live knob, or logged a note it could not auto-apply. A text post's
        # change carries the rejected action's id in source_event_id, so this is
        # the read side that lets the card stop silently returning a near-
        # identical redo — it can say "adjusted automatically" or "logged".
        outcomes = await conn.fetch(
            "SELECT DISTINCT ON (source_event_id) source_event_id::text AS src, "
            "status, kind, plain_english, config_key "
            "FROM feedback_changes "
            "WHERE source_event_id::text = ANY($1::text[]) AND status <> 'dismissed' "
            "ORDER BY source_event_id, created_at DESC", ids,
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

    # applied → a live knob moved (their next redo reflects it); queued/done →
    # a note we could not auto-apply (source blur, a new layout). config_key
    # lets the card name what moved without leaking the raw knob key.
    outcome_by_src: dict[str, dict[str, Any]] = {
        o["src"]: {
            "state": o["status"],
            "kind": o["kind"],
            "summary": o["plain_english"],
            "config_key": o["config_key"],
        } for o in outcomes
    }

    return {
        "posts": [{
            "id": str(r["id"]), "status": "rejected", "platform": r["platform"],
            "format": r["format"], "caption": r["caption"],
            "image_url": r["image_url"], "reason": r["reason"],
            "created_at": r["created_at"].isoformat(),
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "replacements": by_parent.get(str(r["id"]), []),
            "feedback_outcome": outcome_by_src.get(str(r["id"])),
        } for r in rows],
    }


class RegenerateBody(BaseModel):
    feedback: str = ""
    force_format: str = ""
    # The destination's shape and every other platform shape, like /v1/generate
    # takes — so a rebuilt post is re-laid-out for every network too, instead of
    # the others keeping the image the owner just changed.
    image_width: int = 0
    image_height: int = 0
    sizes: list = Field(default_factory=list)
    # A generated scene to put in the photo slot of a cloned/learned design.
    scene_prompt: str = ""
    # The owner is editing by hand (not the automatic reject-and-rebuild loop).
    by_owner: bool = False
    # The owner asked to change how the PHOTO looks ("brighter", "make the sky
    # orange") — edit the picture pixels in place and keep the same layout + text,
    # instead of re-authoring the card. When unset, the feedback text is classified
    # (see _wants_photo_edit) so any caller still gets the in-place behaviour.
    edit_photo: bool = False


# Colours a "make the text <colour>" instruction can request. White/black lead so
# "make it white, not black" resolves to white. The render engine resolves each to
# a readable tone (image_compose._NAMED_INK); an unknown one falls back to auto.
_COLOR_WORDS = ("white", "black", "red", "crimson", "maroon", "orange", "amber",
                "gold", "yellow", "green", "emerald", "teal", "blue", "navy", "sky",
                "purple", "violet", "pink", "grey", "gray", "silver", "brown",
                "cream", "charcoal")


def _styling_override(feedback: str) -> dict:
    """A rejection about on-image TEXT STYLE (colour / weight) → the render tuning
    that fixes it, so the redo APPLIES it (ANY colour, or a #hex) instead of only
    logging it. Empty when the feedback isn't a text-styling complaint."""
    import re

    f = (feedback or "").lower()
    out: dict = {}
    color = None
    m = re.search(r"#([0-9a-f]{6}|[0-9a-f]{3})\b", f)
    if m:
        color = "#" + m.group(1)
    else:
        # First colour word that ISN'T negated — "not black" / "no black" REJECTS
        # black, it doesn't request it (the common "make it white, not black").
        for w in _COLOR_WORDS:
            idx = f.find(w)
            while idx != -1:
                pre = f[max(0, idx - 6):idx]
                if "not " not in pre and "no " not in pre and "n't " not in pre:
                    color = w
                    break
                idx = f.find(w, idx + 1)
            if color:
                break
    if color:
        out["image_text_color_hex"] = color
    if any(w in f for w in ("thicker", "bolder", "heavier", "thick font", "thicker font",
                            "bold font", "bolder font", "heavier font", "make it bold")):
        out["image_text_weight"] = 1
    return out


# Layout families that carry no photograph (designed_render.PHOTO_FORMATS is the
# complement). Kept here as names rather than imported so this stays a pure
# string decision — "there is no image on it" cannot be answered by one of these.
_TEXT_ONLY_FORMATS = ("brand_quote", "bold_statement", "big_stat")

# One original plus two rebuilds. Rejecting now rebuilds on its own (BM2.0 fires
# this endpoint from its reject route), so an uncapped chain is a spend loop with
# no human in it: reject -> render -> reject -> render, forever, at real cost per
# image. Past the cap the rejection still records and still teaches; only the
# automatic rebuild stops, because at that point the brief is what needs to
# change and that is the owner's call.
MAX_REGEN_VERSION = 3
# ...and an owner editing a post by hand ("brighter", "different photo") has a
# human in every pass, which is what the cap above exists to guarantee. Held to
# the automatic cap, their third edit was refused and fell back to a fresh render
# — a different design, on exactly the posts where the design was the point.
MAX_OWNER_EDIT_VERSION = 13


def _regen_cap(by_owner: bool) -> int:
    return MAX_OWNER_EDIT_VERSION if by_owner else MAX_REGEN_VERSION

# The owner asking for the layout to be LEFT ALONE. This is the strongest signal
# a redo can receive and it used to be the weakest: none of these matched any
# rule, so "keep rest template same" fell into the vague bucket — the one branch
# that hands the art director a blank slate and lets it choose a different
# template entirely. The instruction produced the exact opposite of itself.
_KEEP_LAYOUT = (
    "keep the template", "keep template", "same template", "template same",
    "keep the layout", "keep layout", "same layout", "layout same",
    "keep the design", "keep design", "same design", "design same",
    "keep the style", "same style", "style same",
    "keep the rest", "keep rest", "keep everything else", "keep the format",
    "same format", "format same", "keep it the same", "keep the same",
)
# The owner asking for a DIFFERENT look. Only these earn a fresh composition.
_CHANGE_LAYOUT = (
    "different layout", "different template", "different design", "different format",
    "change the layout", "change the template", "change the design", "change the format",
    "new layout", "new template", "new design", "another layout", "another template",
    "design not good", "design is not good", "bad design", "design is bad",
    "hate the design", "hate this design", "looks bad", "doesn't look good",
    "does not look good", "ugly", "boring", "redesign", "re-design",
)
# "There is no picture on this" — a complaint that the post is text-only. It can
# only be answered by a layout that HAS a photo slot, which is why it steers the
# format as well as the photo.
_WANTS_PHOTO_PRESENT = (
    "no image on it", "no photo on it", "no picture on it", "no image", "no photo",
    "no picture", "without an image", "without a photo", "missing image",
    "missing photo", "add an image", "add a photo", "add a picture",
    "needs an image", "needs a photo", "put an image", "put a photo",
    "where is the image", "where is the photo",
)
# The owner asking for the SAME picture back.
_KEEP_PHOTO = (
    "same image", "same photo", "same picture", "image same", "photo same",
    "picture same", "keep the image", "keep the photo", "keep the picture",
    "keep image", "keep photo",
)

# Tone/light/colour/mood/scene changes to the PHOTO itself — the ChatGPT/Gemini
# "here is the image, apply this change" edits. These edit the picture pixels in
# place (imagegen.edit_hero_photo) and keep the SAME layout + on-card text.
_PHOTO_EDIT = (
    "brighter", "brighten", "darker", "darken", "lighter", "less bright",
    "warmer", "cooler", "warm light", "cooler light", "warmer light", "golden hour",
    "more dramatic", "dramatic", "moodier", "moody", "cinematic",
    "more contrast", "contrast", "more vibrant", "vibrant", "saturated",
    "desaturate", "desaturated", "muted", "faded", "vintage", "sepia",
    "black and white", "b&w", "grayscale", "greyscale", "monochrome",
    "sharper", "softer", "soften", "blur the background", "blurred background",
    "sunset", "sunrise", "orange sky", "make the sky", "change the sky", "relight",
    "re-light", "washed out", "punchier",
)


def _wants_photo_edit(feedback: str) -> bool:
    """True for a change to how the PHOTO looks (light/colour/tone/mood/scene) that
    should be applied to the picture pixels IN PLACE — "brighter", "warmer light",
    "make the sky orange", "more dramatic" — as opposed to swapping the photo,
    changing the layout, or editing the on-card TEXT. Deliberately conservative: any
    mention of text/copy/font is a text edit, and a swap phrase is a new photo, so
    both defer to their own paths."""
    f = (feedback or "").lower()
    if not f:
        return False
    if any(t in f for t in (
        "text", "caption", "headline", "title", "font", "word", "copy", "wording",
        "less text", "more text", "smaller", "bigger",
    )):
        return False
    if _wants_new_photo(feedback):
        return False
    return any(p in f for p in _PHOTO_EDIT)


# The layout names imagegen._FORMAT_MAP can actually resolve. Anything else in
# payload["image_format"] — "cloned" above all — is a record of HOW an image was
# made, not a layout the renderer can be asked for.
_PINNABLE_FORMATS = frozenset({
    "brand_quote", "hero_quote", "statement", "bold_statement", "full_bleed",
    "editorial_split", "big_stat", "minimal_over", "framed_print",
    "carousel", "text_carousel",
})


def _is_pinnable_format(fmt: str) -> bool:
    """Can this value be handed to the renderer as force_format and honoured?"""
    return (fmt or "").strip().lower() in _PINNABLE_FORMATS


def _layout_intent(feedback: str) -> str:
    """'new' | 'same' | '' — what the owner asked for the LAYOUT.

    A redo is a FIX, not a replacement: unless they said the look itself is
    wrong, the template that was on screen is the one they were correcting. An
    explicit complaint wins over an explicit keep, because "keep the rest the
    same, but the design is bad" is still asking for a different design."""
    f = (feedback or "").lower()
    if any(p in f for p in _CHANGE_LAYOUT):
        return "new"
    if any(p in f for p in _KEEP_LAYOUT):
        return "same"
    return ""


def _wants_photo_present(feedback: str) -> bool:
    """Did they say the post has no picture on it?"""
    f = (feedback or "").lower()
    return any(p in f for p in _WANTS_PHOTO_PRESENT)


def _keeps_photo(feedback: str) -> bool:
    """Did they explicitly ask for the SAME picture back?"""
    f = (feedback or "").lower()
    return any(p in f for p in _KEEP_PHOTO)


def _wants_new_photo(feedback: str) -> bool:
    """True only when the owner asked for a DIFFERENT photo/image, or complained
    about the photo's quality. Otherwise a redo keeps the SAME picture and changes
    only what was asked — the default, so a redo is the same image restyled rather
    than a brand-new composition."""
    f = (feedback or "").lower()
    # explicit "give me a different picture"
    if any(p in f for p in (
        "different photo", "different image", "different picture", "another photo",
        "another image", "another picture", "new photo", "new image", "new picture",
        "change the photo", "change the image", "change the picture",
        "swap the photo", "swap the image", "replace the photo", "replace the image",
        "try another", "try a different", "not this photo", "not this image",
        "use a different photo", "use a different image", "use another photo",
    )):
        return True
    # a complaint about the PHOTO itself → they want a better one
    return any(p in f for p in (
        "wrong photo", "wrong image", "bad photo", "bad image", "hate this photo",
        "hate the photo", "hate this image", "don't like the photo",
        "don't like this photo", "blurry", "out of focus", "low quality", "grainy",
        "pixelated", "doesn't fit", "does not fit",
    ))


def _rebuilds_in_place(payload: dict, layout: str) -> bool:
    """Is this post redone in ITS OWN design (template_clone.rebuild_design)
    rather than composed afresh? Cloned and learned posts are — and so is a card
    the owner edited by hand: its image is theirs now, and rebuild_design reads
    the layout and the words back off that image, so "brighter" or "a different
    photo" changes the card they made. Composing afresh threw the edit away and
    brought back an unrelated design with rewritten copy — for a learned card,
    one of the nine it had been drawn to replace. Unless they rejected the design
    itself ("new"), which is the one answer preserving it cannot give."""
    fmt = str(payload.get("image_format") or "")
    return bool(payload.get("cloned_from_competitor") or fmt in ("cloned", "learned", "owner_edit")) \
        and layout != "new"


async def _rebuild_cloned_action(
    new_id, payload: dict, feedback: str, tenant_id, *,
    exclude: tuple[str, ...] = (), extra_sizes: tuple[tuple[int, int], ...] = (),
    scene_prompt: str = "",
) -> tuple[str, str]:
    """Re-render a cloned or learned post in ITS OWN design and attach it to the
    new row — with the owner's change to its words and look, a different photo
    when `exclude` names the current one, and every extra platform shape.

    Returns ("", "") when the design cannot be recovered — the caller's contract
    for "no image", which the board already reports honestly. Substituting a
    different layout here is exactly the behaviour this replaces."""
    import asyncio as _asyncio

    from . import template_clone
    from .media import storage as media_storage

    out = await template_clone.rebuild_design(
        payload, feedback, tenant_id, exclude_photo_keys=tuple(k for k in exclude if k),
        scene_prompt=scene_prompt, sizes=extra_sizes)
    if not out:
        _log.warning("could not recover the cloned design for %s", new_id)
        return "", ""
    tenant = str(tenant_id or settings.default_tenant_id)
    served, _fp = await _asyncio.to_thread(
        media_storage().save, tenant, out["png"], "template-clone.png")
    by_size: dict[str, str] = {}
    for key, png in (out.get("by_size") or {}).items():
        try:
            uri, _ = await _asyncio.to_thread(media_storage().save, tenant, png, f"rebuild-{key}.png")
            by_size[key] = uri
        except Exception:  # noqa: BLE001 — one shape must never cost the rebuild
            _log.warning("could not store the %s rebuild of %s", key, new_id, exc_info=True)
    learned = payload.get("image_format") == "learned"
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1::uuid",
            new_id, json.dumps({
                "image_url": served, "media_url": served, "has_image": True,
                # A learned post stays learned — and stays tied to its library
                # layout, so the verdict on the redo still reaches that layout.
                **({"image_format": "learned",
                    "design_template_id": payload.get("design_template_id") or "",
                    "design_template_source": payload.get("design_template_source") or {},
                    # Same layout, so a pinned parent's redo is still the pin.
                    **({"house_layout_pinned": True}
                       if payload.get("house_layout_pinned") is True else {})}
                   if learned else
                   {"image_format": "cloned", "cloned_from_competitor": True}),
                # The layout AS REBUILT — the owner's look change included — and
                # the words as edited, so the NEXT edit starts from this card, not
                # from the one before it.
                "clone_spec": out.get("spec") or payload.get("clone_spec") or {},
                "clone_content": out.get("content") or payload.get("clone_content") or {},
                "clone_source_url": payload.get("clone_source_url") or "",
                **({"hero_photo_key": out["hero_key"]} if out.get("hero_key") else {}),
                **({"image_urls_by_size": by_size} if by_size else {}),
            }))
    return served, ("learned" if learned else (out.get("kind") or "cloned"))


async def _run_regenerate(
    job_id: str, tenant_id: UUID, parent_id: UUID, feedback: str, force_format: str,
    *, canvas: tuple[int, int] | None = None, extra_sizes: tuple[tuple[int, int], ...] = (),
    scene_prompt: str = "", by_owner: bool = False, edit_photo: bool = False,
) -> None:
    """Rebuild the IMAGE for a rejected post, keeping its words.

    Only the picture is redone: the caption was written in the brand's voice and
    is edited by hand (PATCH /v1/queue/post/{id}), so regenerating it would
    throw away good copy to fix a bad photo.

    The redo is a NEW row rather than an overwrite, so the original and the
    reason it was rejected stay readable — /v1/queue/rejected joins them through
    payload.regen_of."""
    from . import main as _main

    job = _JOBS.get(job_id)
    if job is None:
        return
    job["status"] = "running"
    job["updated_at"] = _now()
    try:
        async with acquire(tenant_id) as conn:
            parent = await conn.fetchrow(
                "SELECT payload, rejection_reason_code FROM actions "
                "WHERE id=$1 AND action_type='content'", parent_id)
        if parent is None:
            raise ValueError("post not found")
        payload = parent["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = payload or {}

        # Their words: an explicit feedback argument wins, else the reason they
        # gave when rejecting — so "Regenerate" needs no retyping.
        reason = (feedback or "").strip() or (parent["rejection_reason_code"] or "").strip()
        prev_photo = str(payload.get("hero_photo_key") or "")
        version = int(str(payload.get("version") or "1") if str(payload.get("version") or "1").isdigit() else 1)
        if version >= _regen_cap(by_owner):
            raise ValueError(
                f"this post has already been rebuilt {_regen_cap(by_owner) - 1} times — "
                "change the brief or edit it by hand rather than asking for another pass"
            )

        # The new row carries the SAME copy and points back at what it replaces.
        new_payload = {
            **payload,
            "regen_of": str(parent_id),
            "version": version + 1,
            "regen_feedback": reason,
        }
        # The parent's own image must not be inherited if the redo fails to make
        # one — a v2 showing v1's rejected picture is the worst possible outcome.
        # Nor its other platform shapes: a redo that copied them showed the NEW
        # picture on one network and the rejected one on every other.
        for k in ("image_url", "media_url", "has_image", "hero_photo_key", "image_format",
                  "image_urls_by_size"):
            new_payload.pop(k, None)
        # Nor the claim that it was drawn from a PINNED house layout. That flag
        # describes how the parent's picture was made; a redo composed afresh
        # (the owner rejected the design) is one of the nine, and a copied flag
        # told BM2 the pin had rendered when it had not. A redo rebuilt in place
        # IS that layout again, and _rebuild_cloned_action re-stamps it there.
        new_payload.pop("house_layout_pinned", None)
        # Nor the parent's hand IMAGE edit. The card editor resumes whatever layout
        # a row carries, so a rebuild that kept edit_layers opened on the rejected
        # parent's canvas — and one save replaced the rebuild's fix with the very
        # picture the owner had just turned down. (edited_by_owner is deliberately
        # NOT cleared: it marks a hand-edited CAPTION, which the rebuild keeps.)
        # generated_parts too: they describe the PARENT's card, and the editor
        # would lay the rebuild out from the words it was rejected for.
        # render_layers are the parent's card in layers — the rejected picture.
        for k in ("edit_layers", "image_edited_by_owner", "original_image_url", "generated_parts",
                  "render_layers", "edit_layers_slides", "original_media_urls",
                  "render_layers_slides"):
            new_payload.pop(k, None)

        async with acquire(tenant_id) as conn:
            new_id = await conn.fetchval(
                "INSERT INTO actions (proposed_by, action_type, payload, status) "
                "VALUES ('regenerate', 'content', $1::jsonb, 'pending') RETURNING id",
                json.dumps(new_payload),
            )

        # A text-styling rejection ("make it white / red / thicker") is APPLIED as
        # a render knob so the redo actually restyles the image.
        overrides = _styling_override(reason)
        if overrides:
            try:
                from .render_tuning import set_render_tuning
                await set_render_tuning(overrides, tenant_id)
            except Exception:  # noqa: BLE001 — a knob write must never block a redo
                pass

        # A REDO IS A FIX, NOT A REPLACEMENT. Whatever was on screen is what the
        # owner was correcting, so the photo AND the layout are kept unless they
        # asked otherwise. The old rule kept the photo but handed the layout back
        # to the art director whenever the feedback matched no keyword — which is
        # most real feedback — and the art director then reached for the house
        # style. "keep rest template same and image same" produced a different
        # template with the photo dropped: the instruction inverted.
        prev_format = str(payload.get("image_format") or "")
        layout = _layout_intent(reason)
        want_photo = _wants_photo_present(reason)
        new_photo = _wants_new_photo(reason) or (want_photo and not _keeps_photo(reason))
        # A PHOTO EDIT ("brighter", "make the sky orange") edits the CURRENT photo's
        # pixels in place and keeps the same layout + on-card text — the ChatGPT-style
        # behaviour. Only when there is a photo to edit, they didn't ask for a new
        # one or a new layout, and no explicit format was pinned.
        photo_edit = (
            (edit_photo or _wants_photo_edit(reason))
            and not new_photo and not want_photo and not force_format and bool(prev_photo)
        )

        if new_photo:
            eff_force_photo = ""
            eff_exclude = (prev_photo,) if prev_photo else ()
        else:
            eff_force_photo = prev_photo
            eff_exclude = ()

        eff_avoid = ""
        if force_format:
            eff_force = force_format               # explicit layout (e.g. "make James big")
        elif layout == "new":
            # They said the look itself is wrong — compose afresh, and do not hand
            # back the very template they just turned down.
            eff_force = ""
            eff_avoid = prev_format
        else:
            # 'same' and unspecified both keep it. Unspecified is the common case
            # and keeping is the safe reading: they were fixing this piece.
            #
            # But only a REAL layout name can be pinned. image_format also holds
            # "cloned" (template_clone.py writes it for a cloned competitor
            # design), which _FORMAT_MAP has no key for: imagegen resolves the pin
            # with .get(name, model_pick), so an unknown name silently becomes the
            # art director's free choice — and, being truthy, it consumes the
            # branch that downgrades v2 layouts for a brand with design intel
            # switched off. Pinning a name we cannot honour is worse than not
            # pinning: it disables a guard and delivers nothing.
            eff_force = prev_format if _is_pinnable_format(prev_format) else ""

        if want_photo:
            # "there is no image on it" cannot be answered by a text-only card, so
            # steer off that whole family however the layout was decided above.
            if eff_force in _TEXT_ONLY_FORMATS:
                eff_force = ""
            eff_avoid = " ".join(x for x in (eff_avoid, *_TEXT_ONLY_FORMATS) if x).strip()

        # A CLONED post is a competitor's template read by vision, not one of the
        # nine designed layouts — so the designed-image path cannot preserve it
        # and never could. Rebuild it in its own design instead...
        #
        # ...UNLESS the owner rejected the design itself. This branch used to run
        # unconditionally, before intent was consulted at all: "i hate this
        # design" resolved correctly to layout="new" and was then ignored,
        # because a cloned post was always rebuilt in the very template it was
        # being rejected for. Preserving a template is the right default and the
        # wrong answer to "do not use this template".
        # A LEARNED post is the same kind of thing — a layout from the brand's
        # design library, not one of the nine — and carries its spec and copy,
        # so it is rebuilt in its own design the same way.
        rebuild_in_place = _rebuilds_in_place(payload, layout)
        from . import image_compose as _ic
        if rebuild_in_place:
            with _ic.canvas(*(canvas or (0, 0))):
                served, fmt = await _rebuild_cloned_action(
                    new_id, payload, reason, tenant_id, exclude=eff_exclude,
                    extra_sizes=extra_sizes, scene_prompt=scene_prompt)
        else:
            # KEEP THE CARD, CHANGE THE ONE THING. When the layout is being kept
            # and we still hold the spec that produced it, edit that spec rather
            # than re-running the art director — otherwise every untouched line of
            # on-image copy is rewritten and the owner does not recognise their
            # own card. A layout the owner asked to REPLACE gets a fresh
            # composition, which is what they asked for.
            base = payload.get("image_spec")
            keep_the_card = (
                layout != "new" and not want_photo and not force_format
                and isinstance(base, dict) and base.get("format")
            )
            with _ic.canvas(*(canvas or (0, 0))):
                served, fmt = await _main._generate_designed_post_image(
                    new_id,
                    str(payload.get("topic") or ""),
                    str(payload.get("content") or payload.get("caption") or ""),
                    tenant_id,
                    avoid=eff_avoid,
                    feedback=reason,
                    force_format=eff_force,
                    exclude_photos=eff_exclude,
                    force_photo=eff_force_photo,
                    base_spec=base if keep_the_card else None,
                    extra_sizes=extra_sizes,
                    # In-place photo edit: keep the spec (same layout + text), edit the
                    # CURRENT photo's pixels. reason is the owner's change ("brighter").
                    edit_photo_instruction=(reason if photo_edit else ""),
                    is_redo=True,
                )
        job["result"] = {
            "action_id": str(new_id), "regen_of": str(parent_id),
            "version": version + 1, "image_url": served, "image_format": fmt,
            "feedback": reason,
            # The hero we dropped this pass (empty unless a swap was requested and
            # the parent had a photo). A caller comparing this to the child's new
            # hero_photo_key can tell a real swap from a one-photo no-op.
            "excluded_photo_key": (eff_exclude[0] if eff_exclude else ""),
        }
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
        _log.exception("regenerate failed for %s", parent_id)
    finally:
        job["updated_at"] = _now()


@router.post("/queue/post/{action_id}/regenerate", status_code=202)
async def v1_post_regenerate(
    action_id: UUID, body: RegenerateBody, tenant_id: TenantDep,
) -> dict[str, Any]:
    """Redo the image for a post that was rejected, using the owner's feedback.

    Returns a job_id immediately; poll /v1/jobs/{id}. The result is a NEW
    pending post whose payload names the original in regen_of, so it appears
    under it in /v1/queue/rejected instead of as an unrelated card."""
    # Refuse a runaway chain here as well as in the job, so a caller that fires
    # this automatically (BM2.0's reject route does) gets a straight answer it
    # can show the owner instead of a job that fails a second later.
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT payload->>'version' AS version FROM actions "
            "WHERE id=$1 AND action_type='content'", action_id)
    if row is None:
        raise HTTPException(404, "post not found")
    _v = str(row["version"] or "1")
    _cap = _regen_cap(body.by_owner)
    if (int(_v) if _v.isdigit() else 1) >= _cap:
        return {
            "job_id": "",
            "status": "refused",
            "error": f"already rebuilt {_cap - 1} times — "
                     "change the brief or edit it by hand",
        }
    job = {
        "id": str(uuid.uuid4()), "type": "regenerate", "tenant_id": str(tenant_id),
        "status": "queued", "result": None, "error": None,
        "created_at": _now(), "updated_at": _now(),
    }
    _put_job(job)
    _w, _h = int(body.image_width or 0), int(body.image_height or 0)
    _spawn(_run_regenerate(
        job["id"], tenant_id, action_id, body.feedback, body.force_format,
        canvas=(_w, _h) if _w > 0 and _h > 0 else None,
        extra_sizes=tuple(
            (int(p[0]), int(p[1])) for p in body.sizes
            if isinstance(p, (list, tuple)) and len(p) == 2
            and int(p[0]) > 0 and int(p[1]) > 0 and (int(p[0]), int(p[1])) != (_w, _h)),
        scene_prompt=body.scene_prompt, by_owner=body.by_owner, edit_photo=body.edit_photo))
    return {"job_id": job["id"], "status": "queued"}


class CaptionBody(BaseModel):
    caption: str = Field(min_length=1)


@router.patch("/queue/post/{action_id}")
async def v1_post_edit_caption(
    action_id: UUID, body: CaptionBody, tenant_id: TenantDep,
) -> dict[str, Any]:
    """Edit a pending post's caption by hand.

    Captions are corrected, not regenerated — instant, free, and exactly what
    was wanted, where a rewrite would gamble good copy. The legacy dashboard had
    a PATCH for this but it was unusable from the service API: it wrote
    payload.content while every reader (v1_queue, postgen_adopt) reads
    payload.caption, it set an updated_at column this table does not have, and
    it was cookie-authenticated only.

    Both keys are written here, because content.py seeds both at creation and
    different consumers read different ones — updating one would leave the post
    disagreeing with itself."""
    text = body.caption.strip()
    if not text:
        raise HTTPException(422, "caption must not be blank")
    async with acquire(tenant_id) as conn:
        # record_edit turns the before -> after delta into a corrective style
        # rule, so it needs the copy as it was. RETURNING yields the row AFTER
        # the update, which would hand it the new text as the "old" and teach a
        # no-op — the self-join captures the pre-update snapshot instead.
        row = await conn.fetchrow(
            "UPDATE actions a SET payload = a.payload || $2::jsonb "
            "FROM actions prev "
            "WHERE prev.id = a.id AND a.id = $1 "
            "  AND a.action_type='content' AND a.status='pending' "
            "RETURNING coalesce(prev.payload->>'content', prev.payload->>'caption', '') AS old",
            action_id,
            json.dumps({"caption": text, "content": text, "edited_by_owner": True}),
        )
    if row is None:
        raise HTTPException(404, "post not found, or no longer pending")
    # The edit itself is a lesson: what they changed teaches the writer.
    try:
        from . import learning
        await learning.record_edit(action_id, row["old"] or "", text, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — never fail an edit on the learning leg
        _log.exception("could not record caption edit for %s", action_id)
    return {"ok": True, "id": str(action_id), "caption": text}


# ── the card editor ──────────────────────────────────────────────────────────
#
# Regeneration asks a model to reinterpret an instruction and re-render the whole
# card, which is exactly where "move the logo to the center" and "keep everything,
# just add my handle" kept failing: there was nothing in the card to move. The
# editor is the other way in — the owner changes the card directly, in the browser,
# and the PNG they see is the PNG that posts. These two endpoints give it the card
# as parts, and take the finished image back.

_EDIT_MAX_BYTES = 15 * 1024 * 1024
_EDIT_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _palette_roles(roles) -> dict:
    """A brand role-list → {bg, ink, accent, surface} hex, for the editor's swatches."""
    out: dict = {}
    for p in roles or []:
        if not isinstance(p, dict):
            continue
        role, hexv = str(p.get("role") or "").lower(), str(p.get("hex") or "")
        key = {"background": "bg", "ink": "ink", "accent": "accent", "surface": "surface"}.get(role)
        if key and hexv:
            out[key] = hexv
    return out


def _sniff_image(data: bytes) -> str:
    """The image type the BYTES say they are — the upload's declared content-type
    is whatever the client chose to send. Empty for anything that is not one."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _layout_urls(layout: dict) -> list[str]:
    """Every URL a saved layout will make the editor fetch."""
    urls = []
    bg = layout.get("background") if isinstance(layout.get("background"), dict) else {}
    if bg.get("url"):
        urls.append(str(bg["url"]))
    for layer in layout.get("layers") or []:
        if isinstance(layer, dict) and layer.get("url"):
            urls.append(str(layer["url"]))
    return urls


def _edited_photo(layout: dict, generated: set[str]) -> str | None:
    """The photo an edited card is built on — what hero_photo_key should say now.

    A URL when the owner put a photo on it (a new background, or a photo panel in
    a rebuilt card); "" when the card no longer has one (a plain colour); None when
    it still shows the generated card, whose own photo is still the right answer.
    The key decides what a later regenerate keeps or excludes, so leaving the old
    one in place after the owner removed or replaced the photo brought it back."""
    if not layout:
        return None
    bg = layout.get("background") if isinstance(layout.get("background"), dict) else {}
    url = str(bg.get("url") or "")
    if bg.get("kind") == "image" and url:
        # the generated card itself (flattened) is not a photo to pin
        return None if url in generated else (url if url.startswith("https://") else None)
    for layer in layout.get("layers") or []:
        if (isinstance(layer, dict) and layer.get("type") == "image"
                and layer.get("fit") == "cover" and str(layer.get("url") or "").startswith("https://")
                and layer["url"] not in generated):
            return str(layer["url"])
    return ""


def _first_sentence(text: str, limit: int = 140) -> str:
    t = " ".join((text or "").split())
    for stop in (". ", "! ", "? ", "\n"):
        i = t.find(stop)
        if 0 < i < limit:
            return t[: i + 1].strip()
    return t[:limit].rstrip()


@router.get("/queue/post/{action_id}/layers")
async def v1_post_layers(action_id: UUID, tenant_id: TenantDep,
                         slide: int | None = None) -> dict[str, Any]:
    """Everything the editor needs to rebuild this card as movable parts.

    Returns ingredients, not a layout: the words by role, the photo it was made
    from, the brand's palette, typeface, handle and logo, and — when the card was
    edited before — the saved layout itself so the owner picks up where they left
    off. Laying those out is the editor's job; this only says what exists.

    `slide` asks for ONE slide of a carousel, as a card of its own: its picture,
    its layers, and whatever was saved for that slide before. Without it a
    carousel answers is_carousel — there is no single canvas to edit."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT status, payload FROM actions WHERE id=$1 AND action_type='content'",
            action_id)
    if row is None:
        raise HTTPException(404, "post not found")
    p = row["payload"]
    p = json.loads(p) if isinstance(p, str) else (p or {})

    # After a hand edit the live spec keys are gone (nothing may re-render the
    # replaced card from them); the card's parts as generated wait in
    # generated_parts, which only this endpoint reads.
    gp = p.get("generated_parts") if isinstance(p.get("generated_parts"), dict) else {}

    def _part(key: str) -> dict | None:
        for v in (p.get(key), gp.get(key)):
            if isinstance(v, dict):
                return v
        return None

    spec = _part("image_spec") or {}
    caption = str(p.get("caption") or p.get("content") or "")
    headline = (spec.get("statement") or spec.get("headline") or spec.get("quote")
                or _first_sentence(caption))
    texts = {
        "headline": str(headline or "").strip(),
        "kicker": str(spec.get("kicker") or spec.get("top_text") or "").strip(),
        "sub": str(spec.get("bottom_text") or spec.get("stat_label") or "").strip(),
        "stat": str(spec.get("stat") or "").strip(),
    }

    # The photo a card was built on is recorded by its source URL — so it can be
    # put straight back as the editable background.
    photo = str(p.get("hero_photo_key") or "")
    photo_url = photo if photo.startswith(("http://", "https://")) else ""

    # The brand's palette, typeface, kit and logo: four independent reads, and
    # they used to be awaited one after another — about 1.5s of the editor's
    # open, every time, entirely spent waiting. Together they take as long as
    # the slowest one. Each still fails on its own: gather(return_exceptions)
    # keeps one missing piece from costing the other three, which is what the
    # separate try blocks were for.
    from .brand_identity import ensure_brand_palette, get_brand_font
    from .brand_kit import get_brand_kit
    from .media import list_media

    raw_palette, raw_font, raw_kit, raw_logos = await asyncio.gather(
        ensure_brand_palette(tenant_id),
        get_brand_font(tenant_id),
        get_brand_kit(tenant_id),
        list_media(role="brand_logo", tenant_id=tenant_id),
        return_exceptions=True,
    )

    def _ok(v, what: str):
        if isinstance(v, BaseException):
            _log.warning("editor: %s unavailable: %s", what, v)
            return None
        return v

    try:
        palette = _palette_roles(_ok(raw_palette, "palette") or {})
    except Exception:  # noqa: BLE001 — the editor still opens without them
        palette = {}
    font = str(_ok(raw_font, "font") or "") or "bold"
    kit = _ok(raw_kit, "brand kit") or {}
    logos = _ok(raw_logos, "logo") or []
    logo_url = str((logos[0] if logos else {}).get("uri") or "")

    media_urls = p.get("media_urls")
    if isinstance(media_urls, str):
        try:
            media_urls = json.loads(media_urls)
        except (ValueError, TypeError):
            media_urls = None
    # The card as it was DRAWN, in layers: a clean background plate plus every
    # line of text and every badge exactly where the renderer put them — so the
    # editor opens the real card with each part movable, instead of rebuilding
    # an approximation from the ingredients below. Captured on first open.
    from .layer_capture import slide_urls
    slides_now, slides_was = slide_urls(p)
    carousel = len(slides_now) > 1
    if slide is not None:
        if not carousel:
            raise HTTPException(404, "this post has no slides")
        if not (0 <= slide < len(slides_now)):
            raise HTTPException(404, "no such slide")

    render_layers = None
    if slide is not None:
        try:
            from .layer_capture import ensure_layers
            render_layers = await ensure_layers(action_id, tenant_id, p, slide=slide)
        except Exception:  # noqa: BLE001 — the slide still opens as a flat picture
            _log.warning("editor: slide capture failed for %s/%s", action_id, slide, exc_info=True)
    elif not (isinstance(media_urls, list) and len(media_urls) > 1):
        try:
            from .layer_capture import ensure_layers
            render_layers = await ensure_layers(action_id, tenant_id, p)
        except Exception:  # noqa: BLE001 — the editor still opens from ingredients
            _log.warning("editor: layer capture failed for %s", action_id, exc_info=True)
    return {
        "id": str(action_id),
        "status": row["status"],
        "editable": row["status"] == "pending",
        # One image per card in this version — a carousel's slides would each
        # need their own canvas, and pretending to edit slide one of five is worse
        # than saying so.
        "is_carousel": carousel and slide is None,
        **({"slide": slide} if slide is not None else {}),
        **({"slide_count": len(slides_now)} if carousel else {}),
        "image_url": slides_now[slide] if slide is not None else str(p.get("image_url") or ""),
        # The card as it was GENERATED, before any hand edit. After a save,
        # image_url is the edited PNG; starting from that again stacks every
        # addition on top of its own flattened copy.
        "original_image_url": slides_was[slide] if slide is not None
        else str(p.get("original_image_url") or p.get("image_url") or ""),
        "format": str(p.get("image_format") or ""),
        "photo_url": photo_url,
        "texts": texts,
        "clone_spec": _part("clone_spec"),
        "clone_content": _part("clone_content"),
        "saved": ((p.get("edit_layers_slides") or {}).get(str(slide)) if slide is not None
                  else p.get("edit_layers") if isinstance(p.get("edit_layers"), dict) else None),
        "palette": palette,
        "font_theme": font,
        "handle": str(kit.get("handle") or ""),
        "display_name": str(kit.get("display_name") or ""),
        "logo_url": logo_url,
        "render_layers": render_layers,
    }


@router.post("/queue/post/{action_id}/image")
async def v1_post_set_image(
    action_id: UUID,
    tenant_id: TenantDep,
    file: UploadFile = File(...),
    doc: str = Form(""),
    slide: int | None = Form(None),
) -> dict[str, Any]:
    """Replace a pending post's image with one the owner edited by hand.

    The PNG is stored in the same bucket every generated image publishes from, so
    the aggregator fetches it at post time like any other. The layout that made it
    is kept beside it, so opening the editor again resumes the edit instead of
    starting over from the generated card. Pending posts only — an approved post
    has already been adopted into a work order, and swapping its image here would
    change something the owner signed off on.

    `slide` replaces ONE slide of a carousel and leaves the rest of the deck
    alone; the cover is mirrored into image_url the way the renderer mirrors
    it."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty image")
    if len(data) > _EDIT_MAX_BYTES:
        raise HTTPException(413, "edited image too large (max 15 MB)")
    # Judge the bytes, not the label. The content-type is client-chosen; a file
    # that is not actually an image must not be stored and served as a post.
    if _sniff_image(data) not in _EDIT_TYPES:
        raise HTTPException(415, "that file is not a PNG, JPEG or WebP image")
    layout: dict = {}
    if doc.strip():
        try:
            parsed = json.loads(doc)
            layout = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            raise HTTPException(422, "doc must be a JSON object") from None
        if len(doc) > 400_000:
            raise HTTPException(413, "layout too large")
        # A saved layout is handed back to every member's browser, which then
        # fetches each URL in it. Only https — no data:, file:, javascript: or
        # plain-http origins riding along in a stored document.
        for u in _layout_urls(layout):
            if not u.startswith("https://"):
                raise HTTPException(422, "layout images must be https URLs")

    async with acquire(tenant_id) as conn:
        cur = await conn.fetchrow(
            "SELECT status, payload, payload->>'image_url' AS image_url, "
            "payload->>'original_image_url' AS original_image_url "
            "FROM actions WHERE id=$1 AND action_type='content'", action_id)
    if cur is None:
        raise HTTPException(404, "post not found")
    if cur["status"] != "pending":
        raise HTTPException(409, "only a post awaiting approval can be edited")
    if slide is not None:
        from .layer_capture import slide_urls
        _payload = cur["payload"]
        _payload = json.loads(_payload) if isinstance(_payload, str) else (_payload or {})
        _now, _was = slide_urls(_payload)
        if len(_now) < 2:
            raise HTTPException(404, "this post has no slides")
        if not (0 <= slide < len(_now)):
            raise HTTPException(404, "no such slide")

    from .media import storage as media_storage
    tenant = str(tenant_id or settings.default_tenant_id)
    served, _fp = await asyncio.to_thread(
        media_storage().save, tenant, data, "edited-card.png")

    # ONE SLIDE of a carousel: the deck keeps its other slides, the slides as
    # generated are remembered once (so a re-edit starts from the slide as
    # drawn, not from its own flattened copy), and the layout is kept per slide.
    if slide is not None:
        patch: dict[str, Any] = {"has_image": True, "image_edited_by_owner": True,
                                 "design_qa_failed": False}
        if slide == 0:
            patch["image_url"] = patch["media_url"] = served
        async with acquire(tenant_id) as conn:
            tag = await conn.execute(
                "UPDATE actions SET payload = jsonb_set("
                "    jsonb_set("
                "      jsonb_set(payload, '{original_media_urls}',"
                "        COALESCE(payload->'original_media_urls', payload->'media_urls'), true),"
                "      ARRAY['media_urls', $3::text], to_jsonb($2::text), false),"
                "    '{edit_layers_slides}',"
                "    COALESCE(payload->'edit_layers_slides', '{}'::jsonb)"
                "      || jsonb_build_object($3::text, $4::jsonb), true)"
                "  || $5::jsonb - $6::text "
                "WHERE id=$1 AND action_type='content' AND status='pending'",
                action_id, served, str(slide), json.dumps(layout), json.dumps(patch),
                # the cover's per-network renders were of the slide just replaced
                "image_urls_by_size" if slide == 0 else "")
        if not tag.endswith(" 1"):
            raise HTTPException(409, "only a post awaiting approval can be edited")
        return {"ok": True, "id": str(action_id), "image_url": served, "slide": slide}

    # Record what is NOW on screen, so nothing downstream acts on the card the
    # owner replaced. A later regenerate reads image_spec / clone_spec to "keep the
    # card" and hero_photo_key to keep or exclude the photo — left as they were,
    # a rejection of an edited card silently rebuilt the pre-edit one: the wrong
    # headline back, and the photo the owner removed pinned in place.
    photo = _edited_photo(layout, {u for u in (cur["image_url"], cur["original_image_url"]) if u})
    # design_template_*: the card is no longer that library layout. Left on the
    # row, approving it credited the layout with a picture the owner made, and a
    # redo re-adopted the id onto a card of a different design. The same for
    # house_layout_pinned: the owner's picture is not the pinned house render.
    drop = ["image_spec", "clone_spec", "clone_content", "image_urls_by_size",
            "design_template_id", "design_template_source", "house_layout_pinned"]
    patch = {
        "image_url": served, "media_url": served, "has_image": True,
        "edit_layers": layout, "image_edited_by_owner": True,
        # the owner made this image; an automated QA verdict on the one it
        # replaced no longer describes anything on screen
        "design_qa_failed": False,
        # not a layout the renderer can reproduce — _is_pinnable_format rejects
        # it, so a later "keep the layout" composes rather than pins nonsense
        "image_format": "owner_edit",
    }
    if photo:
        patch["hero_photo_key"] = photo   # the photo the owner put on the card
    elif photo == "":
        drop.append("hero_photo_key")     # the owner took the photo off
    async with acquire(tenant_id) as conn:
        tag = await conn.execute(
            # original_image_url and generated_parts are set ONCE, from the card as
            # generated — the coalesces read the row's pre-update payload, so a
            # second edit does not overwrite them with the first edit's output.
            # generated_parts keeps the words and layout the spec keys carried, so
            # "Rebuild from parts" still has them after a save (only the layers
            # endpoint reads it; no renderer does).
            # The per-network renders (image_urls_by_size) were of the OLD picture —
            # left in place, every other platform would post the card the owner
            # just replaced. Dropped, they fall back to the edited primary.
            "UPDATE actions SET payload = (payload - $3::text[]) "
            "  || jsonb_build_object("
            "       'original_image_url', COALESCE(payload->>'original_image_url', payload->>'image_url'), "
            "       'generated_parts', COALESCE(payload->'generated_parts', jsonb_strip_nulls(jsonb_build_object("
            "           'image_spec', payload->'image_spec', 'clone_spec', payload->'clone_spec', "
            "           'clone_content', payload->'clone_content')))) "
            "  || $2::jsonb "
            "WHERE id=$1 AND action_type='content' AND status='pending'",
            action_id, json.dumps(patch), drop)
    if not tag.endswith(" 1"):
        # approved or rejected in the moment between the check and the write
        raise HTTPException(409, "only a post awaiting approval can be edited")
    return {"ok": True, "id": str(action_id), "image_url": served}


class ReplateBody(BaseModel):
    photo_url: str = Field(..., max_length=2000)


async def _fetch_photo(url: str) -> bytes:
    """A photo the owner picked (a brand-library photo, or one they uploaded),
    fetched the way a user-supplied URL must be: public https only, no
    redirects, capped, and it has to be an image."""
    import httpx

    from .netguard import url_is_public

    if not await url_is_public(url):
        raise HTTPException(422, "the photo must be a public https URL")
    buf = bytearray()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    raise HTTPException(422, "that photo could not be fetched")
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > _EDIT_MAX_BYTES:
                        raise HTTPException(413, "that photo is too large (max 15 MB)")
    except httpx.HTTPError:
        raise HTTPException(422, "that photo could not be fetched") from None
    if _sniff_image(bytes(buf)) not in _EDIT_TYPES:
        raise HTTPException(415, "that file is not a PNG, JPEG or WebP image")
    return bytes(buf)


class PieceBody(BaseModel):
    url: str = Field(..., max_length=2000)
    slide: int | None = None


@router.post("/queue/post/{action_id}/piece-words")
async def v1_post_piece_words(action_id: UUID, body: PieceBody,
                              tenant_id: TenantDep) -> dict[str, Any]:
    """The words on one cut piece, and the line that sets them again in type.

    A card an older renderer drew is opened as pieces of itself (layer_capture
    .dissect), so its words are pictures: they move, but nothing can be typed
    into them. This reads the words off the piece and works out the face, size,
    tracking and colour that put them back exactly where the piece sits — so the
    editor can swap that picture for text the owner types into.

    Only a piece of THIS draft can be read: the url must be one this card's own
    layers carry. Read once, then kept on the draft."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT status, payload FROM actions WHERE id=$1 AND action_type='content'",
            action_id)
    if row is None:
        raise HTTPException(404, "post not found")
    if row["status"] != "pending":
        raise HTTPException(409, "only a post awaiting approval can be edited")
    p = row["payload"]
    p = json.loads(p) if isinstance(p, str) else (p or {})

    url = body.url.strip()
    docs = [p.get("render_layers")] + list((p.get("render_layers_slides") or {}).values())
    box = next((im for doc in docs if isinstance(doc, dict)
                for im in (doc.get("images") or []) if im.get("url") == url), None)
    if box is None:
        raise HTTPException(404, "that piece is not part of this card")
    cached = (p.get("piece_words") or {}).get(url)
    if isinstance(cached, dict):
        return cached

    from .layer_capture import as_words, read_words
    from .template_clone import _fetch_bytes

    png = await _fetch_bytes(url)
    if not png:
        raise HTTPException(404, "that piece could not be read")
    words = await read_words(png)
    theme = None
    try:
        from . import brand_identity as _bi, font_themes as _ftm
        theme = _ftm.resolve(await _bi.get_brand_font(tenant_id))
    except Exception:  # noqa: BLE001 — the house faces are the fallback
        theme = None
    line = None
    try:
        line = as_words(png, words, box, theme) if words else None
    except Exception:  # noqa: BLE001 — the piece stays a piece
        _log.warning("editor: could not set the words of a piece for %s", action_id, exc_info=True)
    out = {"words": words, "line": line}
    if line:
        try:
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE actions SET payload = jsonb_set(payload, '{piece_words}', "
                    "  COALESCE(payload->'piece_words', '{}'::jsonb) "
                    "  || jsonb_build_object($2::text, $3::jsonb), true) WHERE id = $1",
                    action_id, url, json.dumps(out))
        except Exception:  # noqa: BLE001 — serve it anyway
            _log.warning("editor: could not keep the words of a piece", exc_info=True)
    return out


@router.post("/queue/post/{action_id}/replate")
async def v1_post_replate(action_id: UUID, body: ReplateBody, tenant_id: TenantDep) -> dict[str, Any]:
    """This card's design drawn again around another photo, for the editor.

    "Change the picture" used to mean replacing the whole background, which
    threw away what the design did with its photo — the scrim under the words,
    the rounded frame, the panel it fades out of. Here the same design is drawn
    with the new photo and handed back in layers: a new plate, the photo panel if
    the design has one, and the lines (so the editor can see where they fall).

    {"replated": false} when the design has no photo to replace — the editor
    then sets the photo as a plain background instead. Nothing is written to the
    draft; the owner's save is what changes the card. Pending posts only."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT status, payload FROM actions WHERE id=$1 AND action_type='content'",
            action_id)
    if row is None:
        raise HTTPException(404, "post not found")
    if row["status"] != "pending":
        raise HTTPException(409, "only a post awaiting approval can be edited")
    p = row["payload"]
    p = json.loads(p) if isinstance(p, str) else (p or {})

    from .layer_capture import replate, takes_photo

    if not takes_photo(p):
        return {"replated": False}
    photo = await _fetch_photo(body.photo_url.strip())

    # Drawn at the card's own shape: the one its layers were captured at, or
    # the size of the picture it is.
    canvas: tuple[int, int] | None = None
    rl = p.get("render_layers")
    if isinstance(rl, dict) and isinstance(rl.get("canvas"), list) and len(rl["canvas"]) == 2:
        canvas = (int(rl["canvas"][0]), int(rl["canvas"][1]))
    if not canvas:
        from io import BytesIO

        from PIL import Image

        from .template_clone import _fetch_bytes
        src = str(p.get("original_image_url") or p.get("image_url") or "")
        img = await _fetch_bytes(src) if src else None
        if not img:
            return {"replated": False}
        canvas = Image.open(BytesIO(img)).size
    try:
        layers = await replate(tenant_id, p, photo, canvas)
    except Exception:  # noqa: BLE001 — the editor falls back to a plain background
        _log.warning("editor: replate failed for %s", action_id, exc_info=True)
        layers = None
    return {"replated": True, **layers} if layers else {"replated": False}


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


# ── brand theme (design intelligence): propose + apply a palette per brand ──

class ThemeApplyBody(BaseModel):
    palette: list = Field(default_factory=list)   # role-list [{role,hex,label}]
    enable: bool = False


@router.post("/brand/theme/propose")
async def v1_brand_theme_propose(tenant_id: TenantDep) -> dict[str, Any]:
    """Read the brand's OWN post photos and propose a theme (palette + verdict).
    On-demand; costs one vision call. The `proposed_theme.palette` it returns is
    exactly what /brand/theme/apply expects, so an owner can accept it as-is."""
    from . import brand_identity as bi
    from .hero_context import get_hero_photo_files
    try:
        refs = await get_hero_photo_files(tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        refs = []
    imgs = [b for (_k, b) in (refs or []) if b][:5]
    res = await bi.assess_and_propose(imgs, {})
    res["current_palette"] = await bi.get_brand_palette(tenant_id)
    res["design_intel_enabled"] = await bi.get_design_intel_enabled(tenant_id)
    return res


@router.post("/brand/theme/apply")
async def v1_brand_theme_apply(body: ThemeApplyBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Store a brand palette (role-list) and switch the design brain on/off for
    THIS brand — so every future post renders in these colours across the 8
    layouts. Reversible: apply enable=false to revert to the shipped 3 formats."""
    from . import brand_identity as bi
    if body.palette:
        await bi.set_brand_palette(body.palette, tenant_id)
    await bi.set_design_intel_enabled(bool(body.enable), tenant_id)
    return {"ok": True, "palette_set": bool(body.palette), "enabled": bool(body.enable)}


class FormatsBody(BaseModel):
    enabled_formats: list = Field(default_factory=list)  # designed-format names


@router.post("/brand/formats")
async def v1_brand_formats_set(body: FormatsBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Set the designed-image formats THIS brand may produce — the per-brand
    template control, synced from the Brand Manager admin. The autonomous art
    director (and any forced build) is then constrained to this set. An empty
    list stores 'nothing'; to lift the restriction entirely, this isn't called."""
    from . import brand_identity as bi
    stored = await bi.set_enabled_formats(body.enabled_formats, tenant_id)
    return {"ok": True, "enabled_formats": stored}


@router.get("/brand/formats")
async def v1_brand_formats_get(tenant_id: TenantDep) -> dict[str, Any]:
    """Current allowed formats for THIS brand (null = no restriction)."""
    from . import brand_identity as bi
    allowed = await bi.get_enabled_formats(tenant_id)
    return {"enabled_formats": (sorted(allowed) if allowed is not None else None)}


class FontBody(BaseModel):
    font: str = ""  # typography theme key (see font_themes.FONT_THEMES); '' = default


@router.post("/brand/font")
async def v1_brand_font_set(body: FontBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Set the typography theme THIS brand's static posts render in — synced from the
    Brand Manager owner's font picker. '' = the default house look. Unknown keys are
    stored as-is and safely fall back to the default at render time."""
    from . import brand_identity as bi
    from . import font_themes as ftm
    stored = await bi.set_brand_font(ftm.normalize(body.font) if body.font else "", tenant_id)
    return {"ok": True, "font": stored}


@router.get("/brand/font")
async def v1_brand_font_get(tenant_id: TenantDep) -> dict[str, Any]:
    """This brand's current typography theme key ('' = default house look)."""
    from . import brand_identity as bi
    return {"font": await bi.get_brand_font(tenant_id)}


class LookBody(BaseModel):
    headline_case: str = ""      # ''|upper|title|sentence ('' = each format's default)
    logo_show: bool = True       # draw the brand logo/profile badge on posts
    logo_position: str = "footer"  # footer|top_left|top_right|bottom_left|bottom_right


@router.post("/brand/look")
async def v1_brand_look_set(body: LookBody, tenant_id: TenantDep) -> dict[str, Any]:
    """Set the brand's 'look' extras (headline case + logo visibility/position) —
    synced from the Brand Manager owner's brand-look panel. Applied to every static
    render; empty/defaults leave the shipped look unchanged."""
    from . import brand_identity as bi
    stored = await bi.set_brand_look(body.model_dump(), tenant_id)
    return {"ok": True, "look": stored}


@router.get("/brand/look")
async def v1_brand_look_get(tenant_id: TenantDep) -> dict[str, Any]:
    """This brand's current look extras ({} = house defaults)."""
    from . import brand_identity as bi
    return {"look": await bi.get_brand_look(tenant_id)}


@router.get("/brand/theme")
async def v1_brand_theme_get(tenant_id: TenantDep) -> dict[str, Any]:
    """Current brand theme state — cheap read (no vision), for the panel to show
    whether the design brain is on and which palette it uses."""
    from . import brand_identity as bi
    return {
        "palette": await bi.get_brand_palette(tenant_id),
        "design_intel_enabled": await bi.get_design_intel_enabled(tenant_id),
    }


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


# ===================================================== design template library
#
# Every still layout learned from a reference post, kept forever (see
# design_templates). Autopilot draws on it when BM2 asks for a "learned" layout;
# these routes let BM2 grow it, show it, and teach it.

_LEARN_RUNNING: set[str] = set()


class DesignReferenceRequest(BaseModel):
    image_url: str = Field(..., min_length=8, description="The post to learn a layout from")
    source_url: str = ""
    # Whose layout this is. 'own' means the BRAND'S OWN post, read back so it
    # keeps making the kind of thing it already makes — and it must be labelled
    # as such, because house_layouts shares by a whitelist of source kinds and
    # 'reference' is on it. Mislabelling a brand's own design would publish it
    # into the cross-brand catalogue. learn_from_reference has always taken this
    # argument; only the route forgot to pass it, so everything minted here
    # landed as 'reference' whatever it really was.
    source_kind: str = "reference"


# What this route may mint. An allowlist rather than a pass-through, because the
# value reaches a CHECK constraint and a sharing whitelist: a typo would be a 500
# at best and a cross-brand leak at worst.
_REFERENCE_KINDS = frozenset({"reference", "own"})


class DesignVerdictRequest(BaseModel):
    approved: bool


@router.get("/design-templates")
async def v1_design_templates(
    tenant_id: TenantDep, limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """The brand's learned layouts, newest first, with where each came from.

    Paged: a page is capped at 200 and real libraries are already past that
    (Trouvailler holds 223), so a caller that reads only the first page and
    counts the rows under-reports the library. `total` and `active` are COUNTs
    over the whole table and are the numbers to trust."""
    from . import design_templates

    lim = max(1, min(200, limit))
    off = max(0, int(offset))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, kind, source_kind, source_handle, source_platform, source_url, "
            "source_image_uri, status, times_used, approvals, rejections, qa_passes, "
            "qa_fails, created_at FROM design_templates "
            "ORDER BY created_at DESC LIMIT $1 OFFSET $2", lim, off)
        unread = await conn.fetchval(
            "SELECT count(*) FROM competitor_posts WHERE stored_media_url <> '' "
            "AND media_type IN ('image','carousel') AND template_read_at IS NULL")
    return {
        "active": await design_templates.count(tenant_id),
        "total": await design_templates.count(tenant_id, active_only=False),
        "min_to_rotate": design_templates.MIN_LIBRARY,
        # competitor stills we hold but have not read yet — what "learn" would do
        "unread_competitor_posts": int(unread or 0),
        "templates": [{**{k: (str(v) if k == "id" else v) for k, v in dict(r).items()
                          if k != "created_at"},
                       "created_at": r["created_at"].isoformat()} for r in rows],
    }


@router.get("/niche-references")
async def v1_niche_references(
    tenant_id: TenantDep, limit: int = 200, offset: int = 0,
) -> dict[str, Any]:
    """The pictures monitoring filed for this brand, and what became of each.

    Pairs the two halves the operator wants side by side: the image we pulled,
    and the layout (if any) that was learned from it. A picture that was read
    but yielded nothing still appears — see `niche_reference.listing`."""
    from . import niche_reference

    return await niche_reference.listing(tenant_id, limit=limit, offset=offset)


@router.post("/design-templates/learn", status_code=202)
async def v1_design_templates_learn(
    tenant_id: TenantDep, background: BackgroundTasks, limit: int = 12,
) -> dict[str, Any]:
    """Read the competitor images we hold into new layouts. Backgrounded — each
    read is a vision-model call. One run per tenant at a time: two overlapping
    runs would both pick the same unread posts and pay for each twice."""
    from . import design_templates

    key = str(tenant_id)
    if key in _LEARN_RUNNING:
        return {"started": False, "reason": "already learning for this brand"}
    _LEARN_RUNNING.add(key)

    async def _run() -> None:
        try:
            await design_templates.learn_from_competitors(tenant_id, limit=min(max(1, limit), 24))
        except Exception:  # noqa: BLE001 — surfaced in logs; the next run retries
            _log.exception("design template learning failed for %s", tenant_id)
        finally:
            _LEARN_RUNNING.discard(key)

    background.add_task(_run)
    return {"started": True}


@router.post("/design-templates/reference", status_code=201)
async def v1_design_templates_reference(
    tenant_id: TenantDep, body: DesignReferenceRequest,
) -> dict[str, Any]:
    """Learn a layout from ANY post, not only a competitor's. The image is saved
    to our own storage FIRST, so the reference is kept in-house even after the
    link it came from dies."""
    import httpx

    from . import design_templates
    from .media import storage as media_storage

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(body.image_url)
            r.raise_for_status()
            img = r.content
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"could not fetch that image: {str(exc)[:120]}") from exc
    kind = body.source_kind if body.source_kind in _REFERENCE_KINDS else "reference"
    uri, _ = await asyncio.to_thread(
        media_storage().save, str(tenant_id), img, f"{kind}.png")
    tid = await design_templates.learn_from_reference(
        tenant_id, img, source_url=body.source_url or body.image_url, image_uri=uri,
        source_kind=kind)
    if not tid:
        raise HTTPException(422, "no usable layout could be read from that image")
    return {"template_id": tid, "stored_image_uri": uri}


class TemplatePause(BaseModel):
    paused: bool = True


@router.put("/design-templates/{template_id}/paused")
async def v1_design_template_paused(
    template_id: str, req: TemplatePause, tenant_id: TenantDep,
) -> dict[str, Any]:
    """Switch one learned layout off for this brand, or back on.

    Separate from 'retired', which is design QA's verdict after repeated render
    failures — an owner's preference and a measured failure are different facts
    and the screen has to be able to show which is which."""
    from . import design_templates

    out = await design_templates.set_paused(tenant_id, template_id, bool(req.paused))
    if out is None:
        raise HTTPException(404, "no such layout for this brand")
    return out


@router.post("/design-templates/{template_id}/verdict")
async def v1_design_template_verdict(
    tenant_id: TenantDep, template_id: UUID, body: DesignVerdictRequest,
) -> dict[str, Any]:
    """The owner approved or rejected a post built on this layout — so layouts
    that land get drawn more and ones that don't fade (never deleted)."""
    from . import design_templates

    # A verdict for a layout this brand does not hold (wrong tenant, a catalogue
    # id, a deleted row) used to answer ok while RLS updated nothing — so the
    # caller believed it had taught a layout it had not.
    counts = await design_templates.mark_verdict(tenant_id, str(template_id), body.approved)
    if counts is None:
        raise HTTPException(404, "no such layout for this brand")
    return {"ok": True, **counts}


# ── the video editor ─────────────────────────────────────────────────────────
#
# A rendered reel that is 90% right used to have one lever: regenerate — another
# credit, another wait, and a DIFFERENT video, so the 90% that was fine went with
# the 10% that wasn't. Seven of the last eight renders on this tenant were
# rejected. These endpoints edit the FILE instead: trim the dead air, fix the
# words on screen, put the logo on, reframe it for the network, pick the cover.
# One ffmpeg pass (see video_edit.py), saved to durable storage, so what is saved
# is exactly what plays.

_VIDEO_MAX_BYTES = 400 * 1024 * 1024


async def _fetchable(url: str) -> bool:
    """Only https, and only a host that resolves to a public address — the same
    bar the job callbacks clear. The editor hands us a URL from the owner's own
    library, but an unguarded fetcher is an SSRF hole whatever it is fed."""
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
    for info in infos or []:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return bool(infos)


async def _download_capped(url: str, dst: str, cap: int = _VIDEO_MAX_BYTES) -> int:
    """Stream to disk, stopping at the cap. A 60s reel is ~60 MB; loading one into
    memory to edit it is how a container dies on the third concurrent edit."""
    import httpx

    total = 0
    with open(dst, "wb") as fh:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as c:
            async with c.stream("GET", url, follow_redirects=True) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes(1 << 20):
                    total += len(chunk)
                    if total > cap:
                        raise ValueError("that video is too large to edit (400 MB cap)")
                    fh.write(chunk)
    return total


async def _brand_video_bits(tenant_id) -> dict:
    """The brand's own palette, typeface, logo and handle — what the editor offers
    as ready-made choices, so a caption added to a reel is in the brand's type and
    colours rather than whatever the browser defaults to. Four independent reads;
    one missing piece must not cost the other three."""
    from .brand_identity import ensure_brand_palette, get_brand_font
    from .brand_kit import get_brand_kit
    from .media import list_media

    raw_palette, raw_font, raw_kit, raw_logos = await asyncio.gather(
        ensure_brand_palette(tenant_id), get_brand_font(tenant_id),
        get_brand_kit(tenant_id), list_media(role="brand_logo", tenant_id=tenant_id),
        return_exceptions=True,
    )

    def _ok(v):
        return None if isinstance(v, BaseException) else v

    kit = _ok(raw_kit) or {}
    logos = _ok(raw_logos) or []
    return {
        "palette": _palette_roles(_ok(raw_palette) or {}),
        "font_theme": str(_ok(raw_font) or "bold"),
        "logo_url": str((logos[0] if logos else {}).get("uri") or ""),
        "handle": str(kit.get("handle") or ""),
        "display_name": str(kit.get("display_name") or ""),
    }


class VideoLayers(BaseModel):
    video_url: str


@router.post("/video/edit/layers")
async def v1_video_layers(body: VideoLayers, tenant_id: TenantDep) -> dict[str, Any]:
    """What the editor needs to open a video: how long it is, what shape it is,
    whether it carries sound, and the brand's own palette, typeface, logo and
    handle. ffprobe reads the header over the network — the file itself is only
    downloaded when an edit is actually rendered."""
    from . import video_edit

    url = (body.video_url or "").strip()
    if not await _fetchable(url):
        raise HTTPException(422, "video_url must be an https URL on a public host")
    info = await video_edit.probe(url)
    if not info.get("width"):
        raise HTTPException(422, "could not read that video")
    return {**info, **await _brand_video_bits(tenant_id)}


class VideoEditRequest(BaseModel):
    video_url: str
    doc: dict = Field(default_factory=dict)
    # A cover is only made when the doc asks for one; the poster a network shows
    # is otherwise whatever frame it picks itself.
    callback_url: str | None = None


async def _run_video_edit(job_id: str, tenant_id, req: VideoEditRequest) -> None:
    import tempfile

    from . import font_themes, video_edit
    from .image_compose import _ANTON, _ARCHIVO
    from .media import storage as media_storage

    job = _JOBS.get(job_id)
    if job is None:
        return
    try:
        doc = video_edit.normalize(req.doc)
        bits = await _brand_video_bits(tenant_id)
        theme = font_themes.resolve(bits.get("font_theme")) or {}
        fonts = {"display": theme.get("display") or _ANTON,
                 "body": theme.get("body") or _ARCHIVO}
        with tempfile.TemporaryDirectory() as td:
            src = f"{td}/source.mp4"
            await _download_capped(req.video_url, src)

            logo_path = None
            if doc.get("logo") and bits.get("logo_url"):
                try:
                    logo_path = f"{td}/logo.png"
                    await _download_capped(bits["logo_url"], logo_path, cap=20 * 1024 * 1024)
                except Exception:  # noqa: BLE001 — a missing logo must not cost the edit
                    _log.warning("video edit: could not fetch the brand logo", exc_info=True)
                    logo_path = None

            out = await video_edit.render(src, doc, fonts=fonts, logo_path=logo_path,
                                          work_dir=td)
            if not out.get("ok"):
                job.update(status="failed", error=str(out.get("reason") or "render failed"),
                           updated_at=_now())
                return

            tenant = str(tenant_id or settings.default_tenant_id)
            url, _ = await asyncio.to_thread(
                media_storage().save_from_path, tenant, out["path"], "edited-video.mp4")
            cover_url = ""
            if out.get("cover_path"):
                try:
                    cover_url, _ = await asyncio.to_thread(
                        media_storage().save_from_path, tenant, out["cover_path"],
                        "edited-cover.jpg")
                except Exception:  # noqa: BLE001 — the video is the point; the still is a bonus
                    _log.warning("video edit: could not store the cover frame", exc_info=True)
        job.update(status="done", updated_at=_now(), result={
            "video_url": url, "cover_url": cover_url, "duration": out["duration"],
            "width": out["width"], "height": out["height"],
        })
    except Exception as exc:  # noqa: BLE001 — a failed edit is reported, never silent
        _log.exception("video edit job %s failed", job_id)
        job.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:400], updated_at=_now())
    finally:
        if req.callback_url and job:
            await _fire_callback(req.callback_url, job)


@router.post("/video/edit", status_code=202)
async def v1_video_edit(body: VideoEditRequest, tenant_id: TenantDep) -> dict[str, Any]:
    """Apply an edit to a finished video. Returns a job_id at once — a 60-second
    reel is a real re-encode, far longer than a request should be held open — then
    poll /v1/jobs/{id} for {video_url, cover_url, duration, width, height}."""
    from . import video_edit

    url = (body.video_url or "").strip()
    if not await _fetchable(url):
        raise HTTPException(422, "video_url must be an https URL on a public host")
    if video_edit.is_noop(body.doc):
        raise HTTPException(422, "nothing to change — a re-encode that changes nothing "
                                 "costs quality and minutes")
    job = _new_job("video_edit", tenant_id)
    _put_job(job)
    _spawn(_run_video_edit(job["id"], tenant_id, body))
    return {"job_id": job["id"], "status": job["status"]}


# --- moving the captions on a reel that is already made ----------------------
#
# The video editor draws ON TOP of a finished file, which is the wrong tool for
# captions: they are already baked into the pixels, so an overlay can cover them
# but never move them. Moving them means drawing them again on a cut without
# them — which is why the pipeline now keeps both (migration 062).

# A headline is two short lines on a phone screen; the assembler caps it at the
# same place. Longer is not a headline, it is a caption in the wrong font.
_HOOK_TEXT_MAX = 80
# Cues handed to the editor for the live preview. A reel runs 30-40; this is a
# cap, not a budget.
_PREVIEW_CUES = 200


class RecaptionRequest(BaseModel):
    production_id: str
    # Percent from the top of the frame; the caption block's CENTRE, the same
    # number the assembler uses. Omitted = leave it where it is.
    caption_y: str | float | None = None
    caption_style: str | None = None     # None = keep the reel's own style
    captions_off: bool = False
    # The big boxed HEADLINE over the first few seconds is a separate element
    # with its own position, so it gets its own controls.
    hook_y: str | float | None = None
    hook_off: bool = False
    # The headline's WORDS. The original was written at render time and never
    # stored, so on a rebuilt reel it is a paraphrase — the owner gets the last
    # word on what their own video says. "" means "leave it as it is"; a reel
    # with no headline gets one.
    hook_text: str | None = None
    callback_url: str | None = None


async def _recaption_row(tenant_id, production_id: str) -> dict[str, Any]:
    """The production, or a 404/422 explaining why it can't be re-captioned."""
    try:
        pid = UUID(str(production_id))
    except (TypeError, ValueError) as e:
        raise HTTPException(422, "production_id must be a uuid") from e
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, mode, status, scenes, clean_cut_url, caption_cues, "
            "caption_hook, caption_style, final_url, caption_rebuild_error "
            "FROM video_productions "
            "WHERE id=$1", pid)
    if row is None:
        raise HTTPException(404, "no such reel")
    return dict(row)


def _broll_inserts(row: dict[str, Any]) -> int:
    """How many B-roll cutaways this reel has composited over it.

    They live in `scenes` alongside the source window — a window entry carries
    source_url/start_s/end_s, an INSERT carries image_url/video_url with its own
    start/end. Creatomate composites them on track 2 over the cut, so they are
    NOT in the captionless cut we stash: redrawing captions on that cut alone
    would hand the owner their reel with every cutaway gone. 33 of James's 43
    reels have them.
    """
    scenes = row.get("scenes")
    if isinstance(scenes, str):
        try:
            scenes = json.loads(scenes)
        except (TypeError, ValueError):
            return 0
    if not isinstance(scenes, list):
        return 0
    return sum(1 for x in scenes
               if isinstance(x, dict)
               and (str(x.get("image_url") or "").startswith("http")
                    or str(x.get("video_url") or "").startswith("http")))


def _recaption_readiness(row: dict[str, Any]) -> dict[str, Any]:
    """Whether this reel's captions can be moved cheaply, and if not, why.

    Every reel rendered before the cut and cues were kept lands in the "no"
    branch. That is worth saying plainly rather than failing later: the owner's
    only route for those is a full re-render, which costs money and comes back a
    slightly different video."""
    cues = row.get("caption_cues")
    if isinstance(cues, str):
        try:
            cues = json.loads(cues)
        except (TypeError, ValueError):
            cues = []
    cues = caption_burn.normalize_cues(cues)
    hook = row.get("caption_hook")
    if isinstance(hook, str):
        try:
            hook = json.loads(hook)
        except (TypeError, ValueError):
            hook = {}
    hook = hook if isinstance(hook, dict) else {}
    has_hook = bool(str(hook.get("text") or "").strip())
    inserts = _broll_inserts(row)
    if inserts:
        # Refusing beats returning the reel without its cutaways. Until the
        # re-draw composites them back in, this is the honest answer.
        return {"can_recaption": False, "cue_count": len(cues), "has_hook": has_hook,
                "hook_text": "", "needs_rebuild": False, "broll_inserts": inserts,
                "reason": f"this reel has {inserts} B-roll cutaway"
                          f"{'' if inserts == 1 else 's'} laid over it, and redrawing "
                          f"its captions would drop them — moving these still needs a "
                          f"full re-render"}

    cut = str(row.get("clean_cut_url") or "").strip()
    if not cut:
        # Made before the cut was kept — but for a reel cut from the brand's own
        # footage everything that went into it was persisted anyway, so the cut
        # can be rebuilt on the first change instead of refusing.
        # A rebuild we already TRIED and refused must not be offered again —
        # the owner would click, wait a minute, and get the same failure.
        failed = str(row.get("caption_rebuild_error") or "").strip()
        if failed:
            return {"can_recaption": False, "cue_count": 0, "has_hook": False,
                    "hook_text": "", "needs_rebuild": False,
                    "reason": f"this reel was made before the captionless cut was kept, "
                              f"and it can't be rebuilt: {failed}"}
        rebuildable, why = caption_backfill.can_rebuild(row)
        if rebuildable:
            return {"can_recaption": True, "cue_count": 0, "has_hook": False,
                    "hook_text": "", "needs_rebuild": True, "reason": ""}
        return {"can_recaption": False, "cue_count": len(cues), "has_hook": has_hook,
                "hook_text": "", "needs_rebuild": False,
                "reason": f"this reel was made before the captionless cut was kept, and "
                          f"{why} — so its captions can only be moved by rendering it again"}
    if not cues and not has_hook:
        return {"can_recaption": False, "cue_count": 0, "has_hook": False,
                "hook_text": "", "needs_rebuild": False,
                "reason": "this reel has no stored captions to move"}
    return {"can_recaption": True, "cue_count": len(cues), "has_hook": has_hook,
            "hook_text": str(hook.get("text") or ""), "needs_rebuild": False,
            "reason": "",
            # The words themselves, so the editor can show them where the owner
            # is putting them instead of a band labelled CAPTIONS HERE. The
            # burned-in ones are pixels until the re-draw runs, so a guide that
            # only says "here" leaves them dragging a label and seeing nothing
            # move.
            "cues": [{"start": c["start"], "end": c["end"],
                      "text": c.get("raw_text") or c.get("text") or ""}
                     for c in cues[:_PREVIEW_CUES]],
            "hook_hold": float(hook.get("hold") or 0.0)}


@router.get("/video/recaption/{production_id}")
async def v1_recaption_layers(production_id: str, tenant_id: TenantDep) -> dict[str, Any]:
    """What the caption editor needs to open: whether this reel's captions can be
    moved at all, how many there are, where they currently sit, and the looks
    available."""
    from .caption_styles import (
        CAPTION_Y_MAX, CAPTION_Y_MIN, HOOK_DEFAULT_Y, list_presets,
    )

    row = await _recaption_row(tenant_id, production_id)
    ready = _recaption_readiness(row)
    opts = {}
    async with acquire(tenant_id) as conn:
        raw = await conn.fetchval(
            "SELECT options FROM video_productions WHERE id=$1", UUID(str(production_id)))
    try:
        opts = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        opts = {}
    return {
        **ready,
        "caption_y": str(opts.get("caption_y") or "") or "78%",
        "captions_off": bool(opts.get("captions_off") or False),
        "hook_y": str(opts.get("hook_y") or "") or HOOK_DEFAULT_Y,
        "hook_off": bool(opts.get("hook_off") or False),
        "caption_style": row.get("caption_style") or "",
        "y_min": CAPTION_Y_MIN, "y_max": CAPTION_Y_MAX,
        "styles": list_presets(),
    }


async def _run_recaption(job_id: str, tenant_id, req: RecaptionRequest) -> None:
    import tempfile

    from .media import storage as media_storage

    job = _JOBS.get(job_id)
    if job is None:
        return
    try:
        row = await _recaption_row(tenant_id, req.production_id)
        ready = _recaption_readiness(row)
        if not (ready["can_recaption"] or req.captions_off):
            job.update(status="failed", error=ready["reason"], updated_at=_now())
            return
        if ready.get("needs_rebuild"):
            # Reconstruct the captionless cut from the footage and transcript this
            # reel was made from, then carry on as if it had been kept all along.
            # Takes the time of one re-cut; it happens once per reel.
            job.update(status="running", updated_at=_now(),
                       result={"kind": "recaption", "stage": "rebuilding"})
            built = await caption_backfill.rebuild(req.production_id, tenant_id)
            if not built.get("ok"):
                job.update(status="failed", updated_at=_now(),
                           error=str(built.get("reason") or "could not rebuild this reel"))
                return
            row = await _recaption_row(tenant_id, req.production_id)

        cues = row.get("caption_cues")
        if isinstance(cues, str):
            cues = json.loads(cues or "[]")
        with tempfile.TemporaryDirectory() as td:
            cut = f"{td}/cut.mp4"
            await _download_capped(str(row.get("clean_cut_url") or ""), cut)
            hook = row.get("caption_hook")
            if isinstance(hook, str):
                hook = json.loads(hook or "{}")
            hook = hook if isinstance(hook, dict) else {}
            typed = (req.hook_text or "").strip()[:_HOOK_TEXT_MAX]
            if typed:
                from .caption_styles import hook_hold_seconds

                # A reel that never had a headline can be given one: the hold is
                # the same few seconds the assembler uses, so the captions still
                # wait for it to clear.
                # No stored hold means this reel never had a headline. Take the
                # length from the last caption flash — there is no duration
                # column on the row, and the cues are the timeline we have.
                last = 0.0
                for c in (cues if isinstance(cues, list) else []):
                    try:
                        last = max(last, float((c or {}).get("end") or 0.0))
                    except (TypeError, ValueError, AttributeError):
                        continue
                hook = {**hook, "text": typed,
                        "hold": float(hook.get("hold") or 0.0)
                                or hook_hold_seconds(last or 12.0),
                        "owner_wrote": True}
            # The frame the owner already has. The stored cut is the footage as
            # it was cut from the source (360x640 on one brand); the assembly
            # upscaled its output to 1080x1920. Re-captioning onto the cut and
            # stopping there returned a third of the resolution.
            target = None
            try:
                _d, _w, _h = await caption_backfill._probe(
                    str(row.get("final_url") or ""))
                if _w > 0 and _h > 0:
                    target = (_w, _h)
            except Exception:  # noqa: BLE001 — a missing probe just means no scale
                target = None
            out = await caption_burn.render(
                cut, cues, target_size=target,
                y_pct=req.caption_y,
                style=req.caption_style or row.get("caption_style") or "",
                off=bool(req.captions_off),
                hook=hook or None,
                hook_y=req.hook_y,
                hook_off=bool(req.hook_off),
                work_dir=td,
            )
            if not out.get("ok"):
                job.update(status="failed", error=str(out.get("reason") or "recaption failed"),
                           updated_at=_now())
                return
            tenant = str(tenant_id or settings.default_tenant_id)
            url, _ = await asyncio.to_thread(
                media_storage().save_from_path, tenant, out["path"], "recaptioned.mp4")
        if typed:
            # So the editor opens on what the owner wrote, not on the paraphrase
            # it replaced.
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    "UPDATE video_productions SET caption_hook=$2, updated_at=now() "
                    "WHERE id=$1", UUID(str(req.production_id)), json.dumps(hook))
        job.update(status="done", updated_at=_now(), result={
            "kind": "recaption", "video_url": url, "duration": out["duration"],
            "width": out["width"], "height": out["height"],
            "caption_y": str(req.caption_y or "") or "78%",
            "captions_off": bool(req.captions_off),
            "hook_y": str(req.hook_y or ""),
            "hook_off": bool(req.hook_off),
            "hook_text": str(hook.get("text") or ""),
        })
    except HTTPException as e:
        job.update(status="failed", error=str(e.detail), updated_at=_now())
    except Exception as e:  # noqa: BLE001
        _log.exception("recaption failed")
        job.update(status="failed", error=f"{type(e).__name__}: {e}", updated_at=_now())
    finally:
        if req.callback_url and job:
            await _fire_callback(req.callback_url, job)


@router.post("/video/recaption", status_code=202)
async def v1_video_recaption(body: RecaptionRequest, tenant_id: TenantDep) -> dict[str, Any]:
    """Re-draw a finished reel's captions somewhere else — or not at all.

    This is a local ffmpeg pass over the cut we already have, not another trip
    through the pipeline: no transcription, no B-roll, no assembly spend, and
    the footage is the same pixels the owner approved. Returns a job_id; poll
    /v1/jobs/{id}."""
    row = await _recaption_row(tenant_id, body.production_id)
    ready = _recaption_readiness(row)
    if not ready["can_recaption"]:
        raise HTTPException(409, ready["reason"])
    if (body.caption_y is None and not body.captions_off and not body.caption_style
            and body.hook_y is None and not body.hook_off
            and not (body.hook_text or "").strip()):
        raise HTTPException(422, "nothing to change — say where the captions or the "
                                 "headline should go, which look they take, or that "
                                 "you want none")
    job = _new_job("recaption", tenant_id)
    _put_job(job)
    _spawn(_run_recaption(job["id"], tenant_id, body))
    return {"job_id": job["id"], "status": job["status"]}


# ───────────────────────────────────────────── the house catalogue ──
# The shared pool every brand may adopt from, and the curator's screen over it.
#
# PLATFORM KEY ONLY, all of it. Every other /v1 route is tenant-bound and a
# brand's key is the right credential for it. This table is not a brand's: a
# layout approved here is forked into every brand that runs short, so a
# tenant-bound key that could approve rows would let one brand push shapes into
# its competitors' libraries. require_curator() is require_service with that one
# extra condition.


async def require_curator(authorization: str | None = Header(default=None)) -> bool:
    """Gate for catalogue writes: the platform key, never a tenant's."""
    if not (settings.service_api_platform_key or "").strip():
        raise HTTPException(503, "no platform key configured")
    if not is_platform_key(authorization):
        raise HTTPException(403, "the house catalogue needs the platform key")
    return True


CuratorDep = Annotated[bool, Depends(require_curator)]

# An uploaded reference is read by a vision model and drawn at full size, so the
# ceiling is the editor's, not a thumbnail's.
_HOUSE_MAX_BYTES = 15 * 1024 * 1024
_HOUSE_MAX_FILES = 12


@router.get("/house-layouts")
async def v1_house_layouts(
    _: CuratorDep, status: str = "", layout_type: str = "", niche: str = "",
    limit: int = 200, offset: int = 0,
    harvested: bool | None = None, run_id: str = "",
) -> dict[str, Any]:
    """The catalogue, with its status and type breakdowns."""
    from . import house_layouts

    return await house_layouts.catalogue(
        status=status.strip(), layout_type=layout_type.strip(), niche=niche.strip(),
        limit=limit, offset=offset, harvested=harvested, run_id=run_id.strip())


@router.get("/house-layouts/for-brand")
async def v1_house_layouts_for_brand(tenant_id: TenantDep, limit: int = 8) -> dict[str, Any]:
    """The approved, drawable catalogue layouts that suit THIS brand, on-niche
    first and latest first within each tier — what a showcase offers, and what
    /v1/generate's house_layout_id then renders.

    Tenant-bound like every brand route (bound key, or platform key plus
    X-Tenant-Id): the niche and the "already drawn" exclusion are this brand's.
    Declared before any /house-layouts/{layout_id} route so the literal segment
    is matched first."""
    from . import house_layouts

    if not 1 <= int(limit) <= house_layouts.FOR_BRAND_MAX:
        raise HTTPException(422, f"limit must be 1..{house_layouts.FOR_BRAND_MAX}")
    return await house_layouts.for_brand(tenant_id, limit=limit)


@router.post("/house-layouts/upload", status_code=201)
async def v1_house_layouts_upload(
    _: CuratorDep,
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    niches: str = Form(""),
    by: str = Form(""),
    approve: bool = Form(True),
    source_url: str = Form(""),
) -> dict[str, Any]:
    """Add reference images to the shared pool by hand.

    This is the ceiling-remover. Learning only from competitors means the
    catalogue can only ever contain shapes competitors already post — a brand
    wanting a kind of post nobody in its niche makes has nowhere to get it. Here
    the curator supplies the shape directly.

    Every file is read INDEPENDENTLY and reported on independently: one image the
    extractor cannot read must not fail the other eleven. `approve` defaults true
    because the person holding the platform key IS the curator — sending your own
    uploads to your own review queue is a step with no reader.

    Only the ARRANGEMENT is kept. The extracted spec holds boxes, roles and
    treatments; the image itself is stored as the reference a curator browses,
    and its colours, words and photographs never reach a generated post.
    """
    from . import house_layouts
    from .media import storage as media_storage

    if not files:
        raise HTTPException(400, "no files")
    if len(files) > _HOUSE_MAX_FILES:
        raise HTTPException(413, f"at most {_HOUSE_MAX_FILES} images per upload")
    tags = [t.strip() for t in niches.split(",") if t.strip()]

    results: list[dict[str, Any]] = []
    for f in files:
        name = (f.filename or "reference")[:120]
        try:
            data = await f.read()
            if not data:
                results.append({"file": name, "ok": False, "reason": "empty file"})
                continue
            if len(data) > _HOUSE_MAX_BYTES:
                results.append({"file": name, "ok": False, "reason": "larger than 15 MB"})
                continue
            # Judge the bytes, not the declared content-type.
            if _sniff_image(data) not in _EDIT_TYPES:
                results.append({"file": name, "ok": False,
                                "reason": "not a PNG, JPEG or WebP image"})
                continue
            # Stored under the platform's own id, not a tenant's: this reference
            # belongs to the catalogue and outlives any brand.
            uri, _ = await asyncio.to_thread(
                media_storage().save, "house", data, name)
            got = await house_layouts.ingest(
                data, title=(title or name), source_url=source_url, image_uri=uri,
                niches=tags, by=by, approve=bool(approve))
            results.append({"file": name, "stored_image_uri": uri, **got})
        except Exception as exc:  # noqa: BLE001 — one bad file, eleven good ones
            _log.exception("house layout ingest failed for %s", name)
            results.append({"file": name, "ok": False, "reason": str(exc)[:160]})

    added = [r for r in results if r.get("ok")]
    return {
        "added": len(added),
        "duplicates": sum(1 for r in added if r.get("duplicate")),
        "failed": len(results) - len(added),
        "results": results,
    }


class HouseReview(BaseModel):
    verdict: str  # approved | rejected | discard
    by: str = ""
    note: str = ""


@router.put("/house-layouts/{layout_id}/review")
async def v1_house_layout_review(
    layout_id: UUID, req: HouseReview, _: CuratorDep,
) -> dict[str, Any]:
    """Approve, reject or discard one catalogue row.

    'rejected' keeps the row and the reason. 'discard' really deletes — the one
    destructive verb in this system, and deliberate: a curated pool that cannot
    forget is not curated."""
    from . import house_layouts

    out = await house_layouts.review(
        str(layout_id), req.verdict.strip(), by=req.by, note=req.note)
    if not out.get("ok"):
        raise HTTPException(422 if "verdict" in str(out.get("reason", "")) else 404,
                            out.get("reason") or "no such catalogue layout")
    return out


class HouseTags(BaseModel):
    title: str | None = None
    niches: list[str] | None = None


@router.put("/house-layouts/{layout_id}/tags")
async def v1_house_layout_tags(
    layout_id: UUID, req: HouseTags, _: CuratorDep,
) -> dict[str, Any]:
    """Rename a catalogue row, or change which niches it suits."""
    from . import house_layouts

    out = await house_layouts.retag(str(layout_id), title=req.title, niches=req.niches)
    if not out.get("ok"):
        raise HTTPException(404, out.get("reason") or "no such catalogue layout")
    return out


@router.post("/house-layouts/{layout_id}/promote", status_code=201)
async def v1_house_layout_promote(
    layout_id: UUID, tenant_id: TenantDep, note: str = "",
) -> dict[str, Any]:
    """Copy one of THIS brand's learned layouts into the catalogue as a candidate.

    Tenant-bound, unlike the rest of this section: a brand offering its own
    competitor-derived layout to the pool is the brand's action. It lands as a
    candidate whatever the caller thinks of it — only the platform key approves."""
    from . import house_layouts

    out = await house_layouts.promote(tenant_id, str(layout_id), note=note)
    if not out.get("promoted"):
        raise HTTPException(422, out.get("reason") or "could not promote that layout")
    return out


@router.post("/house-layouts/retype")
async def v1_house_layouts_retype(_: CuratorDep, limit: int = 1000) -> dict[str, Any]:
    """Name any rows that predate the taxonomy."""
    from . import house_layouts

    return await house_layouts.retype(limit=limit)


@router.post("/house-layouts/adopt")
async def v1_house_layouts_adopt(
    tenant_id: TenantDep, limit: int = 8,
) -> dict[str, Any]:
    """Fork approved catalogue layouts into this brand now, rather than waiting
    for a pick to find the library short."""
    from . import house_layouts

    return await house_layouts.adopt(tenant_id, limit=limit)


# ───────────────────────────────────────── the nightly niche harvest ──
# BM2's automatic intake into the catalogue: ONE harvested image per request,
# behind machine gates (see house_harvest.py). Platform key only, like the rest
# of the catalogue, and tenant-free: no X-Tenant-Id is read and none is needed.
#
# Fails CLOSED, unlike /upload: approve defaults false, nothing undrawable is
# approved, nothing is stored before the gates pass. That is why this is its own
# route rather than a flag on /upload — an engine that did not know the flag
# would ignore it and approve everything usable.
#
# Route order: these paths are literal (/harvest, /harvest/stats,
# /harvest/revoke). The only parameterised siblings are {layout_id}/review
# (PUT), {layout_id}/tags (PUT) and {layout_id}/promote (POST), whose last
# segment is literal too, so none of them can match a harvest path even though
# they are registered first (pinned by test_house_harvest_api).

from fastapi import Query  # noqa: E402 — kept with the routes that use it

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


def _form_bool(name: str, raw: str | None) -> bool:
    val = str(raw if raw is not None else "").strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    raise HTTPException(400, f"{name} must be 'true' or 'false'")


def _form_json(name: str, raw: str | None) -> dict | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        val = json.loads(raw)
    except ValueError:
        raise HTTPException(400, f"{name} is not valid JSON") from None
    if not isinstance(val, dict):
        raise HTTPException(400, f"{name} must be a JSON object")
    return val


def _form_json_any(name: str, raw: str | None) -> dict | list | None:
    """An optional JSON object OR array (blank = absent)."""
    if raw is None or not str(raw).strip():
        return None
    try:
        val = json.loads(raw)
    except ValueError:
        raise HTTPException(400, f"{name} is not valid JSON") from None
    if not isinstance(val, (dict, list)):
        raise HTTPException(400, f"{name} must be a JSON object or array")
    return val


def _form_niches(niche: list[str], niches: list[str]) -> list[str]:
    """The niche tags of a harvest upload, one tag per item.

    `niche` (repeatable, like /harvest/stats takes it) is one tag per field and
    is NEVER split, so a tag containing a comma survives whole. `niches` is the
    legacy encoding: one comma-separated field, split on commas; sent more than
    once, each field is one tag. Both at once is ambiguous and refused.
    """
    one = [n for n in (niche or []) if str(n or "").strip()]
    many = [n for n in (niches or []) if str(n or "").strip()]
    if one and many:
        raise HTTPException(400, "send niche (repeatable) or niches, not both")
    if one:
        return one
    if len(many) == 1:
        return many[0].split(",")
    return many


@router.post("/house-layouts/harvest")
async def v1_house_layouts_harvest(
    _: CuratorDep,
    file: list[UploadFile] | None = File(None),
    source_key: str = Form(""),
    niches: list[str] = Form(default=[]),
    niche: list[str] = Form(default=[]),
    run_id: str = Form(""),
    by: str = Form("harvest:nightly"),
    source_url: str = Form(""),
    title: str = Form(""),
    source_media: str = Form("OTHER"),
    approve: str = Form("false"),
    dry_run: str = Form("false"),
    policy: str = Form(""),
    meta: str = Form(""),
    store_image: str = Form("true"),
    spec_hint: str = Form(""),
) -> dict[str, Any]:
    """Read ONE harvested image and decide what the catalogue does with it.

    200 with a verdict (approved | held | duplicate | rejected | retry) for every
    image it could judge. 400 for a missing or invalid field, 413 over 15 MB,
    415 for bytes that are not PNG, JPEG or WebP. Anything else is a fault the
    caller should retry.

    store_image (default true; blank = default): false keeps the row's spec and
    source link but no copy of the picture. spec_hint: optional JSON (object or
    array) the source already knows about the layout — stored in harvest_meta,
    never trusted for a gate. Over 16 KB of compact UTF-8 JSON it is dropped
    (harvest_meta.spec_hint_dropped='oversize') and the image is still judged;
    only a hint that is not a JSON object/array is a 400.
    """
    from . import house_harvest

    files = [f for f in (file or []) if f is not None]
    if len(files) != 1:
        raise HTTPException(400, "exactly one file per request")
    tags = _form_niches(niche, niches)
    want_approve = _form_bool("approve", approve)
    want_dry = _form_bool("dry_run", dry_run)
    pol = _form_json("policy", policy)
    info = _form_json("meta", meta)
    want_store = (True if not str(store_image or "").strip()
                  else _form_bool("store_image", store_image))
    hint = _form_json_any("spec_hint", spec_hint)
    # Read one byte past the ceiling, so an oversized upload is refused without
    # holding all of it.
    data = await files[0].read(house_harvest.MAX_BYTES + 1)
    try:
        return await house_harvest.harvest_ingest(
            data, source_key=source_key.strip(), niches=tags,
            run_id=run_id, by=by, source_url=source_url, title=title,
            source_media=source_media, approve=want_approve, dry_run=want_dry,
            meta=info, policy=pol, store_image=want_store, spec_hint=hint)
    except house_harvest.HarvestInputError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/house-layouts/harvest/stats")
async def v1_house_layouts_harvest_stats(
    _: CuratorDep, niche: list[str] = Query(default=[]),
) -> dict[str, Any]:
    """Approved / held / family / type coverage for each named niche tag."""
    from . import house_harvest

    return await house_harvest.harvest_stats(niche)


class HarvestRevoke(BaseModel):
    run_id: str
    by: str = "harvest:manual"


@router.post("/house-layouts/harvest/revoke")
async def v1_house_layouts_harvest_revoke(
    req: HarvestRevoke, _: CuratorDep,
) -> dict[str, Any]:
    """Reject every row one harvest run decided by itself. Idempotent; rows a
    human has reviewed since are left alone and counted."""
    from . import house_harvest

    try:
        return await house_harvest.harvest_revoke(req.run_id, req.by)
    except house_harvest.HarvestInputError as exc:
        raise HTTPException(exc.status, str(exc)) from None
