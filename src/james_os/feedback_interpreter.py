"""Feedback interpreter — the intelligence between a human's reason and a change.

Reads ONE feedback reason + the render-knob catalog and decides: a LIVE config
tweak we can apply instantly (a known knob, a concrete in-range value, high
confidence) or a QUEUED code change (new logic/layout, or anything not a known
knob). When unsure it queues — a plain-English note is always safe; a wrong
live tweak silently degrades every render. The interpreter never renders or
edits code; it only emits a decision that feedback_changes.record_change acts on.
"""

import json
from uuid import UUID

from .db import acquire
from .feedback_changes import record_change
from .llm import get_llm
from .render_tuning import get_render_tuning

# Knob key → (min, max). The ONLY knobs the interpreter may set live.
_KNOB_RANGE = {
    "broll_insert_min_dur": (1.0, 4.0),
    "broll_insert_max_dur": (1.0, 4.0),
}

_SYSTEM = (
    "You convert ONE piece of human feedback on a generated video, caption, or "
    "TEXT POST into a structured change decision. Feedback about WHAT IS SAID "
    "(tone, structure, wording — e.g. 'too salesy', 'sounds like a numbered "
    "list, not a story') is a content/voice change → kind=code_change with "
    "area='voice' (or 'text'); describe the concrete writing change "
    "(e.g. 'Generate scripts as one flowing story arc, never a numbered list'). "
    "You are given the feedback reason, the "
    "production context, and a KNOWN_KNOBS catalog — the ONLY render parameters "
    "that can change live without a code deploy (each has key, current value, "
    "range, unit). Decide exactly ONE: (a) live_config — the feedback maps "
    "cleanly to ONE knob in KNOWN_KNOBS AND you can name a concrete new value "
    "inside its range; or (b) code_change — it needs new logic, a new layout, a "
    "new element, or a value for something NOT in KNOWN_KNOBS. When unsure, "
    "choose code_change (a queued plain-English note is always safe; a wrong "
    "live tweak silently degrades every future render). NEVER invent a knob "
    "key.\n\n"
    "Return STRICT JSON: {\"kind\": \"live_config\"|\"code_change\", "
    "\"area\": \"broll\"|\"captions\"|\"music\"|\"pacing\"|\"voice\"|\"layout\"|"
    "\"text\"|\"general\", \"diagnosis\": \"<short: what was disliked>\", "
    "\"plain_english\": \"<what is changing or queued, plain user-facing English; "
    "PRESENT tense if live_config (it's applied now), INTENT/FUTURE tense if "
    "code_change (it's queued). e.g. 'B-roll clips now stay on screen longer "
    "(2s to 4s)' or 'Add a split-screen layout — speaker on top, text/visual "
    "below'>\", \"config_key\": \"<a KNOWN_KNOBS key, or empty>\", "
    "\"config_value\": <number or null>, \"confidence\": <0.0-1.0>}.\n"
    "Example: feedback 'the 2-second B-roll inserts feel too short' → "
    "kind=live_config, area=broll, config_key=broll_insert_max_dur, "
    "config_value=4, confidence~0.9. Feedback 'captions overlap his face / need a "
    "split-screen' → code_change."
)


def _known_knobs_text(knobs: dict) -> str:
    return (
        f"- broll_insert_min_dur: B-roll insert MIN on-screen seconds. "
        f"current={knobs['broll_insert_min_dur']}, range 1.0-4.0, unit=seconds\n"
        f"- broll_insert_max_dur: B-roll insert MAX on-screen seconds. "
        f"current={knobs['broll_insert_max_dur']}, range 1.0-4.0, unit=seconds"
    )


