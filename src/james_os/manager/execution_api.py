"""The execution spine's HTTP surface — the owner's approve → do → progress →
impact loop over opportunities. Ported from bm2.0 routers/execution.py +
routers/queue.py (approve/reject) onto tenant-scoped manager routes.

    POST /manager/opportunities/{action_id}/execute   act on an opportunity (draft the asset)
    POST /manager/work-orders/{id}/approve            D5 human gate; learns + attempts the publish
    POST /manager/work-orders/{id}/reject             D5 human gate; learns the guardrail
    POST /manager/work-orders/{id}/publish            manual publish / retry
    GET  /manager/work                                what it did with each opportunity + impact

Gated behind manager_v2 like every manager surface. D5 transition guards come
from state_machine (invalid edge -> 409); ownership comes from RLS (a
work order outside the tenant simply isn't found -> 404); a drafting/provider
crash surfaces as 502 with the failed job_run on record. Every human decision
appends an ApprovalEvent-shaped entry to payload.approvals[] (the queue_signal
source of record), and the learning hooks fire: approve -> record_approval
(the draft joins the voice corpus), reject -> record_rejection (the reason
becomes a guardrail).
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..learning import record_approval, record_rejection
from . import execution
from . import publish as publish_service
from .contracts import WorkOrderStatus
from .sources_api import require_manager_v2
from .state_machine import APPROVE_FROM, REJECT_FROM, InvalidTransition, transition

logger = logging.getLogger("manager.execution_api")

router = APIRouter(tags=["execution"], dependencies=[Depends(require_manager_v2)])


def _502(agent: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"{agent} failed: {str(exc)[:300]} (the failed run is on record in job_runs)",
    )


def _value_error(err: ValueError) -> HTTPException:
    # donor mapping: ownership/'not found' -> 404, state errors -> 409
    return HTTPException(status_code=404 if "not found" in str(err) else 409, detail=str(err))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── execute an opportunity ──────────────────────────────────────────────────


class ExecuteBody(BaseModel):
    format: str | None = None  # blog | email | social_post | video | image; inferred when omitted


@router.post("/manager/opportunities/{action_id}/execute")
async def execute_opportunity(action_id: str, body: ExecuteBody | None = None) -> dict:
    """Put a set of hands on an opportunity: create its work order and draft
    the asset (or leave video/image queued for the production pipelines)."""
    try:
        return await execution.execute_opportunity(
            None, action_id, fmt=body.format if body else None, trigger="manual"
        )
    except InvalidTransition as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise _value_error(err) from err
    except Exception as exc:  # noqa: BLE001
        raise _502("hands", exc) from exc


# ── approve / reject: the D5 human gate ─────────────────────────────────────


class ApproveBody(BaseModel):
    reason: str = ""


class RejectBody(BaseModel):
    reason: str = Field(min_length=1)


async def _decide(
    work_order_id: str, allowed: set[WorkOrderStatus], target: WorkOrderStatus,
    action: str, reason: str,
) -> dict:
    """Walk the D5 edge atomically and append the ApprovalEvent to the
    payload's approvals trail. Returns the updated payload."""
    try:
        async with db.acquire() as conn:
            updated = await transition(conn, work_order_id, allowed, target)
            payload = updated["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload or "{}")
            payload = payload or {}
            payload.setdefault("approvals", []).append(
                {"action": action, "reason": reason, "actor": "user", "at": _now_iso()}
            )
            await conn.execute(
                """UPDATE actions SET payload = $2::jsonb, approval_reason = $3
                   WHERE id = $1::uuid""",
                work_order_id, json.dumps(payload), reason[:500],
            )
    except InvalidTransition as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise _value_error(err) from err
    return payload


def _learn_target(payload: dict, work_order_id: str) -> UUID:
    """The learning hooks read the content engine's own queued row (it has the
    draft in the payload shape they expect); fall back to the work order id,
    where record_approval honestly returns None (action_type mismatch)."""
    return UUID(str(payload.get("content_action_id") or work_order_id))


@router.post("/manager/work-orders/{work_order_id}/approve")
async def approve_work_order(work_order_id: str, body: ApproveBody | None = None) -> dict:
    """Approve (pending_approval -> approved, 409 on any other edge), teach the
    voice (record_approval: the blessed draft joins the exemplar corpus), then
    attempt the publish. An honest publish failure leaves the order approved
    with the failure detail — the response says exactly what happened."""
    reason = body.reason if body else ""
    payload = await _decide(work_order_id, APPROVE_FROM, WorkOrderStatus.APPROVED, "approve", reason)

    learned_event_id: str | None = None
    try:
        learned_event_id = await record_approval(_learn_target(payload, work_order_id), None)
    except Exception:  # noqa: BLE001 — learning must never fail an approval
        logger.exception("approval learning failed for work order %s", work_order_id)

    publish_result: dict
    try:
        publish_result = await publish_service.publish_work_order(None, work_order_id)
    except ValueError as err:  # e.g. no artifact — surfaced honestly, order stays approved
        publish_result = {"ok": False, "error": str(err)[:300]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("publish-on-approve failed for work order %s", work_order_id)
        publish_result = {"ok": False, "error": str(exc)[:300]}

    return {
        "work_order_id": work_order_id,
        "status": publish_result.get("status") or WorkOrderStatus.APPROVED.value,
        "approved": True,
        "learned_event_id": learned_event_id,
        "publish": publish_result,
    }


@router.post("/manager/work-orders/{work_order_id}/reject")
async def reject_work_order(work_order_id: str, body: RejectBody) -> dict:
    """Reject (review|pending_approval -> rejected, 409 otherwise) and learn:
    the reason becomes a durable guardrail (record_rejection)."""
    payload = await _decide(
        work_order_id, REJECT_FROM, WorkOrderStatus.REJECTED, "reject", body.reason
    )
    learned_event_id: str | None = None
    try:
        learned_event_id = await record_rejection(
            _learn_target(payload, work_order_id), body.reason, None
        )
    except Exception:  # noqa: BLE001 — learning must never fail a rejection
        logger.exception("rejection learning failed for work order %s", work_order_id)
    return {
        "work_order_id": work_order_id,
        "status": WorkOrderStatus.REJECTED.value,
        "rejected": True,
        "learned_event_id": learned_event_id,
    }


# ── publish (manual / retry) ────────────────────────────────────────────────


@router.post("/manager/work-orders/{work_order_id}/publish")
async def publish_work_order(work_order_id: str) -> dict:
    try:
        return await publish_service.publish_work_order(None, work_order_id)
    except ValueError as err:
        raise _value_error(err) from err
    except Exception as exc:  # noqa: BLE001
        raise _502("publish", exc) from exc


# ── the owner work view ─────────────────────────────────────────────────────


@router.get("/manager/work")
async def list_work() -> dict:
    """Every opportunity acted on: the work order joined with its source
    opportunity, latest artifact, status, and impact — 'what it did with each
    opportunity, and what came of it' (donor list_work shape)."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.id, a.status, a.payload, a.created_at,
                      ai.id AS opp_id, ai.title AS opp_title, ai.kind AS opp_kind, ai.meta AS opp_meta
               FROM actions a
               LEFT JOIN action_items ai ON ai.id = (a.payload->>'source_action_id')::uuid
               WHERE a.action_type = 'work_order' AND a.payload->>'source' = 'opportunity'
               ORDER BY a.created_at DESC"""
        )
    work: list[dict] = []
    for r in rows:
        payload = execution.payload_of(r)
        opp_meta = r["opp_meta"]
        if isinstance(opp_meta, str):
            opp_meta = json.loads(opp_meta or "{}")
        art = execution.latest_artifact(payload)
        work.append(
            {
                "work_order_id": str(r["id"]),
                "content_type": payload.get("content_type"),
                "platform": payload.get("platform"),
                "topic": payload.get("topic"),
                "status": r["status"],
                "routed_to": (payload.get("routing") or {}).get("to")
                or (payload.get("format_spec") or {}).get("route"),
                "opportunity": {
                    "action_id": str(r["opp_id"]),
                    "title": r["opp_title"],
                    "kind": r["opp_kind"],
                    "source": (opp_meta or {}).get("source"),
                } if r["opp_id"] else None,
                "artifact": art,
                "predicted_metrics": payload.get("predicted_metrics"),
                "actual_metrics": payload.get("actual_metrics"),
                "published_ref": payload.get("published_ref"),
                "publish_attempts": payload.get("publish_attempts") or [],
                "approvals": payload.get("approvals") or [],
                "measured_at": payload.get("measured_at"),
                "created_at": r["created_at"].isoformat(),
            }
        )
    return {"work": work}
