"""Document ingestion — extract text, chunk it, turn it into events.

Extraction covers the FULL company-document spectrum (ported feature-for-
feature from the PreReal Intelligence platform's extract/transcribe/ocr
engine): PDF, Word, PowerPoint (slides in order + speaker notes), Excel
(per-sheet CSV text), HTML/XML, RTF, plain text, audio/video via Whisper
(25 MB cap, graceful skip), and images via vision OCR (verbatim text +
factual description, 20 MB cap, graceful skip). Non-extractable types are
STORED but skipped for text — the original is never lost.

Chunking is a production-RAG concern: too big and retrieval dilutes, too
small and context is lost. We split on paragraph boundaries then pack into
~800-char windows with ~120-char overlap so a chunk rarely cuts a thought
in half and adjacent context isn't lost at the seam.
"""

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from . import spend
from .models import EventCreate, EventSource

CHUNK_TARGET = 800
CHUNK_OVERLAP = 120


@dataclass
class ExtractResult:
    """Extraction outcome. `skipped=True` + `reason` means 'original kept,
    no searchable text' — never an exception, so one bad file can't kill a
    batch (the PreReal ingest contract)."""
    text: str
    skipped: bool = False
    reason: str | None = None


# ── format helpers (ported from PreReal lib/extract.ts) ──

