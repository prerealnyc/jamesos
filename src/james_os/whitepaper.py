"""White-paper generation — ported feature-for-feature from the PreReal
Intelligence platform's whitepaper engine, on BM's multi-tenant substrate.

The pipeline (same three acts as the original):
  1. GROUND — retrieve the tenant's own document corpus for the topic
     (hybrid vector+FTS, k=50) and rerank to the best 14 chunks; each
     becomes a numbered [n] source (1100-char excerpts).
  2. LEARN THE STRUCTURE — ask live web research how the best, most
     credible recent white papers on this subject are actually organized
     (section order, flow, tone, use of data); fall back to the classic
     8-section template when research isn't configured.
  3. WRITE — a senior-analyst system prompt + the structural guidance +
     the cited corpus → strict-JSON paper {title, subtitle, abstract,
     sections[], key_takeaways[]}, every substantive claim cited [n].

The finished paper is persisted as Markdown into the Knowledge Base
(category='research'), which chunks + embeds it into the tenant's memory —
so Ask cites it and the content engine grounds posts/reels/podcasts on it.
That's the thesis → white paper → content pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from .config import settings
from .llm import get_llm
from .rerank import rerank
from .retrieval import search

VECTOR_K = 50           # candidates pulled per index before rerank
CORPUS_KEEP = 14        # chunks kept after rerank
CHUNK_EXCERPT_MAX = 1100

_WRITER_SYSTEM = """You are a senior analyst writing a professional, publication-grade WHITE PAPER for {brand}.

Follow the STRUCTURAL GUIDANCE — it describes how the best, most credible white papers in this domain are actually built. Match that professional structure, narrative flow, depth, and tone. A white paper opens with framing/abstract, builds an evidence-based argument across well-developed sections, and closes with implications and a call to action.

