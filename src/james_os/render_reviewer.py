"""The design QA reviewer — a strict senior designer doing FINAL QA on a rendered
post before it ships to the brand owner.

design_eye.py grades design APPEAL (would this stop a scroll). This is different:
it judges EXECUTION — is the finished artwork actually clean, or does it have the
tells of a broken auto-generated post: text clipped / overlapping / running out
of frame, an empty coloured box or pill with no label, a logo that overlaps text
or is cut off, a stray text fragment, unreadable text lost in the photo. Only
posts that pass this gate reach the user.

It can be GROUNDED with a few exemplar images (the best-performing templates in
the brand's niche) so it calibrates "shippable" against a real quality bar.

Honesty rules mirror design_eye: no key / unreadable image → status 'no_key' /
'failed' (the caller decides how to treat a non-ok review — we FAIL-OPEN so a
reviewer outage never blocks all output). The blended polish score + the pass
decision are computed HERE from the axes, never taken from the model (D2).
"""

from __future__ import annotations

import base64
import json
import logging

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger("render_reviewer")

REVIEW_RUBRIC_VERSION = "v1"
_MODEL = "gpt-4o"

# axis → weight. Legibility and placement are what actually make an auto-post
# look broken, so they count double and also carry hard floors below.
_AXES: dict[str, int] = {
    "text_legibility": 2,   # every word fully in-frame, unclipped, readable
    "placement": 2,         # nothing empty/floating/misaligned; logo clean
    "composition": 1,       # balanced; text doesn't bury the subject/face
    "contrast": 1,          # text separates from the background everywhere
    "finish": 1,            # looks professionally made, not amateur/glitchy
}
# A post must clear the blended bar AND both critical-axis floors to ship.
_MIN_POLISH = 62.0
_FLOOR_LEGIBILITY = 55
_FLOOR_PLACEMENT = 52
# Any of these, flagged by the reviewer, is an automatic fail regardless of score.
_CRITICAL = ("clipped_text", "out_of_frame_text", "unreadable_text",
             "empty_element", "broken_logo", "overlapping_text", "glitch")


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


_SYSTEM = (
    "You are a ruthless senior brand designer doing FINAL QA on a finished social "
    "post (1080x1350) before it goes to the client. You judge EXECUTION, not the "
    "idea — a broken layout fails even if the concept is great. Be HARSH: most "
    "auto-generated posts have a flaw and should NOT ship.\n\n"
    "Score FIVE axes 0–100, each with a one-line 'evidence' naming exactly what "
    "you SEE:\n"
    "  text_legibility — is EVERY word fully inside the frame, not clipped at an "
    "edge, not overlapping other text, not cramped, and readable at a glance? Any "
    "cut-off or colliding text scores below 40.\n"
    "  placement — is every element intentional? PENALISE HARD: an empty coloured "
    "box/bar/pill with no text on it, a floating or misaligned block, a stray "
    "word fragment, a logo that overlaps text / is cut off / sits awkwardly.\n"
    "  composition — balanced with breathing room, and text does NOT bury the "
    "main subject or a face?\n"
    "  contrast — does the text separate from the background EVERYWHERE (not lost "
    "in bright sky or busy detail)?\n"
    "  finish — does it look professionally made, or amateur / broken / "
    "AI-glitchy (warped shapes, garbled marks)?\n\n"
    "Then list critical_flaws — include ONLY those clearly present, from exactly: "
    f"{list(_CRITICAL)}. Empty list if none.\n"
    "Also give issues: 1–4 short, concrete fixes (e.g. 'headline clipped on the "
    "right edge', 'empty yellow pill under the subhead').\n\n"
    "If REFERENCE images are provided, they are the quality bar this niche ships "
    "at — hold the post to that standard.\n\n"
    "Return STRICT JSON: {\"axes\": {\"text_legibility\": {\"score\": int, "
    "\"evidence\": str}, \"placement\": {...}, \"composition\": {...}, "
    "\"contrast\": {...}, \"finish\": {...}}, \"critical_flaws\": [str], "
    "\"issues\": [str]}. If the image is blank or corrupt, score everything low "
    "and say so — never invent a clean review."
)


def _as_data_uri(image: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def _polish(axes: dict) -> float:
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


def _axis(axes: dict, key: str) -> float:
    node = axes.get(key) if isinstance(axes, dict) else None
    try:
        return float(node.get("score"))
    except (TypeError, ValueError, AttributeError):
        return 0.0


async def review_post(image: bytes | str, *, exemplars: list[bytes] | None = None,
                      mime: str = "image/png") -> dict:
    """QA one rendered post. Returns {status, passed, polish_score, axes,
    critical_flaws, issues}. status 'ok' | 'no_key' | 'failed'. `passed` and
    `polish_score` are computed HERE. `exemplars` are reference 'good' images.

    Callers should FAIL-OPEN on a non-ok status (don't discard a post because the
    reviewer was unavailable) but FAIL-CLOSED on a real 'ok' verdict."""
    client = _client()
    if client is None:
        return {"status": "no_key", "rubric_version": REVIEW_RUBRIC_VERSION}
    cand = _as_data_uri(bytes(image), mime) if isinstance(image, (bytes, bytearray)) else str(image)

    content: list = []
    for ex in (exemplars or [])[:2]:
        if isinstance(ex, (bytes, bytearray)):
            content.append({"type": "text", "text": "REFERENCE (the quality bar in this niche):"})
            content.append({"type": "image_url", "image_url": {"url": _as_data_uri(bytes(ex), "image/jpeg")}})
    content.append({"type": "text", "text": "NOW QA THIS POST — is it clean enough to ship?"})
    content.append({"type": "image_url", "image_url": {"url": cand, "detail": "high"}})

    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": content}],
            max_tokens=700, temperature=0.0, response_format={"type": "json_object"})
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — reported, never faked
        return {"status": "failed", "rubric_version": REVIEW_RUBRIC_VERSION, "error": str(exc)[:200]}

    axes = out.get("axes") if isinstance(out.get("axes"), dict) else {}
    polish = _polish(axes)
    critical = [c for c in (out.get("critical_flaws") or []) if c in _CRITICAL]
    passed = (
        not critical
        and polish >= _MIN_POLISH
        and _axis(axes, "text_legibility") >= _FLOOR_LEGIBILITY
        and _axis(axes, "placement") >= _FLOOR_PLACEMENT
    )
    return {
        "status": "ok",
        "rubric_version": REVIEW_RUBRIC_VERSION,
        "passed": bool(passed),
        "polish_score": polish,
        "axes": axes,
        "critical_flaws": critical,
        "issues": [str(i)[:160] for i in (out.get("issues") or [])][:4],
    }


__all__ = ["review_post", "REVIEW_RUBRIC_VERSION"]
