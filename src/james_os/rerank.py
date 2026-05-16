"""Reranker — turns OK retrieval into great retrieval.

Cohere Rerank v3 if a key is configured; otherwise pass-through (just truncate
to top_k). Falling back gracefully matters for local dev and tests.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .models import RetrievedEvent


async def rerank(
    query: str,
    candidates: list[RetrievedEvent],
    top_k: int | None = None,
) -> list[RetrievedEvent]:
    k = top_k or settings.retrieval_top_k_after_rerank
    if not candidates:
        return []
    if not settings.cohere_api_key:
        return candidates[:k]
    return await _cohere_rerank(query, candidates, k)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def _cohere_rerank(
    query: str, candidates: list[RetrievedEvent], top_k: int
) -> list[RetrievedEvent]:
    docs = [(c.raw_content or "") for c in candidates]
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://api.cohere.com/v2/rerank",
            headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
            json={
                "model": "rerank-v3.5",
                "query": query,
                "documents": docs,
                "top_n": min(top_k, len(docs)),
            },
        )
        r.raise_for_status()
        data = r.json()

    reranked: list[RetrievedEvent] = []
    for hit in data["results"]:
        idx = hit["index"]
        item = candidates[idx]
        item.score = float(hit["relevance_score"])
        reranked.append(item)
    return reranked
