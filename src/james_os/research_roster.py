"""Social Research roster — the curated set of peer/competitor creators we
watch, kept fresh by a weekly Apify scrape.

This is *peer research*, deliberately separate from the brand's own analytics.
The roster IS the watchlist (stored per-tenant in `tenants.config.watchlist`
by trends.py); this module wraps it with research-flavoured reads and the
weekly-refresh cadence:

    get_roster()          watchlist + per-handle trend-event stats
    refresh_roster()      scrape every watched handle, stamp last-refresh
    roster_status()       when did we last scrape; are we due (>7d)?
    maybe_weekly_refresh() the scheduler's once-a-week entry point

Scraping itself is NOT reimplemented here — it delegates to
`trends.refresh_watchlist`, which already handles per-handle resilience,
viral scoring, and ingest into the `events` table.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .db import acquire
from .trends import get_watchlist, refresh_watchlist, watchlist_by_platform

REFRESH_INTERVAL = timedelta(days=7)


async def get_roster(tenant_id: UUID | None = None) -> dict:
    """The watched creators, each enriched with how many trend posts we've
    scraped for them and when the most recent one was posted.

    Handles on the watchlist are stored without a leading @ and trend events
    persist them the same way, but we match case-insensitively to be safe.
    One grouped events query covers every handle; we merge in Python.
    """
    creators = await get_watchlist(tenant_id)

    sql = (
        "SELECT lower(payload ->> 'handle') AS handle, "
        "count(*) AS post_count, "
        "max(payload ->> 'posted_at') AS last_post_at "
        "FROM events "
        "WHERE payload ->> 'category' = 'trend' AND superseded_by IS NULL "
        "GROUP BY lower(payload ->> 'handle')"
    )
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql)

    stats: dict[str, dict] = {}
    for r in rows:
        h = r["handle"]
        if not h:
            continue
        stats[h] = {
            "post_count": r["post_count"],
            "last_post_at": r["last_post_at"],
        }

    enriched: list[dict] = []
    for c in creators:
        handle = (c.get("handle") or "").strip().lstrip("@")
        s = stats.get(handle.lower(), {})
        enriched.append({
            **c,
            "post_count": s.get("post_count", 0),
            "last_post_at": s.get("last_post_at"),
        })
    return {"creators": enriched}


async def refresh_roster(limit: int = 15, tenant_id: UUID | None = None) -> dict:
    """Scrape every watched creator and ingest fresh trend posts, then stamp
    `roster_last_refresh`. Per-handle resilience comes from refresh_watchlist.

    Returns {scraped, stored, creators}: how many posts came back, how many
    became (deduped) events, and the watchlist we scraped against.
    """
    creators = await get_watchlist(tenant_id)
    handles_by_platform = watchlist_by_platform(creators)

    if handles_by_platform:
        result = await refresh_watchlist(handles_by_platform, limit, tenant_id)
    else:
        result = {"found": 0, "stored_event_ids": []}

    now = datetime.now(timezone.utc).isoformat()
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{roster_last_refresh}', "
            "to_jsonb($1::text)) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            now,
        )

    return {
        "scraped": result.get("found", 0),
        "stored": len(result.get("stored_event_ids", [])),
        "creators": creators,
    }


def _parse_iso(s: str) -> datetime | None:
    """Parse a stored ISO timestamp into a UTC-aware datetime, defensively.

    Returns None on anything unparseable so callers treat it as 'never
    refreshed' (i.e. due) rather than crashing.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def roster_status(tenant_id: UUID | None = None) -> dict:
    """When the roster was last scraped and whether a refresh is due.

    Due when we've never refreshed, the stored value is unparseable, or the
    last refresh is older than the weekly interval.
    """
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    raw = (cfg or {}).get("roster_last_refresh")

    last = _parse_iso(raw) if raw else None
    if last is None:
        due = True
    else:
        due = datetime.now(timezone.utc) - last > REFRESH_INTERVAL

    return {
        "last_refresh": last.isoformat() if last else None,
        "due": due,
    }


async def maybe_weekly_refresh(tenant_id: UUID | None = None) -> dict:
    """Scheduler entry point: refresh only if the roster is due (>7d stale).

    Returns refresh_roster's result when it runs, else {skipped: True}.
    """
    status = await roster_status(tenant_id)
    if not status["due"]:
        return {"skipped": True, "last_refresh": status["last_refresh"]}
    return await refresh_roster(tenant_id=tenant_id)


__all__ = [
    "get_roster",
    "refresh_roster",
    "roster_status",
    "maybe_weekly_refresh",
]
