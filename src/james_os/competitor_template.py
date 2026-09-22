"""Templatize — a competitor's reel becomes a reusable template in YOUR library.

"Templatize" says the STRUCTURE is worth keeping, not this one post. That only
means something if the structure becomes an object the brand owns and can
render again. Otherwise the verdict is a hint consumed once at draft time and
forgotten.

There is already a whole system for exactly this shape of thing:

    style_reference video → Design Inspector → style_templates row
                                                     ↓
                                    template_apply.map_template_to_render()
                                                     ↓
                                    video_pipeline.start_production(...)

So this mints a style_templates row rather than inventing a parallel store. The
moment it lands, everything downstream works unchanged — the template library,
/templates/{id}/replicate, the autopilot's distinct-template picker, and
template_apply's render mapping.

The raw material already exists too. perception watched the competitor's reel
and returned hook / structure / pacing / captions / visual_style — the same
kind of read the Design Inspector produces from an uploaded reference. We are
not analysing anything new here; we are translating a read we already hold into
the builder vocabulary.

That vocabulary is CLOSED (template_spec.capabilities()), and it is handed to
the model as the only permitted values. A template that cannot be rendered
cannot be authored: build_template() validates and raises rather than storing
something half-valid.

HONEST SCOPE: style_templates is a VIDEO production template — mode, captions,
music bed, beat structure. A still or carousel has no honest mapping onto that,
so templatizing one is handled elsewhere: competitor_kickoff maps its layout
family onto an image format at generation time. Minting a video template from
a photo would be a lie dressed as a feature.
"""

from __future__ import annotations

import json
from uuid import UUID

from .db import acquire
from .template_spec import build_template, capabilities

_SYSTEM = """You translate a competitor's reel — already watched and described
— into a reusable production template for a DIFFERENT brand.

You are given that description and the EXACT vocabulary the render engine
accepts. Every value you return must come from that vocabulary; nothing else
can be rendered.

You are copying STRUCTURE, never content. The beats describe the shape of the
video — what kind of thing happens, in what order, for roughly how long — not
what this particular competitor said. A beat prompt must be a reusable
instruction ("open on the hook, face to camera") and must never mention the
competitor, their people, their venue, or their claims.

Return JSON:
{"name": str,              // short, descriptive, no brand names
 "summary": str,           // one line: what this format IS
 "format_type": str,       // from format_types
 "layout": str,            // from layouts
 "production_mode": str,   // from modes
 "aspect": str,            // from aspects — a reel is 9:16
 "caption_preset": str,    // from caption_presets ("" = let the mode pick)
 "music": str,             // from music_moods ("" = none)
 "logo": bool, "logo_position": str,
 "beats": [{"role": str,           // from beat_roles
            "seconds": number,     // within the beat limits
            "energy": str,         // from energies
            "prompt": str}]        // reusable instruction, no competitor specifics
}"""


def _read_of(post: dict) -> str:
    """What we already know about how this reel is built."""
    fp = post.get("fingerprint") or {}
    cl = post.get("classification") or {}
    parts = [f"platform: {post.get('platform')}", f"media: {post.get('media_type')}"]
    for k in ("hook", "structure", "pacing", "captions", "visual_style"):
        if fp.get(k):
            parts.append(f"{k}: {str(fp[k])[:600]}")
    for k, label in (("format", "format"), ("hook_pattern", "hook pattern")):
        if post.get(k):
            parts.append(f"{label}: {post[k]}")
    if cl.get("value_type"):
        parts.append(f"value type: {cl['value_type']}")
    if post.get("engagement_rate"):
        parts.append(f"engagement: {round(float(post['engagement_rate']) * 100, 2)}%")
    return "\n".join(parts)


