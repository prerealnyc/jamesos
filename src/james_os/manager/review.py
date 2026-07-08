"""Reviewer legs (D7) — ported from bm2.0 backend/app/agents/reviewer.py
(+ the em-dash post-pass from backend/app/agents/hands.py) as pure/async
functions with no coupling to the actions queue or the state machine.

Two callers share these legs:
  * content.py — after its existing voice-QA it runs the deterministic legs
    (lint + guardrails) and merges violations into its flagged/qa annotations;
  * the execution service — runs the full 4-leg `review()` gate and, on fail,
    the donor's single revise-and-rescore loop via `revise_once()`.

The four legs, semantics intact from the donor:
  1. deterministic AI-ism lint (manager/ai_isms.py, ZERO tolerance per D7)
  2. profile guardrail substring checks (guardrails.* incl. learned_avoid)
  3. voice-fidelity threshold (0.6 floor; None = cold start, "voice
     unverified", passes) — the LLM voice judge itself stays the content
     engine's voice-QA; this leg only maps its score into ReviewResult
  4. best-effort fact-check of specific claims vs provided evidence
     (extract tier; an outage never blocks — the other legs still gate)

Nothing here runs unless a caller opts in: the pre-merge content engine
paths are untouched when these functions are never invoked.
"""

import asyncpg

from . import ai_isms, profile
from .contracts import ReviewResult
from .providers import Providers, get_providers

LINT_MAX_VIOLATIONS = 0  # D7: the deterministic AI-ism gate has no tolerance
VOICE_MIN_SCORE = 0.6

# only these guardrail fields are substring-matchable; others (autonomy
# tiers, mandatory-approval types) are not text rules
_GUARDRAIL_KEY_MARKERS = ("banned", "off_limits", "off-limits", "offlimits", "topic", "avoid")

_REVISE_SYSTEM = (
    "You are the brand's copy editor. You rewrite drafts to clear review annotations while "
    "keeping the topic, facts, and the brand's voice. Return only the revised post copy — "
    "no commentary, no preamble."
)

_FACT_SYSTEM = (
    "You are a strict fact-checker for brand content. Compare a draft's SPECIFIC "
    "factual assertions (numbers, names, dates, events, claims of record) against "
    "the evidence and context provided. Generic opinions, advice, and brand voice "
    "are NOT claims. Return JSON only: {\"unsupported_claims\": [str (the claim, "
    "quoted short, and why it is unsupported)]}. Empty list when every specific "
    "claim is supported or the draft makes no specific claims."
)


def lint_clean(text: str) -> str:
    """Deterministic house-style post-pass (donor hands.py): em/en dashes are
    a pure formatting tic the D7 lint bans (density > 1/1000w); strip them so
    a good draft isn't bounced on punctuation alone (voice fidelity remains
    the real gate)."""
    if not text:
        return text
    return text.replace(" — ", ", ").replace("—", "-").replace("–", "-")


def lint(text: str) -> list[str]:
    """Leg 1 — deterministic AI-ism lint, zero tolerance (D7)."""
    return ai_isms.lint(text or "")


async def guardrail_check(conn: asyncpg.Connection, text: str) -> list[str]:
    """Leg 2 — case-insensitive substring match of banned words / off-limits
    topics from the profile envelope (guardrails.*, incl. learned_avoid)."""
    lowered = (text or "").lower()
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in await profile.current_fields(conn, section="guardrails"):
        field_key = row["field_key"]
        if not any(marker in field_key.lower() for marker in _GUARDRAIL_KEY_MARKERS):
            continue
        value = row["value"].get("v") if isinstance(row["value"], dict) else row["value"]
        for term in _terms(value):
            term = term.strip()
            key = (field_key, term.lower())
            if not term or key in seen:
                continue
            seen.add(key)
            if term.lower() in lowered:
                violations.append(f"{field_key}: contains '{term}'")
    return violations


