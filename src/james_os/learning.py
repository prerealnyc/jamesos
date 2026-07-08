"""Rejection → learning loop.

When a manager rejects a queued draft and says why, that reason must change
future output — otherwise the system repeats the same mistake forever. We
turn each rejection into a `frustration`-category memory event (a hard
"never do this" guardrail). The content engine already:

  * retrieves the frustration bucket and injects it as <avoid> rules, and
  * fails voice-QA on any frustration violation,

so a recorded rejection actively steers — and gates — the next attempt. A
human decision is authoritative, so these events carry full confidence and
are always surfaced to the engine (see content.assemble_memory).
"""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .ingestion import ingest_many
from .models import EventCreate, EventSource

FRUSTRATION_CATEGORY = "frustration"


def rejection_to_event(payload: dict, reason: str) -> EventCreate:
    platform = str(payload.get("platform", "") or "")
    fmt = str(payload.get("format", "") or "")
    topic = str(payload.get("topic", "") or payload.get("pillar", "") or "")
    draft = str(payload.get("content") or payload.get("caption") or "")[:600]

    text = (
        f"REJECTED BY A HUMAN — learn from this and never repeat it. "
        f"A {platform or 'social'} {fmt or 'post'}"
        f"{f' about “{topic}”' if topic else ''} was rejected. "
        f"Reason given: {reason}. "
        f"Treat this reason as a hard rule for all future content. "
        f"Rejected draft (so you can recognise and AVOID this pattern — do not "
        f"reproduce it):\n{draft}"
    )
    digest = hashlib.sha256(
        f"{topic}|{reason}|{draft[:80]}".encode()
    ).hexdigest()[:16]

    return EventCreate(
        event_type="note",
        payload={
            "text": text,
            "category": FRUSTRATION_CATEGORY,
            "platform": platform,
            "format": fmt,
            "topic": topic,
            "reason": reason,
            "source": "rejection_feedback",
        },
        raw_content=text,
        source=EventSource(
            adapter="rejection_feedback",
            dedupe_key=f"reject-{digest}",
            raw_metadata={"category": FRUSTRATION_CATEGORY, "reason": reason},
        ),
        entities=[
            f"category:{FRUSTRATION_CATEGORY}",
            *([f"platform:{platform}"] if platform else []),
        ],
        effective_at=datetime.now(UTC),
        confidence=1.0,  # a human's decision is authoritative
    )


APPROVED_EXEMPLAR_SOURCE = "approved_exemplar"


def approval_to_event(payload: dict) -> EventCreate | None:
    """Turn an APPROVED draft into a voice_corpus exemplar — the positive
    half of the loop. The human blessed this as on-brand, so future drafts
    should imitate it. Returns None when there's nothing worth learning."""
    text = str(
        payload.get("content") or payload.get("caption") or payload.get("draft") or ""
    ).strip()
    if len(text) < 60:
        return None
    platform = str(payload.get("platform", "") or "")
    fmt = str(payload.get("format", "") or "")
    topic = str(payload.get("topic", "") or payload.get("pillar", "") or "")
    vscore = payload.get("voice_score")
    digest = hashlib.sha256(f"{fmt}|{text[:120]}".encode()).hexdigest()[:16]
    return EventCreate(
        # event_type must be an allowed literal; the approved-exemplar marker
        # lives in payload.source (which _voice_exemplars keys on).
        event_type="note",
        payload={
            # category=voice_corpus so it flows into the voice bucket that
            # grounds every text post AND video script.
            "text": text,
            "category": "voice_corpus",
            "source": APPROVED_EXEMPLAR_SOURCE,
            # one corpus, tagged by origin (harvested/uploaded/approved/audited)
            # — the bm2.0 merge convention, so the reviewer can weigh them.
            "origin": "approved",
            "approved": True,
            "platform": platform,
            "format": fmt,
            "topic": topic,
            "voice_score": vscore,
            "filename": f"approved/{fmt or 'post'}",
        },
        raw_content=text,
        source=EventSource(
            adapter="approval_feedback",
            dedupe_key=f"approved-{digest}",
            raw_metadata={"category": "voice_corpus", "source": APPROVED_EXEMPLAR_SOURCE},
        ),
        entities=[
            "category:voice_corpus",
            APPROVED_EXEMPLAR_SOURCE,
            *([f"platform:{platform}"] if platform else []),
        ],
        effective_at=datetime.now(UTC),
        confidence=1.0,  # a human approval is authoritative
    )


