"""Growth Intelligence Agent — 'how did the competitors grow?'.

Reads the accumulated peer_snapshots history for each tracked competitor and
computes their trajectory (follower delta over elapsed days, engagement trend),
then has the LLM reason about what is DRIVING the growth from what they post
(cadence, formats, topics). Honest about history: with one snapshot it reports
a baseline and notes trends build as daily snapshots accumulate; with two or
more it computes real deltas. This feeds the daily plan ('do what's working
for the accounts that are pulling ahead').

Ported from bm2.0 backend/app/agents/growth.py: tracked peers come from the
tenant watchlist (the PeerEntity map, status='tracked' is the human gate) and
per-peer history from peer_snapshots rows (052_strategy.sql, written by
peers.snapshot_all — stats jsonb carries the donor's metrics shape).
"""

import json
from datetime import timezone
from uuid import UUID

from .. import db
from ..trends import get_watchlist
from . import runs
from .peers import _status
from .providers import get_providers

AGENT = "growth"

PROMPT_SYSTEM = (
    "You are a growth analyst. Given how competitor accounts changed over time "
    "and what they post, you explain concisely what is likely driving the "
    "growth of the ones pulling ahead — grounded only in the data provided."
)
PROMPT = (
    "For these competitors, explain what is driving the growth of the ones "
    "gaining fastest, and name the specific tactics a challenger brand should "
    "borrow. Ground everything in the trajectory + content data.\n\n"
    "COMPETITOR GROWTH DATA:\n{data}\n\n"
    'Return JSON only: {{"drivers": [str, ...], "borrow": [str, ...], '
    '"narrative": str}} — drivers = what is fuelling the leaders\' growth; '
    "borrow = concrete tactics this brand should adopt."
)


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _trajectory(snaps: list[dict]) -> dict:
    """Oldest→newest follower/engagement delta over elapsed days."""
    dated = sorted(snaps, key=lambda s: s["captured_at"])
    first, last = dated[0], dated[-1]
    days = max((_aware(last["captured_at"]) - _aware(first["captured_at"])).days, 0)
    fm, lm = first["stats"] or {}, last["stats"] or {}

    def _delta(k):
        a, b = fm.get(k), lm.get(k)
        return round(float(b) - float(a), 2) if a is not None and b is not None else None

    fdelta = _delta("followers")
    return {
        "snapshots": len(dated),
        "days": days,
        "followers_now": lm.get("followers"),
        "follower_delta": fdelta,
        "followers_per_week": round(fdelta / days * 7, 2) if fdelta is not None and days > 0 else None,
        "engagement_now": lm.get("avg_engagement"),
        "engagement_delta": _delta("avg_engagement"),
        "cadence_per_week": lm.get("cadence_per_week"),
        "formats": lm.get("formats"),
        "top_topics": lm.get("top_topics"),
    }


def _stats(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    try:
        watchlist = await get_watchlist(tenant_id)
        peers = [e for e in watchlist if _status(e) == "tracked"]

        competitors, have_history = [], False
        async with db.acquire(tenant_id) as conn:
            for p in peers:
                rows = await conn.fetch(
                    "SELECT stats, captured_at FROM peer_snapshots WHERE peer = $1 "
                    "ORDER BY captured_at",
                    str(p.get("handle") or ""),
                )
                snaps = [{"stats": _stats(r["stats"]), "captured_at": r["captured_at"]} for r in rows]
                if not snaps:
                    continue
                traj = _trajectory(snaps)
                if traj["snapshots"] >= 2 and traj["follower_delta"] is not None:
                    have_history = True
                competitors.append(
                    {
                        "handle": p.get("handle"),
                        "kind": str(p.get("kind") or ""),
                        "platform": str(p.get("platform") or ""),
                        **traj,
                    }
                )

        # rank by weekly follower growth where known, else current followers
        competitors.sort(
            key=lambda c: (c.get("followers_per_week") or -1, c.get("followers_now") or 0),
            reverse=True,
        )

        drivers, borrow, narrative = [], [], ""
        if competitors:
            data = "\n".join(
                f"- @{c['handle']} ({c['platform']},{c['kind']}): {c.get('followers_now')} followers, "
                f"Δ{c.get('follower_delta')} over {c.get('days')}d "
                f"(~{c.get('followers_per_week')}/wk), cadence {c.get('cadence_per_week')}/wk, "
                f"formats={c.get('formats')}, topics={c.get('top_topics')}"
                for c in competitors
            )
            raw = await providers.llm.complete_json("content", PROMPT_SYSTEM, PROMPT.format(data=data))
            drivers = [str(x) for x in (raw.get("drivers") or [])]
            borrow = [str(x) for x in (raw.get("borrow") or [])]
            narrative = str(raw.get("narrative") or "").strip()

        note = (
            "Growth trends build as daily snapshots accumulate — deltas shown are from the history so far."
            if not have_history
            else "Growth computed over accumulated daily snapshots."
        )
        report = {
            "brand_id": str(tenant_id or ""),
            "competitors": competitors,
            "drivers": drivers,
            "borrow": borrow,
            "narrative": narrative,
            "note": note,
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle, output={"competitor_count": len(competitors), "have_history": have_history}
    )
    return report
