"""The ask() pipeline — the core of JAMES OS.

Question → retrieve → rerank → cite-or-refuse generation → verification → log.
This is the only path through which an AI ever speaks for the company.
"""

import asyncio
import json
import logging
import time
from uuid import UUID

from .config import settings
from .db import acquire
from .llm import LLMParseError, get_llm
from .models import AskRequest, AskResponse, Citation, RetrievedEvent
from .prompts import (
    VERIFICATION_PROMPT,
    build_system_prompt,
    build_verification_messages,
    format_memory_block,
)
from .rerank import rerank
from .retrieval import search

logger = logging.getLogger("james_os.ask")


async def ask(req: AskRequest, tenant_id: UUID | None = None) -> AskResponse:
    started = time.perf_counter()
    timings: dict[str, int] = {}

    def _mark(label: str, t0: float) -> None:
        timings[label] = int((time.perf_counter() - t0) * 1000)

    # Conversational follow-ups: keep the recent turns, refuse politely when
    # the thread outgrows what we can prompt with (mirror of the intelligence
    # platform's clear-the-chat-to-continue behavior).
    history = [t_ for t_ in (req.history or []) if (t_.content or "").strip()][-12:]
    if sum(len(t_.content) for t_ in history) > 24_000:
        return AskResponse(
            response="This conversation is too long to keep in context — "
                     "clear the chat and ask again.",
            citations=[], refused=True, refusal_reason="conversation_too_long",
            confidence=0.0, retrieved_event_ids=[],
            model=get_llm().model_name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # The system prompt only needs the tenant's guidelines, not the retrieved
    # events — so build it CONCURRENTLY with retrieval+rerank instead of after.
    system_task = asyncio.create_task(build_system_prompt(tenant_id))

    # A follow-up like "what about the second one?" retrieves nothing on its
    # own — condense it against the conversation into a standalone query
    # (retrieval only; generation still sees the true history).
    retrieval_q = req.question
    if history:
        t = time.perf_counter()
        retrieval_q = await _standalone_question(req.question, history) or req.question
        _mark("condense", t)

    t = time.perf_counter()
    candidates = await search(
        retrieval_q,
        tenant_id=tenant_id,
        event_types=req.event_types,
        since=req.since,
        until=req.until,
    )
    _mark("search", t)
    t = time.perf_counter()
    retrieved = await rerank(retrieval_q, candidates)
    _mark("rerank", t)

    if not retrieved:
        system_task.cancel()  # nothing to generate — don't leave it dangling
        return await _persist_and_return(
            req,
            AskResponse(
                response="I don't have anything in memory on that topic.",
                citations=[],
                refused=True,
                refusal_reason="no_relevant_events_found",
                confidence=0.0,
                retrieved_event_ids=[],
                model=get_llm().model_name,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
            retrieved,
            tenant_id,
        )

    try:
        system = await system_task
        # Sensitivity gating (intelligence parity): stamp knowledge-base
        # passages with their travel tier and append the READ-vs-REPRODUCE
        # policy — internal by default; 'public' excludes Restricted/NDA data
        # from output. FAIL-CLOSED: the policy is appended whenever memory is
        # in play (even if the tier lookup errored and returned {}), so a
        # silent lookup failure can never strip the guardrails from an answer
        # that does contain knowledge-base content.
        from .sensitivity import policy_for, sensitivity_map_for
        sens_map = await sensitivity_map_for(retrieved, tenant_id)
        # Structural sensitivity gate (defense-in-depth beyond the prompt policy):
        # never place NDA-Protected passages in the prompt, and for a PUBLIC
        # audience also drop Restricted. Non-KB memory has no tier and is kept.
        _aud = getattr(req, "audience", "internal")
        _blocked = {"NDA-Protected", "Restricted"} if _aud == "public" else {"NDA-Protected"}
        retrieved = [ev for ev in retrieved
                     if sens_map.get(str(ev.event_id)) not in _blocked]
        if not retrieved:
            # Everything relevant was filtered out for this audience — refuse
            # cleanly instead of generating against an empty corpus.
            return await _persist_and_return(
                req,
                AskResponse(
                    response="I don't have anything shareable on that for this audience.",
                    citations=[], refused=True,
                    refusal_reason="no_shareable_events_for_audience",
                    confidence=0.0, retrieved_event_ids=[],
                    model=get_llm().model_name,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
                retrieved, tenant_id,
            )
        system = f"{system}\n\n{policy_for(_aud)}"
        t = time.perf_counter()
        answer = await _generate(req.question, retrieved, system, sens_map,
                                 history=history)
        _mark("generate", t)
    except LLMParseError as e:
        # The model produced unparseable output (most often: hit max_tokens
        # mid-JSON). Refuse cleanly instead of 500'ing the request.
        answer = {
            "answer": "I couldn't return a structured answer for that question.",
            "claims": [],
            "refused": True,
            "refusal_reason": f"llm_output_unparseable: {e}",
        }

    if not answer.get("refused"):
        t = time.perf_counter()
        try:
            verified = await _verify(answer, retrieved, sens_map)
        except LLMParseError:
            verified = False
        _mark("verify", t)
        if not verified:
            answer = {
                "answer": "I cannot reliably ground that answer in memory.",
                "claims": [],
                "refused": True,
                "refusal_reason": "verification_pass_failed",
            }

    response = _to_response(answer, retrieved, started)
    timings["total"] = response.latency_ms
    logger.info(
        "ask timings(ms): %s | candidates=%d retrieved=%d",
        " ".join(f"{k}={v}" for k, v in timings.items()),
        len(candidates),
        len(retrieved),
    )
    await _persist_and_return(req, response, retrieved, tenant_id)
    return response


async def _standalone_question(question: str, history: list) -> str:
    """Rewrite a follow-up into one self-contained retrieval question.
    Best-effort — empty string on any failure, caller falls back to the
    raw question."""
    convo = "\n".join(f"{t.role}: {t.content[:600]}" for t in history[-6:])
    try:
        out = await get_llm().complete_json(
            system=(
                "Given a conversation and the user's next question, rewrite "
                "that question as ONE standalone search query that contains "
                "every entity/topic it implicitly refers to. Return STRICT "
                'JSON: {"question": str}'
            ),
            messages=[{"role": "user",
                       "content": f"<conversation>\n{convo}\n</conversation>\n\n"
                                  f"<next_question>\n{question}\n</next_question>"}],
            max_tokens=200,
        )
        return str(out.get("question") or "").strip()[:500]
    except Exception:  # noqa: BLE001 — condensation must never break Ask
        return ""


async def _generate(
    question: str, retrieved: list[RetrievedEvent], system: str,
    sensitivity_map: dict | None = None,
    history: list | None = None,
) -> dict:
    memory = format_memory_block(retrieved, sensitivity_map)
    follow_up_rule = (
        "\n\n(Consider the prior conversation for what the question refers "
        "to, but ground every NEW claim in the memory passages above.)"
        if history else ""
    )
    messages = [
        *({"role": t.role, "content": t.content} for t in (history or [])),
        {
            "role": "user",
            "content": f"{memory}\n\n<question>\n{question}\n</question>{follow_up_rule}",
        },
    ]
    # 2000 gives headroom for a grounded answer + claims list without
    # truncating the JSON (the historical 1024 default chopped rich answers
    # mid-object and surfaced as a 500). The model stops at the natural end,
    # so this is a safety ceiling, not the typical output size.
    return await get_llm().complete_json(
        system=system, messages=messages, max_tokens=2000
    )


async def _verify(answer: dict, retrieved: list[RetrievedEvent],
                  sensitivity_map: dict | None = None) -> bool:
    claims = answer.get("claims") or []
    if not claims:
        # No claims to verify is OK if the answer text is empty/refused;
        # otherwise it's a violation of cite-or-refuse.
        return not (answer.get("answer") or "").strip()

    messages = build_verification_messages(answer, retrieved, sensitivity_map)
    # The verifier returns a small JSON verdict ({verified: bool, ...}); it
    # doesn't need a big budget. 768 is plenty and trims the worst case.
    result = await get_llm().complete_json(
        system=VERIFICATION_PROMPT, messages=messages, max_tokens=768
    )
    return bool(result.get("verified"))


def _to_response(
    answer: dict, retrieved: list[RetrievedEvent], started: float
) -> AskResponse:
    citations = [
        Citation(
            event_id=UUID(c["event_id"]) if isinstance(c["event_id"], str) else c["event_id"],
            span=c.get("span", ""),
            confidence=float(c.get("confidence", 0.5)),
        )
        for c in (answer.get("claims") or [])
        if c.get("event_id")
    ]
    avg_conf = (
        sum(c.confidence for c in citations) / len(citations) if citations else 0.0
    )
    # Claude returns answer:null on refusal; coerce to "" since the key
    # exists (so .get's default never fires).
    return AskResponse(
        response=answer.get("answer") or "",
        citations=citations,
        refused=bool(answer.get("refused", False)),
        refusal_reason=answer.get("refusal_reason"),
        confidence=avg_conf,
        retrieved_event_ids=[r.event_id for r in retrieved],
        model=get_llm().model_name,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def _persist_and_return(
    req: AskRequest,
    response: AskResponse,
    retrieved: list[RetrievedEvent],
    tenant_id: UUID | None,
) -> AskResponse:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO queries (
                user_id, question, retrieved_event_ids,
                response, citations, confidence,
                refused, refusal_reason, model, latency_ms
            ) VALUES ($1, $2, $3::uuid[], $4, $5::jsonb, $6, $7, $8, $9, $10)
            """,
            req.user_id,
            req.question,
            response.retrieved_event_ids,
            response.response,
            json.dumps([c.model_dump(mode="json") for c in response.citations]),
            response.confidence,
            response.refused,
            response.refusal_reason,
            response.model,
            response.latency_ms,
        )
    return response


__all__ = ["ask", "settings"]
