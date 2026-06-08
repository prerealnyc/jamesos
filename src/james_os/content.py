"""The content engine — the half that consumes memory to produce voice.

ask() refuses unless memory answers a question. This does the opposite
job: it WRITES. But it writes in the brand's voice, and the voice is not
the LLM's — it's assembled from the memory substrate:

    brief
      │
      ▼
  assemble_memory ── category-weighted retrieval
      │   voice_exemplars + thesis  → how the brand sounds / believes
      │   research + reference      → facts it may cite
      │   frustration ledger        → hard "never do this" guardrails
      │   plug-in rules (verbatim)  → non-negotiable constraints
      ▼
  generate (LLM #1)  → on-voice draft + grounded event ids
      │
      ▼
  voice-QA (LLM #2, independent)  → 0-1 voice score + drift notes
      │
      ▼
  actions table (status=pending)  → a human approves before anything ships

Honesty rules baked in:
  * If there is no voice/thesis material in memory, the engine REFUSES
    rather than emit generic text dressed up as on-voice. Nothing is
    queued in that case.
  * The QA pass is a real second LLM call that did not write the draft —
    not a hardcoded score. Below the floor the draft is still queued for
    a human but flagged, never silently shipped.
  * Nothing auto-publishes. The pending row in `actions` is the gate the
    existing approval queue already enforces.
  * Quality is bounded by what's ingested. `memory_used` is returned so
    the human sees exactly how thin/rich the voice grounding was.
"""

import json
import time
from uuid import UUID

from .config import settings
from .db import acquire
from .llm import get_llm
from .models import ContentBrief, ContentDraft, QAVerdict, RetrievedEvent
from .prompts import (
    build_content_system_prompt,
    build_qa_messages,
    format_content_memory,
    VOICE_QA_PROMPT,
)
from .rerank import rerank
from .retrieval import search

# event payload.category (and event_type) → memory bucket.
_VOICE_CATS = {"voice_corpus", "guideline"}
_THESIS_CATS = {"thesis"}
_FRUSTRATION_CATS = {"frustration"}
_FACT_CATS = {"research", "reference", "trend"}


def _bucket_of(ev: RetrievedEvent) -> str:
    cat = (ev.payload or {}).get("category", "")
    if ev.event_type == "voice_memo" or cat in _VOICE_CATS:
        return "voice"
    if cat in _THESIS_CATS:
        return "thesis"
    if cat in _FRUSTRATION_CATS:
        return "frustration"
    if cat in _FACT_CATS:
        return "facts"
    # Uncategorised / older memory is still usable as grounding context.
    return "facts"


async def assemble_memory(
    brief: ContentBrief, tenant_id: UUID | None
) -> tuple[dict[str, list[RetrievedEvent]], dict[str, int]]:
    """Two retrievals: topic-relevant facts, and voice-anchored material.
    Merged, deduped, bucketed by category."""
    topic_q = " ".join(
        x for x in (brief.topic, brief.research_subject, brief.pillar) if x
    ).strip()
    voice_q = (
        f"brand voice tone style point of view how it sounds "
        f"{brief.pillar} {brief.topic}"
    ).strip()

    topic_hits = await rerank(topic_q, await search(topic_q, tenant_id=tenant_id))
    # Voice/thesis must not be reranked against the topic — it would bury
    # the voice signal. Take it on retrieval score.
    voice_hits = (await search(voice_q, tenant_id=tenant_id))[
        : settings.retrieval_top_k_after_rerank
    ]

    seen: set[UUID] = set()
    buckets: dict[str, list[RetrievedEvent]] = {
        "voice": [], "thesis": [], "frustration": [], "facts": []
    }
    # Recent human rejections are authoritative guardrails — always include
    # them, even if semantic retrieval didn't surface them, so the engine
    # cannot repeat a freshly-flagged mistake.
    for ev in await _recent_frustrations(tenant_id):
        if ev.event_id in seen:
            continue
        seen.add(ev.event_id)
        buckets["frustration"].append(ev)
    # Diverse, real voice exemplars (random voice-corpus samples) so the
    # model sees James's actual cadence — not just near-duplicates of a
    # generic "brand voice" query.
    exemplars = await _voice_exemplars(tenant_id, settings.content_voice_exemplars)
    for ev in [*exemplars, *voice_hits, *topic_hits]:
        if ev.event_id in seen:
            continue
        seen.add(ev.event_id)
        buckets[_bucket_of(ev)].append(ev)

    # Cap each bucket so one huge category can't crowd the prompt.
    buckets["voice"] = buckets["voice"][: settings.content_voice_k]
    buckets["thesis"] = buckets["thesis"][: settings.content_voice_k]
    buckets["facts"] = buckets["facts"][: settings.content_facts_k]
    buckets["frustration"] = buckets["frustration"][: settings.content_voice_k]

    used = {k: len(v) for k, v in buckets.items()}
    return buckets, used


