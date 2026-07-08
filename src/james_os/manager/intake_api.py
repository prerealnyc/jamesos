"""Intake interview API — bm2.0's onboarding routes (routers/onboarding.py)
on the manager surface. Interview next/suggest/answer, the batch auto-answer
(202 + status poll, 409 while one is in flight — the manager_api discover
pattern over job_runs), and research-seed persistence into
tenants.config['research_seed'].

The research fan-out routes (discover/confirm/status) live with the
researcher port, not here — this file owns only the interview loop.

Failure semantics (the bm2.0 run_agent wrapper): an agent that dies mid-run
has already committed its failed job_runs row (runs.finish_run) — the HTTP
layer surfaces a 502 with the reason instead of a fake 200.
"""

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, intake_agent
from .sources_api import require_manager_v2

logger = logging.getLogger("manager.intake")

router = APIRouter(tags=["intake"], dependencies=[Depends(require_manager_v2)])


def _502(agent: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"{agent} failed: {str(exc)[:300]} (the failed run is on record in job_runs)",
    )


# ── the interview loop ──────────────────────────────────────────────────────


@router.get("/manager/intake/interview/next")
async def interview_next(budget: int = Query(default=3, ge=1, le=25)) -> dict:
    """The Interviewer agent: materializes the question bank on demand,
    sweeps already-settled questions, and returns the next batch — with a
    'why' per question and the tenant's onboarding_status."""
    try:
        return await intake_agent.interview_next(session_budget=budget, trigger="manual")
    except Exception as exc:  # noqa: BLE001
        raise _502(intake_agent.AGENT_INTERVIEWER, exc) from exc


@router.post("/manager/intake/questions/{question_id}/suggest")
async def suggest_answer(question_id: UUID) -> dict:
    """Answerer agent drafts a researched suggestion for one question. Does
    not write anything — the user accepts/edits via the /answer endpoint."""
    try:
        return await intake_agent.suggest_answer(question_id=question_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        raise _502(intake_agent.AGENT_ANSWERER, exc) from exc


class AnswerBody(BaseModel):
    # intake_agent parses free text (list fields split on ,;/newlines)
    answer: str = Field(min_length=1)


@router.post("/manager/intake/questions/{question_id}/answer")
async def answer_question(question_id: UUID, body: AnswerBody) -> dict:
    """Records the human answer: profile write (source=user_stated),
    aspirational-peer seeding, question -> 'confirmed', memory filing."""
    try:
        return await intake_agent.answer_question(question_id=question_id, answer=body.answer)
    except ValueError as err:
        # unknown/stale question id (e.g. after a reseed) is a client 404
        raise HTTPException(status_code=404, detail=str(err)) from err


# ── batch auto-answer: 202 + poll (manager_api discover pattern) ────────────

# tenant -> in-flight batch task. In-process truth: entries are added
# synchronously in the trigger handler (so a double trigger 409s
# deterministically) and reaped when the task finishes.
_ANSWER_TASKS: dict[str, asyncio.Task] = {}


def _task_alive(key: str) -> bool:
    task = _ANSWER_TASKS.get(key)
    return task is not None and not task.done()


async def _running(conn, agent: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM job_runs WHERE agent = $1 AND status = 'running' "
            "AND started_at > now() - interval '30 minutes' LIMIT 1",
            agent,
        )
    )


@router.post("/manager/intake/questions/auto-answer", status_code=202)
async def auto_answer() -> dict:
    """Draft AI suggestions for every open question in the background; poll
    GET .../auto-answer/status for the review list. Drafts are not recorded —
    the human accepts/edits each on the review screen. 409 while a batch is
    already in flight (in-process task, or another worker's committed
    'running' job_runs row)."""
    async with db.acquire() as conn:
        tenant_id = await conn.fetchval(
            "SELECT current_setting('app.current_tenant', true)::uuid"
        )
        if await _running(conn, intake_agent.BATCH_AGENT):
            raise HTTPException(status_code=409, detail="auto-answer already running")
    key = str(tenant_id)
    if _task_alive(key):
        raise HTTPException(status_code=409, detail="auto-answer already running")

    async def _bg() -> None:
        try:
            await intake_agent.run_answer_batch(tenant_id)
        except Exception:  # noqa: BLE001 — recorded on the job_runs row
            logger.exception("background auto-answer batch failed for tenant %s", tenant_id)

    task = asyncio.create_task(_bg())
    _ANSWER_TASKS[key] = task
    task.add_done_callback(
        lambda t: _ANSWER_TASKS.pop(key, None) if _ANSWER_TASKS.get(key) is t else None
    )
    return {"state": "running", "poll": "/manager/intake/questions/auto-answer/status"}


@router.get("/manager/intake/questions/auto-answer/status")
async def auto_answer_status() -> dict:
    """Latest auto-answer batch run + its drafts (once succeeded). Drafts for
    questions no longer open (answered/confirmed/dismissed since the batch)
    are filtered out, so a stale run can never re-surface a handled question."""
    async with db.acquire() as conn:
        tenant_id = await conn.fetchval(
            "SELECT current_setting('app.current_tenant', true)::uuid"
        )
        row = await conn.fetchrow(
            """SELECT status, output, error, started_at, finished_at FROM job_runs
               WHERE agent = $1 ORDER BY started_at DESC LIMIT 1""",
            intake_agent.BATCH_AGENT,
        )
    # an in-process task whose 'running' row hasn't landed yet still reports
    # running, so a poller never mistakes the previous run's terminal state
    # for the new run's
    if _task_alive(str(tenant_id)) and (row is None or row["status"] != "running"):
        return {"state": "running", "drafts": [], "count": 0}
    if row is None:
        return {"state": "none", "drafts": [], "count": 0}
    output = row["output"]
    if isinstance(output, str):
        output = json.loads(output or "{}")
    output = output or {}
    drafts = output.get("drafts") or []
    if drafts:
        async with db.acquire() as conn:
            open_ids = {
                str(r["id"])
                for r in await conn.fetch(
                    "SELECT id FROM brand_questions WHERE status IN ('open','asked')"
                )
            }
        drafts = [d for d in drafts if d.get("question_id") in open_ids]
    return {
        "state": row["status"],  # running | succeeded | failed
        "drafts": drafts,
        "count": len(drafts),
        "considered": int(output.get("considered") or 0),
        "started_at": row["started_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "error": row["error"] or "",
    }


# ── research seed persistence ───────────────────────────────────────────────


class SeedBody(BaseModel):
    name: str = Field(default="", max_length=200)
    entity_type: str = ""
    website: str | None = None
    socials: list[str] = Field(default_factory=list)


@router.post("/manager/intake/research-seed")
async def set_research_seed(body: SeedBody) -> dict:
    """Persist the discover seed {name, entity_type, website, socials} into
    tenants.config['research_seed'] (merged with what's stored) so the
    research fan-out can rebuild its primary-source sets without re-asking."""
    seed = await intake_agent.set_research_seed(body.model_dump())
    return {"research_seed": seed}


@router.get("/manager/intake/research-seed")
async def get_research_seed() -> dict:
    return {"research_seed": await intake_agent.get_research_seed()}
