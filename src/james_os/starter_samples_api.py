"""Starter samples — the demonstration API.

Classify the brand's own photos, and turn the best ones into EXAMPLE posts the
owner reacts to instead of a template-picking form. Every route is a /v1 service
route: the auth middleware treats /v1 as public and the router's own
require_service (TenantDep) validates the key and resolves the tenant from the
X-Tenant-Id header — exactly like /v1/queue. (Reading _request_tenant here would
be None, because the middleware never sets it for /v1.)

    POST /v1/photos/classify        judge every hero photo (background)
    GET  /v1/photos/templatizable   the usable photos + the layouts they fit
    POST /v1/samples/generate       build example posts from the best photos (bg)
    GET  /v1/samples/generate/{id}  poll a generate job
    GET  /v1/samples                the example posts we've built, best first
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from . import hero_templatize
from .api_v1 import TenantDep

router = APIRouter()

# In-memory job stores (mirrors competitors_api): only FINISHED entries are
# pruned, so a running job is never evicted out from under its own task.
_CLASSIFY_JOBS: dict[str, dict] = {}
_SAMPLE_JOBS: dict[str, dict] = {}


def _bind_tenant(tid: UUID) -> None:
    """A background task runs in a fresh context where the request's tenant is
    gone — so any internal DB call that doesn't take an explicit tenant would
    fall back to settings.default_tenant_id (the legacy tenant) and write under
    the WRONG brand. Re-bind the request tenant on the task's own context so the
    whole job stays tenant-correct."""
    from .db import set_request_tenant
    set_request_tenant(tid)


def _prune(store: dict[str, dict], keep: int = 40) -> None:
    if len(store) <= keep:
        return
    for key in [k for k, v in list(store.items()) if v.get("status") in ("done", "failed")]:
        if len(store) <= keep:
            break
        store.pop(key, None)


class ClassifyRequest(BaseModel):
    force: bool = False


@router.post("/v1/photos/classify", status_code=202)
async def photos_classify(
    req: ClassifyRequest, background: BackgroundTasks, tenant: TenantDep
) -> dict:
    """Judge every hero photo for templatizability + which layouts it fits.
    Idempotent: skips photos already judged at the current rubric (unless force)."""
    job_id = str(uuid4())
    _CLASSIFY_JOBS[job_id] = {"status": "running"}
    _prune(_CLASSIFY_JOBS)

    async def _run() -> None:
        _bind_tenant(tenant)
        try:
            res = await hero_templatize.classify_brand_photos(tenant, force=req.force)
            _CLASSIFY_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _CLASSIFY_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/photos/classify/{job_id}")
async def photos_classify_job(job_id: str, tenant: TenantDep) -> dict:
    return {"job_id": job_id, **(_CLASSIFY_JOBS.get(job_id) or {"status": "unknown"})}


@router.get("/v1/photos/templatizable")
async def photos_templatizable(tenant: TenantDep, limit: int = 30) -> dict:
    """The usable photos, best first — each with its stable id, url and the
    ranked layouts it fits. Reads cached verdicts (run classify first)."""
    return {"photos": await hero_templatize.templatizable_photos(tenant, limit=limit)}


class SamplesRequest(BaseModel):
    n: int = 6
    grade: bool = True


@router.post("/v1/samples/generate", status_code=202)
async def samples_generate(
    req: SamplesRequest, background: BackgroundTasks, tenant: TenantDep
) -> dict:
    """Build example posts from the best templatizable photos — classify (if
    needed) → auto-match a fitting layout → render on the exact photo →
    self-grade. Drops real pending posts into the queue, tagged as samples."""
    n = max(1, min(req.n, 10))
    job_id = str(uuid4())
    _SAMPLE_JOBS[job_id] = {"status": "running"}
    _prune(_SAMPLE_JOBS)

    async def _run() -> None:
        _bind_tenant(tenant)
        try:
            res = await hero_templatize.generate_samples(tenant, n=n, grade=req.grade)
            _SAMPLE_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _SAMPLE_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/samples/generate/{job_id}")
async def samples_generate_job(job_id: str, tenant: TenantDep) -> dict:
    return {"job_id": job_id, **(_SAMPLE_JOBS.get(job_id) or {"status": "unknown"})}


@router.post("/v1/samples/clone", status_code=202)
async def samples_clone(
    req: SamplesRequest, background: BackgroundTasks, tenant: TenantDep
) -> dict:
    """Clone the top competitor templates into OUR versions — extract each
    design → fill the brand's copy into its slots → render on a brand photo (or
    an AI placeholder if the brand has none). Drops pending samples in the queue,
    listed alongside the photo samples by /v1/samples."""
    from . import template_clone

    n = max(1, min(req.n, 10))
    job_id = str(uuid4())
    _SAMPLE_JOBS[job_id] = {"status": "running"}
    _prune(_SAMPLE_JOBS)

    async def _run() -> None:
        _bind_tenant(tenant)
        try:
            res = await template_clone.generate_template_samples(tenant, n=n, grade=req.grade)
            _SAMPLE_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _SAMPLE_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/samples")
async def samples_list(tenant: TenantDep, limit: int = 20) -> dict:
    """The example posts we've built, best first (DB-backed, survives restarts)."""
    return {"samples": await hero_templatize.list_samples(tenant, limit=limit)}


@router.get("/v1/competitors/templates")
async def competitor_templates(tenant: TenantDep, limit: int = 40) -> dict:
    """Split the scraped competitor stills into DESIGNED TEMPLATES (reusable
    layouts worth rebuilding) vs REGULAR posts (plain photos) — counts + the top
    templates. Derived from the analysis we already hold; no new vision calls."""
    from . import template_clone
    return await template_clone.separate_posts(tenant, limit=limit)


__all__ = ["router"]
