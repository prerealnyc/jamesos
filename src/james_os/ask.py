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

    # The system prompt only needs the tenant's guidelines, not the retrieved
    # events — so build it CONCURRENTLY with retrieval+rerank instead of after.
    system_task = asyncio.create_task(build_system_prompt(tenant_id))

    t = time.perf_counter()
    candidates = await search(
        req.question,
        tenant_id=tenant_id,
        event_types=req.event_types,
        since=req.since,
        until=req.until,
    )
    _mark("search", t)
    t = time.perf_counter()
    retrieved = await rerank(req.question, candidates)
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
        t = time.perf_counter()
        answer = await _generate(req.question, retrieved, system)
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
            verified = await _verify(answer, retrieved)
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


async def _generate(
    question: str, retrieved: list[RetrievedEvent], system: str
) -> dict:
    memory = format_memory_block(retrieved)
    messages = [
        {
            "role": "user",
            "content": f"{memory}\n\n<question>\n{question}\n</question>",
        }
    ]
    # 2000 gives headroom for a grounded answer + claims list without
    # truncating the JSON (the historical 1024 default chopped rich answers
    # mid-object and surfaced as a 500). The model stops at the natural end,
    # so this is a safety ceiling, not the typical output size.
    return await get_llm().complete_json(
        system=system, messages=messages, max_tokens=2000
    )


async def _verify(answer: dict, retrieved: list[RetrievedEvent]) -> bool:
    claims = answer.get("claims") or []
    if not claims:
        # No claims to verify is OK if the answer text is empty/refused;
        # otherwise it's a violation of cite-or-refuse.
        return not (answer.get("answer") or "").strip()

    messages = build_verification_messages(answer, retrieved)
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
