"""Document ingestion — extract text, chunk it, turn it into events.

Chunking is a production-RAG concern: too big and retrieval dilutes, too
small and context is lost. We split on paragraph boundaries then pack into
~800-char windows with ~120-char overlap so a chunk rarely cuts a thought
in half and adjacent context isn't lost at the seam.
"""

import hashlib
import io
from datetime import UTC, datetime

from .models import EventCreate, EventSource

CHUNK_TARGET = 800
CHUNK_OVERLAP = 120


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith((".txt", ".md", ".markdown", ".csv", ".json")):
        return data.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        return _extract_pdf(data)
    if name.endswith((".docx",)):
        return _extract_docx(data)
    # Best effort: treat unknown as utf-8 text.
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def chunk_text(text: str, target: int = CHUNK_TARGET, overlap: int = CHUNK_OVERLAP) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= target:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            if buf:
                chunks.append(buf)
            if len(para) <= target:
                buf = para
            else:
                # paragraph itself bigger than target — hard-split it
                for i in range(0, len(para), target - overlap):
                    chunks.append(para[i : i + target])
                buf = ""
    if buf:
        chunks.append(buf)

    # add overlap by prefixing the tail of the previous chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:], strict=False):
            tail = prev[-overlap:]
            overlapped.append(f"{tail} {cur}")
        chunks = overlapped
    return chunks


# Upload categories — drive retrieval weighting + the future content
# engine. Stored on every chunk (payload.category + entities) so a query
# can prefer voice material, treat protocols as hard constraints, etc.
CATEGORIES = {
    "thesis": "Founder thesis / vision (point of view)",
    "guideline": "Brand guidelines / voice rules (how it must sound)",
    "frustration": "Frustration ledger (what NOT to do)",
    "voice_corpus": "Voice corpus (podcast / academy — how James sounds)",
    "reference": "Reference / strategy (context, not voice)",
}


def _build_events(
    filename: str, data: bytes, text: str, event_type: str, category: str
) -> list[EventCreate]:
    chunks = chunk_text(text)
    file_hash = hashlib.sha256(data).hexdigest()[:16]
    now = datetime.now(UTC)
    events: list[EventCreate] = []
    for idx, chunk in enumerate(chunks):
        events.append(
            EventCreate(
                event_type=event_type,
                payload={"text": chunk, "filename": filename, "chunk": idx,
                         "total_chunks": len(chunks), "category": category},
                raw_content=chunk,
                source=EventSource(
                    adapter="document_upload",
                    uri=f"file://{filename}",
                    dedupe_key=f"{file_hash}-{idx}",
                    raw_metadata={"filename": filename, "chunk_index": idx,
                                  "category": category},
                ),
                entities=[filename, f"category:{category}"],
                effective_at=now,
            )
        )
    return events


def document_to_events(
    filename: str, data: bytes, event_type: str = "document",
    category: str = "reference",
) -> list[EventCreate]:
    """Synchronous path — text documents only. Audio raises (use the async
    path); this keeps the sync test suite free of network dependencies."""
    from .transcription import is_audio

    if is_audio(filename):
        raise ValueError(
            f"{filename} is audio — use document_to_events_async (Whisper path)"
        )
    return _build_events(filename, data, extract_text(filename, data), event_type, category)


async def document_to_events_async(
    filename: str, data: bytes, event_type: str = "document",
    category: str = "reference",
) -> list[EventCreate]:
    """Async path. Audio → Whisper transcript → events (event_type
    'voice_memo'); everything else → normal text extraction."""
    from .transcription import is_audio, transcribe

    if is_audio(filename):
        text = await transcribe(filename, data)
        return _build_events(filename, data, text, "voice_memo", category or "voice_corpus")
    return _build_events(filename, data, extract_text(filename, data), event_type, category)
