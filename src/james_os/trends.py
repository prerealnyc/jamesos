"""Trend layer — scraped posts → scored, queryable memory.

Sits on top of apify.py and the shared memory substrate:

    discover(topic)            scrape_watchlist()
            │                         │
            ▼                         ▼
        TrendItem[] ───────────────────
            │
            ▼
   viral scoring (outlier vs creator median + views/hour velocity)
            │
            ▼
   events (category:trend, Voyage-embedded)  ← citable like any memory
            │
            ▼
   list_trends()  → ranked feed for the UI / script generation

Virality signal is the research-backed pair: Outlier Score (a post's views
divided by that creator's median views — surfaces break-outs, not just big
accounts) and velocity (views per hour since posting). Both are stored on
the event payload so ranking is a cheap read, not a recompute.
"""

import hashlib
import json
import statistics
from datetime import UTC, datetime
from uuid import UUID

from .apify import TREND_CATEGORY, TrendItem, get_trend_provider
from .db import acquire
from .ingestion import ingest_many
from .models import EventCreate, EventSource


# ── viral scoring ──

def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    txt = str(s).strip()
    # epoch seconds
    if txt.isdigit():
        try:
            return datetime.fromtimestamp(int(txt), tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None


def _velocity(views: int, posted_at: str) -> float:
    """Views per hour since posting. 0 when the timestamp is unknown."""
    dt = _parse_dt(posted_at)
    if not dt or views <= 0:
        return 0.0
    hours = (datetime.now(UTC) - dt).total_seconds() / 3600.0
    if hours < 1:
        hours = 1.0
    return round(views / hours, 1)


def score_items(items: list[TrendItem]) -> list[dict]:
    """Attach outlier_score + velocity to each item. Outlier is computed
    against the median views of the SAME creator within this batch, falling
    back to the batch median when a creator has only one post here."""
    by_handle: dict[str, list[int]] = {}
    for it in items:
        by_handle.setdefault(it.handle, []).append(it.views)
    batch_median = statistics.median([it.views for it in items if it.views > 0] or [0])

    scored: list[dict] = []
    for it in items:
        peers = [v for v in by_handle.get(it.handle, []) if v > 0]
        median = statistics.median(peers) if len(peers) >= 2 else batch_median
        outlier = round(it.views / median, 2) if median > 0 else 0.0
        scored.append({
            "item": it,
            "outlier_score": outlier,
            "velocity": _velocity(it.views, it.posted_at),
        })
    # Highest outlier first, then raw views.
    scored.sort(key=lambda s: (s["outlier_score"], s["item"].views), reverse=True)
    return scored


# ── ingest: trend items → memory events ──

def trend_items_to_events(scored: list[dict]) -> list[EventCreate]:
    """Each scored post becomes one citable event. raw_content is the
    caption + transcript so retrieval can match on what the creator
    actually said; metrics + scores ride on the payload for ranking."""
    now = datetime.now(UTC)
    events: list[EventCreate] = []
    for s in scored:
        it: TrendItem = s["item"]
        body_parts = [
            f"[{it.platform}] @{it.handle}",
            it.caption,
        ]
        if it.transcript:
            body_parts.append(f"Transcript: {it.transcript}")
        raw = "\n".join(p for p in body_parts if p).strip()
        if not raw:
            continue

        digest = hashlib.sha256(it.dedupe_basis().encode()).hexdigest()[:16]
        payload = {
            "text": raw,
            "category": TREND_CATEGORY,
            "platform": it.platform,
            "handle": it.handle,
            "url": it.url,
            "caption": it.caption,
            "has_transcript": bool(it.transcript),
            "views": it.views,
            "likes": it.likes,
            "comments": it.comments,
            "shares": it.shares,
            "posted_at": it.posted_at,
            "thumbnail": it.thumbnail,
            "duration": it.duration,
            "outlier_score": s["outlier_score"],
            "velocity": s["velocity"],
        }
        events.append(
            EventCreate(
                event_type="document",
                payload=payload,
                raw_content=raw,
                source=EventSource(
                    adapter=f"trend:{it.platform}",
                    uri=it.url or None,
                    dedupe_key=f"trend-{it.platform}-{digest}",
                    raw_metadata={
                        "category": TREND_CATEGORY,
                        "platform": it.platform,
                        "handle": it.handle,
                        "url": it.url,
                    },
                ),
                entities=[
                    f"category:{TREND_CATEGORY}",
                    f"platform:{it.platform}",
                    f"creator:{it.handle}",
                ],
                effective_at=now,
                # Competitor/trend signal is informative, not brand-authoritative.
                confidence=0.5,
            )
        )
    return events


async def discover_and_ingest(
    topic: str, platforms: list[str], limit: int, tenant_id: UUID | None = None
) -> dict:
    provider = get_trend_provider()
    items = await provider.discover(topic, platforms, limit)
    scored = score_items(items)
    events = trend_items_to_events(scored)
    stored = await ingest_many(events, tenant_id) if events else []
    return {
        "provider": provider.name,
        "topic": topic,
        "platforms": platforms,
        "found": len(items),
        "stored_event_ids": [str(e.id) for e in stored],
        "trends": _scored_to_out(scored),
    }


async def refresh_watchlist(
    handles: dict[str, list[str]], limit: int, tenant_id: UUID | None = None
) -> dict:
    provider = get_trend_provider()
    items = await provider.scrape_handles(handles, limit)
    scored = score_items(items)
    events = trend_items_to_events(scored)
    stored = await ingest_many(events, tenant_id) if events else []
    return {
        "provider": provider.name,
        "found": len(items),
        "stored_event_ids": [str(e.id) for e in stored],
        "trends": _scored_to_out(scored),
    }


def _scored_to_out(scored: list[dict]) -> list[dict]:
    out = []
    for s in scored:
        it: TrendItem = s["item"]
        out.append({
            "platform": it.platform,
            "handle": it.handle,
            "url": it.url,
            "caption": it.caption,
            "has_transcript": bool(it.transcript),
            "views": it.views,
            "likes": it.likes,
            "comments": it.comments,
            "shares": it.shares,
            "posted_at": it.posted_at,
            "thumbnail": it.thumbnail,
            "outlier_score": s["outlier_score"],
            "velocity": s["velocity"],
        })
    return out


# ── read: ranked trend feed from memory ──

async def list_trends(
    tenant_id: UUID | None = None,
    platform: str = "",
    limit: int = 60,
) -> list[dict]:
    """Ranked viral feed straight from stored trend events. Ordered by the
    outlier score computed at ingest, so this is a cheap read."""
    clauses = ["payload ->> 'category' = $1", "superseded_by IS NULL"]
    args: list = [TREND_CATEGORY]
    if platform:
        args.append(platform)
        clauses.append(f"payload ->> 'platform' = ${len(args)}")
    where = " AND ".join(clauses)
    args.append(limit)
    sql = (
        "SELECT id, payload, created_at FROM events "
        f"WHERE {where} "
        "ORDER BY (payload ->> 'outlier_score')::float DESC NULLS LAST, "
        "(payload ->> 'views')::bigint DESC NULLS LAST "
        f"LIMIT ${len(args)}"
    )
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *args)

    out = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        out.append({
            "event_id": str(r["id"]),
            "platform": p.get("platform", ""),
            "handle": p.get("handle", ""),
            "url": p.get("url", ""),
            "caption": p.get("caption", ""),
            "has_transcript": p.get("has_transcript", False),
            "views": p.get("views", 0),
            "likes": p.get("likes", 0),
            "comments": p.get("comments", 0),
            "shares": p.get("shares", 0),
            "posted_at": p.get("posted_at", ""),
            "thumbnail": p.get("thumbnail", ""),
            "outlier_score": p.get("outlier_score", 0),
            "velocity": p.get("velocity", 0),
        })
    return out


# ── watchlist (tracked creators) stored per-tenant in tenants.config ──

async def get_watchlist(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return (cfg or {}).get("watchlist", [])


async def set_watchlist(
    creators: list[dict], tenant_id: UUID | None = None
) -> list[dict]:
    """creators = [{platform, handle}]. Replaces the list wholesale."""
    clean = [
        {"platform": c["platform"], "handle": c["handle"].lstrip("@").strip()}
        for c in creators
        if c.get("platform") and c.get("handle", "").strip()
    ]
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{watchlist}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(clean),
        )
    return clean


def watchlist_by_platform(creators: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in creators:
        out.setdefault(c["platform"], []).append(c["handle"])
    return out


__all__ = [
    "discover_and_ingest", "refresh_watchlist", "list_trends",
    "get_watchlist", "set_watchlist", "watchlist_by_platform",
    "score_items", "trend_items_to_events",
]
