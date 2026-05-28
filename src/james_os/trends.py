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
    """Parse an Apify-supplied timestamp into a UTC-AWARE datetime.

    Apify actors emit ISO strings in three shapes: epoch seconds,
    `2026-05-28T12:00:00Z`, and bare `2026-05-28T12:00:00` (no tz).
    The third shape used to come back naive, and `datetime.now(UTC) - dt`
    blew up with 'can't subtract offset-naive and offset-aware'.
    Now any successful parse is forced to UTC.
    """
    if not s:
        return None
    txt = str(s).strip()
    # epoch seconds (or ms)
    if txt.isdigit():
        try:
            v = int(txt)
            if v > 10_000_000_000:  # heuristic: 10^10 → looks like ms
                v //= 1000
            return datetime.fromtimestamp(v, tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive timestamp from the actor — treat as UTC (Apify's docs say
        # it is, but they emit it bare anyway).
        dt = dt.replace(tzinfo=UTC)
    return dt


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
    """creators = [{platform, handle, name?, interests?}]. Wholesale replace.

    Extra fields (name, interests) are preserved on the watchlist so the UI
    can show human labels and the content engine can match a brief's topic
    to creators whose interests overlap.
    """
    clean: list[dict] = []
    for c in creators:
        platform = (c.get("platform") or "").strip()
        handle = (c.get("handle") or "").strip().lstrip("@")
        if not platform or not handle:
            continue
        entry = {"platform": platform, "handle": handle}
        name = (c.get("name") or "").strip()
        if name:
            entry["name"] = name
        interests = c.get("interests") or []
        if isinstance(interests, str):
            interests = [s.strip() for s in interests.split(",") if s.strip()]
        if interests:
            entry["interests"] = list(interests)
        clean.append(entry)
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{watchlist}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(clean),
        )
    return clean


def _interest_tokens(s: str) -> set[str]:
    """Lower-case tokenize a string into individual words AND keep the
    original lower-cased multi-word string. Matching uses either, so
    'Real Estate' on a creator matches a topic_hint of 'real estate' or
    just 'real'.

    Plus a tiny domain-scoped abbreviation expansion so the user can
    write 'Staten Island commercial RE' as a topic_hint and still match
    a creator tagged with 'Real Estate'. Conservative on purpose — only
    bidirectional, unambiguous expansions live here.
    """
    base = (s or "").strip().lower()
    tokens = {t for t in base.replace("-", " ").split() if len(t) >= 3}
    if base and " " in base:
        tokens.add(base)
    # Bidirectional expansions: tagging one form unlocks matches on the
    # other (so a creator interest of 'real estate' matches a topic of
    # 'RE', and vice versa).
    raw = set(base.replace("-", " ").split())
    for abbr, full in _ABBREVS.items():
        if abbr in raw or abbr in tokens:
            tokens.update(full.split())
            tokens.add(full)
        if any(t in raw for t in full.split()):
            tokens.add(abbr)
    return tokens


_ABBREVS = {
    "re": "real estate",
    "cre": "commercial real estate",
    "vc": "venture capital",
    "ai": "artificial intelligence",
}


def _interests_overlap(creator_interests: list[str], topic: str) -> bool:
    """True when the topic_hint shares any meaningful token with a creator's
    interests. Both sides are tokenised + lower-cased. A 3-char minimum
    drops 'is', 'to', etc. without needing a full stopword list."""
    topic_tokens = _interest_tokens(topic)
    if not topic_tokens:
        return False
    for i in creator_interests or []:
        ci = _interest_tokens(i)
        # Either side's multi-word phrase contains the other's token, OR
        # they share a single-word token. The phrase-contains test catches
        # 'real estate' (creator) vs 'staten island commercial re' (topic).
        if ci & topic_tokens:
            return True
        for tt in topic_tokens:
            if " " in tt and any(t in tt for t in ci if " " not in t):
                return True
            if " " in (i or "").lower() and tt in (i or "").lower():
                return True
    return False


async def matched_creators(
    topic: str, tenant_id: UUID | None = None
) -> list[dict]:
    """Watchlist subset whose interests overlap with `topic`. Empty list is
    a clean fallback signal (no curated cohort for this topic — caller
    decides what to do, e.g. fall back to a generic trend pull)."""
    if not topic.strip():
        return []
    creators = await get_watchlist(tenant_id)
    return [c for c in creators if _interests_overlap(c.get("interests") or [], topic)]


async def list_cohort_trends(
    topic: str, limit: int = 8, tenant_id: UUID | None = None
) -> dict:
    """Trend events from watchlist creators whose interests overlap `topic`.
    Returns {creators, trends}. Trends are ordered by stored outlier_score.

    Honest scope: depends on `refresh_watchlist` having scraped at least
    once — without that there are no trend events to filter. Caller falls
    back to the existing top-N recent feed in that case.
    """
    matched = await matched_creators(topic, tenant_id)
    if not matched:
        return {"creators": [], "trends": []}
    # Match by (platform, handle) pair — the watchlist stores handles
    # without the leading @, same as how trend events persist them.
    pairs = [(c["platform"], c["handle"].lower()) for c in matched]
    placeholders = ", ".join(f"(${i*2+1}, ${i*2+2})" for i in range(len(pairs)))
    args: list = []
    for p, h in pairs:
        args.extend([p, h])
    args.append(limit)
    sql = (
        "SELECT payload, raw_content, created_at FROM events "
        f"WHERE payload->>'category'='trend' AND superseded_by IS NULL "
        f"AND (payload->>'platform', lower(payload->>'handle')) IN ({placeholders}) "
        "ORDER BY (payload->>'outlier_score')::float DESC NULLS LAST, "
        "created_at DESC "
        f"LIMIT ${len(args)}"
    )
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *args)
    trends = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        trends.append({
            "platform": p.get("platform", ""),
            "handle": p.get("handle", ""),
            "caption": (p.get("caption") or "")[:240],
            "url": p.get("url", ""),
            "views": p.get("views", 0),
            "outlier_score": p.get("outlier_score", 0),
        })
    return {
        "creators": [
            {"name": c.get("name") or c["handle"],
             "platform": c["platform"], "handle": c["handle"],
             "interests": c.get("interests") or []}
            for c in matched
        ],
        "trends": trends,
    }


def watchlist_by_platform(creators: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in creators:
        out.setdefault(c["platform"], []).append(c["handle"])
    return out


__all__ = [
    "discover_and_ingest", "refresh_watchlist", "list_trends",
    "get_watchlist", "set_watchlist", "watchlist_by_platform",
    "matched_creators", "list_cohort_trends",
    "score_items", "trend_items_to_events",
]
