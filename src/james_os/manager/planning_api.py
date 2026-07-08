"""Planning endpoints — the strategist brain surface (P2). Ported from bm2.0
routers/planning.py (weekly-plan / activate / brief; the eye-trigger routes
already live in manager_api.py) onto tenant-scoped routes.

Failure semantics match manager_api.py: an agent that dies mid-run has
already committed its failed job_runs row — the HTTP layer surfaces a 502
with the reason instead of a fake 200. Activation maps the strategist's
ValueErrors onto the donor's codes: unknown plan -> 404, activating a plan a
newer prescription has overtaken -> 409 (D5: it would overthrow the newer
plan), bad selections -> 422.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from .sources_api import require_manager_v2

router = APIRouter(tags=["manager-planning"], dependencies=[Depends(require_manager_v2)])


def _502(agent: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"{agent} failed: {str(exc)[:300]} (the failed run is on record in job_runs)",
    )


def _loads(value) -> object:
    return json.loads(value) if isinstance(value, str) else value


@router.post("/manager/plan/weekly")
async def weekly_plan() -> dict:
    """Run the Strategist now: one strategy-tier call over gathered state,
    persisted as a proposed prescriptions row. Activation stays human."""
    from . import strategist

    try:
        return await strategist.run(config={"trigger": "manual"}, mode="weekly_plan")
    except Exception as exc:  # noqa: BLE001
        raise _502("strategist", exc) from exc


@router.get("/manager/plan/latest")
async def latest_plan() -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, week_of, status, plan, growth_actions, accepted_items, created_at
               FROM prescriptions ORDER BY created_at DESC LIMIT 1"""
        )
    if row is None:
        return {"plan": None}
    return {
        "plan": {
            "plan_id": str(row["id"]),
            "week_of": row["week_of"].isoformat(),
            "status": row["status"],
            "items": _loads(row["plan"]) or [],
            "growth_actions": _loads(row["growth_actions"]) or [],
            "accepted_items": _loads(row["accepted_items"]) or [],
            "created_at": row["created_at"].isoformat(),
        }
    }


class ActivateBody(BaseModel):
    """PRD R2.3: partial acceptance — activate only the selected plan items
    (0-based indices into the plan's item list). Omit for accept-all."""

    item_indices: list[int] | None = None


@router.post("/manager/plan/{plan_id}/activate")
async def activate_plan(plan_id: str, body: ActivateBody | None = None) -> dict:
    from . import strategist

    try:
        UUID(plan_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found") from err
    try:
        return await strategist.activate(None, plan_id, item_indices=body.item_indices if body else None)
    except ValueError as err:
        msg = str(err)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from err
        if "superseded" in msg:
            # activating an overtaken plan would overthrow the newer one (D5)
            raise HTTPException(status_code=409, detail=msg) from err
        raise HTTPException(status_code=422, detail=msg) from err


@router.get("/manager/brief")
async def morning_brief() -> dict:
    """The morning brief: deterministic assembly (no LLM, donor v0) — cheap
    enough to read on every dashboard load."""
    from . import strategist

    return await strategist.brief()