async def _load_post(post_id: str, tenant_id: UUID | None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT p.id, p.media_type, p.caption, p.engagement_rate,
                      c.handle, c.platform,
                      a.format, a.hook_pattern, a.fingerprint, a.classification,
                      a.design_dna, a.status AS a_status
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE p.id = $1::uuid""", post_id)
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    for k in ("fingerprint", "classification", "design_dna"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


async def templatize_post(post_id: str, tenant_id: UUID | None = None) -> dict:
    """One templatized competitor reel → one renderable template in the library.

    Refuses rather than guesses. A post nobody has watched has no structure to
    copy, and a still has no video structure at all — in both cases the honest
    answer is what is missing, not a template built on nothing.
    """
    post = await _load_post(post_id, tenant_id)
    if not post:
        return {"error": "post not found"}
    if (post.get("media_type") or "") != "video":
        return {"error": "Only reels become production templates. A still's "
                         "layout is applied when the post is generated instead.",
                "media_type": post.get("media_type")}
    if not (post.get("fingerprint") or {}):
        return {"error": "This reel has not been watched yet — run the analysis "
                         "first, then templatize it."}

    from .llm import get_llm
    llm = get_llm()
    if getattr(llm, "model_name", "") == "stub":
        return {"error": "No LLM configured."}

    caps = capabilities()
    body = (f"THE REEL, AS WATCHED:\n{_read_of(post)}\n\n"
            f"THE ONLY VALUES YOU MAY USE:\n{json.dumps(caps, default=str)[:4000]}")
    try:
        spec = await llm.complete_json(
            system=_SYSTEM,
            messages=[{"role": "user", "content": body}],
            max_tokens=1200, temperature=0.2)
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not read the structure: {type(e).__name__}"}
    if not isinstance(spec, dict):
        return {"error": "the structure did not come back as a template"}

    # Name it for the brand's library, not for the competitor. The lineage is
    # worth keeping, but the template is theirs now.
    # The builder's key is `name` (validate_spec) — `style_name` is what the
    # stored template carries afterwards, and using it here failed validation
    # with "give the template a name" on an otherwise valid spec.
    name = (spec.get("name") or spec.get("style_name")
            or f"{post.get('format') or 'Reel'} format")
    spec["name"] = str(name)[:100]

    # A reel is vertical. The model picked 16:9 on the first run, which would
    # have stored a landscape template built from a portrait source — enforced
    # here rather than left to the prompt, because the source's shape is a
    # fact we already know and not something worth asking about.
    spec["aspect"] = "9:16"

    try:
        template = build_template(spec)
    except ValueError as e:
        # build_template lists every problem; surfacing them beats a silent
        # half-valid template that fails at render time instead.
        return {"error": f"not renderable as specified: {e}"}

    from .templates import _insert_template
    row = await _insert_template(
        write_tenant=tenant_id, scope="brand", origin="authored",
        template=template,
        tags=["competitor", f"from:{post.get('handle') or 'peer'}"],
        trending_score=float(post.get("engagement_rate") or 0) * 100,
    )
    return {"template": row, "from": f"@{post.get('handle')}",
            "source_post_id": post["id"]}


async def templatize_all_picked(
    limit: int = 10, tenant_id: UUID | None = None
) -> dict:
    """Mint templates for every reel the brand marked "Layout only".

    REELS only, and that is the split, not an oversight: a "Layout only" pick
    that is a still goes to design_templates, which reads the picture and mints
    a card layout the brand's own posts render into. One verdict, two minters,
    chosen by what the post actually is — so picking "Layout only" always means
    something, whichever kind of post it was.

    Sequential and bounded: each is an LLM call, and a library filling itself
    with near-identical formats is worse than a small deliberate one.
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT p.id FROM competitor_posts p
                 JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE p.replicate_status = 'template'
                  AND p.media_type = 'video' AND a.fingerprint <> '{}'::jsonb
             ORDER BY p.engagement_rate DESC NULLS LAST LIMIT $1""",
            max(1, min(limit, 20)))

    made, skipped = [], []
    for r in rows:
        res = await templatize_post(str(r["id"]), tenant_id)
        if res.get("error"):
            skipped.append({"post_id": str(r["id"]), "reason": res["error"][:140]})
        else:
            made.append({"name": (res["template"] or {}).get("name"),
                         "from": res["from"]})
    return {"created": len(made), "templates": made, "skipped": skipped,
            "note": ("No templatized reels yet — mark a reel ⬚ Template first."
                     if not rows else "")}


async def run_templatize(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point. Tenant-bound and explicit."""
    cfg = config or {}
    await templatize_all_picked(limit=int(cfg.get("limit") or 10),
                                tenant_id=tenant_id)


__all__ = ["templatize_post", "templatize_all_picked", "run_templatize"]
