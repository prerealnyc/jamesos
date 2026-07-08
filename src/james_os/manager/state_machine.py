"""Validated work-order transitions — the single implementation of the D5
edges, now operating on the james-os actions queue. Ported from bm2.0
backend/app/services/state_machine.py.

Humans (queue routes) and agents (reviewer, creator, publisher) both go
through transition(); nothing else may assign a lifecycle status for a
manager work order. Invalid moves raise InvalidTransition; the HTTP layer
maps that to 409.
"""

import asyncpg

from .contracts import WorkOrderStatus

# D5 edges, keyed by the states a target may be entered FROM.
APPROVE_FROM = {WorkOrderStatus.PENDING_APPROVAL}
REJECT_FROM = {WorkOrderStatus.REVIEW, WorkOrderStatus.PENDING_APPROVAL}
GENERATE_FROM = {WorkOrderStatus.QUEUED}
REVIEW_FROM = {WorkOrderStatus.GENERATING}
PASS_REVIEW_FROM = {WorkOrderStatus.REVIEW}
# the execution steps that close the loop past approval: publish the approved
# artifact (the "do it"), then measure its impact (predicted-vs-actual, D5).
PUBLISH_FROM = {WorkOrderStatus.APPROVED, WorkOrderStatus.SCHEDULED}
MEASURE_FROM = {WorkOrderStatus.PUBLISHED}


class InvalidTransition(Exception):
    """A D5-invalid state change was attempted; the order is left untouched."""

    def __init__(self, order_id: str, current: str, allowed: set[WorkOrderStatus], target: WorkOrderStatus):
        self.order_id = order_id
        self.current = current
        self.target = target
        super().__init__(
            f"work order {order_id} is {current!r}; "
            f"{target.value} allowed only from {sorted(s.value for s in allowed)}"
        )


def validate(order_id: str, current: str, allowed: set[WorkOrderStatus], target: WorkOrderStatus) -> None:
    if WorkOrderStatus(current) not in allowed:
        raise InvalidTransition(order_id, current, allowed, target)


def can_transition(current: str, allowed: set[WorkOrderStatus]) -> bool:
    try:
        return WorkOrderStatus(current) in allowed
    except ValueError:
        return False  # a pre-merge james-os status (pending/executed/...) — not a D5 order


async def transition(
    conn: asyncpg.Connection,
    action_id: str,
    allowed: set[WorkOrderStatus],
    target: WorkOrderStatus,
) -> dict:
    """Atomically move an actions-queue row along a D5 edge. Locks the row,
    validates the edge against its CURRENT status, then updates — so two
    concurrent approvals can't both fire."""
    row = await conn.fetchrow(
        "SELECT id, status FROM actions WHERE id = $1::uuid FOR UPDATE", action_id
    )
    if row is None:
        raise ValueError(f"work order {action_id} not found")
    validate(action_id, row["status"], allowed, target)
    updated = await conn.fetchrow(
        """UPDATE actions SET status = $2,
               decided_at = CASE WHEN $2 IN ('approved','rejected') THEN now() ELSE decided_at END,
               executed_at = CASE WHEN $2 = 'published' THEN now() ELSE executed_at END
           WHERE id = $1::uuid
           RETURNING id, proposed_by, action_type, payload, status, created_at, decided_at, executed_at""",
        action_id, target.value,
    )
    d = dict(updated)
    d["id"] = str(d["id"])
    return d
