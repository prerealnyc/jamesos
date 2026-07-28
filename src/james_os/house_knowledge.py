"""House knowledge — the shared, cross-brand marketing canon (the Brand
Intelligence Corpus in ./knowledge_corpus).

Stored ONCE under a dedicated house tenant and retrieved for EVERY brand's
generation + strategy, so all brands stand on the same proven playbook without
copying it per-tenant or leaking anything brand-private (the corpus is generic
marketing science — hooks, frameworks, benchmarks, platform specs).

RLS-safe by construction: the corpus lives in its own tenant and every read
opens a connection scoped to that tenant (retrieval.search(tenant_id=HOUSE...)),
completely separate from the brand's own memory. Nothing here is brand data, so
sharing it is the intended design, not a D10 exception.

Lifecycle: `ensure_ingested()` (idempotent, version-gated) creates the house
tenant and loads the corpus once; the app calls it as a non-blocking startup
task. `grounding_block()` is what the generators/strategist call at request time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from .models import EventCreate, EventSource, RetrievedEvent

logger = logging.getLogger("james_os.house_knowledge")

# A dedicated tenant that holds the shared corpus. Not a real brand — no user,
# no publishing; only reference chunks. Fixed id so ingest + read agree.
HOUSE_TENANT_ID = UUID("00000000-0000-0000-0000-000000000ca1")  # 'ca1' ~ canon

_CORPUS_DIR = Path(__file__).parent / "knowledge_corpus"
# Bump when the corpus content changes to force a re-ingest (old rows are left
# in place but superseded by the new version marker on read/skip logic).
CORPUS_VERSION = "2026-07"

# Which layer each directory represents — surfaced in the block so the model
# knows whether a chunk is a hard rule, a fillable template, or a benchmark.
_LAYER = {
    "intelligence": "rule",       # WHY — marketing science, trust, persuasion
    "templates": "template",      # HOW — hooks, formulas, post/carousel skeletons
    "playbooks": "playbook",      # WHAT — growth stages, loops, cases, benchmarks
    "platform-specs": "spec",     # WHERE — per-platform formats/specs/algorithms
}


# ── chunking ──────────────────────────────────────────────────────────────
def _iter_chunks(text: str, title: str, max_chars: int = 1700):
    """Split a markdown file into (breadcrumb, body) chunks along ## / ###
    headings, further splitting an over-long section on blank lines. The
    breadcrumb (file title › section) is prepended to each chunk's embedded
    text so a retrieved fragment still carries its context."""
    h2 = ""
    section = ""
    buf: list[str] = []
    out: list[tuple[str, str]] = []

    def flush(sec: str):
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        crumb = " › ".join([c for c in (title, sec) if c])
        cur = ""
        for para in body.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            if cur and len(cur) + len(para) + 2 > max_chars:
                out.append((crumb, cur))
                cur = para
            else:
                cur = f"{cur}\n\n{para}".strip()
        if cur:
            out.append((crumb, cur))

    for ln in text.splitlines():
        if ln.startswith("## "):
            flush(section)
            h2 = ln[3:].strip()
            section = h2
        elif ln.startswith("### "):
            flush(section)
            h3 = ln[4:].strip()
            section = f"{h2} › {h3}" if h2 else h3
        elif ln.startswith("# "):
            flush(section)
            section = ""
        else:
            buf.append(ln)
    flush(section)
    return out


def _events_from_corpus() -> list[EventCreate]:
    """Every corpus file → a list of EventCreate chunks (no DB, no embed)."""
    events: list[EventCreate] = []
    if not _CORPUS_DIR.exists():
        logger.warning("house knowledge corpus dir missing: %s", _CORPUS_DIR)
        return events
    for path in sorted(_CORPUS_DIR.rglob("*.md")):
        rel = path.relative_to(_CORPUS_DIR).as_posix()
        if rel.lower() == "readme.md":
            continue                                  # index, not content
        layer = _LAYER.get(rel.split("/", 1)[0], "reference")
        text = path.read_text(encoding="utf-8", errors="ignore")
        # File title = its first H1, else a cleaned filename.
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")),
                     path.stem.replace("-", " "))
        for i, (crumb, body) in enumerate(_iter_chunks(text, title)):
            events.append(EventCreate(
                event_type="document",
                payload={"category": "canon", "layer": layer, "title": crumb, "file": rel},
                raw_content=f"[{crumb}]\n{body}",
                source=EventSource(
                    adapter="house_knowledge",
                    uri=f"corpus://{rel}",
                    dedupe_key=f"house:{CORPUS_VERSION}:{rel}:{i}",
                ),
                metadata={"category": "canon", "layer": layer,
                          "file": rel, "section": crumb,
                          "corpus_version": CORPUS_VERSION},
                confidence=1.0,
            ))
    return events


# ── house tenant + ingest ───────────────────────────────────────────────────
async def _ensure_house_tenant() -> None:
    from .db import acquire
    async with acquire(HOUSE_TENANT_ID) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, config) "
            "VALUES ($1, $2, $3, '{}'::jsonb) ON CONFLICT (id) DO NOTHING",
            HOUSE_TENANT_ID, "House Knowledge", "house-knowledge",
        )


async def _already_ingested() -> bool:
    from .db import acquire
    async with acquire(HOUSE_TENANT_ID) as conn:
        n = await conn.fetchval(
            "SELECT count(*) FROM events WHERE event_type = 'document' "
            "AND metadata->>'corpus_version' = $1 AND superseded_by IS NULL",
            CORPUS_VERSION,
        )
    return bool(n and n > 0)


async def ingest_corpus(force: bool = False) -> dict:
    """Chunk + embed + store the corpus under the house tenant. Idempotent:
    dedupe_key stops duplicate rows and the version check short-circuits a boot
    once loaded. Returns {ingested, chunks, skipped}."""
    from .ingestion import ingest_many
    await _ensure_house_tenant()
    if not force and await _already_ingested():
        return {"ingested": 0, "skipped": True, "version": CORPUS_VERSION}
    events = _events_from_corpus()
    if not events:
        return {"ingested": 0, "skipped": False, "chunks": 0}
    stored = await ingest_many(events, tenant_id=HOUSE_TENANT_ID)
    logger.info("house knowledge ingested: %d chunks (v%s)", len(stored), CORPUS_VERSION)
    return {"ingested": len(stored), "skipped": False, "chunks": len(events),
            "version": CORPUS_VERSION}


async def ensure_ingested() -> None:
    """Startup entrypoint — best-effort, never breaks boot."""
    try:
        res = await ingest_corpus()
        if res.get("ingested"):
            logger.info("house knowledge loaded: %s", res)
    except Exception as exc:  # noqa: BLE001 — house knowledge is additive
        logger.warning("house knowledge ingest failed (will retry next boot): %s", exc)


# ── retrieval ───────────────────────────────────────────────────────────────
async def search(query: str, k: int = 5, layers: tuple[str, ...] | None = None
                 ) -> list[RetrievedEvent]:
    """Retrieve the most relevant canon chunks for `query`, scoped to the house
    tenant only. `layers` optionally filters to e.g. ('template','rule')."""
    from .retrieval import search as _search
    try:
        hits = await _search(query, tenant_id=HOUSE_TENANT_ID,
                             event_types=["document"], top_k_per_index=max(k, 6))
    except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
        logger.warning("house knowledge search failed: %s", exc)
        return []
    # Only canon — never a brand's own 'document' rows, even if an RLS gap ever
    # let them into this tenant-scoped read. The boundary is explicit, not
    # solely RLS-enforced.
    hits = [h for h in hits if (h.payload or {}).get("category") == "canon"]
    if layers:
        hits = [h for h in hits if ((h.payload or {}).get("layer") in layers)]
    return hits[:k]


async def grounding_block(query: str, k: int = 4, layers: tuple[str, ...] | None = None,
                          chunk_cap: int = 700) -> str:
    """Render the top canon chunks as a <playbook> block for a prompt, or "".
    Each chunk is labelled with its source file/section so the model (and any
    later audit) can see where a rule came from."""
    hits = await search(query, k=k, layers=layers)
    if not hits:
        return ""
    lines = ["<playbook>  <!-- proven cross-brand marketing craft (hooks, "
             "structure, CTA, benchmarks). APPLY it, but the brand's VOICE and "
             "<rules> always win. -->"]
    for h in hits:
        src = (h.payload or {}).get("title") or (h.payload or {}).get("file") or "canon"
        body = (h.raw_content or "").strip()
        # raw_content already begins with "[breadcrumb]\n" — drop it, we label src.
        if body.startswith("["):
            body = body.split("\n", 1)[-1].strip()
        lines.append(f'<p src="{src}">{body[:chunk_cap]}</p>')
    lines.append("</playbook>")
    return "\n".join(lines)


async def status() -> dict:
    """Counts for verification — how much canon is loaded, by layer."""
    from .db import acquire
    await _ensure_house_tenant()
    async with acquire(HOUSE_TENANT_ID) as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM events WHERE event_type='document' "
            "AND metadata->>'category'='canon' AND superseded_by IS NULL")
        by_layer = await conn.fetch(
            "SELECT metadata->>'layer' AS layer, count(*) AS n FROM events "
            "WHERE event_type='document' AND metadata->>'category'='canon' "
            "AND superseded_by IS NULL GROUP BY 1 ORDER BY 2 DESC")
    return {"version": CORPUS_VERSION, "chunks": int(total or 0),
            "by_layer": {r["layer"]: int(r["n"]) for r in by_layer}}


__all__ = ["ensure_ingested", "ingest_corpus", "search", "grounding_block",
           "status", "HOUSE_TENANT_ID", "CORPUS_VERSION"]