def _terms(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def voice_leg(voice_score: float | None) -> tuple[bool, str]:
    """Leg 3 — threshold semantics over an externally-judged score. The LLM
    judge stays the content engine's independent voice-QA (its LLM #2); this
    only maps that score onto the D7 floor. None = cold start / QA skipped:
    the leg passes but the draft is honestly 'voice unverified' (donor D7)."""
    if voice_score is None:
        return True, "voice check skipped: no independent voice score (cold start; voice unverified)"
    return voice_score >= VOICE_MIN_SCORE, ""


async def fact_check(
    draft_text: str,
    evidence: list[dict] | None,
    *,
    topic: str = "",
    rationale: str = "",
    providers: Providers | None = None,
) -> list[str]:
    """Leg 4 — PRD R3.2 'vet if it's accurate': flag specific factual claims
    not supported by the supplied evidence/rationale. Best-effort: a
    fact-check outage never blocks the pipeline — the other legs still gate."""
    if not (draft_text or "").strip():
        return []
    providers = providers or get_providers()
    evidence_block = "\n".join(
        f"- {e.get('url') or e.get('ref')}: {e.get('note', '')}"
        for e in (evidence or []) if isinstance(e, dict)
    )
    try:
        raw = await providers.llm.complete_json(
            "extract", _FACT_SYSTEM,
            "List the unsupported claims in this draft.\n"
            f"TOPIC: {topic}\nWHY IT WAS PLANNED: {rationale[:400]}\n"
            f"EVIDENCE:\n{evidence_block or '(none supplied — flag only concrete stats/names presented as fact)'}\n\n"
            f"DRAFT:\n{draft_text[:2500]}",
        )
        return [str(c).strip() for c in (raw.get("unsupported_claims") or []) if str(c).strip()][:5]
    except Exception:  # noqa: BLE001 — honest degradation: other legs still gate
        return []


async def review(
    conn: asyncpg.Connection,
    draft_text: str,
    *,
    platform: str,
    fmt: str,
    evidence: list[dict] | None = None,
    voice_score: float | None = None,
    topic: str = "",
    rationale: str = "",
    providers: Providers | None = None,
) -> ReviewResult:
    """The full 4-leg reviewer gate (donor _score + _apply_fact_check).

    `voice_score` is the content engine's independent voice-QA score (leg 3
    maps it onto the floor; None = cold start, voice unverified). Leg 4 runs
    only when the caller supplies `evidence` (pass [] to fact-check a draft
    that cites nothing — donor semantics: only concrete stats/names presented
    as fact get flagged then). `platform`/`fmt` are recorded for the notes so
    annotations stay legible in the queue."""
    lint_violations = lint(draft_text)
    guardrail_violations = await guardrail_check(conn, draft_text)
    voice_passed, judge_notes = voice_leg(voice_score)
    result = ReviewResult(
        passed=(
            not guardrail_violations
            and len(lint_violations) <= LINT_MAX_VIOLATIONS
            and voice_passed
        ),
        lint_violations=lint_violations,
        guardrail_violations=guardrail_violations,
        voice_score=voice_score,
        judge_notes=judge_notes or f"reviewed as {fmt} for {platform}",
    )
    if evidence is not None:
        claims = await fact_check(
            draft_text, evidence, topic=topic, rationale=rationale, providers=providers
        )
        if claims:
            result.fact_violations = claims
            result.passed = False
    return result


async def revise_once(
    draft_text: str,
    result: ReviewResult,
    *,
    brand_name: str,
    topic: str,
    platform: str,
    providers: Providers | None = None,
) -> str:
    """D7: the ONE automatic revise pass (retry cap 1) — a content-tier call
    that rewrites the draft to clear the review annotations. The caller
    rescores the returned text once via review(); if it still fails, the
    order is rejected (never a second loop)."""
    providers = providers or get_providers()
    issues = "; ".join(
        [*result.guardrail_violations, *result.lint_violations]
        + [f"unsupported claim — cut it or hedge it: {c}" for c in result.fact_violations]
        + (
            [f"voice fidelity {result.voice_score:.2f} below {VOICE_MIN_SCORE}"]
            if result.voice_score is not None and result.voice_score < VOICE_MIN_SCORE
            else []
        )
    )
    prompt = (
        "Revise this draft so it clears the review annotations below. Keep it a ready-to-publish "
        "post on the same topic in the brand's voice.\n"
        f"Brand: {brand_name}\n"
        f"Topic: {topic}\n"
        f"Platform: {platform}\n"
        f"Annotations: {issues or 'below-threshold voice fidelity'}\n\n"
        f"Draft:\n{draft_text}\n\n"
        "Return only the revised post copy."
    )
    revised = await providers.llm.complete("content", _REVISE_SYSTEM, prompt, max_tokens=700)
    return revised.strip()


def reject_reason(result: ReviewResult) -> str:
    """Human-legible one-liner for queue annotations / rejection events."""
    parts: list[str] = []
    if result.fact_violations:
        parts.append("unsupported claims: " + "; ".join(result.fact_violations[:3]))
    if result.guardrail_violations:
        parts.append("guardrails: " + "; ".join(result.guardrail_violations))
    if len(result.lint_violations) > LINT_MAX_VIOLATIONS:
        shown = "; ".join(result.lint_violations[:5])
        parts.append(
            f"{len(result.lint_violations)} lint violation(s) (max {LINT_MAX_VIOLATIONS}): {shown}"
        )
    if result.voice_score is not None and result.voice_score < VOICE_MIN_SCORE:
        parts.append(f"voice fidelity {result.voice_score:.2f} below {VOICE_MIN_SCORE}")
    return " | ".join(parts) or "rejected by reviewer"


__all__ = [
    "LINT_MAX_VIOLATIONS",
    "VOICE_MIN_SCORE",
    "lint_clean",
    "lint",
    "guardrail_check",
    "voice_leg",
    "fact_check",
    "review",
    "revise_once",
    "reject_reason",
]
