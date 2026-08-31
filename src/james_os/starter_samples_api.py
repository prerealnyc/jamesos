"""Starter samples — the demonstration API.

Classify the brand's own photos, and turn the best ones into EXAMPLE posts the
owner reacts to instead of a template-picking form. Every route is tenant-scoped
via the request tenant (X-Tenant-Id), same as the competitor engine.

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

router = APIRouter()

# In-memory job stores (mirrors competitors_api): only FINISHED entries are
# pruned, so a running job is never evicted out from under its own task.
_CLASSIFY_JOBS: dict[str, dict] = {}
_SAMPLE_JOBS: dict[str, dict] = {}


def _tenant() -> UUID | None:
    from .db import _request_tenant
    try:
        return _request_tenant.get()
    except LookupError:
        return None


def _bind_tenant(tid: UUID | None) -> None:
    """A background task runs in a fresh context where the request's tenant
    contextvar is gone — so any internal DB call that doesn't take an explicit
    tenant would fall back to settings.default_tenant_id (the legacy tenant) and
    write under the WRONG brand. Re-bind the captured tenant on the task's own
    context so the whole job stays tenant-correct."""
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
async def photos_classify(req: ClassifyRequest, background: BackgroundTasks) -> dict:
    """Judge every hero photo for templatizability + which layouts it fits.
    Idempotent: skips photos already judged at the current rubric (unless force)."""
    tid = _tenant()
    job_id = str(uuid4())
    _CLASSIFY_JOBS[job_id] = {"status": "running"}
    _prune(_CLASSIFY_JOBS)

    async def _run() -> None:
        _bind_tenant(tid)
        try:
            res = await hero_templatize.classify_brand_photos(tid, force=req.force)
            _CLASSIFY_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _CLASSIFY_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/photos/classify/{job_id}")
async def photos_classify_job(job_id: str) -> dict:
    return {"job_id": job_id, **(_CLASSIFY_JOBS.get(job_id) or {"status": "unknown"})}


@router.get("/v1/photos/templatizable")
async def photos_templatizable(limit: int = 30) -> dict:
    """The usable photos, best first — each with its stable id, url and the
    ranked layouts it fits. Reads cached verdicts (run classify first)."""
    return {"photos": await hero_templatize.templatizable_photos(_tenant(), limit=limit)}


class SamplesRequest(BaseModel):
    n: int = 6
    grade: bool = True


@router.post("/v1/samples/generate", status_code=202)
async def samples_generate(req: SamplesRequest, background: BackgroundTasks) -> dict:
    """Build example posts from the best templatizable photos — classify (if
    needed) → auto-match a fitting layout → render on the exact photo →
    self-grade. Drops real pending posts into the queue, tagged as samples."""
    tid = _tenant()
    n = max(1, min(req.n, 10))
    job_id = str(uuid4())
    _SAMPLE_JOBS[job_id] = {"status": "running"}
    _prune(_SAMPLE_JOBS)

    async def _run() -> None:
        _bind_tenant(tid)
        try:
            res = await hero_templatize.generate_samples(tid, n=n, grade=req.grade)
            _SAMPLE_JOBS[job_id] = {"status": "done", **res}
        except Exception as e:  # noqa: BLE001
            _SAMPLE_JOBS[job_id] = {"status": "failed", "error": str(e)[:300]}

    background.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/samples/generate/{job_id}")
async def samples_generate_job(job_id: str) -> dict:
    return {"job_id": job_id, **(_SAMPLE_JOBS.get(job_id) or {"status": "unknown"})}


@router.get("/v1/samples")
async def samples_list(limit: int = 20) -> dict:
    """The example posts we've built, best first (DB-backed, survives restarts)."""
    return {"samples": await hero_templatize.list_samples(_tenant(), limit=limit)}


__all__ = ["router"]
