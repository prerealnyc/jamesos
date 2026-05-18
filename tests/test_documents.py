import pytest

from james_os.documents import chunk_text, document_to_events, document_to_events_async
from james_os.transcription import is_audio


def test_is_audio_detection():
    assert is_audio("podcast_ep47.mp3")
    assert is_audio("Interview.M4A")
    assert is_audio("clip.mp4")
    assert not is_audio("thesis.md")
    assert not is_audio("deck.pdf")


def test_chunk_text_overlaps_and_splits():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 80 for i in range(6))
    chunks = chunk_text(text, target=400, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) > 0 for c in chunks)


def test_sync_path_handles_text_and_rejects_audio():
    evs = document_to_events("note.md", b"# Title\n\nbody text here", "document")
    assert evs and evs[0].event_type == "document"
    with pytest.raises(ValueError, match="audio"):
        document_to_events("ep.mp3", b"\x00\x01", "document")


@pytest.mark.asyncio
async def test_async_path_text_still_works():
    evs = await document_to_events_async("plan.md", b"para one\n\npara two", "document")
    assert len(evs) >= 1
    assert evs[0].source.adapter == "document_upload"


@pytest.mark.asyncio
async def test_async_audio_requires_openai_key(monkeypatch):
    # With no key, the Whisper path must fail loudly, not silently no-op.
    from james_os import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "")
    with pytest.raises(transcription.TranscriptionError, match="OPENAI_API_KEY"):
        await document_to_events_async("ep47.mp3", b"fake-audio-bytes", "document")
