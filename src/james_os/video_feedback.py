"""Video feedback → learning loop.

Mirrors the text-content learning loop in `learning.py`. When a
marketing manager rejects a rendered video and tells us why, we turn
that reason into a `video_feedback`-category memory event with
structured tags (caption_style, mode, what-broke). The next render
prompts retrieve these events and surface them as <avoid> rules so
the system stops repeating the same mistake.

Why a SEPARATE category from `frustration` (text):
  * Text rejections shape WHAT is written (voice, tone, hashtags).
  * Video rejections shape HOW it's rendered (caption position,
    B-roll length, music volume, transition pacing).
  Mixing them in one bucket would pollute both prompts. The
  retrieval queries in each pipeline can stay narrow.

Tag taxonomy (free-form text the LLM later reads back):
  * `captions`   — position, color, weight, safe-zone issues
  * `broll`      — animated inserts, photoreal stills, pacing
  * `music`      — track, volume, vibe
  * `pacing`     — overall cut speed, dwell times
  * `voice`      — HeyGen avatar / VO issues
  * `general`    — anything else
The set is open — the LLM at retrieval time matches by semantic
similarity, not by exact tag.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .ingestion import ingest_many
from .models import EventCreate, EventSource

VIDEO_FEEDBACK_CATEGORY = "video_feedback"


def _heuristic_tags(reason: str) -> list[str]:
    """Cheap keyword tagging for the structured filter; the LLM at
    retrieval time also does semantic matching, but this gives us
    fast `WHERE tags @> ARRAY['captions']` reads for the dashboard.
    """
    r = (reason or "").lower()
    tags: list[str] = []
    if any(w in r for w in ("caption", "subtitle", "text", "danger zone", "safe zone")):
        tags.append("captions")
    if any(w in r for w in ("broll", "b-roll", "cutaway", "footage", "insert", "2 second", "2 seconds", "stock")):
        tags.append("broll")
    if any(w in r for w in ("music", "track", "sound", "audio mix", "volume")):
        tags.append("music")
    if any(w in r for w in ("pace", "pacing", "speed", "cut", "dwell", "linger")):
        tags.append("pacing")
    if any(w in r for w in ("voice", "avatar", "heygen", "vo ", "voiceover")):
        tags.append("voice")
    if not tags:
        tags.append("general")
    return tags


def _feedback_to_event(
    production: dict, reason: str, status: str,
) -> EventCreate:
    """Build the memory event that the video pipeline will read on
    its next render. `status` is 'rejected' or 'approved_with_notes'
    — both are useful signal; approval-with-notes is "this is
    acceptable but next time also do X."
    """
    mode = production.get("mode") or ""
    caption_style = production.get("caption_style") or ""
    platform = production.get("platform") or ""
    script = (production.get("script") or "")[:180]
    tags = _heuristic_tags(reason)
    verdict = "REJECTED" if status == "rejected" else "APPROVED WITH NOTES"

    text = (
        f"{verdict} BY A HUMAN — learn from this and improve future renders. "
        f"A {platform or 'social'} video"
        f"{f' rendered in {mode} mode' if mode else ''}"
        f"{f' with caption style {caption_style}' if caption_style else ''}"
        f" was {status}. "
        f"Reason: {reason}. "
        f"Treat this as a hard rule for all future video renders. "
        f"Aspect tagged: {', '.join(tags)}. "
        f"Hook of the rejected video (so you can recognise context — "
        f"do NOT reproduce the offending behaviour): {script}"
    )
    digest = hashlib.sha256(
        f"{production.get('id','')}|{reason}|{','.join(tags)}".encode()
    ).hexdigest()[:16]

    return EventCreate(
        event_type="note",
        payload={
            "text": text,
            "category": VIDEO_FEEDBACK_CATEGORY,
            "status": status,
            "mode": mode,
            "caption_style": caption_style,
            "platform": platform,
            "reason": reason,
            "tags": tags,
            "production_id": str(production.get("id", "")),
            "source": "video_feedback",
        },
        raw_content=text,
        source=EventSource(
            adapter="video_feedback",
            dedupe_key=f"video-feedback-{digest}",
            raw_metadata={
                "category": VIDEO_FEEDBACK_CATEGORY,
                "reason": reason,
                "tags": tags,
            },
        ),
        entities=[
            f"category:{VIDEO_FEEDBACK_CATEGORY}",
            *([f"mode:{mode}"] if mode else []),
            *([f"caption_style:{caption_style}"] if caption_style else []),
            *[f"tag:{t}" for t in tags],
        ],
        effective_at=datetime.now(UTC),
        confidence=1.0,
    )


async def record_video_feedback(
    production_id: UUID, reason: str, status: str = "rejected",
    tenant_id: UUID | None = None,
) -> str | None:
    """Persist the manager's reason into memory so the next render
    reads it. Returns the new event id, or None if there's nothing
    learnable (empty reason / production not found)."""
    reason = (reason or "").strip()
    if not reason:
        return None
    if status not in ("rejected", "approved_with_notes"):
        status = "rejected"
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT id, mode, caption_style, image_style, platform,
                      script
                 FROM video_productions WHERE id = $1""",
            production_id,
        )
    if row is None:
        return None
    production = dict(row)
    production["id"] = str(production["id"])
    event = _feedback_to_event(production, reason, status)
    stored = await ingest_many([event], tenant_id)
    return str(stored[0].id) if stored else None


async def recent_video_feedback(
    tenant_id: UUID | None = None, limit: int = 25,
    tag: str = "",
) -> list[dict]:
    """Newest-first ledger of video feedback for the UI. When `tag` is
    set (e.g. 'captions'), filters to events whose tags include it.

    Used by:
      * /library — show the user what has been learned
      * the video pipeline (Phase 2) — pull recent items into the
        rendering prompts as <avoid> rules
    """
    clauses = [
        "payload ->> 'category' = $1",
        "payload ->> 'source' = 'video_feedback'",
        "superseded_by IS NULL",
    ]
    args: list = [VIDEO_FEEDBACK_CATEGORY]
    if tag:
        args.append(tag)
        clauses.append(f"payload -> 'tags' ? ${len(args)}")
    sql = f"""
        SELECT id, payload, created_at
          FROM events
         WHERE {' AND '.join(clauses)}
         ORDER BY created_at DESC LIMIT {int(limit)}
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *args)
    out: list[dict] = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p)
        out.append({
            "id": str(r["id"]),
            "reason": p.get("reason", ""),
            "status": p.get("status", "rejected"),
            "mode": p.get("mode", ""),
            "caption_style": p.get("caption_style", ""),
            "platform": p.get("platform", ""),
            "tags": p.get("tags", []),
            "production_id": p.get("production_id", ""),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


async def set_production_review(
    production_id: UUID, status: str, reason: str = "",
    tenant_id: UUID | None = None,
) -> bool:
    """Update the production row's review fields so the Library shows
    the right chip. Independent of the learning event — we ALWAYS
    write the chip, even on empty-reason rejections, but only learn
    when there's something to learn.
    """
    if status not in ("approved", "rejected", "approved_with_notes"):
        return False
    async with acquire(tenant_id) as conn:
        tag = await conn.execute(
            """UPDATE video_productions
                  SET review_status = $2,
                      review_reason = $3,
                      reviewed_at = now(),
                      updated_at = now()
                WHERE id = $1""",
            production_id, status, reason,
        )
    return tag.endswith(" 1")


__all__ = [
    "record_video_feedback", "recent_video_feedback",
    "set_production_review", "VIDEO_FEEDBACK_CATEGORY",
]
