"""Template clone — the full "our version of a competitor template" pipeline.

For a competitor post we hold:
  1. extract its design into a structured spec (design_cloner),
  2. fill the spec's text slots with the BRAND's own copy in the brand voice,
  3. put it on the brand's own photo — or, if the brand has none yet, an AI
     placeholder scene so the templatized model still shows,
  4. render our version (spec_render) and store it as a pending sample.

Auto-select needs no owner "likes": it pulls the top competitor posts by
engagement + design read and clones them, so onboarding can show finished work.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .db import acquire
from .design_cloner import extract_template_spec
from .spec_render import render_spec

logger = logging.getLogger("template_clone")


async def _fetch_bytes(url: str) -> bytes | None:
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content
    except Exception:  # noqa: BLE001
        return None


async def _fill_copy(spec: dict, tenant_id, ref: dict) -> dict:
    """Write SHORT on-image copy for each of the spec's text roles, in the
    brand's voice — never copying the competitor's words, only its structure."""
    roles = [e["role"] for e in (spec.get("elements") or [])]
    if not roles:
        return {}
    from .main import _brand_voice_and_profile
    from .llm import get_llm

    voice, profile = await _brand_voice_and_profile(tenant_id)
    system = (
        "You write SHORT on-image copy for a brand's social post, filling a "
        "template's slots in the BRAND's voice. Each role is a few words: a "
        "'stat' is a number + unit (e.g. '20 Years', 'No. 1'); a 'kicker' is a "
        "short label; a 'headline' is ONE punchy line; a 'subhead' one short "
        "line; a 'cta' is 2-4 words; a 'byline' is the brand name. Ground it in "
        "THIS brand — never copy the competitor's words. Fill only the roles asked."
    )
    user = (
        f"BRAND VOICE:\n{(voice or '')[:1500]}\n\n"
        f"BRAND:\n{(profile or '')[:800]}\n\n"
        f"The reference design's angle (for inspiration only): "
        f"{ref.get('topic') or ref.get('transferable_pattern') or 'a strong moment for the brand'}\n\n"
        f"Return STRICT JSON with exactly these keys: {roles}."
    )
    try:
        out = await get_llm().complete_json(
            system=system, messages=[{"role": "user", "content": user}],
            max_tokens=300, temperature=0.6)
    except Exception:  # noqa: BLE001
        out = {}
    return {r: str((out or {}).get(r, "")).strip() for r in roles if str((out or {}).get(r, "")).strip()}


async def _brand_logo(tenant_id) -> bytes | None:
    """The brand's uploaded logo bytes (first brand_logo asset), for the design's
    logo slot. Best-effort — None just means no logo is dropped in."""
    try:
        from .media import list_media
        rows = await list_media(role="brand_logo", tenant_id=tenant_id)
        if not rows:
            return None
        return await _fetch_bytes(rows[0].get("uri") or "")
    except Exception:  # noqa: BLE001
        return None


async def _hero_or_placeholder(tenant_id, topic: str) -> tuple[bytes | None, bool]:
    """The brand's own photo if it has one (sharpness-gated pick), else an AI
    placeholder scene from the topic. Returns (bytes, was_generated)."""
    from .hero_context import get_hero_photo_files
    from .photo_pick import pick_hero_bytes

    refs = await get_hero_photo_files(tenant_id=tenant_id, limit=None)
    if refs:
        picked = await pick_hero_bytes(refs, tenant_id)
        if picked:
            return picked[1], False
    # No usable photo — fabricate a clean scene so the template still shows.
    from .imagegen import generate_post_image
    png, _meta, _err = await generate_post_image(
        topic=(topic or "the brand") + " — cinematic editorial photograph, no text, no words, no logos",
        platform="instagram", aspect="4:5", style="cinematic_real", tenant_id=tenant_id)
    return png, True


async def clone_post(post: dict, tenant_id, *, hero_bytes: bytes | None = None) -> dict | None:
    """Turn ONE competitor post into our version. Returns {png, kind, content,
    topic, generated_hero, from} or None if it can't be cloned."""
    img = await _fetch_bytes(post.get("stored_media_url") or "")
    if not img:
        return None
    spec = await extract_template_spec(img)
    if spec.get("status") != "ok":
        return None
    content = await _fill_copy(spec, tenant_id, post)
    # A photo_forward post with no text is just "a great photo in this framing" —
    # give it at least a headline so our version reads as a designed post.
    topic = (content.get("headline") or content.get("stat") or post.get("topic") or "").strip() \
        or "a moment that captures the brand"
    generated = False
    if hero_bytes is None:
        hero_bytes, generated = await _hero_or_placeholder(tenant_id, topic)
    logo = await _brand_logo(tenant_id) if spec.get("logo_box") else None
    png, kind = render_spec(spec, content, hero_bytes=hero_bytes, logo_bytes=logo)
    return {"png": png, "kind": kind, "content": content, "topic": topic,
            "generated_hero": generated, "from": post.get("handle") or ""}


