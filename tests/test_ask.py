import pytest

from james_os import llm as llm_module
from james_os.ask import ask
from james_os.ingestion import ingest_many
from james_os.llm import LLM, LLMParseError, _extract_json
from james_os.models import AskRequest, EventCreate, EventSource


def _spec(text: str, dedupe: str) -> EventCreate:
    return EventCreate(
        event_type="note",
        payload={"text": text},
        raw_content=text,
        source=EventSource(adapter="manual", dedupe_key=dedupe),
    )


@pytest.mark.asyncio
async def test_ask_refuses_when_memory_is_empty():
    resp = await ask(AskRequest(question="What is X?"))
    assert resp.refused is True
    assert resp.refusal_reason == "no_relevant_events_found"
    assert resp.citations == []


@pytest.mark.asyncio
async def test_ask_refuses_under_stub_llm_even_with_events():
    """Stub LLM always refuses — proves cite-or-refuse default behavior."""
    await ingest_many([_spec("The pilot price is $500K", "p-1")])
    resp = await ask(AskRequest(question="What's the pilot price?"))
    assert resp.refused is True
    # retrieved_event_ids should still be populated even on refusal
    assert len(resp.retrieved_event_ids) >= 1


def test_extract_json_raises_LLMParseError_on_truncation():
    """Regression: truncated JSON (the bug behind the live HTTP 500) must
    surface as LLMParseError, not a bare JSONDecodeError tenacity will
    retry pointlessly."""
    truncated = '{"answer": "a long answer that got chopped before the cl'
    with pytest.raises(LLMParseError):
        _extract_json(truncated, truncated=True)
    # No JSON object at all — same error class.
    with pytest.raises(LLMParseError):
        _extract_json("the model produced prose with no braces")


@pytest.mark.asyncio
async def test_ask_returns_graceful_refusal_when_llm_output_unparseable():
    """Regression: a parse failure inside _generate must NOT 500. It must
    return a refused AskResponse the UI can render."""
    class _ParseFailLLM(LLM):
        model_name = "parse-fail-mock"

        async def complete_json(self, system, messages, max_tokens=1024, temperature=0.0):
            raise LLMParseError("truncated by max_tokens: ...")

    llm_module._llm = _ParseFailLLM()
    try:
        await ingest_many([_spec("The pilot price is $500K", "p-1")])
        resp = await ask(AskRequest(question="What's the pilot price?"))
        assert resp.refused is True
        assert resp.refusal_reason and "unparseable" in resp.refusal_reason
        assert resp.citations == []
    finally:
        llm_module._llm = None