Ground every substantive claim in the CORPUS (the brand's own documents + gathered research) provided below. Cite corpus documents inline as [n]. Do not invent facts; where the corpus is thin, write at an appropriate level of generality and state assumptions plainly.

Write substantial, fully-developed sections with real analysis — this is a white paper, not a summary. Use Markdown inside section bodies (subheadings with ###, bold, bullet lists) where it aids readability.

Output STRICT JSON, no prose outside it:
{{
  "title": "compelling white-paper title",
  "subtitle": "one-line subtitle",
  "abstract": "one-paragraph executive abstract",
  "sections": [ {{ "heading": "Section heading", "body": "multi-paragraph markdown with [n] citations" }} ],
  "key_takeaways": ["concise takeaway", "..."]
}}"""

_FALLBACK_STRUCTURE = "\n".join([
    "Use a classic white-paper structure:",
    "1. Executive Summary / Abstract — the problem and the headline conclusion.",
    "2. Introduction & Context — why this matters now.",
    "3. Background — the landscape, definitions, current state.",
    "4. Analysis — the core evidence-based argument, broken into themed sub-sections with data.",
    "5. Implications & Opportunities — what it means for the reader.",
    "6. Risks & Considerations — honest treatment of challenges.",
    "7. Recommendations / Path Forward — concrete, actionable next steps.",
    "8. Conclusion — restate the thesis and the call to action.",
    "Tone: authoritative, analytical, professional. Use data and citations throughout.",
])


def _structure_query(topic: str) -> str:
    return (
        f'Analyze the STRUCTURE of the best, most credible white papers '
        f'published in the last 1-2 years on the subject of "{topic}" (or its '
        f'closest professional field). Describe, section by section, how a '
        f'top-tier white paper in this space is organized: the standard '
        f'sections and their order, the typical narrative flow, length and '
        f'depth, the tone, and how they use data, figures, and citations to '
        f'build a persuasive evidence-based argument. Name the strongest '
        f'exemplar white papers you can find. Be concrete and prescriptive '
        f'so it can be used as a template.'
    )


async def _brand_label(tenant_id: UUID | None) -> str:
    """Who the paper is 'for' — the tenant's brand, with its industry."""
    try:
        from .brand_kit import get_brand_kit

        bk = await get_brand_kit()
        name = (bk.get("display_name") or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    industry = (settings.brand_industry or "").strip()
    if name and industry:
        return f"{name} ({industry})"
    return name or industry or "the brand"


async def _learn_structure(topic: str) -> tuple[str, list[dict]]:
    """Act 2: live-web structure learning (best-effort; exact PreReal query).
    Returns (structure_text, exemplars[{url,title}]); fallback template on
    any failure or when no real research provider is configured."""
    try:
        from .research import get_research_provider

        provider = get_research_provider()
        if getattr(provider, "name", "stub") == "stub":
            return _FALLBACK_STRUCTURE, []
        res = await provider.research(_structure_query(topic))
        text = (res.summary or "").strip()
        if res.findings:
            text += "\n" + "\n".join(f"- {f}" for f in res.findings)
        exemplars = [
            {"url": s.url, "title": s.title or None}
            for s in (res.sources or []) if getattr(s, "url", "")
        ]
        return (text.strip() or _FALLBACK_STRUCTURE), exemplars
    except Exception:  # noqa: BLE001 — non-fatal, use the built-in structure
        return _FALLBACK_STRUCTURE, []


async def _guidelines_block(tenant_id: UUID | None) -> str:
    """The brand's uploaded guideline docs, injected as hard rules (parity
    with PreReal's guidelinesBlock)."""
    try:
        from .content import _brand_guidelines

        guides = await _brand_guidelines(tenant_id)
        texts = [
            (g.raw_content or "").strip()
            for g in guides if (g.raw_content or "").strip()
        ][:6]
        if not texts:
            return ""
        joined = "\n- ".join(t[:400] for t in texts)
        return f"\n\nBRAND GUIDELINES (hard rules — follow them):\n- {joined}"
    except Exception:  # noqa: BLE001
        return ""


async def generate_whitepaper(
    *,
    topic: str,
    audience: str = "",
    goal: str = "",
    tenant_id: UUID | None = None,
    thesis_doc_id: UUID | None = None,
) -> dict:
    """Generate a grounded, cited white paper from the tenant's Knowledge
    Base. Returns the full paper + sources + provenance, and persists it as
    a Markdown Knowledge-Base document (category='research') so memory, Ask,
    and the content engine can immediately use it.

    When `thesis_doc_id` is set, that document (the author's weekly thesis)
    becomes the paper's PRIMARY SOURCE: the paper argues the thesis's point
    of view, grounding supporting facts in the corpus."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required")
    audience = (audience or "").strip() or (
        "executives, partners, and prospective stakeholders")
    goal = (goal or "").strip() or "inform strategy and support decision-making"

    thesis_name, thesis_text = "", ""
    if thesis_doc_id:
        from .db import acquire
        async with acquire(tenant_id) as conn:
            trow = await conn.fetchrow(
                "SELECT filename, extracted_text, sensitivity "
                "FROM document_metadata WHERE id=$1", thesis_doc_id)
        # NDA-Protected text must never be reproduced into a persisted
        # research doc (same rule drop_nda_protected enforces on the corpus).
        if (trow and (trow["extracted_text"] or "").strip()
                and (trow["sensitivity"] or "") != "NDA-Protected"):
            thesis_name = trow["filename"]
            thesis_text = trow["extracted_text"].strip()[:9000]

    # ── 1. Retrieve grounding corpus from the tenant's memory ──
    hits = await search(topic, tenant_id=tenant_id, top_k_per_index=VECTOR_K)
    # NDA-Protected docs must never feed a persisted artifact (the paper is
    # saved back as ordinary research — reproducing NDA data would launder it).
    from .sensitivity import drop_nda_protected
    hits, _nda_dropped = await drop_nda_protected(hits, tenant_id)
    kept = await rerank(topic, hits, top_k=CORPUS_KEEP)
    corpus: list[dict] = []
    for ev in kept:
        payload = ev.payload or {}
        filename = (payload.get("filename")
                    or payload.get("title")
                    or ev.event_type or "memory")
        text = (ev.raw_content or payload.get("text") or "").strip()
        if not text:
            continue
        corpus.append({
            "n": len(corpus) + 1,
            "filename": str(filename)[:160],
            "text": text[:CHUNK_EXCERPT_MAX],
        })
    low_grounding = len(corpus) < 3

    # ── 2. Learn the structure from the best recent white papers ──
    structure_text, exemplars = await _learn_structure(topic)

    # ── 3. Write the paper, grounded in the corpus ──
    corpus_block = (
        "\n\n".join(
            f'<doc index="{d["n"]}" filename="{d["filename"]}">\n{d["text"]}\n</doc>'
            for d in corpus
        )
        if corpus else
        "(No internal corpus found for this topic. Write at an appropriate "
        "level of generality, and clearly mark assumptions. Recommend "
        "uploading relevant company documents to the Knowledge Base to "
        "build grounding.)"
    )
    brand = await _brand_label(tenant_id)
    guidelines = await _guidelines_block(tenant_id)
    thesis_block = (
        f'\n\nTHE AUTHOR\'S THESIS (cite as [T] — this paper ARGUES this '
        f'point of view; where the thesis asserts an opinion, present it as '
        f'the paper\'s position; ground every supporting FACT in the corpus '
        f'[n]):\n<thesis filename="{thesis_name}">\n{thesis_text}\n</thesis>'
        if thesis_text else ""
    )
    system = (
        _WRITER_SYSTEM.format(brand=brand)
        + guidelines
        + "\n\nSTRUCTURAL GUIDANCE (how the best white papers in this space are built):\n"
        + structure_text
        + thesis_block
        + "\n\nCORPUS (cite as [n]):\n" + corpus_block
    )
    user = (f"TOPIC: {topic}\nAUDIENCE: {audience}\nGOAL: {goal}\n\n"
            f"Write the full white paper as JSON now.")

    out = await get_llm().complete_json(
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=8000,
        temperature=0.4,
    )
    if not isinstance(out, dict):
        raise RuntimeError("The model did not return a valid white paper. Try again.")

    title = str(out.get("title") or f"White Paper: {topic}")[:240]
    subtitle = str(out.get("subtitle") or "")[:300]
    abstract = str(out.get("abstract") or "")[:4000]
    sections = [
        {"heading": str((s or {}).get("heading") or "")[:200],
         "body": str((s or {}).get("body") or "")[:12000]}
        for s in (out.get("sections") or []) if isinstance(s, dict)
    ]
    sections = [s for s in sections if s["heading"] or s["body"]]
    takeaways = [str(t)[:400] for t in (out.get("key_takeaways") or []) if str(t).strip()]

    # ── 4. Persist as a Knowledge-Base document (markdown → memory) ──
    date = datetime.now(UTC).date().isoformat()
    md_parts = [
        f"# {title}",
        f"\n*{subtitle}*" if subtitle else "",
        f"\n> White paper generated {date} by the brand intelligence engine.",
        f"\n## Abstract\n{abstract}" if abstract else "",
        *[f"\n## {s['heading']}\n{s['body']}" for s in sections],
        ("\n## Key Takeaways\n" + "\n".join(f"- {t}" for t in takeaways))
        if takeaways else "",
        ("\n## Sources\n"
         + (f"T. {thesis_name} (author's thesis)\n" if thesis_text else "")
         + "\n".join(
            f"{d['n']}. {d['filename']}" for d in corpus))
        if (corpus or thesis_text) else "",
        ("\n## Structural references\n" + "\n".join(
            f"{i + 1}. [{(c.get('title') or c['url'])}]({c['url']})"
            for i, c in enumerate(exemplars))) if exemplars else "",
        "",
    ]
    md = "\n".join(p for p in md_parts if p)

    file_info: dict | None = None
    try:
        from .knowledge import ingest_knowledge_document

        safe_topic = "".join(
            c if c.isalnum() or c in "-_ " else "" for c in topic
        ).strip().replace(" ", "-")[:60] or "topic"
        saved = await ingest_knowledge_document(
            data=md.encode("utf-8"),
            original_name=f"WhitePaper-{safe_topic}-{date}.md",
            mime="text/markdown",
            category="research",
            notes=f"White paper on: {topic}",
            auto_classify=False,   # we know what it is — skip the filing clerk
            tenant_id=tenant_id,
        )
        if saved.get("ok"):
            file_info = {"id": saved.get("fileId"),
                         "filename": saved.get("filename"),
                         "chunks": saved.get("chunks", 0)}
    except Exception:  # noqa: BLE001 — non-fatal; still return the paper
        file_info = None

    return {
        "ok": True,
        "topic": topic,
        "low_grounding": low_grounding,
        "title": title,
        "subtitle": subtitle,
        "abstract": abstract,
        "sections": sections,
        "key_takeaways": takeaways,
        "structure_summary": structure_text[:1200],
        "exemplars": exemplars,
        "sources": [{"n": d["n"], "filename": d["filename"]} for d in corpus],
        "thesis": thesis_name or None,
        "markdown": md,
        "file": file_info,
    }


# ── background jobs (the paper takes 1-2 min; the HTTP gateway times out
# synchronous calls around ~50s, so generation runs detached and the UI
# polls). Jobs are in-memory: a restart loses the *status*, never the paper —
# a finished paper is already persisted in the Knowledge Base ledger.
import asyncio as _asyncio
import uuid as _uuid

_WP_JOBS: dict[str, dict] = {}
_WP_OWNER: dict[str, str] = {}   # job_id -> tenant, for the cross-tenant poll guard
_WP_TASKS: set = set()   # strong refs so detached jobs aren't GC'd
_WP_JOBS_MAX = 40        # keep memory bounded; oldest finished jobs pruned


def _prune_jobs() -> None:
    if len(_WP_JOBS) <= _WP_JOBS_MAX:
        return
    finished = [k for k, v in _WP_JOBS.items() if v.get("status") != "running"]
    for k in finished[: len(_WP_JOBS) - _WP_JOBS_MAX]:
        _WP_JOBS.pop(k, None)
        _WP_OWNER.pop(k, None)


def start_whitepaper_job(
    *, topic: str, audience: str = "", goal: str = "",
    tenant_id: UUID | None = None,
) -> str:
    """Kick a detached white-paper generation; returns a job id to poll.

    The tenant is resolved EXPLICITLY here (while the request context still
    exists) and bound into the job — a detached task must never depend on the
    request contextvar surviving, or a multi-tenant deploy would file one
    brand's paper under another's corpus."""
    from .db import _request_tenant
    tenant_id = tenant_id or _request_tenant.get()
    job_id = _uuid.uuid4().hex[:12]
    _WP_JOBS[job_id] = {"status": "running", "topic": topic}
    _WP_OWNER[job_id] = str(tenant_id or "")
    _prune_jobs()

    async def _run() -> None:
        try:
            result = await generate_whitepaper(
                topic=topic, audience=audience, goal=goal, tenant_id=tenant_id,
            )
            _WP_JOBS[job_id] = {"status": "done", "topic": topic, "result": result}
        except Exception as e:  # noqa: BLE001 — surfaced via the poll
            _WP_JOBS[job_id] = {"status": "failed", "topic": topic,
                                "error": str(e)[:500]}

    task = _asyncio.create_task(_run())
    _WP_TASKS.add(task)
    task.add_done_callback(_WP_TASKS.discard)
    return job_id


def get_whitepaper_job(job_id: str, tenant_id=None) -> dict | None:
    job = _WP_JOBS.get(job_id)
    if job is None:
        return None
    owner = _WP_OWNER.get(job_id)
    if tenant_id is not None and owner and owner != str(tenant_id):
        return None   # cross-tenant poll — pretend it doesn't exist
    return job


__all__ = ["generate_whitepaper", "start_whitepaper_job", "get_whitepaper_job"]