async def record_approval(
    action_id: UUID, tenant_id: UUID | None = None
) -> str | None:
    """Positive feedback: promote an approved content draft into voice_corpus
    as an exemplar so the engine makes MORE like it. Idempotent (dedupe on
    content). Only learns from content drafts; returns None otherwise."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT action_type, payload FROM actions WHERE id = $1", action_id
        )
    if row is None or (row["action_type"] or "") != "content":
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    payload = payload or {}
    # Never reinforce a draft that FAILED voice-QA, even when a human
    # override-approved it. An override is "ship it despite the flag", not
    # "imitate this" — promoting it would teach the engine to repeat the very
    # violation the gate caught.
    if payload.get("flagged") is True or payload.get("qa_passed") is False:
        return None
    event = approval_to_event(payload)
    if event is None:
        return None
    stored = await ingest_many([event], tenant_id)
    return str(stored[0].id) if stored else None


async def record_rejection(
    action_id: UUID, reason: str, tenant_id: UUID | None = None
) -> str | None:
    """Load the rejected action and persist its lesson into memory.
    Returns the stored guardrail event id (or None if nothing usable)."""
    reason = (reason or "").strip()
    if not reason or reason.lower() in ("rejected", "reject"):
        # No real reason given → nothing to learn. Don't pollute memory.
        return None
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT payload FROM actions WHERE id = $1", action_id)
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)

    event = rejection_to_event(payload or {}, reason)
    stored = await ingest_many([event], tenant_id)
    await _distill_learned_avoid(payload or {}, reason, action_id, tenant_id)
    return str(stored[0].id) if stored else None


_DISTILL_SYSTEM = (
    "You distill one content rejection into durable never-again rules. From the "
    "rejection reason and the rejected draft, extract the short phrases or topics "
    "the brand should never publish again. Return JSON only: "
    '{"terms": [str (each a short literal phrase/topic to avoid, max 6 words)]}. '
    "Max 3 terms; empty list if the rejection is about quality, not content."
)


async def _distill_learned_avoid(
    payload: dict, reason: str, action_id: UUID, tenant_id: UUID | None
) -> None:
    """bm2.0 merge (PRD R6.1): besides the frustration event above, distill the
    rejection into avoid-terms on the profile envelope
    (guardrails.learned_avoid, source=queue_signal) so the ported reviewer's
    substring gate and the strategist read the same permanent rule. Best
    effort — a reject must never fail because the distillation hiccuped."""
    try:
        from .manager.contracts import Citation, FieldWrite, Source
        from .manager.profile import write_field
        from .manager.providers import get_providers

        draft = str(payload.get("content") or payload.get("caption") or "")[:600]
        raw = await get_providers().llm.complete_json(
            "extract", _DISTILL_SYSTEM,
            "Distill this rejection into never-again rules.\n"
            f"Reason: {reason[:300]}\n"
            f"Rejected draft (excerpt): {draft}",
        )
        terms = [str(t).strip() for t in (raw.get("terms") or []) if str(t).strip()][:3]
        if not terms:
            return
        slug = "".join(ch if ch.isalnum() else "-" for ch in terms[0].lower()).strip("-")[:60]
        async with acquire(tenant_id) as conn:
            await write_field(
                conn,
                FieldWrite(
                    section="guardrails",
                    field_key="guardrails.learned_avoid",
                    item_key=slug or f"action-{str(action_id)[:8]}",
                    value=terms,
                    source=Source.QUEUE_SIGNAL,
                    citations=[Citation(ref=f"action:{action_id}", note=f"rejected: {reason[:80]}")],
                    updated_by="queue_learning",
                ),
            )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("learning").exception(
            "rejection distillation failed for action %s", action_id
        )


EDIT_FEEDBACK_SOURCE = "edit_feedback"


def edit_to_event(payload: dict, old_content: str, new_content: str) -> EventCreate | None:
    """A human EDITED an AI draft before approving — the richest signal there
    is ('engine wrote X, a human changed it to Y'). Capture before→after as a
    corrective style rule (frustration category, so it's injected as an <avoid>
    and gated by voice-QA, same as rejections). Returns None for trivial,
    near-identical edits (typo fixes) so the ledger doesn't fill with noise."""
    import difflib

    old = (old_content or "").strip()
    new = (new_content or "").strip()
    if not old or not new or old == new:
        return None
    # Only a SUBSTANTIVE rewrite is worth a rule — skip reworded/cosmetic edits
    # (raises the bar vs. a simple ratio check so the ledger isn't flooded by
    # the far-more-frequent edits, which would evict rejection guardrails).
    if difflib.SequenceMatcher(None, old, new).ratio() > 0.85:
        return None
    platform = str(payload.get("platform", "") or "")
    fmt = str(payload.get("format", "") or "")
    topic = str(payload.get("topic", "") or payload.get("pillar", "") or "")
    # AVOID-only: capture the ORIGINAL phrasing as something to avoid. The
    # positive half (the edited text) is already learned when the manager
    # approves the edited draft (record_approval → voice_corpus). Putting the
    # edited text here would (a) double-count and (b) tell voice-QA to AVOID
    # the very phrasing the human chose.
    text = (
        f"A human REWROTE this AI {platform or 'social'} {fmt or 'post'}"
        f"{f' about “{topic}”' if topic else ''} before approving — the phrasing "
        f"below was NOT good enough and was rewritten. Avoid writing like this:\n"
        f"{old[:600]}"
    )
    digest = hashlib.sha256(f"{old[:80]}|{new[:80]}".encode()).hexdigest()[:16]
    return EventCreate(
        event_type="note",
        payload={
            "text": text,
            "category": FRUSTRATION_CATEGORY,
            "platform": platform,
            "format": fmt,
            "topic": topic,
            "source": EDIT_FEEDBACK_SOURCE,
        },
        raw_content=text,
        source=EventSource(
            adapter="edit_feedback",
            dedupe_key=f"edit-{digest}",
            raw_metadata={"category": FRUSTRATION_CATEGORY, "source": EDIT_FEEDBACK_SOURCE},
        ),
        entities=[
            f"category:{FRUSTRATION_CATEGORY}",
            EDIT_FEEDBACK_SOURCE,
            *([f"platform:{platform}"] if platform else []),
        ],
        effective_at=datetime.now(UTC),
        confidence=1.0,  # a human's hands-on edit is authoritative
    )