async def _voice_exemplars(
    tenant_id: UUID | None, limit: int = 4
) -> list[RetrievedEvent]:
    """Voice-corpus samples that ANCHOR the prompt on the brand's explicit
    voice profile, then add real, varied cadence from the rest of the corpus.

    Why not pure random: the corpus mixes a distilled voice-profile spec
    (the canonical 'how this brand sounds') with long-form material (e.g.
    academy transcripts). A flat `ORDER BY random()` surfaces the profile
    only as often as its share of rows, so most drafts get grounded on
    random mid-paragraphs and drift to generic voice. We always include a
    few profile chunks as the anchor, then fill with cadence samples.
    """
    if limit <= 0:
        return []
    # Heuristic: a doc whose filename marks it as the voice profile/spec.
    _PROFILE = (
        "(coalesce(payload->>'filename','') ILIKE '%voice_profile%' "
        "OR coalesce(payload->>'filename','') ILIKE '%brand_voice%' "
        "OR coalesce(payload->>'filename','') ILIKE '%voice_spec%')"
    )
    async with acquire(tenant_id) as conn:
        anchors = await conn.fetch(
            f"""
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'voice_corpus'
              AND superseded_by IS NULL
              AND length(raw_content) > 120
              AND {_PROFILE}
            ORDER BY random() LIMIT 3
            """
        )
        cadence = await conn.fetch(
            f"""
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'voice_corpus'
              AND superseded_by IS NULL
              AND length(raw_content) > 200
              AND NOT {_PROFILE}
            ORDER BY random() LIMIT $1
            """,
            limit,
        )
    # Anchors first so they win the bucket cap; fall back to all-random
    # behaviour automatically when no profile doc exists (anchors empty).
    rows = [*anchors, *cadence] if anchors else list(cadence)
    out: list[RetrievedEvent] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            RetrievedEvent(
                event_id=r["id"], event_type=r["event_type"],
                raw_content=r["raw_content"], payload=payload,
                effective_at=r["effective_at"], score=1.0,
                source_signal=["voice_exemplar"],
            )
        )
    return out


async def _recent_frustrations(
    tenant_id: UUID | None, limit: int = 8
) -> list[RetrievedEvent]:
    """The newest human-rejection guardrails, fetched by recency (not
    semantic match) so a just-recorded rejection always reaches the prompt."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'frustration'
              AND superseded_by IS NULL
            ORDER BY created_at DESC LIMIT $1
            """,
            limit,
        )
    out: list[RetrievedEvent] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            RetrievedEvent(
                event_id=r["id"],
                event_type=r["event_type"],
                raw_content=r["raw_content"],
                payload=payload,
                effective_at=r["effective_at"],
                score=1.0,
                source_signal=["recency"],
            )
        )
    return out


