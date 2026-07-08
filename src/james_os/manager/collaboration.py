"""Collaboration Strategy Agent — the brand-manager 'what do we DO with them'.
Ported from bm2.0 backend/app/agents/collaboration.py onto the substrate.

For each TRACKED, approved peer it thinks like a brand manager: what does the
peer have (audience, reach, format) vs what does this brand have (positioning,
audience, offer), where does the mutual interest lie, and what specific play
fits the relationship kind —
  - leader        → seek an endorsement / feature (borrow credibility)
  - aspirational  → get featured / guest on their channel (borrow audience up)
  - collaborator  → true co-creation (mutual)
  - competitor    → usually monitor; co-market only when genuinely mutual
It suggests the best OUTREACH PATH with a confidence, and never fabricates
specific contact details (leads to verify, not guarantees).

Crucially it never dead-ends: with zero collaboration targets (many brands
won't have any) it falls back to VISIBILITY PLAYS for the brand's exact target
audience, so the output is always 'here is the next lever'.

Plays persist as deduped action_items (the donor did this in its route; here
the agent owns it, like the eyes) and the full report is stored on the
job_run's output so GET /manager/collab/latest serves it without re-running.
"""

import json
from datetime import datetime, timezone
from uuid import UUID

from .. import db
from ..trends import get_watchlist
from . import actions as action_service
from . import profile, runs
from .peers import _status as _peer_status  # pre-merge entries count as tracked
from .providers import get_providers

AGENT = "collaboration"
_PROFILE_CHARS = 3500

