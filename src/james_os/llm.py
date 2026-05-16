"""LLM provider abstraction.

The LLM is a contractor — it gets called for specific tasks under specific
constraints. Provider-agnostic interface so models swap freely.

Stub provider returns deterministic structured refusals so the pipeline
can be tested without API keys.
"""

import json
from abc import ABC, abstractmethod
from typing import Any

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


class LLM(ABC):
    model_name: str

    @abstractmethod
    async def complete_json(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Single-shot completion that must return valid JSON."""
        ...


class StubLLM(LLM):
    """Always returns a refusal. Useful for plumbing tests without API keys."""

    model_name = "stub"

    async def complete_json(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "answer": "I don't have that in memory.",
            "claims": [],
            "refused": True,
            "refusal_reason": "stub LLM is not connected to a real model",
        }


class AnthropicLLM(LLM):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for AnthropicLLM")
        self.client = AsyncAnthropic(api_key=api_key)
        self.model_name = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def complete_json(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        result = await self.client.messages.create(
            model=self.model_name,
            system=system,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(block.text for block in result.content if hasattr(block, "text"))
        return _extract_json(text)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction. Handles ```json fences and stray prose."""
    text = text.strip()
    if "```" in text:
        # pull the first fenced block
        parts = text.split("```")
        for part in parts:
            stripped = part.lstrip("json").strip()
            if stripped.startswith("{"):
                text = stripped
                break
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM output: {text[:200]}")
    return json.loads(text[start : end + 1])


def make_llm() -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "stub":
        return StubLLM()
    if provider == "anthropic":
        return AnthropicLLM(api_key=settings.anthropic_api_key, model=settings.llm_model)
    raise ValueError(f"Unknown LLM provider: {provider}")


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = make_llm()
    return _llm