async def record_edit(
    action_id: UUID, old_content: str, new_content: str,
    tenant_id: UUID | None = None,
) -> str | None:
    """Persist a manager's pre-approval edit as a corrective style rule.
    Returns the stored event id, or None for trivial / non-content edits."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT action_type, payload FROM actions WHERE id = $1", action_id
        )
    if row is None or (row["action_type"] or "") != "content":
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    event = edit_to_event(payload or {}, old_content, new_content)
    if event is None:
        return None
    stored = await ingest_many([event], tenant_id)
    return str(stored[0].id) if stored else None


async def recent_guardrails(tenant_id: UUID | None = None, limit: int = 25) -> list[dict]:
    """The learned 'never do this' ledger from rejections, newest first —
    for surfacing in the UI so the team sees what the system has learned."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, payload, created_at FROM events
            WHERE payload ->> 'category' = $1
              AND payload ->> 'source' = 'rejection_feedback'
              AND superseded_by IS NULL
            ORDER BY created_at DESC LIMIT $2
            """,
            FRUSTRATION_CATEGORY, limit,
        )
    import json
    out = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        out.append({
            "id": str(r["id"]),
            "reason": p.get("reason", ""),
            "platform": p.get("platform", ""),
            "topic": p.get("topic", ""),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


__all__ = ["record_rejection", "rejection_to_event", "recent_guardrails",
           "FRUSTRATION_CATEGORY"]