PROMPT_SYSTEM = (
    "You are a brand manager planning outreach and growth. You reason about "
    "mutual benefit between brands, propose concrete plays, and never invent "
    "specific private contact details — you suggest an outreach PATH and flag "
    "it as needing verification."
)
PROMPT = (
    "Produce collaboration plays for this brand.\n\n"
    "BRAND FACTS:\n{profile}\n\n"
    "TRACKED PEERS (already approved by the brand manager):\n{peers}\n\n"
    "For EACH tracked peer, propose ONE specific play matched to its kind: "
    "leader → seek an endorsement or feature; aspirational → get featured / "
    "guest on their channel to borrow their audience upward; collaborator → "
    "genuine co-creation (podcast swap, joint content, event); competitor → "
    "usually just monitor, co-market only if genuinely mutual. Identify where "
    "the MUTUAL INTEREST lies (what they have vs what this brand offers). "
    "Suggest the best OUTREACH PATH (e.g. business email on their site, DM + "
    "follow-up, press/booking page, via their manager) with confidence "
    "low|medium|high; never state a specific private email/phone.\n"
    "THEN, regardless of peers, propose 3-5 VISIBILITY PLAYS to make this "
    "brand more recognizable to its EXACT target audience (content series, "
    "SEO/AEO, PR, community, partnerships, paid) — so there is always a next "
    "action even with zero collaboration targets.\n\n"
    'Return JSON only: {{"plays": [{{"peer_handle": str, "kind": str, '
    '"mutual_interest": str, "play_type": str, "play": str, '
    '"outreach_path": str, "outreach_confidence": "low|medium|high"}}], '
    '"visibility_plays": [{{"title": str, "action": str, "why": str}}], '
    '"note": str}}. Set plays to [] when there are no tracked peers.'
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_digest(fields: list[dict]) -> str:
    keep = ("identity", "positioning", "audience", "products")
    lines = []
    for f in fields:
        if f["section"] in keep:
            val = f["value"].get("v") if isinstance(f["value"], dict) else f["value"]
            lines.append(f"- {f['field_key']}: {str(val)[:180]}")
    text = "\n".join(lines)
    return text[:_PROFILE_CHARS] if text else "(no profile facts yet)"


async def _tracked_peers(tenant_id: UUID | None) -> list[dict]:
    """TRACKED watchlist entries (the human gate), each with its latest
    snapshot metrics — the donor's PeerEntity+PeerSnapshot join, here the
    watchlist plus the newest peer_snapshots row per handle."""
    tracked = [e for e in await get_watchlist(tenant_id) if _peer_status(e) == "tracked"]
    out: list[dict] = []
    async with db.acquire(tenant_id) as conn:
        for e in tracked:
            handle = str(e.get("handle") or "")
            stats = await conn.fetchval(
                "SELECT stats FROM peer_snapshots WHERE peer = $1 "
                "ORDER BY captured_at DESC LIMIT 1",
                handle,
            )
            if isinstance(stats, str):
                stats = json.loads(stats or "{}")
            m = stats or {}
            out.append(
                {
                    "handle": handle,
                    "display_name": e.get("display_name") or handle,
                    "kind": e.get("kind", ""),
                    "platform": e.get("platform", ""),
                    "followers": m.get("followers"),
                    "cadence_per_week": m.get("cadence_per_week"),
                    "top_topics": m.get("top_topics"),
                }
            )
    return out


def _peers_block(peers: list[dict]) -> str:
    if not peers:
        return "(none approved yet — produce visibility plays only)"
    return "\n".join(
        f"- {p['display_name']} (@{p['handle']}, {p['platform']}, kind={p['kind']}): "
        f"{p.get('followers') or '?'} followers, ~{p.get('cadence_per_week') or '?'}/wk, "
        f"topics={', '.join(p.get('top_topics') or []) or 'n/a'}"
        for p in peers
    )


async def _persist_plays(tenant_id: UUID | None, report: dict) -> None:
    """Persist plays + visibility levers as trackable follow-up action items
    (dedupe so re-generating doesn't duplicate; a dismissed item stays gone)."""
    async with db.acquire(tenant_id) as conn:
        for p in report.get("plays", []):
            handle = str(p.get("peer_handle") or "").strip()
            await action_service.upsert_action(
                conn,
                kind="collaboration",
                title=f"{p.get('play_type', 'collab')}: {p.get('play', '')}"[:300],
                detail=str(p.get("mutual_interest") or ""),
                related_peer=handle or None,
                meta={
                    "peer_handle": handle, "peer_kind": p.get("kind"),
                    "outreach_path": p.get("outreach_path"),
                    "outreach_confidence": p.get("outreach_confidence"), "source": "collaboration",
                },
                dedupe_key=f"collab:{p.get('kind')}:{handle}:{p.get('play_type')}",
            )
        for v in report.get("visibility_plays", []):
            await action_service.upsert_action(
                conn,
                kind="visibility",
                title=str(v.get("title") or "")[:300],
                detail=f"{v.get('action', '')} — {v.get('why', '')}",
                meta={"source": "collaboration_visibility"},
                dedupe_key=f"visibility:{v.get('title')}",
            )


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)
        peers = await _tracked_peers(tenant_id)
        prompt = PROMPT.format(profile=_profile_digest(fields), peers=_peers_block(peers))
        # 'collaboration plays' phrase in the prompt routes the mock LLM
        raw = await providers.llm.complete_json("content", PROMPT_SYSTEM, prompt)

        plays = [p for p in (raw.get("plays") or []) if isinstance(p, dict)]
        visibility = [v for v in (raw.get("visibility_plays") or []) if isinstance(v, dict)]
        # never dead-end: guarantee at least a visibility floor
        if not visibility:
            visibility = [
                {
                    "title": "Publish for your target audience",
                    "action": "Ship a consistent content series on your core topics for the audience you're trying to reach.",
                    "why": "Visibility compounds; a steady cadence is the baseline growth lever when no partner exists.",
                }
            ]
        note = str(raw.get("note") or "").strip() or (
            "No approved collaboration targets yet — focus on the visibility plays."
            if not peers
            else "Collaboration plays are leads to verify before outreach."
        )
        report = {
            "brand_id": str(tenant_id or ""),
            "generated_at": _now().isoformat(),
            "tracked_peer_count": len(peers),
            "plays": plays,
            "visibility_plays": visibility,
            "note": note,
        }
        await _persist_plays(tenant_id, report)
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    # store the full report so GET .../collab/latest can serve it without re-running
    await runs.finish_run(handle, output={"report": report})
    return report


__all__ = ["run"]
