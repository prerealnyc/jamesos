"""The brand's first posts, made from what it picked off the competitor shelf.

Onboarding ends with a brand that knows who it is and has just looked at ~70
real posts from the accounts it competes with. This turns the ones it picked
into actual drafts in the approval queue — the shortest path from "here is
what the good accounts do" to "here is yours".

The three verdicts route DIFFERENTLY, which is the whole reason they are three
verdicts and not one flag:

    saved     replicate the PIECE — match its hook pattern, structure and
              pacing, in our voice about our world
    template  reuse the STRUCTURE only — the layout and format carry over,
              the subject is ours entirely
    idea      take the CONCEPT — the angle is the seed, the execution is
              ours from scratch, and the post it came from is the reference

Nothing here copies. Every draft goes through the same content engine and
voice-QA gate as any other, and a verbatim rip fails that gate.

A generated post is marked 'queued' so a second run does not redraft it —
which also makes this safe to call again as the brand picks more.
"""

from __future__ import annotations

import json
from uuid import UUID

from .db import acquire
from .models import ContentBrief

# One brief per verdict. Kept as prose rather than a template string because
# the difference between them is the instruction, not the formatting.
_STEER = {
    "saved": (
        "Model this on a post that is genuinely working for a competitor in "
        "our niche RIGHT NOW. Match its HOOK pattern, structure and pacing. "
        "Write it 100% in our brand voice, about OUR world — do not copy its "
        "words, claims, numbers or specifics."
    ),
    "template": (
        "Reuse only the STRUCTURE of this competitor post — its format, the "
        "order it reveals things in, how it opens and closes. The subject is "
        "entirely ours and shares nothing with theirs. Think of it as a "
        "reusable layout we are filling with our own material."
    ),
    "idea": (
        "Take only the CONCEPT behind this competitor post — the angle, the "
        "question it answers, the reason it lands. Execute it from scratch in "
        "our voice, in whatever structure suits us best. This is a seed, not "
        "a model to imitate."
    ),
}


def _reference(post: dict) -> str:
    """What the writer is shown about the source post."""
    bits = [f'Reference post by @{post.get("handle") or "a competitor"}']
    if post.get("format"):
        bits.append(f'format: {post["format"]}')
    if post.get("hook_pattern"):
        bits.append(f'hook pattern: {post["hook_pattern"]}')
    if post.get("engagement_rate"):
        bits.append(f'engagement: {round(float(post["engagement_rate"]) * 100, 2)}%')
    head = " · ".join(bits)
    body = (post.get("caption") or "")[:1500]
    why = (post.get("why_it_works") or "").strip()
    out = f'{head}\n"""\n{body}\n"""'
    if why:
        out += f"\nWhat makes it work: {why[:400]}"
    return out


async def picked_posts(
    limit: int = 10, include_queued: bool = False, tenant_id: UUID | None = None
) -> list[dict]:
    """The brand's picks, best first. Highest engagement leads, because if we
    are only making ten things they should be the ten with the most evidence
    behind them."""
    statuses = ["saved", "template", "idea"]
    if include_queued:
        statuses.append("queued")
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.caption, p.url, p.media_type, p.engagement_rate,
                      p.replicate_status, p.replicate_note,
                      c.handle, c.platform,
                      a.format, a.hook, a.hook_pattern, a.topic,
                      a.why_it_works, a.classification
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE p.replicate_status = ANY($1::text[])
             ORDER BY p.engagement_rate DESC NULLS LAST
                LIMIT $2""", statuses, max(1, min(limit, 30)))
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        if isinstance(d.get("classification"), str):
            d["classification"] = json.loads(d["classification"])
        out.append(d)
    return out


async def generate_first_posts(
    n: int = 5, platform: str = "instagram", tenant_id: UUID | None = None,
    progress=None,
) -> dict:
    """Draft the brand's first posts from its picks.

    Sequential on purpose. These land in a human approval queue, and a burst
    of ten simultaneous generations buys a few seconds while making a runaway
    much harder to notice or stop.
    """
    from .content import generate_content

    picks = await picked_posts(limit=n, tenant_id=tenant_id)
    if not picks:
        return {"generated": 0, "drafts": [],
                "note": "Nothing picked yet — choose posts to Replicate, "
                        "Templatize or save as Ideas first."}

    drafts, failed = [], []
    for i, p in enumerate(picks):
        verdict = p.get("replicate_status") or "saved"
        if progress:
            progress({"stage": f"drafting {i + 1} of {len(picks)}",
                      "done": i, "total": len(picks)})
        steer = _STEER.get(verdict, _STEER["saved"]) + "\n\n" + _reference(p)
        if (p.get("replicate_note") or "").strip():
            steer += f"\n\nThe brand added: {p['replicate_note'].strip()}"
        brief = ContentBrief(
            platform=platform,
            format=("reel_script" if p.get("media_type") == "video" else "post"),
            topic=(p.get("topic") or "").strip()
                  or "a piece grounded in what works in our niche",
            extra_instructions=steer,
        )
        try:
            draft = await generate_content(brief)
        except Exception as e:  # noqa: BLE001 — one bad draft ≠ the batch
            failed.append({"post_id": p["id"], "error": str(e)[:160]})
            continue
        # Mark it drafted so a second run does not redo the same pick.
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE competitor_posts SET replicate_status = 'queued' "
                "WHERE id = $1::uuid", p["id"])
        drafts.append({
            "post_id": p["id"], "from": f"@{p.get('handle')}",
            "verdict": verdict,
            # ContentDraft carries `draft`, not `text` — reading `text` would
            # have returned "" for every post and looked like empty output.
            "draft": (getattr(draft, "draft", "") or "")[:400],
            "voice_score": getattr(draft, "voice_score", None),
            "status": getattr(draft, "status", ""),
            "action_id": str(getattr(draft, "action_id", "") or ""),
        })

    return {"generated": len(drafts), "drafts": drafts, "failed": failed,
            "note": ""}


async def run_first_posts(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point. Tenant-bound and explicit."""
    cfg = config or {}
    await generate_first_posts(
        n=int(cfg.get("n") or 5),
        platform=str(cfg.get("platform") or "instagram"),
        tenant_id=tenant_id)


__all__ = ["picked_posts", "generate_first_posts", "run_first_posts"]
