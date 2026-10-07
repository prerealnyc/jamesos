"""The design eye — a harsh, consistent critic that SEES a single post image.

Where perception.py fingerprints a VIDEO (frames → format/pacing) for our own
reference clips, this looks at ONE still post image (ours or a competitor's) and
returns two things in a single GPT-4o vision call:

  1. A 7-axis design SCORE (0–100 per axis, each with a one-line observable
     reason) — the "eye". The blended score is computed by the CALLER from these
     axes (D2: never model-reported), via `compute_eye_score`.
  2. The design DNA — a structured, brand-agnostic description of WHAT makes the
     image work, spoken in the compositor's own vocabulary so it can later feed
     generation with no translation layer.

Honesty rules (mirrors perception.py):
  * No OpenAI key, or an image we cannot send, returns status='no_key' /
    'failed' — never a faked score.
  * The rubric is FROZEN and versioned (RUBRIC_VERSION). Changing an anchor or a
    weight REQUIRES bumping the version, so stored scores stay comparable and a
    drift harness can gate the bump.
  * The model scores the IMAGE ALONE — it is told nothing about engagement, so
    the eye cannot rationalise a number it was handed (anti-anchoring). Whether a
    post actually performed is a SEPARATE signal the caller blends in.
  * `non_design_driver` lets the model flag engagement it suspects came from a
    giveaway / news / celebrity / tragedy rather than the craft, so the ranker
    can down-weight it.
"""

from __future__ import annotations

import base64

from openai import AsyncOpenAI

from . import spend
from .config import settings

RUBRIC_VERSION = "v1"
_MODEL = "gpt-4o"

# axis key → weight. Stopping-power and message-transmission are what actually
# drive a scroll-stop-and-read on Instagram, so they count double. Weights are
# part of the frozen rubric: changing them bumps RUBRIC_VERSION.
_AXES: dict[str, int] = {
    "stopping_power": 2,
    "compositional_craft": 1,
    "focal_clarity": 1,
    "color_contrast": 1,
    "type_legibility": 1,
    "message_transmission": 2,
    "craft_finish": 1,
}


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


_SYSTEM = (
    "You are a ruthless, senior Instagram creative director grading ONE still "
    "post image. You are HARSH and specific: most branded posts are mediocre, so "
    "the middle of your scale (40–60) is where the average post lives, 80+ is "
    "genuinely exceptional work, and below 30 is broken. Do NOT be generous. You "
    "are told NOTHING about how the post performed — judge only what you SEE.\n\n"
    "Score SEVEN axes, each 0–100, using these anchors "
    "(0–20 broken / 21–40 weak / 41–60 average / 61–80 strong / 81–100 "
    "exceptional). For EVERY axis give a one-line 'evidence' naming the concrete "
    "thing you saw that justifies the number.\n"
    "  1. stopping_power — would this halt a fast scroll in the feed? (visual "
    "tension, a face, a bold claim, an unexpected image). WEIGHTED x2.\n"
    "  2. compositional_craft — layout, balance, use of margins/space, grid "
    "discipline, intentional hierarchy vs cluttered or arbitrary.\n"
    "  3. focal_clarity — is there ONE clear focal point, or does the eye not "
    "know where to land? Is the subject crisp and prominent or lost/faded?\n"
    "  4. color_contrast — is the palette deliberate with strong figure/ground "
    "contrast, or muddy / low-contrast / off-brand-looking?\n"
    "  5. type_legibility — read it at THUMBNAIL size: is the text sharp, big "
    "enough, well-set, and readable, or thin/cramped/over-long?\n"
    "  6. message_transmission — can a stranger grasp the ONE idea in under 1.5 "
    "seconds WITHOUT the caption? Reward a single clear message. WEIGHTED x2.\n"
    "  7. craft_finish — does it look professionally finished, or amateur "
    "(stretched photo, placeholder-looking imagery, tangents, jpeg mush)?\n\n"
    "Then extract the design DNA — brand-agnostic, describing HOW it is built:\n"
    "  layout_family: one of [text_card, photo_beside_text, photo_with_overlay, "
    "full_bleed_photo, framed_photo, split_panel, data_viz, carousel_cover, "
    "other].\n"
    "  focal_element: what the eye lands on first (short phrase).\n"
    "  color_strategy: e.g. 'dark ground + one bright accent', 'high-key pastel'.\n"
    "  text_density: one of [none, minimal, moderate, heavy].\n"
    "  hook_mechanism: what earns the stop (bold_claim, curiosity_gap, number, "
    "face, contrast, pattern_interrupt, aspirational_image, none).\n"
    "  emotional_register: e.g. 'defiant', 'calm authority', 'playful'.\n"
    "  type_treatment: short note on the type (e.g. 'huge condensed all-caps, "
    "one emphasis word in accent color').\n"
    "  primary_format_guess: one of [static, carousel, reel, unknown].\n\n"
    "Also give:\n"
    "  why_it_works: 1–2 sentences on the design reason it would earn engagement "
    "(or why it would NOT).\n"
    "  transferable_pattern: the reusable, brand-agnostic recipe another brand "
    "could apply (NOT this brand's words or images).\n"
    "  non_design_driver: {present: boolean, reason: string} — set present=true "
    "only if the engagement would likely come from something OTHER than the "
    "craft (giveaway, breaking news, celebrity, tragedy); else present=false.\n\n"
    "Return STRICT JSON: {\"axes\": {\"<axis>\": {\"score\": int, \"evidence\": "
    "str}, ...for all 7}, \"design_dna\": {...the fields above}, \"why_it_works\":"
    " str, \"transferable_pattern\": str, \"non_design_driver\": {\"present\": "
    "bool, \"reason\": str}}. If the image is blank, corrupt, or uninformative, "
    "say so honestly in every evidence field and score accordingly — never invent."
)


