"""Voice Studio API — drop a Drive folder/links → ingest as voice_corpus.

Plain APIRouter (no prefix); main.py wires it with include_router. Auth +
tenant are set by the global middleware. Listing the folder is fast and done
inline (so we can return the file list + a real total); the slow
transcription/extraction runs in a background task tracked by a durable
voice_ingest_jobs row — observed via GET /voice/jobs, never faked.
"""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from . import voice_ingest as V
from .config import settings

router = APIRouter()


def _tenant():
    try:
        from .db import _request_tenant

        t = _request_tenant.get()
    except (LookupError, ImportError):
        t = None
    return t or settings.default_tenant_id


class IngestRequest(BaseModel):
    folder_url: str = ""
    links: list[str] = Field(default_factory=list)
    category: str = "voice_corpus"


@router.post("/voice/ingest-drive", status_code=202)
async def ingest_drive(req: IngestRequest, background: BackgroundTasks) -> dict:
    """List the Drive folder/links, then ingest each file as voice_corpus in
    the background. Returns immediately with a job id + the file list."""
    tenant_id = _tenant()
    category = (req.category or "voice_corpus").strip() or "voice_corpus"
    files, errors = await V.list_sources(req.folder_url, req.links)
    if not files:
        return {
            "started": False,
            "total": 0,
            "errors": errors
            or [{
                "error": "No files found. Check the folder/file is shared with the "
                "Google service account (or is link-accessible)."
            }],
        }
    source = req.folder_url.strip() or f"{len(req.links)} link(s)"
    job_id = await V.create_job(source, category, files, tenant_id)
    background.add_task(V.run_ingest, job_id, files, category, tenant_id)
    return {
        "started": True,
        "job_id": job_id,
        "total": len(files),
        "files": [f.get("name") for f in files][:50],
        "list_errors": errors,
        "note": (
            "Transcribing / extracting in the background — watch progress below. "
            "New voice lands in your corpus as each file finishes."
        ),
    }


@router.get("/voice/jobs")
async def voice_jobs() -> dict:
    return {"jobs": await V.recent_jobs(_tenant(), limit=10)}


@router.get("/voice/corpus")
async def voice_corpus() -> dict:
    return await V.corpus_summary(_tenant())


__all__ = ["router"]