def _strip_html(html: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;", " ", s, flags=re.I)
    s = re.sub(r"&amp;", "&", s, flags=re.I)
    s = re.sub(r"&lt;", "<", s, flags=re.I)
    s = re.sub(r"&gt;", ">", s, flags=re.I)
    s = re.sub(r"&quot;", '"', s, flags=re.I)
    s = re.sub(r"&#39;", "'", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def _decode_xml_entities(s: str) -> str:
    """Decode the XML entities that appear in Office XML text runs."""
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&apos;", "'"))
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)


def _extract_pptx(data: bytes) -> str:
    """PowerPoint (.pptx): text from every slide, in order, plus speaker
    notes. pptx is a ZIP of XML; text lives in <a:t> runs."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()

    def runs_from(xml: str) -> str:
        runs = re.findall(r"<a:t>([\s\S]*?)</a:t>", xml)
        return re.sub(r"\s+", " ", " ".join(_decode_xml_entities(r) for r in runs)).strip()

    def slide_no(name: str, pat: str) -> int:
        m = re.search(pat, name)
        return int(m.group(1)) if m else 0

    slides = sorted(
        (n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: slide_no(n, r"slide(\d+)\.xml$"),
    )
    notes = sorted(
        (n for n in names if re.match(r"^ppt/notesSlides/notesSlide\d+\.xml$", n)),
        key=lambda n: slide_no(n, r"notesSlide(\d+)\.xml$"),
    )
    parts: list[str] = []
    for i, n in enumerate(slides):
        t = runs_from(zf.read(n).decode("utf-8", errors="replace"))
        if t:
            parts.append(f"[Slide {i + 1}]\n{t}")
    note_text = "\n".join(
        t for t in (runs_from(zf.read(n).decode("utf-8", errors="replace")) for n in notes) if t
    )
    if note_text:
        parts.append(f"[Speaker notes]\n{note_text}")
    return "\n\n".join(parts)


def _extract_xlsx(data: bytes, legacy_xls: bool = False) -> str:
    """Excel: each sheet flattened to CSV-style text, labelled [Sheet: name]."""
    parts: list[str] = []
    if legacy_xls:
        import xlrd

        wb = xlrd.open_workbook(file_contents=data)
        for ws in wb.sheets():
            rows = []
            for r in range(ws.nrows):
                cells = [str(ws.cell_value(r, c)).strip() for c in range(ws.ncols)]
                if any(cells):
                    rows.append(",".join(cells))
            if rows:
                parts.append(f"[Sheet: {ws.name}]\n" + "\n".join(rows))
        return "\n\n".join(parts)

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                rows.append(",".join(cells))
        if rows:
            parts.append(f"[Sheet: {ws.title}]\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _strip_rtf(rtf: str) -> str:
    """RTF: strip control words/groups, keep the readable text."""
    s = re.sub(r"\\par[d]?", "\n", rtf)
    s = re.sub(r"\{\\[^{}]*\}", "", s)          # font/color/style groups
    s = re.sub(r"\\'[0-9a-fA-F]{2}", "", s)     # hex-escaped chars
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)    # control words
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# ── audio / image routing predicates (ported from transcribe.ts / ocr.ts) ──

_TRANSCRIBE_EXTS = {"mp3", "m4a", "wav", "flac", "ogg", "opus", "webm", "mp4", "mpeg", "mpga"}
_OCR_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_OCR_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
_AUDIO_MAX_BYTES = 25 * 1024 * 1024   # Whisper per-upload cap
_OCR_MAX_BYTES = 20 * 1024 * 1024     # vision per-image cap

_OCR_PROMPT = """You are an OCR and image-understanding tool for a document library.
1. Transcribe ALL text visible in the image, verbatim, preserving structure (headings, labels, table rows, numbers, dates).
2. Then, if the image is a chart, diagram, photo, screenshot, or scanned page, add a short factual description of what it depicts and any data shown.
Do NOT invent text or details that aren't present. If there is no text at all, just give the description. Output plain text."""


def _ext_of(filename: str) -> str:
    return (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()


def is_transcribable(mime: str, filename: str) -> bool:
    mt = (mime or "").lower()
    if mt.startswith("audio/") or mt.startswith("video/"):
        return True
    return _ext_of(filename) in _TRANSCRIBE_EXTS


def is_ocrable(mime: str, filename: str) -> bool:
    return _ext_of(filename) in _OCR_EXTS or (mime or "").lower() in _OCR_MIMES


async def describe_image(data: bytes, filename: str, mime: str) -> ExtractResult:
    """Image → vision OCR: transcribe visible text verbatim + describe what the
    image depicts, so the data is captured, not lost. The original image is
    preserved upstream; this only produces the searchable derived text.
    Graceful skip (never crash the pipeline) without a key / over the cap."""
    import base64

    from .config import settings

    if not (settings.openai_api_key or "").strip():
        return ExtractResult("", True, "OPENAI_API_KEY not set — image OCR disabled")
    if len(data) > _OCR_MAX_BYTES:
        return ExtractResult(
            "", True,
            f"image too large ({len(data) / 1024 / 1024:.1f} MB; OCR cap is 20 MB)")

    ext = _ext_of(filename)
    m = (mime or "").lower()
    if m not in _OCR_MIMES:
        m = ("image/jpeg" if ext in ("jpg", "jpeg") else
             "image/gif" if ext == "gif" else
             "image/webp" if ext == "webp" else "image/png")
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        data_url = f"data:{m};base64,{base64.b64encode(data).decode()}"
        resp = await client.chat.completions.create(
            model=getattr(settings, "ocr_model", "") or "gpt-4o-mini",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
        await spend.record_tokens("openai", getattr(resp, "model", "") or "gpt-4o-mini", getattr(resp, "usage", None), "documents.ocr_image")
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return ExtractResult(text)
    except Exception as e:  # noqa: BLE001 — OCR failure must not kill an ingest
        return ExtractResult("", True, str(e) or "image OCR failed")


async def _transcribe_capped(data: bytes, filename: str) -> ExtractResult:
    """Audio/video → Whisper with the 25 MB cap + graceful skips (the PreReal
    contract: a missing key or oversize file skips, it never crashes)."""
    from .config import settings

    if not (settings.openai_api_key or "").strip():
        return ExtractResult("", True, "OPENAI_API_KEY not set — transcription disabled")
    if len(data) > _AUDIO_MAX_BYTES:
        return ExtractResult(
            "", True,
            f"audio file too large ({len(data) / 1024 / 1024:.1f} MB; Whisper cap is 25 MB)")
    try:
        from .transcription import transcribe

        text = await transcribe(filename, data)
        return ExtractResult(text or "")
    except Exception as e:  # noqa: BLE001
        return ExtractResult("", True, str(e) or "transcription failed")


# ── the extractors ──

def extract_text_ex(filename: str, data: bytes, mime: str = "") -> ExtractResult:
    """Full-coverage SYNC extraction (text formats). Audio/images need the
    async `extract_any` (network calls). Unsupported → skipped + reason."""
    mt = (mime or "").lower()
    ext = _ext_of(filename)
    try:
        if "pdf" in mt or ext == "pdf":
            return ExtractResult(_extract_pdf(data))
        if ("wordprocessingml" in mt or "msword" in mt or ext in ("docx", "doc")):
            return ExtractResult(_extract_docx(data))
        if mt.startswith("text/") or ext in ("txt", "md", "markdown", "csv", "json",
                                             "html", "htm", "xml"):
            raw = data.decode("utf-8", errors="replace")
            if "html" in mt or ext in ("html", "htm", "xml"):
                return ExtractResult(_strip_html(raw))
            return ExtractResult(raw)
        if "presentationml" in mt or ext == "pptx":
            return ExtractResult(_extract_pptx(data))
        if "spreadsheetml" in mt or ext == "xlsx":
            return ExtractResult(_extract_xlsx(data))
        if "ms-excel" in mt or ext == "xls":
            return ExtractResult(_extract_xlsx(data, legacy_xls=True))
        if "rtf" in mt or ext == "rtf":
            return ExtractResult(_strip_rtf(data.decode("utf-8", errors="replace")))
        return ExtractResult("", True, f"unsupported type ({mt or ext or 'unknown'})")
    except Exception as e:  # noqa: BLE001 — one bad file can't kill a batch
        return ExtractResult("", True, str(e) or "extract failed")


async def extract_any(filename: str, data: bytes, mime: str = "") -> ExtractResult:
    """Universal ASYNC extraction: routes audio/video → Whisper and images →
    vision OCR before the text extractors — the transcript/OCR text flows
    through the same search/RAG path as any other document."""
    ext = _ext_of(filename)
    if is_transcribable(mime, filename):
        return await _transcribe_capped(data, filename or f"audio.{ext or 'mp3'}")
    if is_ocrable(mime, filename):
        return await describe_image(data, filename or f"image.{ext or 'png'}", mime)
    return extract_text_ex(filename, data, mime)


def extract_text(filename: str, data: bytes) -> str:
    """Backward-compatible extractor (all existing callers keep working, and
    gain PPTX/XLSX/HTML/RTF coverage). Unknown types keep the historical
    utf-8 best-effort decode."""
    r = extract_text_ex(filename, data)
    if r.skipped and (r.reason or "").startswith("unsupported"):
        return data.decode("utf-8", errors="replace")
    return r.text


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
    "company_doc": "Company documents (knowledge base — facts to ground content in)",
    "research": "Research / white papers (citable facts the content engine grounds on)",
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
    """Async path. Audio/video → Whisper transcript → events (event_type
    'voice_memo'); images → vision OCR; everything else → text extraction
    (full format coverage incl. PPTX/XLSX/HTML/RTF)."""
    from . import transcription
    from .transcription import is_audio

    if is_audio(filename):
        r = await _transcribe_capped(data, filename)
        if r.skipped:
            # TranscriptionError, not a bare ValueError: transcription.py
            # defines it so a caller can tell a transcription failure from any
            # other bad input, and tests/test_documents.py has asserted that
            # contract since this path was written (ab590ec). The only caller,
            # main.py's upload route, catches Exception and returns 422, so the
            # type is invisible to every consumer.
            raise transcription.TranscriptionError(r.reason or "transcription failed")
        return _build_events(filename, data, r.text, "voice_memo", category or "voice_corpus")
    if is_ocrable("", filename):
        r = await describe_image(data, filename, "")
        if r.skipped:
            raise ValueError(r.reason or "image OCR failed")
        return _build_events(filename, data, r.text, event_type, category)
    return _build_events(filename, data, extract_text(filename, data), event_type, category)
