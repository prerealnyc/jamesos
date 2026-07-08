"""Action-item follow-up service + the daily cycle ('chunk for the day').
Ported from bm2.0 backend/app/services/actions.py onto asyncpg/RLS.

Suggestions from the eyes/agents are persisted here as trackable cards;
the brand manager accepts / notes / dismisses them, and the daily cycle
assembles what needs attention today. Tenancy comes from the connection.
"""

import json
from datetime import datetime, timedelta, timezone

import asyncpg

_STALE_DAYS = 2  # an active item untouched this long is a follow-up due
VALID_STATUS = {"suggested", "active", "done", "dismissed", "snoozed"}

_COLS = (
    "id, kind, title, detail, status, related_peer, meta, updates, dedupe_key, "
    "snooze_until, last_activity_at, created_at"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _record(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k in ("meta", "updates"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["id"] = str(d["id"])
    return d


async def upsert_action(
    conn: asyncpg.Connection,
    *,
    kind: str,
    title: str,
    detail: str = "",
    related_peer: str | None = None,
    meta: dict | None = None,
    dedupe_key: str = "",
) -> dict:
    """Create an action item, or return the existing one with the same
    dedupe_key (so re-running an agent doesn't duplicate suggestions). Never
    resurrects a dismissed/done item — the manager's decision stands."""
    if dedupe_key:
        existing = await conn.fetchrow(
            f"SELECT {_COLS} FROM action_items WHERE dedupe_key = $1", dedupe_key
        )
        if existing is not None:
            return _record(existing)
    try:
        row = await conn.fetchrow(
            f"""INSERT INTO action_items (kind, title, detail, related_peer, meta, dedupe_key)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                RETURNING {_COLS}""",
            kind, title[:300], detail, related_peer, json.dumps(meta or {}), dedupe_key,
        )
    except asyncpg.UniqueViolationError:
        # lost a race on the dedupe index — the winner's row is the item
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM action_items WHERE dedupe_key = $1", dedupe_key
        )
    return _record(row)


async def _require(conn: asyncpg.Connection, action_id: str) -> dict:
    row = await conn.fetchrow(f"SELECT {_COLS} FROM action_items WHERE id = $1::uuid", action_id)
    if row is None:
        raise ValueError(f"action {action_id} not found")
    return _record(row)


async def set_status(
    conn: asyncpg.Connection, action_id: str, status: str, snooze_days: int = 3
) -> dict:
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r}")
    await _require(conn, action_id)
    snooze_until = (_now() + timedelta(days=snooze_days)) if status == "snoozed" else None
    row = await conn.fetchrow(
        f"""UPDATE action_items SET status = $2, snooze_until = $3, last_activity_at = now()
            WHERE id = $1::uuid RETURNING {_COLS}""",
        action_id, status, snooze_until,
    )
    return _record(row)


async def add_note(conn: asyncpg.Connection, action_id: str, note: str, actor: str = "user") -> dict:
    item = await _require(conn, action_id)
    updates = [*item["updates"], {"ts": _now().isoformat(), "note": note, "actor": actor}]
    # a note on a suggested item implies the manager is acting on it
    status = "active" if item["status"] == "suggested" else item["status"]
    row = await conn.fetchrow(
        f"""UPDATE action_items SET updates = $2::jsonb, status = $3, last_activity_at = now()
            WHERE id = $1::uuid RETURNING {_COLS}""",
        action_id, json.dumps(updates), status,
    )
    return _record(row)


async def delete_action(conn: asyncpg.Connection, action_id: str) -> None:
    await _require(conn, action_id)
    await conn.execute("DELETE FROM action_items WHERE id = $1::uuid", action_id)


async def list_actions(conn: asyncpg.Connection, statuses: list[str] | None = None) -> list[dict]:
    if statuses:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM action_items WHERE status = ANY($1) ORDER BY last_activity_at DESC",
            statuses,
        )
    else:
        rows = await conn.fetch(f"SELECT {_COLS} FROM action_items ORDER BY last_activity_at DESC")
    return [_record(r) for r in rows]


async def due_followups(conn: asyncpg.Connection) -> list[dict]:
    """Active items untouched for _STALE_DAYS, plus snoozes whose time has come."""
    now = _now()
    items = await list_actions(conn, ["active", "snoozed"])
    due: list[dict] = []
    for it in items:
        if it["status"] == "snoozed":
            if it["snooze_until"] and _aware(it["snooze_until"]) <= now:
                due.append(it)
        elif (now - _aware(it["last_activity_at"])) >= timedelta(days=_STALE_DAYS):
            due.append(it)
    return due


async def run_daily_cycle(conn: asyncpg.Connection) -> dict:
    """Assemble the tenant's 'chunk for the day': follow-ups due + the top new
    suggestion + a next-step nudge. Upserts one digest per (tenant, date).
    Cheap by design — reads existing state, no per-day LLM cost."""
    now = _now()

    due = await due_followups(conn)
    suggested = await list_actions(conn, ["suggested"])
    active = await list_actions(conn, ["active"])

    items: list[dict] = []
    for it in due[:5]:
        why = "Snoozed item is due" if it["status"] == "snoozed" else f"No update in {_STALE_DAYS}+ days"
        items.append({"action_id": it["id"], "title": it["title"], "why": why, "kind": it["kind"]})
    for it in suggested[:3]:
        items.append(
            {"action_id": it["id"], "title": it["title"], "why": "New suggestion to review", "kind": it["kind"]}
        )

    parts = []
    if due:
        parts.append(f"{len(due)} follow-up{'s' if len(due) != 1 else ''} need attention")
    if suggested:
        parts.append(f"{len(suggested)} new suggestion{'s' if len(suggested) != 1 else ''} to review")
    if active and not due:
        parts.append(f"{len(active)} in progress, all current")
    if not items:
        summary = (
            "All clear today — nothing due. Generate collaboration or content "
            "plays to line up the next move."
        )
    else:
        summary = "Today: " + "; ".join(parts) + "."

    row = await conn.fetchrow(
        """INSERT INTO daily_digests (date, summary, items)
           VALUES ($1, $2, $3::jsonb)
           ON CONFLICT (tenant_id, date)
           DO UPDATE SET summary = EXCLUDED.summary, items = EXCLUDED.items, created_at = now()
           RETURNING id, date, summary, items, created_at""",
        now.date(), summary, json.dumps(items),
    )
    d = dict(row)
    if isinstance(d.get("items"), str):
        d["items"] = json.loads(d["items"])
    d["id"] = str(d["id"])
    return d
