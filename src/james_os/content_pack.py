"""White paper → content PACK: one click fans a paper into a SERIES.

James's requirement (verbatim): white papers "also will be used for content
— can make podcasts from them etc". The single "→ Create post" button made
ONE post; a pack makes the week: N text+image posts + M reels, each built
from a DIFFERENT angle of the paper, all through the same battle-tested
autopilot machinery (voice engine, QA gate, photo gates, video templates)
and all landing in the Approval Queue.

Runs as a background job; poll for staged progress.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from uuid import UUID

from .db import acquire
from .llm import get_llm

_ANGLES_SYSTEM = """You are the content strategist reading a finished white
paper (or research brief). Extract the {n_posts} strongest POST angles and
{n_reels} strongest REEL angles — each a DISTINCT, specific claim or insight
from the paper, phrased as a content topic in the AUTHOR'S direction (a
position, not a summary; no two angles may cover the same point).

For each angle:
* title — internal label (≤ 90 chars).
* topic — the content brief a writer works from: the specific claim +
  the supporting fact/number from the paper (≤ 220 chars).

Return STRICT JSON:
{{"posts": [{{"title": str, "topic": str}}, ...],
  "reels": [{{"title": str, "topic": str}}, ...]}}
"""


async def _read_source(doc_id: UUID, tenant_id: UUID | None) -> dict:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT filename, extracted_text, sensitivity "
            "FROM document_metadata WHERE id=$1", doc_id)
    if not row or not (row["extracted_text"] or "").strip():
        raise ValueError("source document not found or has no text")
    if (row["sensitivity"] or "") == "NDA-Protected":
        raise ValueError("this document is NDA-Protected — it can't fan out "
                         "into audience-facing content")
    return dict(row)


async def generate_content_pack(
    doc_id: UUID, tenant_id: UUID | None = None,
    posts: int = 3, reels: int = 2, on_stage=None,
) -> dict:
    """The pack: angles → posts (text+image, alternating designed/photo
    looks) → reels (alternating the two locked video templates). Per-item
    failures are recorded, never fatal — a 4/5 pack still ships."""
    def _stage(s: str) -> None:
        if on_stage:
            on_stage(s)

    posts = max(0, min(6, int(posts)))
    reels = max(0, min(4, int(reels)))

    _stage("angles")
    row = await _read_source(doc_id, tenant_id)
    out = await get_llm().complete_json(
        system=_ANGLES_SYSTEM.format(n_posts=posts or 1, n_reels=reels or 1),
        messages=[{"role": "user", "content":
                   f'<paper filename="{row["filename"]}">\n'
                   f'{row["extracted_text"][:24000]}\n</paper>'}],
        max_tokens=1500, temperature=0.5,
    )
    if not isinstance(out, dict):
        raise RuntimeError("could not extract angles from the paper")
    post_ideas = [i for i in (out.get("posts") or [])
                  if isinstance(i, dict) and (i.get("topic") or "").strip()][:posts]
    reel_ideas = [i for i in (out.get("reels") or [])
                  if isinstance(i, dict) and (i.get("topic") or "").strip()][:reels]
    if not post_ideas and not reel_ideas:
        raise RuntimeError("the paper yielded no usable content angles")

    from .autopilot_bulk import _make_text_post, _make_video

    made_posts: list[dict] = []
    errors: list[str] = []
    _stage("posts")
    for i, idea in enumerate(post_ideas):
        try:
            # Alternate the two post looks, same as an autopilot batch.
            kind = "designed" if i % 2 == 0 else "james"
            await _make_text_post(idea, "instagram", tenant_id, image_kind=kind)
            made_posts.append({"title": idea.get("title") or idea["topic"][:60]})
        except Exception as e:  # noqa: BLE001 — one bad post can't kill the pack
            errors.append(f"post '{(idea.get('title') or '')[:40]}': {e}")

    made_reels: list[dict] = []
    _stage("reels")
    for i, idea in enumerate(reel_ideas):
        try:
            # Alternate the two locked reel templates (full-frame / split).
            tmpl = "full" if i % 2 == 0 else "split"
            await _make_video(idea, "instagram", tenant_id, video_template=tmpl)
            made_reels.append({"title": idea.get("title") or idea["topic"][:60],
                               "template": tmpl})
        except Exception as e:  # noqa: BLE001
            errors.append(f"reel '{(idea.get('title') or '')[:40]}': {e}")

    return {
        "source_filename": row["filename"],
        "posts_queued": len(made_posts),
        "reels_started": len(made_reels),
        "posts": made_posts,
        "reels": made_reels,
        "errors": errors,
    }


# ── background job wrapper (start/poll, per-doc dedupe) ──

_PACK_JOBS: dict[str, dict] = {}
_PACK_OWNER: dict[str, str] = {}   # job_id -> tenant, for the cross-tenant poll guard
_PACK_TASKS: set = set()
_PACK_JOBS_MAX = 40


def _prune_jobs() -> None:
    if len(_PACK_JOBS) <= _PACK_JOBS_MAX:
        return
    finished = [k for k, v in _PACK_JOBS.items() if v.get("status") != "running"]
    for k in finished[: len(_PACK_JOBS) - _PACK_JOBS_MAX]:
        _PACK_JOBS.pop(k, None)
        _PACK_OWNER.pop(k, None)


def start_content_pack_job(
    doc_id: UUID, tenant_id: UUID | None = None,
    posts: int = 3, reels: int = 2,
) -> str:
    from .db import _request_tenant
    tenant_id = tenant_id or _request_tenant.get()
    for jid, j in _PACK_JOBS.items():
        if j.get("doc_id") == str(doc_id) and j.get("status") == "running":
            return jid
    job_id = _uuid.uuid4().hex[:12]
    _PACK_JOBS[job_id] = {"status": "running", "stage": "angles",
                          "doc_id": str(doc_id)}
    _PACK_OWNER[job_id] = str(tenant_id or "")
    _prune_jobs()

    def _on_stage(stage: str) -> None:
        j = _PACK_JOBS.get(job_id)
        if j and j.get("status") == "running":
            j["stage"] = stage

    async def _run() -> None:
        try:
            result = await generate_content_pack(
                doc_id, tenant_id, posts=posts, reels=reels,
                on_stage=_on_stage)
            _PACK_JOBS[job_id] = {"status": "done", "doc_id": str(doc_id),
                                  "result": result}
        except Exception as e:  # noqa: BLE001 — surfaced via the poll
            _PACK_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                  "error": str(e)[:500]}
        finally:
            j = _PACK_JOBS.get(job_id)
            if j and j.get("status") == "running":
                _PACK_JOBS[job_id] = {"status": "failed", "doc_id": str(doc_id),
                                      "error": "run was canceled — retry"}

    task = asyncio.create_task(_run())
    _PACK_TASKS.add(task)
    task.add_done_callback(_PACK_TASKS.discard)
    return job_id


def get_content_pack_job(job_id: str, tenant_id=None) -> dict | None:
    job = _PACK_JOBS.get(job_id)
    if job is None:
        return None
    owner = _PACK_OWNER.get(job_id)
    if tenant_id is not None and owner and owner != str(tenant_id):
        return None   # cross-tenant poll — pretend it doesn't exist
    return job


__all__ = [
    "generate_content_pack", "start_content_pack_job", "get_content_pack_job",
]
