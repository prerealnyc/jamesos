"""Commitments — action items mined from meeting transcripts / documents,
ported from the PreReal Intelligence platform's extractor. Runs after a
document is indexed with non-empty text; safely best-effort (extraction
errors never break the upload flow)."""

from __future__ import annotations

import re
from uuid import UUID

from .db import acquire
from .llm import get_llm

_STATUSES = {"open", "in_progress", "blocked", "done", "missed"}
_MAX_CHARS = 80_000

_SYSTEM = """You read meeting transcripts and similar documents, and you extract concrete commitments (action items) from them.

Output rules — strict JSON, no prose:
{
  "commitments": [
    { "owner": "string", "text": "string", "due": "YYYY-MM-DD or null", "status": "open" }
  ]
}

What counts as a commitment:
- A specific person (or named agent) said they would do a specific thing.
- A team or role was assigned a deliverable.
- A blocker was named that requires action ("we need X before Y can happen").

What does NOT count:
- Generic discussion, opinions, background context.
- "Maybe we should consider..." with no owner.
- Already-completed work being recounted.

Owner field:
- Use the person's first name when clear.
- For AI agents: use the agent's name.
- For unknown owners: "unassigned".

Return an empty array if no actionable commitments exist. Never invent commitments. Never include explanations outside the JSON."""


def looks_like_meeting_doc(mime: str, filename: str) -> bool:
    """The post-index trigger heuristic (parity): audio/video sources or
    note-like filenames."""
    from .documents import is_transcribable

    return is_transcribable(mime, filename) or bool(
        re.search(r"transcript|meeting|notes?|call", filename, re.I))


async def extract_commitments_from_text(
    *,
    file_id: str,
    filename: str,
    text: str,
    entity_id: str | None = None,
    author_hint: str = "transcript",
    tenant_id: UUID | None = None,
) -> dict:
    """Mine action items out of a document's text → rows in `commitments`.
    Returns {extracted, skipped, reason?}. Never raises."""
    if not text or len(text.strip()) < 50:
        return {"extracted": 0, "skipped": True,
                "reason": "text too short to extract from"}
    body = text[:_MAX_CHARS]
    try:
        out = await get_llm().complete_json(
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (f"Document: {filename}\n\nExtract every action "
                            f"item / commitment from the following text. "
                            f"Respond with the JSON envelope only.\n\n---\n"
                            f"{body}\n---"),
            }],
            max_tokens=2048, temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        return {"extracted": 0, "skipped": True, "reason": str(e)[:300]}
    items = (out.get("commitments") or []) if isinstance(out, dict) else []
    rows = []
    for c in items:
        if not isinstance(c, dict):
            continue
        owner = str(c.get("owner") or "").strip()
        ctext = str(c.get("text") or "").strip()
        if not owner or not ctext:
            continue
        due = str(c.get("due") or "").strip()
        due = due if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else None
        status = str(c.get("status") or "open")
        rows.append({
            "text": ctext, "owner": owner.lower(),
            "due": due,
            "status": status if status in _STATUSES else "open",
            "notes": (f"Extracted from: {filename}\nEvidence in transcript: "
                      f"{c['evidence']}") if c.get("evidence")
                     else f"Extracted from: {filename}",
        })
    if not rows:
        return {"extracted": 0, "skipped": False}
    try:
        async with acquire(tenant_id) as conn:
            for r in rows:
                await conn.execute(
                    """INSERT INTO commitments
                         (text, owner, author, entity_id, source_file_id,
                          due, status, source_kind, notes)
                       VALUES ($1,$2,$3,$4,$5,$6::date,$7,
                               'transcript-extraction',$8)""",
                    r["text"], r["owner"], author_hint, entity_id,
                    UUID(file_id) if file_id else None,
                    r["due"], r["status"], r["notes"],
                )
        return {"extracted": len(rows), "skipped": False}
    except Exception as e:  # noqa: BLE001
        return {"extracted": 0, "skipped": True, "reason": str(e)[:300]}


async def list_commitments(
    status: str = "", tenant_id: UUID | None = None,
) -> list[dict]:
    async with acquire(tenant_id) as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM commitments WHERE status = $1 "
                "ORDER BY due NULLS LAST, created_at DESC", status)
        else:
            rows = await conn.fetch(
                "SELECT * FROM commitments "
                "ORDER BY (status = 'done'), due NULLS LAST, created_at DESC")
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d.pop("tenant_id", None)
        d["source_file_id"] = str(d["source_file_id"]) if d.get("source_file_id") else None
        for k in ("due", "created_at", "updated_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


async def update_commitment_status(
    commitment_id: UUID, status: str, tenant_id: UUID | None = None,
) -> bool:
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {sorted(_STATUSES)}")
    async with acquire(tenant_id) as conn:
        res = await conn.execute(
            "UPDATE commitments SET status=$2, updated_at=now() WHERE id=$1",
            commitment_id, status,
        )
    return res.rsplit(" ", 1)[-1] != "0"


__all__ = [
    "extract_commitments_from_text", "looks_like_meeting_doc",
    "list_commitments", "update_commitment_status",
]