def compute_eye_score(axes: dict) -> float:
    """Weighted mean of the axis scores, 0–100, computed HERE — never taken from
    the model (D2). Missing / non-numeric axes are skipped so a partial result
    still yields a defensible number over what was actually scored."""
    num = 0.0
    den = 0
    for key, weight in _AXES.items():
        node = axes.get(key) if isinstance(axes, dict) else None
        raw = node.get("score") if isinstance(node, dict) else None
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        val = max(0.0, min(100.0, val))
        num += val * weight
        den += weight
    return round(num / den, 1) if den else 0.0


def _as_data_uri(image: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


async def inspect_image(
    image: bytes | str, rubric_version: str = RUBRIC_VERSION, *, mime: str = "image/jpeg",
) -> dict:
    """Grade ONE post image. `image` is raw bytes (preferred) or an https URL.

    Returns {status, rubric_version, axes, eye_score, design_dna, why_it_works,
    transferable_pattern, non_design_driver}. status is 'ok' on a real grade,
    'no_key' when no OpenAI key is configured, or 'failed' on any error — the
    caller must check status and never treat a non-ok result as a real score."""
    client = _client()
    if client is None:
        return {"status": "no_key", "rubric_version": rubric_version}

    if isinstance(image, (bytes, bytearray)):
        url = _as_data_uri(bytes(image), mime)
    else:
        url = str(image)  # already a URL

    try:
        import json as _json

        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Grade this Instagram post image."},
                    {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
                ]},
            ],
            max_tokens=900,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        await spend.record_tokens("openai", getattr(resp, "model", "") or _MODEL, getattr(resp, "usage", None), "design_eye.grade")
        raw = resp.choices[0].message.content or "{}"
        out = _json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — a vision/parse failure is reported, never faked
        return {"status": "failed", "rubric_version": rubric_version, "error": str(exc)[:200]}

    axes = out.get("axes") if isinstance(out.get("axes"), dict) else {}
    return {
        "status": "ok",
        "rubric_version": rubric_version,
        "axes": axes,
        "eye_score": compute_eye_score(axes),  # computed here, not model-reported
        "design_dna": out.get("design_dna") or {},
        "why_it_works": str(out.get("why_it_works") or "").strip(),
        "transferable_pattern": str(out.get("transferable_pattern") or "").strip(),
        "non_design_driver": out.get("non_design_driver") or {"present": False, "reason": ""},
    }


__all__ = ["inspect_image", "compute_eye_score", "RUBRIC_VERSION"]
