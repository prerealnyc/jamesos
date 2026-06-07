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
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import settings


class LLMParseError(ValueError):
    """LLM produced output we couldn't parse as JSON (e.g. truncated).

    Distinct so callers can refuse gracefully instead of 500'ing, and so
    tenacity doesn't waste retries on a parse failure that won't fix
    itself by trying the exact same prompt again.
    """


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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        # Retry transient network/provider errors, NEVER a parse failure:
        # retrying the same prompt that produced unparseable output just
        # burns tokens and still 500s. The caller handles LLMParseError.
        retry=retry_if_not_exception_type(LLMParseError),
        reraise=True,
    )
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
        truncated = getattr(result, "stop_reason", None) == "max_tokens"
        return _extract_json(text, truncated=truncated)


class OpenAILLM(LLM):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAILLM")
        self.client = AsyncOpenAI(api_key=api_key)
        self.model_name = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_not_exception_type(LLMParseError),
        reraise=True,
    )
    async def complete_json(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        result = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        choice = result.choices[0]
        text = choice.message.content or ""
        truncated = choice.finish_reason == "length"
        return _extract_json(text, truncated=truncated)


def _is_balance_error(e: Exception) -> bool:
    """True when an LLM call failed because the account is out of credits /
    over quota (vs. a transient network error or a bad request)."""
    s = str(e).lower()
    return any(
        k in s
        for k in (
            "credit balance is too low",
            "insufficient_quota",
            "exceeded your current quota",
            "billing",
            "quota",
        )
    )


class FallbackLLM(LLM):
    """Primary provider with automatic fallback to a secondary ONLY when the
    primary fails with a billing/credit/quota error.

    Operator request: "use OpenAI when Claude says balance low." So if a
    Claude call returns 'credit balance is too low', we transparently retry
    the same call on OpenAI instead of failing. Parse errors and other
    failures are NOT swallowed — they propagate as before.
    """

    def __init__(self, primary: LLM, fallback: LLM):
        self.primary = primary
        self.fallback = fallback
        self.model_name = primary.model_name

    async def complete_json(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        try:
            self.model_name = self.primary.model_name
            return await self.primary.complete_json(
                system, messages, max_tokens, temperature
            )
        except LLMParseError:
            raise
        except Exception as e:  # noqa: BLE001
            if not _is_balance_error(e):
                raise
            # Primary is out of credits → switch to the fallback provider.
            self.model_name = self.fallback.model_name
            return await self.fallback.complete_json(
                system, messages, max_tokens, temperature
            )


def _extract_json(text: str, truncated: bool = False) -> dict[str, Any]:
    """Best-effort JSON extraction. Handles ```json fences and stray prose.

    Raises LLMParseError (a ValueError) on failure — distinct from generic
    transport errors so retries don't waste tokens on broken parses.
    """
    text = text.strip()
    if "```" in text:
        # pull the first fenced block — `removeprefix` is exact-substring,
        # unlike `lstrip("json")` which strips ANY combination of j/s/o/n.
        for part in text.split("```"):
            stripped = part.strip().removeprefix("json").strip()
            if stripped.startswith("{"):
                text = stripped
                break
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        why = "truncated by max_tokens" if truncated else "no JSON object found"
        raise LLMParseError(f"{why}: {text[:200]}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        why = "truncated by max_tokens" if truncated else "malformed JSON"
        raise LLMParseError(f"{why} ({e}): {text[:200]}") from e


def make_llm() -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "stub":
        return StubLLM()
    if provider == "anthropic":
        primary = AnthropicLLM(api_key=settings.anthropic_api_key, model=settings.llm_model)
        # Per operator request: when Claude reports a low/zero credit balance,
        # fall back to OpenAI (when a key is configured) so the app keeps
        # working instead of failing. settings.llm_model is a Claude name
        # here, so the fallback uses a sensible OpenAI default.
        okey = (settings.openai_api_key or "").strip()
        if okey:
            ofallback = OpenAILLM(api_key=okey, model="gpt-4o-mini")
            return FallbackLLM(primary, ofallback)
        return primary
    if provider == "openai":
        return OpenAILLM(api_key=settings.openai_api_key, model=settings.llm_model)
    raise ValueError(f"Unknown LLM provider: {provider}")


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = make_llm()
    return _llm