def _guard(out: dict) -> dict | None:
    if not isinstance(out, dict):
        return None
    plain = str(out.get("plain_english") or "").strip()
    if not plain:
        return None
    kind = out.get("kind")
    ck = out.get("config_key") or None
    cv = out.get("config_value")
    try:
        conf = float(out.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    rng = _KNOB_RANGE.get(ck or "")
    valid_live = (
        kind == "live_config" and rng is not None
        and isinstance(cv, (int, float))
        and rng[0] <= float(cv) <= rng[1] and conf >= 0.75
    )
    if not valid_live:
        kind, ck, cv = "code_change", None, None
    return {
        "kind": kind,
        "area": str(out.get("area") or "general"),
        "diagnosis": str(out.get("diagnosis") or "")[:300],
        "plain_english": plain[:300],
        "config_key": ck,
        "config_value": cv,
        "confidence": conf,
    }


async def interpret_one(reason: str, context: dict, knobs: dict) -> dict | None:
    reason = (reason or "").strip()
    if not reason or reason.lower() in ("rejected", "reject"):
        return None
    user = (
        f"FEEDBACK: {reason}\n"
        f"CONTEXT: mode={context.get('mode', '')}, "
        f"caption_style={context.get('caption_style', '')}, "
        f"status={context.get('status', '')}\n\n"
        f"KNOWN_KNOBS (the only things changeable live):\n{_known_knobs_text(knobs)}\n\n"
        "Return the JSON decision."
    )
    try:
        out = await get_llm().complete_json(
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=500,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 — a parse/LLM failure just skips this item
        return None
    return _guard(out)


async def interpret_recent_feedback(tenant_id: UUID | None = None, limit: int = 40) -> dict:
    """Read every recent VIDEO rejection (video_productions.review_reason) AND
    TEXT/post rejection (actions.rejection_reason_code, action_type='content'),
    interpret each, and record the change (applying live knobs, queuing code/
    content changes). Idempotent via the feedback_changes dedupe key."""
    knobs = await get_render_tuning(tenant_id)
    async with acquire(tenant_id) as conn:
        vids = await conn.fetch(
            "SELECT id, review_reason AS reason, review_status AS status, mode, caption_style "
            "FROM video_productions WHERE coalesce(review_reason,'') <> '' "
            "  AND review_status IN ('rejected','approved_with_notes') "
            "ORDER BY reviewed_at DESC NULLS LAST LIMIT $1",
            limit,
        )
        txts = await conn.fetch(
            "SELECT id, rejection_reason_code AS reason, payload "
            "FROM actions WHERE status='rejected' "
            "  AND coalesce(rejection_reason_code,'') <> '' AND action_type='content' "
            "ORDER BY decided_at DESC NULLS LAST LIMIT $1",
            limit,
        )

    processed = 0
    recorded = 0

    async def _do(reason: str, context: dict, production_id, source_event_id=None) -> None:
        nonlocal processed, recorded
        processed += 1
        decision = await interpret_one(reason, context, knobs)
        if not decision:
            return
        item = await record_change(
            area=decision["area"],
            diagnosis=decision["diagnosis"],
            plain_english=decision["plain_english"],
            kind=decision["kind"],
            config_key=decision["config_key"],
            config_value=decision["config_value"],
            confidence=decision["confidence"],
            production_id=production_id,
            source_event_id=source_event_id,
            tenant_id=tenant_id,
        )
        if item:
            recorded += 1

    # Video feedback — traced to its production.
    for r in vids:
        await _do(
            r["reason"],
            {"mode": r["mode"], "caption_style": r["caption_style"], "status": r["status"]},
            r["id"],
        )
    # Text / post feedback — now traced to the rejected action (was None).
    for r in txts:
        payload = r["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}
        ctx = {
            "mode": f"text post ({(payload or {}).get('format', 'post')})",
            "caption_style": "",
            "status": "rejected",
        }
        await _do(r["reason"], ctx, None, source_event_id=r["id"])

    return {"processed": processed, "recorded": recorded}


_BG_TASKS: set = set()


def kick_interpret_background(tenant_id=None) -> None:
    """Fire-and-forget board refresh after new feedback lands. Keeps the
    "What's changing next" board continuously current instead of waiting for
    a manual Refresh click. Idempotent (record_change dedupes on content);
    failures are swallowed — the board is advisory, never in a request path."""
    import asyncio
    try:
        task = asyncio.create_task(interpret_recent_feedback(tenant_id))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    except RuntimeError:
        pass  # no running loop — the next manual refresh covers it


__all__ = ["interpret_one", "interpret_recent_feedback", "kick_interpret_background"]