def _coerce_ids(raw: object) -> list[UUID]:
    out: list[UUID] = []
    for x in raw or []:
        try:
            out.append(UUID(str(x)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


async def generate_content(
    brief: ContentBrief, tenant_id: UUID | None = None
) -> ContentDraft:
    started = time.perf_counter()
    llm = get_llm()

    buckets, used = await assemble_memory(brief, tenant_id)

    def _draft(status: str, **kw) -> ContentDraft:
        return ContentDraft(
            status=status,
            draft=kw.get("draft", ""),
            platform=brief.platform,
            format=brief.format,
            pillar=brief.pillar,
            angle=kw.get("angle", ""),
            voice_score=kw.get("voice_score", 0.0),
            qa=kw.get("qa"),
            grounded_event_ids=kw.get("grounded_event_ids", []),
            memory_used=used,
            action_id=kw.get("action_id"),
            model=llm.model_name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            note=kw.get("note"),
        )

    # No voice grounding at all → refuse honestly, queue nothing.
    if not buckets["voice"] and not buckets["thesis"]:
        return _draft(
            "not_generated",
            note=(
                "No voice corpus or thesis in memory for this tenant — the "
                "engine will not fabricate a generic post and call it "
                "on-voice. Ingest voice_corpus / thesis material in Settings, "
                "then regenerate."
            ),
        )

    # ── LLM #1: generate ──
    system = await build_content_system_prompt(
        brief.platform, brief.format, tenant_id
    )
    brief_block = (
        f"<brief>\n"
        f"platform: {brief.platform}\n"
        f"format: {brief.format}\n"
        f"pillar: {brief.pillar or '(none specified)'}\n"
        f"topic: {brief.topic}\n"
        f"extra_instructions: {brief.extra_instructions or '(none)'}\n"
        f"</brief>"
    )
    memory_block = format_content_memory(buckets)
    try:
        gen = await llm.complete_json(
            system=system,
            messages=[{"role": "user", "content": f"{memory_block}\n\n{brief_block}"}],
            max_tokens=2000,
            temperature=0.7,  # voice work needs some range; QA is the gate
        )
    except Exception as e:  # noqa: BLE001
        return _draft("not_generated", note=f"generation failed: {e}")

    draft_text = (gen.get("draft") or "").strip()
    if gen.get("refused") or not draft_text:
        return _draft(
            "not_generated",
            note=gen.get("refusal_reason")
            or "model did not return a draft (stub LLM, or refused)",
        )

    grounded = _coerce_ids(gen.get("grounded_event_ids"))
    angle = (gen.get("angle") or "").strip()

    # ── LLM #2: independent voice-QA ──
    floor = settings.content_voice_floor
    qa_raw: dict = {}
    try:
        qa_raw = await llm.complete_json(
            system=VOICE_QA_PROMPT.format(floor=floor),
            messages=build_qa_messages(draft_text, buckets),
            max_tokens=600,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 — QA failure must not lose the draft
        qa_raw = {}

    score = float(qa_raw.get("voice_score", 0.0) or 0.0)
    drift = [str(d) for d in (qa_raw.get("drift") or [])]
    passed = bool(qa_raw.get("passed", score >= floor)) and score >= floor
    qa = QAVerdict(voice_score=score, passed=passed, drift=drift)
    status = "generated" if passed else "flagged"

    # ── queue as a pending action (the human gate) ──
    payload = {
        "platform": brief.platform,
        "pillar": brief.pillar,
        "format": brief.format,
        "content": draft_text,
        "caption": draft_text,
        "topic": brief.topic,
        "angle": angle,
        "voice_score": round(score, 3),
        "self_voice_score": gen.get("self_voice_score"),
        "qa_passed": passed,
        "qa_drift": drift,
        "grounded_event_ids": [str(g) for g in grounded],
        "memory_used": used,
        "flagged": not passed,
    }
    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """
            INSERT INTO actions (proposed_by, action_type, payload, status)
            VALUES ('content_engine', 'content', $1::jsonb, 'pending')
            RETURNING id
            """,
            json.dumps(payload),
        )

    note = None
    if not passed:
        note = (
            f"Voice-QA scored {score:.2f} (floor {floor:.2f}) — queued but "
            f"flagged for revision. A human still decides in the queue."
        )
    elif used["voice"] + used["thesis"] <= 1:
        note = (
            "Thin voice grounding (≤1 voice/thesis event in memory). It "
            "passed QA, but ingest more voice corpus for stronger fidelity."
        )

    return _draft(
        status,
        draft=draft_text,
        angle=angle,
        voice_score=score,
        qa=qa,
        grounded_event_ids=grounded,
        action_id=action_id,
        note=note,
    )


__all__ = ["generate_content", "assemble_memory"]
