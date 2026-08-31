"""Hero-photo templatizability — which of the brand's OWN photos can carry a
designed post, and which template each one fits.

design_eye.py grades a FINISHED post image; this looks at a RAW hero photo the
brand uploaded and answers a different question: can we build a designed post ON
this photo, and if so, in which of our layouts? A scenic shot with clean sky
holds text-over-photo; a busy group shot can't hold an overlay but works as a
photo-beside-text split; a screenshot or a low-res crop can't be templatized at
all.

The point is to STOP ASKING the owner what to use. We classify every photo once,
match each usable one to its best-fitting layout, and generate example posts from
the winners — so onboarding shows finished work instead of a configuration form.

Honesty rules mirror design_eye:
  * No OpenAI key / unsendable image → status 'no_key' / 'failed', never a faked
    verdict.
  * The rubric is FROZEN and versioned (TEMPLATIZE_RUBRIC_VERSION); the blended
    score is computed HERE from the sub-axes, never taken from the model (D2).
  * The model sees ONLY the photo — it is told nothing about the brand — so the
    verdict is about the pixels, not a story it was handed.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
from openai import AsyncOpenAI

from .config import settings
from .db import acquire
from .designed_render import PHOTO_FORMATS

logger = logging.getLogger("hero_templatize")

TEMPLATIZE_RUBRIC_VERSION = "v1"
_MODEL = "gpt-4o"

# The layouts a photo can be judged for — the renderer's real photo formats, in a
# plain-language menu the model reasons over. Keep in sync with
# designed_render.PHOTO_FORMATS.
_FORMAT_MENU = (
    "  minimal_over — a few words of text placed OVER the photo in a clean area. "
    "Needs uncluttered negative space (sky, wall, blur) with good contrast.\n"
    "  full_bleed — the photo fills the whole frame; text sits in a darkened "
    "gradient band at the bottom. Needs a strong subject that reads edge-to-edge.\n"
    "  hero_quote — a LARGE quotation set over the photo. Needs a big calm area "
    "and strong contrast so long text stays legible.\n"
    "  statement — one bold line over the photo. Tolerates a busier photo than "
    "hero_quote but still needs a readable area.\n"
    "  editorial_split — photo on ONE side, text on a solid colour panel on the "
    "other. WORKS EVEN FOR BUSY photos because the text is never on the image.\n"
    "  framed_print — the photo inside a bordered frame with a caption around it. "
    "Works for most clean standalone shots (portraits, products) where you don't "
    "want any text on the image itself."
)

# sub-axis → weight. Negative space is the single biggest driver of whether a
# photo can carry text, so it counts double. Weights are part of the frozen
# rubric: changing one bumps TEMPLATIZE_RUBRIC_VERSION.
_AXES: dict[str, int] = {
    "negative_space": 2,     # is there a calm region text could live in?
    "subject_isolation": 1,  # one clear subject, not visual soup
    "overlay_contrast": 1,   # would text stay legible against it
    "resolution_finish": 1,  # sharp, well-exposed, professional-looking
}

# Below this blended score a photo is treated as not worth building on, even if
# nothing hard-disqualifies it.
_SCORE_FLOOR = 45.0
# Always-safe formats when the photo is usable but the model named none — these
# never put text ON the image, so a merely-decent photo still works.
_SAFE_FALLBACK = ("editorial_split", "framed_print")


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


_SYSTEM = (
    "You are a senior art director deciding whether a RAW brand photo can be "
    "turned into a designed social post, and in which layout. You see ONLY the "
    "photo — nothing about the brand. Be practical and honest: a great photo for "
    "a caption overlay is one with a calm, uncluttered area where text stays "
    "readable; a busy or text-covered photo is not.\n\n"
    "Score FOUR sub-axes 0–100 (0–20 broken / 21–40 weak / 41–60 average / 61–80 "
    "strong / 81–100 exceptional), each with a one-line 'evidence' naming what you "
    "SAW:\n"
    "  negative_space — is there a genuine calm region (sky, wall, blur, floor) "
    "big enough to hold text without covering the subject?\n"
    "  subject_isolation — is there ONE clear subject, or is it visual soup with "
    "no place for the eye to rest?\n"
    "  overlay_contrast — if we laid light or dark text over the calm area, would "
    "it stay legible (enough tonal separation), or fight the background?\n"
    "  resolution_finish — does it look sharp, well-exposed and professional, or "
    "soft / noisy / snapshot-y?\n\n"
    "Report where the calm space is and what the photo is:\n"
    "  clean_space: {where: one of [top, bottom, left, right, center, none], "
    "amount: one of [none, small, moderate, large]}.\n"
    "  subject: one of [landscape, person, group, product, action, interior, "
    "food, abstract, other].\n"
    "  faces: {present: bool, region: one of [top, center, bottom, left, right, "
    "none]} — so we never lay text over a face.\n\n"
    "Flag hard DISQUALIFIERS (set true only when clearly so):\n"
    "  screenshot_or_graphic (it's a screenshot, meme, or already a designed "
    "graphic), already_has_heavy_text (lots of baked-in words), too_low_res "
    "(pixelated / tiny / heavily compressed), watermark_or_ui (stock watermark or "
    "app UI chrome).\n\n"
    "Then, from THIS menu of layouts, list which fit and which to avoid:\n"
    f"{_FORMAT_MENU}\n\n"
    "Return STRICT JSON: {\"axes\": {\"negative_space\": {\"score\": int, "
    "\"evidence\": str}, \"subject_isolation\": {...}, \"overlay_contrast\": "
    "{...}, \"resolution_finish\": {...}}, \"clean_space\": {\"where\": str, "
    "\"amount\": str}, \"subject\": str, \"faces\": {\"present\": bool, "
    "\"region\": str}, \"disqualifiers\": {\"screenshot_or_graphic\": bool, "
    "\"already_has_heavy_text\": bool, \"too_low_res\": bool, \"watermark_or_ui\": "
    "bool}, \"recommended_formats\": [str], \"avoid_formats\": [str], \"reason\": "
    "str}. recommended_formats/avoid_formats MUST be names from the menu. If the "
    "image is blank or unreadable, disqualify it and say so — never invent."
)


def _score(axes: dict) -> float:
    """Weighted mean of the sub-axis scores, computed HERE not model-reported
    (D2). Missing/non-numeric axes are skipped so a partial result still yields a
    defensible number over what was actually scored."""
    num, den = 0.0, 0
    for key, weight in _AXES.items():
        node = axes.get(key) if isinstance(axes, dict) else None
        raw = node.get("score") if isinstance(node, dict) else None
        try:
            val = max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError):
            continue
        num += val * weight
        den += weight
    return round(num / den, 1) if den else 0.0


def _derive(out: dict) -> dict:
    """Turn the model's raw judgment into the stored verdict: a blended score, a
    templatizable flag, and the ranked photo formats this photo actually fits —
    all decided HERE, so the rule (not the model) owns the gate."""
    axes = out.get("axes") if isinstance(out.get("axes"), dict) else {}
    score = _score(axes)
    dq = out.get("disqualifiers") or {}
    disqualified = any(bool(dq.get(k)) for k in (
        "screenshot_or_graphic", "already_has_heavy_text", "too_low_res", "watermark_or_ui"))

    # Keep only real photo formats the renderer knows, in the model's order.
    rec = [f for f in (out.get("recommended_formats") or []) if isinstance(f, str)]
    seen: set[str] = set()
    best = [f for f in rec if f in PHOTO_FORMATS and not (f in seen or seen.add(f))]

    usable = not disqualified and score >= _SCORE_FLOOR
    if usable and not best:
        best = list(_SAFE_FALLBACK)  # decent photo, model named none → never-on-image layouts
    templatizable = usable and bool(best)

    clean = out.get("clean_space") or {}
    return {
        "status": "ok",
        "rubric_version": TEMPLATIZE_RUBRIC_VERSION,
        "templatizable": templatizable,
        "score": score,
        "best_formats": best if templatizable else [],
        "text_zone": str(clean.get("where") or "none"),
        "clean_amount": str(clean.get("amount") or "none"),
        "subject": str(out.get("subject") or "other"),
        "faces": out.get("faces") or {"present": False, "region": "none"},
        "disqualified": disqualified,
        "disqualifiers": {k: bool(dq.get(k)) for k in (
            "screenshot_or_graphic", "already_has_heavy_text", "too_low_res", "watermark_or_ui")},
        "axes": axes,
        "reason": str(out.get("reason") or "").strip(),
    }


def _as_data_uri(image: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


async def inspect_photo_templatizability(image: bytes | str, *, mime: str = "image/jpeg") -> dict:
    """Judge ONE raw hero photo. `image` is bytes (preferred) or an https URL.

    Returns the derived verdict {status, templatizable, score, best_formats,
    text_zone, subject, faces, disqualifiers, axes, reason}. status is 'ok' on a
    real judgment, 'no_key' with no OpenAI key, or 'failed' on any error — the
    caller must check status and never treat a non-ok result as a real verdict."""
    client = _client()
    if client is None:
        return {"status": "no_key", "rubric_version": TEMPLATIZE_RUBRIC_VERSION}

    url = _as_data_uri(bytes(image), mime) if isinstance(image, (bytes, bytearray)) else str(image)
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Judge this raw brand photo for a designed post."},
                    {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
                ]},
            ],
            max_tokens=800,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — reported, never faked
        return {"status": "failed", "rubric_version": TEMPLATIZE_RUBRIC_VERSION, "error": str(exc)[:200]}
    return _derive(out)


async def _fetch_bytes(uri: str) -> bytes | None:
    """Download a photo for classification. Only http(s) URIs (prod Supabase
    public URLs); a local dev path is skipped rather than guessed at."""
    if not uri.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(uri)
            r.raise_for_status()
            return r.content
    except Exception:  # noqa: BLE001
        logger.warning("templatize: could not fetch %s", uri[:80], exc_info=True)
        return None


async def _store(media_id: str, verdict: dict, tenant_id) -> None:
    """Merge the verdict into media_assets.analysis under 'templatize' (key-
    additive jsonb merge — leaves any other analysis keys untouched)."""
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE media_assets SET analysis = analysis || $2::jsonb, updated_at = now() "
            "WHERE id = $1::uuid",
            media_id, json.dumps({"templatize": verdict}))


async def classify_brand_photos(tenant_id, *, force: bool = False, limit: int = 60) -> dict:
    """Classify every authentic hero photo once, caching the verdict on the row.

    Skips photos already judged at this rubric version (unless force). Best-effort
    per photo: one bad image never sinks the batch. Returns a summary."""
    from .media import list_media

    photos = [p for p in await list_media(role="hero_photo", tenant_id=tenant_id)
              if p.get("source_type") != "generated"][:limit]
    done, templatizable, skipped, failed = 0, 0, 0, 0
    for p in photos:
        prior = (p.get("analysis") or {}).get("templatize") or {}
        if not force and prior.get("rubric_version") == TEMPLATIZE_RUBRIC_VERSION and prior.get("status") == "ok":
            skipped += 1
            if prior.get("templatizable"):
                templatizable += 1
            continue
        img = await _fetch_bytes(p.get("uri") or "")
        verdict = await inspect_photo_templatizability(img or (p.get("uri") or ""))
        if verdict.get("status") != "ok":
            failed += 1
            # Cache the non-ok status too, so a keyless env doesn't re-hammer it.
            await _store(str(p["id"]), verdict, tenant_id)
            continue
        await _store(str(p["id"]), verdict, tenant_id)
        done += 1
        if verdict.get("templatizable"):
            templatizable += 1
    return {"photos": len(photos), "classified": done, "cached": skipped,
            "failed": failed, "templatizable": templatizable}


async def templatizable_photos(tenant_id, *, limit: int = 30) -> list[dict]:
    """The brand's usable photos, best first — each with its stable id, url and
    the ranked formats it fits. Reads cached verdicts only; call
    classify_brand_photos first to populate them."""
    from .media import list_media

    out = []
    for p in await list_media(role="hero_photo", tenant_id=tenant_id):
        if p.get("source_type") == "generated":
            continue
        v = (p.get("analysis") or {}).get("templatize") or {}
        if v.get("status") == "ok" and v.get("templatizable"):
            out.append({
                "media_id": str(p["id"]),
                "url": p.get("uri"),
                "score": v.get("score") or 0,
                "best_formats": v.get("best_formats") or [],
                "text_zone": v.get("text_zone"),
                "subject": v.get("subject"),
                "reason": v.get("reason"),
            })
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:limit]


# ── the demonstration: turn classified photos into example posts ──────

_SAMPLE_STEER = (
    "This is a FIRST EXAMPLE post to show a new brand what we can make for them. "
    "Keep it concise, concrete and visual — one clear idea, no throat-clearing."
)


async def _tag_sample(action_id: str, tenant_id, extra: dict) -> None:
    """Key-additive merge onto the action payload so the gallery can find and
    rank a sample (payload.sample=true + its self-grade score + source photo)."""
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1::uuid",
            action_id, json.dumps(extra))


async def generate_samples(tenant_id, *, n: int = 6, grade: bool = True) -> dict:
    """Build example posts from the brand's best templatizable photos — one per
    photo, each in a layout that photo actually fits — then grade the rendered
    result so the caller can surface only the strong ones.

    This is the demonstration: no template picking, no photo picking. We classify,
    auto-match, render on the EXACT photo, and self-grade with the same design eye
    that judged the photo. Samples are real pending content actions (tagged
    payload.sample=true) so they render in the queue and can be approved; the
    gallery just shows them best first."""
    from .models import ContentBrief
    from .content import generate_content, strip_internal_labels
    from .autopilot import generate_ideas
    from .brand_identity import get_enabled_formats
    from .main import _generate_designed_post_image
    from . import design_eye

    await classify_brand_photos(tenant_id)
    # Only build in layouts the brand actually ALLOWS. Otherwise the renderer
    # silently clamps a disabled photo format down to a text card (_clamp_format),
    # and the "example on your photo" arrives with no photo. allowed is None = no
    # restriction (every photo format is fair game — the default for a new brand).
    try:
        allowed = await get_enabled_formats(tenant_id)
    except Exception:  # noqa: BLE001
        allowed = None
    allowed_photo = {f for f in PHOTO_FORMATS if allowed is None or f in allowed}
    if not allowed_photo:
        return {"samples": [], "count": 0,
                "note": "Your enabled templates are text-only right now — turn on a "
                        "photo layout (e.g. minimal_over) to see examples on your photos."}

    # Pull extra candidates so we can skip any whose fitting layouts are all
    # disabled for this brand and still land n examples.
    pool = await templatizable_photos(tenant_id, limit=max(n * 3, n))
    photos = [p for p in pool
              if any(f in allowed_photo for f in (p.get("best_formats") or []))][:n]
    if not photos:
        return {"samples": [], "count": 0,
                "note": "No templatizable photos yet — add a few clean, uncluttered "
                        "shots and we'll build examples from them."}

    ideas = await generate_ideas(len(photos), {}, tenant_id) or []
    if not ideas:
        return {"samples": [], "count": 0, "note": "Could not draft example topics."}

    samples, failed = [], 0
    last_fmt = ""
    for i, photo in enumerate(photos):
        idea = ideas[i % len(ideas)]
        # Vary the layout across the set, but only among layouts BOTH this photo
        # fits AND the brand allows.
        fits = [f for f in (photo.get("best_formats") or []) if f in allowed_photo]
        if not fits:
            continue
        fmt = next((f for f in fits if f != last_fmt), fits[0])
        topic = strip_internal_labels(idea.get("topic", "")) or "a moment that captures the brand"
        try:
            draft = await generate_content(
                ContentBrief(platform="instagram", format="post",
                             pillar=idea.get("pillar", ""), topic=topic,
                             extra_instructions=_SAMPLE_STEER),
                tenant_id)
        except Exception:  # noqa: BLE001 — one bad draft ≠ the set
            failed += 1
            continue
        if not getattr(draft, "action_id", None):
            failed += 1
            continue

        draft_text = getattr(draft, "draft", "") or topic
        image_url, image_format = "", ""
        try:
            image_url, image_format = await _generate_designed_post_image(
                draft.action_id, topic, draft_text, tenant_id,
                force_format=fmt, force_photo=photo["url"] or "")
            # The whole point is a post ON a photo. If the exact classified photo
            # wasn't in the render pool (the hero picker filters ~a third for
            # sharpness), a photo format downgrades to a text card — retry letting
            # the picker choose ANY sharp pool photo, so the example keeps an image.
            if image_url and image_format not in PHOTO_FORMATS:
                image_url, image_format = await _generate_designed_post_image(
                    draft.action_id, topic, draft_text, tenant_id, force_format=fmt)
            last_fmt = image_format or last_fmt
        except Exception:  # noqa: BLE001 — copy still stands; just no image
            image_url, image_format = "", ""

        score = None
        if grade and image_url:
            try:
                g = await design_eye.inspect_image(image_url)
                if g.get("status") == "ok":
                    score = g.get("eye_score")
            except Exception:  # noqa: BLE001 — grading is advisory
                score = None

        await _tag_sample(str(draft.action_id), tenant_id, {
            "sample": "true",
            "sample_score": score,
            "sample_from_photo": photo.get("media_id"),
            "sample_format": image_format or fmt,
        })
        samples.append({
            "action_id": str(draft.action_id),
            "image_url": image_url, "format": image_format or fmt,
            "score": score, "from_photo": photo.get("media_id"),
            "topic": topic, "caption": (getattr(draft, "draft", "") or "")[:300],
        })

    # Strong first — a null score (ungraded) sorts below any real score.
    samples.sort(key=lambda s: (s["score"] is not None, s["score"] or 0), reverse=True)
    return {"samples": samples, "count": len(samples), "failed": failed}


async def list_samples(tenant_id, *, limit: int = 20) -> list[dict]:
    """The example posts we've already built, best first — read from the tagged
    pending actions so it survives a restart (the in-memory job does not)."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, payload->>'caption' AS caption,
                      payload->>'image_url' AS image_url,
                      payload->>'image_format' AS format,
                      payload->>'sample_score' AS score,
                      payload->>'sample_from_photo' AS from_photo,
                      payload->>'sample_from' AS "from",
                      payload->>'used_placeholder_photo' AS used_placeholder,
                      created_at
                 FROM actions
                WHERE action_type='content' AND status='pending'
                  AND payload->>'sample' = 'true'
             ORDER BY (payload->>'sample_score')::float DESC NULLS LAST, created_at DESC
                LIMIT $1""", max(1, min(limit, 50)))
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["score"] = float(d["score"]) if d.get("score") not in (None, "", "None") else None
        d["used_placeholder"] = str(d.get("used_placeholder")).lower() == "true"
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


__all__ = [
    "inspect_photo_templatizability", "classify_brand_photos", "templatizable_photos",
    "generate_samples", "list_samples", "TEMPLATIZE_RUBRIC_VERSION",
]
