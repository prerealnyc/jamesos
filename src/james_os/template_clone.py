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


async def _brand_palette(tenant_id):
    """This brand's own colours, for re-colouring a borrowed template.

    Best-effort: None means the template renders in the colours it was read
    with, which is what happened for every cloned post before this — the
    competitor's. A brand that has set no palette is unchanged."""
    try:
        from .brand_identity import ensure_brand_palette
        return await ensure_brand_palette(tenant_id)
    except Exception:  # noqa: BLE001 — a palette read must never stop a render
        logging.getLogger(__name__).warning("brand palette unavailable", exc_info=True)
        return None


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


async def _hero_or_placeholder(tenant_id, topic: str) -> tuple[bytes | None, bool, str]:
    """The brand's own photo if it has one (sharpness-gated pick), else an AI
    placeholder scene from the topic.

    Returns (bytes, was_generated, hero_photo_key). The KEY is what lets a later
    rebuild reuse this exact photo instead of rotating to another one — "keep the
    image" is unanswerable without it."""
    from .hero_context import get_hero_photo_files
    from .photo_pick import pick_hero_bytes

    refs = await get_hero_photo_files(tenant_id=tenant_id, limit=None)
    if refs:
        picked = await pick_hero_bytes(refs, tenant_id)
        if picked:
            return picked[1], False, picked[0]
    # No usable photo — fabricate a clean scene so the template still shows.
    from .imagegen import generate_post_image
    png, _meta, _err = await generate_post_image(
        topic=(topic or "the brand") + " — cinematic editorial photograph, no text, no words, no logos",
        platform="instagram", aspect="4:5", style="cinematic_real", tenant_id=tenant_id)
    return png, True, ""


async def rebuild_cloned(payload: dict, feedback: str, tenant_id) -> tuple[bytes, str] | None:
    """Render a cloned post AGAIN in its own design, with the owner's change.

    A cloned post is not one of the nine designed layouts — it is a competitor's
    template read by vision and re-filled in our voice. So the ordinary redo path
    could never preserve it: image_format is the literal string "cloned", which
    the renderer has no layout for, and every rebuild silently became the house
    poster instead. That is what made "keep the template the same" impossible for
    exactly the posts the owner most wanted kept.

    Posts cloned from now on carry their spec. Older ones carry nothing, so the
    template is recovered by reading it back off OUR OWN rendered card — the same
    vision pass that produced it in the first place, pointed at the image we
    still have. Returns (png, kind) or None when the design cannot be recovered,
    which the caller must report rather than quietly substituting another look."""
    from .design_cloner import extract_template_spec

    spec = payload.get("clone_spec") or {}
    if not isinstance(spec, dict) or spec.get("status") != "ok":
        # Recovery for everything cloned before the spec was persisted: re-read
        # the template off the card we rendered. Our own image is a faithful
        # instance of the design, so this returns the same structure.
        src = str(payload.get("image_url") or payload.get("clone_source_url") or "")
        img = await _fetch_bytes(src)
        if not img:
            return None
        spec = await extract_template_spec(img)
        if spec.get("status") != "ok":
            return None

    roles = [e["role"] for e in (spec.get("elements") or [])]
    content = {k: v for k, v in (payload.get("clone_content") or {}).items() if k in roles}
    if not content:
        # READ OUR OWN CARD BACK rather than writing new copy. The template read
        # above recovers structure only — by design, so cloning never lifts a
        # competitor's words — and filling the roles fresh is what rewrote every
        # line of a card the owner had asked to leave alone.
        from .design_cloner import read_card_copy
        img_bytes = await _fetch_bytes(str(payload.get("image_url") or ""))
        if img_bytes:
            content = await read_card_copy(img_bytes, roles)
    if not content:
        # Nothing recoverable — write it fresh, from the post's own words.
        content = await _fill_copy(spec, tenant_id, {"topic": payload.get("topic") or ""})
    if feedback.strip() and content:
        content = await _edit_clone_copy(content, feedback, tenant_id)
        content = await _apply_handle_request(content, feedback, roles, tenant_id)

    # KEEP THE PHOTO. The picker rotates for variety, which is right when making
    # something new and wrong when rebuilding something that exists: "keep the
    # image" came back with a different one. Reuse the exact photo when the card
    # recorded which one it used.
    hero_bytes = await _hero_by_key(tenant_id, str(payload.get("hero_photo_key") or ""))
    if hero_bytes is None:
        hero_bytes, _generated, _key = await _hero_or_placeholder(
            tenant_id, str(payload.get("topic") or ""))
    logo = await _brand_logo(tenant_id) if spec.get("logo_box") else None
    return render_spec(spec, content, hero_bytes=hero_bytes, logo_bytes=logo,
                       palette=await _brand_palette(tenant_id))


