"""The ask() pipeline — the core of JAMES OS.

Question → retrieve → rerank → cite-or-refuse generation → verification → log.
This is the only path through which an AI ever speaks for the company.
"""

import json
import time
from uuid import UUID

from .config import settings
from .db import acquire
from .llm import get_llm
from .models import AskRequest, AskResponse, Citation, RetrievedEvent
from .prompts import (
    VERIFICATION_PROMPT,
    build_system_prompt,
    build_verification_messages,
    format_memory_block,
)
from .rerank import rerank
from .retrieval import search


async def ask(req: AskRequest, tenant_id: UUID | None = None) -> AskResponse:
    started = time.perf_counter()

    candidates = await search(
        req.question,
        tenant_id=tenant_id,
        event_types=req.event_types,
        since=req.since,
        until=req.until,
    )
    retrieved = await rerank(req.question, candidates)

    if not retrieved:
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

    answer = await _generate(req.question, retrieved, tenant_id)

    if not answer.get("refused"):
        verified = await _verify(answer, retrieved)
        if not verified:
            answer = {
                "answer": "I cannot reliably ground that answer in memory.",
                "claims": [],
                "refused": True,
                "refusal_reason": "verification_pass_failed",
            }

    response = _to_response(answer, retrieved, started)
    await _persist_and_return(req, response, retrieved, tenant_id)
    return response


async def _generate(
    question: str, retrieved: list[RetrievedEvent], tenant_id: UUID | None
) -> dict:
    system = await build_system_prompt(tenant_id)
    memory = format_memory_block(retrieved)
    messages = [
        {
            "role": "user",
            "content": f"{memory}\n\n<question>\n{question}\n</question>",
        }
    ]
    return await get_llm().complete_json(system=system, messages=messages)


async def _verify(answer: dict, retrieved: list[RetrievedEvent]) -> bool:
    claims = answer.get("claims") or []
    if not claims:
        # No claims to verify is OK if the answer text is empty/refused;
        # otherwise it's a violation of cite-or-refuse.
        return not answer.get("answer", "").strip()

    messages = build_verification_messages(answer, retrieved)
    result = await get_llm().complete_json(
        system=VERIFICATION_PROMPT, messages=messages
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
    return AskResponse(
        response=answer.get("answer", ""),
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