# What separates a DESIGNED TEMPLATE (a milestone card, listing card, quote
# poster, announcement — a reusable layout) from a REGULAR post (just a photo).
# Derived from the competitor vision analysis we already store — no new calls.
_TEMPLATE_COND = (
    "((a.design_dna->>'layout_family') IN "
    "('text_card','split_panel','data_viz','carousel_cover','photo_beside_text') "
    "OR (a.design_dna->>'text_density') IN ('moderate','heavy') "
    "OR a.format ~* '(listing|quote|stat|announce|before|milestone|tip|list|poster|infographic)')"
)


def _row_out(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    if isinstance(d.get("design_dna"), str):
        try:
            d["design_dna"] = json.loads(d["design_dna"])
        except (ValueError, TypeError):
            d["design_dna"] = {}
    return d


async def separate_posts(tenant_id, *, limit: int = 40) -> dict:
    """Split the scraped competitor STILLS into designed TEMPLATES vs REGULAR
    posts (photos), using the analysis we already hold. Returns counts + the top
    templates, so the UI can show 'we found N templates worth rebuilding'."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.stored_media_url, c.handle, a.format, a.eye_score,
                       p.engagement_rate, a.status AS a_status,
                       ({_TEMPLATE_COND}) AS is_template
                  FROM competitor_posts p
                  JOIN competitors c ON c.id = p.competitor_id
             LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                 WHERE p.stored_media_url <> '' AND p.media_type IN ('image', 'carousel')
              ORDER BY coalesce(a.eye_score, 0) * 0.5 + p.engagement_rate DESC NULLS LAST
                 LIMIT 200""")
    analysed = [r for r in rows if r["a_status"] == "ok"]
    templates = [r for r in analysed if r["is_template"]]
    regular = [r for r in analysed if not r["is_template"]]
    return {
        "template_count": len(templates),
        "regular_count": len(regular),
        "unanalysed": len(rows) - len(analysed),
        "templates": [{
            "id": str(r["id"]), "url": r["stored_media_url"], "from": r["handle"],
            "format": r["format"], "eye_score": r["eye_score"],
        } for r in templates[:limit]],
    }


