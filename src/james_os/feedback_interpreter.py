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
from .render_tuning import KNOBS, get_render_tuning

# Knob key → (min, max). The ONLY knobs the interpreter may set live.
# Derived from render_tuning.KNOBS: a hand-maintained copy would drift, and a
# knob missing from here is silently downgraded to a queued note — which is
# exactly how image feedback used to die (the catalog held b-roll durations
# only, so every complaint about a picture became a to-do nobody actioned).
_KNOB_RANGE = {k: (v["min"], v["max"]) for k, v in KNOBS.items()}

# Knobs that only take effect in ONE card layout.
#
# The three designed layouts have different geometry: hero_quote puts the photo
# in a tall narrow panel beside the text, statement frames a wide photo under
# the words, and brand_quote has no photo at all. A single knob cannot mean the
# same thing in all three, and only hero_quote_card reads these.
#
# Without this gate, rejecting a brand_quote card for "he's too small" set
# image_photo_width, feedback_changes marked it 'applied' and told the owner it
# was fixed, and the next render was byte-identical — the reject-loop this
# catalog exists to end. The layout is checked deterministically rather than
# hinted in the prompt, because a hint is something a model can talk itself out
# of. Unknown layout is treated as a mismatch: queuing a note is always safe.
_KNOB_LAYOUT = {
    "image_photo_width": "hero_quote",
    "image_text_gutter": "hero_quote",
    "image_quote_max_pt": "hero_quote",
    "image_photo_focus_x": "hero_quote",
    "image_photo_fade": "hero_quote",
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
    "\"image\"|\"text\"|\"general\", \"diagnosis\": \"<short: what was disliked>\", "
    "\"plain_english\": \"<what is changing or queued, plain user-facing English; "
    "PRESENT tense if live_config (it's applied now), INTENT/FUTURE tense if "
    "code_change (it's queued). e.g. 'B-roll clips now stay on screen longer "
    "(2s to 4s)' or 'Add a split-screen layout — speaker on top, text/visual "
    "below'>\", \"config_key\": \"<a KNOWN_KNOBS key, or empty>\", "
    "\"config_value\": <number or null>, \"confidence\": <0.0-1.0>}.\n"
    "Feedback on a generated IMAGE (a post card: the person's photo with the "
    "brand quote set over it) is usually a knob, not new code — reach for "
    "KNOWN_KNOBS first and use area='image'. Map the complaint to the physical "
    "cause: 'he's too small / hidden / hard to see' → a WIDER photo; 'the text "
    "covers him / is crowding him' → a BIGGER gutter; 'the text is too big / "
    "shouty' → a SMALLER max type size; 'his head is cut off' → a LOWER photo "
    "focus point; 'his face looks faded / washed out / soft at the edge / "
    "half-dissolved / blended into the background / not crisp' → a SMALLER photo "
    "FADE (image_photo_fade lower). CAUTION: 'faded / dissolving into the "
    "background' is the fade knob; but if the PHOTO ITSELF is out of focus, "
    "low-resolution, grainy or genuinely blurry (a camera problem, not the "
    "edge), that is NOT a knob → code_change, area=image (the photo picker "
    "already screens sharpness; a plain-English note is logged). Only queue a "
    "code_change for an image when it needs a layout or element that does not "
    "exist yet, OR to fix genuine source-photo blur.\n"
    "Examples: 'the 2-second B-roll inserts feel too short' → live_config, "
    "area=broll, config_key=broll_insert_max_dur, config_value=4, "
    "confidence~0.9. 'James is half hidden behind the text, make him clearly "
    "visible' → live_config, area=image, config_key=image_photo_width, "
    "config_value=0.58, confidence~0.85. 'the writing sits right on his face' → "
    "live_config, area=image, config_key=image_text_gutter, config_value=120. "
    "'his face is all faded into the blue, make him clear' → live_config, "
    "area=image, config_key=image_photo_fade, config_value=0.12, "
    "confidence~0.85. 'the photo of him is blurry / out of focus' → code_change, "
    "area=image (source sharpness, not a knob). 'put his photo in a circle at "
    "the top' → code_change (no such layout)."
)


def _known_knobs_text(knobs: dict) -> str:
    """Render the catalog from render_tuning.KNOBS, so adding a knob there is
    all it takes for the interpreter to be able to reach for it."""
    return "\n".join(
        f"- {key}: {spec['help']} current={knobs.get(key, spec['default'])}, "
        f"range {spec['min']}-{spec['max']}, unit={spec['unit']}"
        for key, spec in KNOBS.items()
    )


def _guard(out: dict, layout: str = "") -> dict | None:
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
    # A layout-scoped knob is only live when the rejected image used that exact
    # layout. Otherwise the value would be stored, announced as applied, and
    # change nothing that the owner can see.
    needs_layout = _KNOB_LAYOUT.get(ck or "")
    layout_ok = needs_layout is None or needs_layout == (layout or "")
    valid_live = (
        kind == "live_config" and rng is not None
        and isinstance(cv, (int, float))
        and rng[0] <= float(cv) <= rng[1] and conf >= 0.75
        and layout_ok
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
    layout = str(context.get("image_format") or "")
    user = (
        f"FEEDBACK: {reason}\n"
        f"CONTEXT: mode={context.get('mode', '')}, "
        f"caption_style={context.get('caption_style', '')}, "
        f"status={context.get('status', '')}"
        + (f", image_layout={layout}" if layout else "")
        + "\n\n"
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
    return _guard(out, layout)


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
            # Which card layout was rendered — stamped by
            # main._generate_designed_post_image. Absent on older rows and on
            # plain photo posts, which _guard treats as "cannot verify", so an
            # image knob stays a queued note rather than a silent no-op.
            "image_format": (payload or {}).get("image_format", ""),
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