async def _hero_by_key(tenant_id, key: str) -> bytes | None:
    """The exact hero photo a card used, by the key it recorded. None when the
    card never recorded one (everything cloned before this was stored) or the
    photo has since left the library — the caller then picks, and says so."""
    if not key:
        return None
    try:
        from .hero_context import get_hero_photo_files
        for name, data in await get_hero_photo_files(tenant_id=tenant_id, limit=None):
            if name == key:
                return data
    except Exception:  # noqa: BLE001 — a lookup failure is "not found"
        logging.getLogger(__name__).warning("hero lookup failed for %r", key[:80])
    return None


# "put my @ on it" — the one request that is not a copy edit at all: the handle
# is a brand fact, not something a model should be asked to invent. A cloned
# template has a byline role for exactly this.
_HANDLE_ASKS = ("@", "handle", "username", "user name", "my name in the image")


async def _apply_handle_request(content: dict, feedback: str, roles: list, tenant_id) -> dict:
    """Put the brand's own handle in the byline when the owner asks for it.

    The nine designed layouts draw the handle from the brand kit automatically.
    A cloned template renders through a different machine that has no handle
    concept at all, so the same request quietly did nothing here."""
    f = (feedback or "").lower()
    if not any(p in f for p in _HANDLE_ASKS) or "byline" not in roles:
        return content
    try:
        from .brand_kit import get_brand_kit
        handle = (( await get_brand_kit(tenant_id)).get("handle") or "").strip()
    except Exception:  # noqa: BLE001
        return content
    if not handle:
        return content
    out = dict(content)
    existing = (out.get("byline") or "").strip()
    # Append rather than replace: a byline that already names the brand should
    # keep doing so, with the handle added to it.
    out["byline"] = existing if handle.lower() in existing.lower() else (
        f"{existing}  ·  {handle}" if existing else handle)
    return out


async def _edit_clone_copy(content: dict, feedback: str, tenant_id) -> dict:
    """Apply the owner's change to a cloned card's copy — and nothing else.

    Same contract as imagegen.edit_designed_spec: every role comes back, one
    changes. Degrades to the input, because returning the card unchanged is a
    better answer to a failed edit than returning a different card."""
    from .llm import get_llm

    system = (
        "You are editing the on-image copy of ONE finished card, not writing a "
        "new one. You get the exact text roles that are on it and the owner's "
        "words about what they want changed.\n\n"
        "Apply THAT change and nothing else. Every role you were given comes "
        "back. A role the owner did not mention comes back BYTE-IDENTICAL — do "
        "not reword, tighten or improve it. If the request is about something "
        "text cannot fix (spacing, colour, position, a logo or handle), change "
        "nothing and return the roles exactly as given.\n\n"
        "Return STRICT JSON with exactly the keys you were given."
    )
    try:
        out = await get_llm().complete_json(
            system=system,
            messages=[{"role": "user", "content":
                       "THE CARD AS IT IS:\n" + json.dumps(content, indent=2)
                       + "\n\nWHAT THE OWNER WANTS CHANGED:\n" + feedback.strip()[:400]}],
            max_tokens=400, temperature=0.0)
    except Exception:  # noqa: BLE001
        return dict(content)
    if not isinstance(out, dict):
        return dict(content)
    edited = dict(content)
    for k in content:
        v = out.get(k)
        if isinstance(v, str) and v.strip():
            edited[k] = v.strip()
    return edited


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
    hero_key = ""
    if hero_bytes is None:
        hero_bytes, generated, hero_key = await _hero_or_placeholder(tenant_id, topic)
    logo = await _brand_logo(tenant_id) if spec.get("logo_box") else None
    png, kind = render_spec(spec, content, hero_bytes=hero_bytes, logo_bytes=logo,
                            palette=await _brand_palette(tenant_id))
    return {"png": png, "kind": kind, "content": content, "topic": topic,
            "generated_hero": generated, "from": post.get("handle") or "",
            "hero_photo_key": hero_key,
            # The template itself, and where it came from. Without these a cloned
            # post could never be rebuilt in its own design: the spec was a live
            # vision read that was thrown away, and the source post id was never
            # written down, so a redo had nothing to go back to and silently
            # produced an unrelated layout instead.
            "spec": spec, "source_url": post.get("stored_media_url") or ""}


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
                    # Everything a rebuild needs to render THIS design again.
                    "clone_spec": cloned.get("spec") or {},
                    "clone_content": cloned.get("content") or {},
                    "clone_source_url": cloned.get("source_url") or "",
                    **({"hero_photo_key": cloned["hero_photo_key"]}
                       if cloned.get("hero_photo_key") else {}),
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
