"""Embedding providers.

Provider-agnostic interface so the embedding model is swappable per tenant
and across migrations. Stub provider returns deterministic fake vectors so
the system runs without API keys for local dev and tests.
"""

import hashlib
from abc import ABC, abstractmethod

import httpx
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


class Embedder(ABC):
    model_name: str
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


class StubEmbedder(Embedder):
    """Deterministic fake embeddings derived from text hash.

    Same text → same vector. Different text → different vector. Useful for
    local dev and tests without API keys. Quality is terrible — only for
    plumbing verification, not retrieval quality testing.
    """

    model_name = "stub"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._fake_embed(t) for t in texts]

    def _fake_embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(h[:8], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype(np.float32)
        v /= np.linalg.norm(v) + 1e-9
        return v.tolist()


class VoyageEmbedder(Embedder):
    def __init__(self, api_key: str, model: str = "voyage-3-large", dim: int = 1024):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required for VoyageEmbedder")
        self.api_key = api_key
        self.model_name = model
        self.dim = dim

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model_name,
                    "input": texts,
                    "input_type": "document",
                },
            )
            r.raise_for_status()
            data = r.json()
            return [item["embedding"] for item in data["data"]]


def make_embedder() -> Embedder:
    provider = settings.embedding_provider.lower()
    if provider == "stub":
        return StubEmbedder(dim=settings.embedding_dim)
    if provider == "voyage":
        return VoyageEmbedder(
            api_key=settings.voyage_api_key,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    raise ValueError(f"Unknown embedding provider: {provider}")


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = make_embedder()
    return _embedder
