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
# What the competitor's layout maps to in OUR image machine. The `template`
# verdict means "reuse the structure", so the structure has to actually carry
# over — otherwise templatizing and replicating produce the same picture.
# Keys are what design_eye reports in design_dna.layout_family, plus the
# classifier's coarser `format` as a fallback.
_LAYOUT_TO_FORMAT = {
    "full_bleed_photo": "full_bleed",
    "photo_with_overlay": "minimal_over",
    "photo with overlay": "minimal_over",
    "split_panel": "editorial_split",
    "photo_beside_text": "editorial_split",
    "photo beside text": "editorial_split",
    "framed_photo": "framed_print",
    "text_only": "brand_quote",
    "typographic": "bold_statement",
    "quote_card": "brand_quote",
}
# Formats that need the brand's own photography. A brand that has none must
# never be handed one of these: the render comes back blank and the post looks
# broken, which is worse than a clean typographic card.
_NEEDS_PHOTO = frozenset({
    "hero_quote", "statement", "full_bleed", "editorial_split",
    "minimal_over", "framed_print",
})
_TEXT_ONLY = ("brand_quote", "bold_statement", "big_stat")

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
    behind them.

    The cap was 30 ROWS across all verdicts, which was fine while the only
    caller drafted five at a time and fatal once the pool reader arrived: a
    brand that picked 43 posts had 30 returned, four of them 'saved', so
    thirteen 'idea' picks were invisible on every run, not just the first —
    the same top thirty come back forever. Reading a few hundred small rows
    costs nothing; silently losing the owner's picks costs everything."""
    statuses = ["saved", "template", "idea"]
    if include_queued:
        statuses.append("queued")
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.caption, p.url, p.media_type, p.engagement_rate,
                      p.replicate_status, p.replicate_note,
                      c.handle, c.platform,
                      a.format, a.hook, a.hook_pattern, a.topic,
                      a.why_it_works, a.classification, a.design_dna
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE p.replicate_status = ANY($1::text[])
             ORDER BY p.engagement_rate DESC NULLS LAST
                LIMIT $2""", statuses, max(1, min(limit, 200)))
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        for k in ("classification", "design_dna"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


def _format_for(post: dict, has_photos: bool) -> str:
    """Which image layout this pick should produce.

    A `template` pick is the strongest signal we have — the brand explicitly
    said "reuse this structure" — so its competitor layout is honoured where we
    can map it. An `idea` pick is the opposite: the concept is the seed and the
    execution is ours, so the art director picks freely (empty string).

    Whatever is chosen, a photo layout is only allowed when the brand actually
    has photos to put in it.
    """
    verdict = post.get("replicate_status") or "saved"
    if verdict == "idea":
        return ""                       # let the art director decide
    dna = post.get("design_dna") or {}
    key = str(dna.get("layout_family") or post.get("format") or "").strip().lower()
    fmt = _LAYOUT_TO_FORMAT.get(key, "")
    if not fmt:
        return ""
    if fmt in _NEEDS_PHOTO and not has_photos:
        # Keep the spirit of the layout without the photography it assumes:
        # a text-forward card rather than an empty frame.
        return "bold_statement" if "overlay" in key or "full" in key else "brand_quote"
    return fmt


async def _has_hero_photos(tenant_id: UUID | None) -> bool:
    """Does this brand have its own photography to design with?

    Most brands arrive at onboarding without any, and the designed-image path
    silently produces nothing when a photo layout finds an empty library — so
    this is checked BEFORE a format is chosen, not discovered during the render.
    """
    try:
        from .hero_context import get_hero_photo_files
        refs = await get_hero_photo_files(tenant_id=tenant_id, limit=1)
        return bool(refs)
    except Exception:  # noqa: BLE001 — assume none; text-only always renders
        return False


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

    has_photos = await _has_hero_photos(tenant_id)
    drafts, failed = [], []
    last_format = ""
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
        # ---- the image ----
        # A caption with no picture is not a post. The layout comes from what
        # the brand picked; the background is generated, so this works for a
        # brand that arrived with no photography of its own.
        action_id = getattr(draft, "action_id", None)
        image_url, image_format = "", ""
        if action_id:
            try:
                from .main import _generate_designed_post_image
                image_url, image_format = await _generate_designed_post_image(
                    action_id,
                    (p.get("topic") or "").strip(),
                    getattr(draft, "draft", "") or "",
                    tenant_id,
                    # Vary the card type across a batch so five posts do not
                    # all arrive as the same layout.
                    avoid=last_format,
                    force_format=_format_for(p, has_photos),
                )
                last_format = image_format or last_format
            except Exception as e:  # noqa: BLE001 — the copy still stands
                failed.append({"post_id": p["id"],
                               "error": f"image: {type(e).__name__}: {e}"[:160]})

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
            "image_url": image_url,
            "image_format": image_format,
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
