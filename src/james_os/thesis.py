"""Weekly thesis → intelligence → white paper.

The CEO's weekly thesis (voice memo, video, or text — already transcribed and
filed as a category='thesis' Knowledge-Base document) is DEVELOPED in one
pipeline:

  1. read      — extract the thesis's core theme + claims (fast LLM pass)
  2. research  — run topic intelligence on the theme (coverage check → plan →
                 web research → briefs filed back into memory)
  3. write     — generate a white paper that ARGUES the thesis (the thesis is
                 the paper's primary source [T]; facts ground in corpus [n])

Runs as a background job (the gateway kills ~50s synchronous calls); poll for
staged progress. Every artifact persists to the Knowledge Base, so the weekly
thesis compounds into memory the content engine can draw from.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from uuid import UUID

from .db import acquire
from .llm import get_llm

_CLAIMS_SYSTEM = """You are the chief of staff reading the CEO's weekly
thesis (a transcribed voice memo or note). Extract:

* theme — the ONE core theme, phrased as a specific research topic
  (≤ 90 chars; e.g. "NYC outer-borough land prices decoupling from Manhattan",
  never "real estate").
* claims — the 3-5 distinct assertions the thesis makes (each ≤ 160 chars,
  in the CEO's direction — these are positions, not questions).
* whitepaper_topic — the title-ready topic a white paper arguing this thesis
  should cover (≤ 110 chars).

Return STRICT JSON:
{"theme": str, "claims": [str, ...], "whitepaper_topic": str}
"""


async def _read_thesis(doc_id: UUID, tenant_id: UUID | None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, filename, extracted_text, category, sensitivity "
            "FROM document_metadata WHERE id=$1", doc_id)
    if not row or not (row["extracted_text"] or "").strip():
        return None
    # Only actual theses can be developed — this endpoint must never become
    # a side door that reads an arbitrary document into a persisted paper.
    if row["category"] != "thesis":
        raise ValueError("that document is not a thesis")
    # NDA-Protected text must never feed a persisted research artifact
    # (mirrors drop_nda_protected on the corpus path — same laundering risk).
    if (row["sensitivity"] or "") == "NDA-Protected":
        raise ValueError("this thesis is NDA-Protected — it can't be "
                         "developed into a shareable paper")
    return dict(row)


async def _extract_claims(text: str) -> dict:
    out = await get_llm().complete_json(
        system=_CLAIMS_SYSTEM,
        messages=[{"role": "user", "content": text[:24000]}],
        max_tokens=700, temperature=0.3,
    )
    if not isinstance(out, dict):
        return {}
    return {
        "theme": str(out.get("theme") or "").strip()[:120],
        "claims": [str(c).strip()[:200] for c in (out.get("claims") or [])
                   if str(c).strip()][:5],
        "whitepaper_topic": str(out.get("whitepaper_topic") or "").strip()[:140],
    }


async def develop_thesis(
    doc_id: UUID, tenant_id: UUID | None = None,
    on_stage=None,
) -> dict:
    """The full pipeline. `on_stage(stage: str)` is called as each stage
    starts so the job wrapper can surface progress."""
    def _stage(s: str) -> None:
        if on_stage:
            on_stage(s)

    _stage("reading")
    row = await _read_thesis(doc_id, tenant_id)
    if not row:
        raise ValueError("thesis document not found or has no extractable text")
    extracted = await _extract_claims(row["extracted_text"])
    theme = extracted.get("theme") or row["filename"]
    claims = extracted.get("claims") or []
    wp_topic = extracted.get("whitepaper_topic") or theme

    _stage("researching")
    from .intelligence import build_topic_intelligence
    # force=True: a weekly thesis always deserves FRESH research — never skip
    # the web sweep just because last week's corpus already covers the theme.
    intel = await build_topic_intelligence(
        topic=theme, force=True, tenant_id=tenant_id)

    _stage("writing")
    from .whitepaper import generate_whitepaper
    paper = await generate_whitepaper(
        topic=wp_topic,
        goal="argue and develop the author's weekly thesis for publication",
        tenant_id=tenant_id,
        thesis_doc_id=doc_id,
    )

    return {
        "thesis_doc_id": str(doc_id),
        "thesis_filename": row["filename"],
        "theme": theme,
        "claims": claims,
        "intelligence": {
            "topic": intel.get("topic"),
            "coverage": intel.get("coverage"),
            "briefs_saved": sum(1 for g in (intel.get("gathered") or [])
                                if g.get("saved")),
            "synthesis_file": (intel.get("synthesis") or {}).get("file"),
        },
        "whitepaper": {
            "title": paper.get("title"),
            "sections": len(paper.get("sections") or []),
            "filename": (paper.get("file") or {}).get("filename"),
            "document_id": (paper.get("file") or {}).get("id"),
            "low_grounding": paper.get("low_grounding"),
        },
    }


# ── background job wrapper (mirrors whitepaper's start/poll pattern) ──

_DEV_JOBS: dict[str, dict] = {}
_DEV_TASKS: set = set()   # strong refs so detached jobs aren't GC'd
_DEV_JOBS_MAX = 40


def _prune_jobs() -> None:
    if len(_DEV_JOBS) <= _DEV_JOBS_MAX:
        return
    finished = [k for k, v in _DEV_JOBS.items() if v.get("status") != "running"]
    for k in finished[: len(_DEV_JOBS) - _DEV_JOBS_MAX]:
        _DEV_JOBS.pop(k, None)


def start_develop_job(doc_id: UUID, tenant_id: UUID | None = None) -> str:
    """Kick a detached thesis-development run; returns a job id to poll.
    Tenant is resolved HERE (request context still alive) and bound into the
    job — detached tasks must never rely on the contextvar surviving."""
    from .db import _request_tenant
    tenant_id = tenant_id or _request_tenant.get()
    # One development per thesis at a time — a second click returns the
    # running job instead of paying for a duplicate research+writer run.
    for jid, j in _DEV_JOBS.items():
        if j.get("doc_id") == str(doc_id) and j.get("status") == "running":
            return jid
    job_id = _uuid.uuid4().hex[:12]
    _DEV_JOBS[job_id] = {"status": "running", "stage": "reading",
                         "doc_id": str(doc_id)}
    _prune_jobs()

    def _on_stage(stage: str) -> None:
        j = _DEV_JOBS.get(job_id)
        if j and j.get("status") == "running":
            j["stage"] = stage

    async def _run() -> None:
        try:
            result = await develop_thesis(doc_id, tenant_id, on_stage=_on_stage)
            _DEV_JOBS[job_id] = {"status": "done", "doc_id": str(doc_id),
                                 "result": result}
        except Exception as e:  # noqa: BLE001 — surfaced via the poll
            _DEV_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                 "error": str(e)[:500]}
        finally:
            # A cancelled task (BaseException) would otherwise stay "running"
            # forever — and the per-doc dedupe would block this thesis until
            # a restart.
            j = _DEV_JOBS.get(job_id)
            if j and j.get("status") == "running":
                _DEV_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                     "error": "run was canceled — retry"}

    task = asyncio.create_task(_run())
    _DEV_TASKS.add(task)
    task.add_done_callback(_DEV_TASKS.discard)
    return job_id


def get_develop_job(job_id: str) -> dict | None:
    return _DEV_JOBS.get(job_id)


__all__ = ["develop_thesis", "start_develop_job", "get_develop_job"]