async def _top_posts(tenant_id, limit: int, *, templates_only: bool = False) -> list[dict]:
    """Top competitor STILLS to clone — best engagement first, design-analysed
    where available. Videos are excluded (a still template needs a still). With
    templates_only, only DESIGNED templates (not plain photos) are returned."""
    where = "p.stored_media_url <> '' AND p.media_type IN ('image', 'carousel')"
    if templates_only:
        where += f" AND a.status = 'ok' AND {_TEMPLATE_COND}"
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.stored_media_url, p.caption, p.media_type, c.handle,
                       a.format, a.topic, a.transferable_pattern, a.design_dna,
                       a.eye_score, p.engagement_rate,
                       ({_TEMPLATE_COND}) AS is_template
                  FROM competitor_posts p
                  JOIN competitors c ON c.id = p.competitor_id
             LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                 WHERE {where}
              ORDER BY coalesce(a.eye_score, 0) * 0.5 + p.engagement_rate DESC NULLS LAST
                 LIMIT $1""", max(1, min(limit, 24)))
    return [_row_out(r) for r in rows]


async def _exemplars(tenant_id, k: int = 2) -> list[bytes]:
    """The best real templates in this niche (highest design-eye score), as bytes,
    to hand the QA reviewer as the quality bar to judge against."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT p.stored_media_url
                  FROM competitor_posts p
                  JOIN competitor_post_analysis a ON a.post_id = p.id
                 WHERE p.stored_media_url <> '' AND a.status = 'ok' AND {_TEMPLATE_COND}
              ORDER BY a.eye_score DESC NULLS LAST
                 LIMIT $1""", max(1, min(k, 3)))
    out = []
    for r in rows:
        b = await _fetch_bytes(r["stored_media_url"])
        if b:
            out.append(b)
    return out


async def generate_template_samples(tenant_id, *, n: int = 6, grade: bool = True,
                                    progress=None) -> dict:
    """Clone the top competitor templates into our versions, store each as a
    pending sample (shared with the samples gallery), best first.

    `progress(dict)` is called as it goes ({done,total,stage}) so the UI can show
    a real bar instead of an endless spinner."""
    from .models import ContentBrief
    from .content import generate_content
    from .media import storage as media_storage
    from . import render_reviewer

    def _emit(**p):
        if progress:
            try:
                progress(p)
            except Exception:  # noqa: BLE001 — progress is advisory
                pass

    _emit(done=0, total=n, stage="Studying the templates winning in your niche…")
    # Prefer DESIGNED templates (the reusable layouts). Pull a generous candidate
    # pool because the QA reviewer will reject some — we keep going until n PASS.
    posts = await _top_posts(tenant_id, 24, templates_only=True)
    seen = {p["id"] for p in posts}
    posts += [p for p in await _top_posts(tenant_id, 24) if p["id"] not in seen]
    if not posts:
        return {"samples": [], "count": 0,
                "note": "No competitor posts to learn from yet — confirm a few "
                        "competitors and let their posts sync, then we'll show our "
                        "versions of what's working in your niche."}
    # The quality bar: the best real templates in this niche, handed to the
    # reviewer as reference so it judges against what actually ships here.
    exemplars = await _exemplars(tenant_id)
    _emit(done=0, total=n, stage=f"Building your first {n} posts…")

    samples, failed, rejected, reviewed = [], 0, 0, 0
    for post in posts:
        if len(samples) >= n:
            break
        cloned = await clone_post(post, tenant_id)
        if not cloned or not cloned.get("png"):
            failed += 1
            continue

        # PRO-DESIGNER QA GATE — review the rendered post BEFORE it becomes an
        # action, so a broken layout (clipped text, empty pill, bad logo) never
        # reaches the user. Fail-OPEN if the reviewer itself is unavailable.
        rev = await render_reviewer.review_post(cloned["png"], exemplars=exemplars)
        if rev.get("status") == "ok":
            reviewed += 1
            if not rev.get("passed"):
                rejected += 1
                logger.info("QA rejected a clone from @%s: flaws=%s issues=%s",
                            cloned.get("from"), rev.get("critical_flaws"), rev.get("issues"))
                continue  # not shown, no action created
        polish = rev.get("polish_score")

        # Passed (or reviewer offline) → caption + action + store.
        try:
            draft = await generate_content(
                ContentBrief(platform="instagram", format="post",
                             topic=cloned["topic"],
                             extra_instructions="This is a first EXAMPLE post shown to a new brand — "
                                                "concise, concrete, on-voice."),
                tenant_id)
            action_id = getattr(draft, "action_id", None)
        except Exception:  # noqa: BLE001
            action_id = None
        if not action_id:
            failed += 1
            continue
        try:
            url, _fp = await asyncio.to_thread(
                media_storage().save, str(tenant_id), cloned["png"], "template-clone.png")
        except Exception:  # noqa: BLE001
            failed += 1
            continue

        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1::uuid",
                action_id, json.dumps({
                    "image_url": url, "media_url": url, "has_image": True,
                    "image_format": "cloned", "sample": "true", "sample_score": polish,
                    "sample_from": cloned["from"], "cloned_from_competitor": True,
                    "used_placeholder_photo": cloned["generated_hero"],
                    "review_passed": bool(rev.get("passed")),
                }))
        made = len(samples) + 1
        _emit(done=made, total=n, stage=f"Built {made} of {n} — "
              f"in the style of @{cloned['from'] or 'your niche'}")
        samples.append({
            "action_id": str(action_id), "image_url": url, "format": "cloned",
            "score": polish, "from": cloned["from"], "topic": cloned["topic"],
            "caption": (getattr(draft, "draft", "") or "")[:300],
            "used_placeholder": cloned["generated_hero"],
        })

    samples.sort(key=lambda s: (s["score"] is not None, s["score"] or 0), reverse=True)
    note = ""
    if not samples and rejected:
        note = ("We built several but none cleared our design review yet — try again, "
                "or add a few clean brand photos to build on.")
    return {"samples": samples, "count": len(samples),
            "failed": failed, "reviewed": reviewed, "rejected": rejected, "note": note}


__all__ = ["clone_post", "generate_template_samples", "separate_posts"]
