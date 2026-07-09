"""Topic Intelligence + cross-silo synthesis — ported feature-for-feature
from the PreReal Intelligence platform (research-planner.ts, intelligence.ts,
api/intelligence, api/synthesize) onto BM's multi-tenant substrate.

TOPIC INTELLIGENCE (the deep-research machine):
  1. COVERAGE — check what the tenant's corpus already knows about the topic;
     skip the (costly) external sweep when coverage is sufficient.
  2. PLAN — a research strategist decides WHAT is worth investigating for
     THIS topic (3-6 gap-targeted directives), falling back to the five
     fixed angles (white papers / latest / issues / players / hidden).
  3. GATHER — run every directive as live web research in parallel; each
     successful brief is saved into the Knowledge Base (category=research)
     so the finds become permanent, queryable corpus.
  4. SYNTHESIZE — the chief-analyst pass connects the dots across existing
     docs + fresh research into a decision-grade brief, itself saved back.

CROSS-SILO SYNTHESIS: the portfolio pass — patterns / synergies / tensions /
risks / opportunities / signals that only emerge ACROSS project silos.
"""

from __future__ import annotations

import asyncio
import re
import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .llm import get_llm
from .rerank import rerank
from .retrieval import search

# Coverage thresholds (parity): how much the corpus must already know before
# we skip the external sweep.
COVERAGE_MIN_DOCS = 3
COVERAGE_MIN_SIM = 0.42
COVERAGE_VECTOR_K = 40
CORPUS_EXISTING_K = 6      # top existing chunks folded into synthesis
ANGLE_EXCERPT_MAX = 3500   # cap each gathered brief in the synthesis prompt
MAX_TOPIC_CHARS = 300

# ── The five fixed angles (fallback when the planner fails) ──
ANGLES: list[dict] = [
    {"key": "whitepapers", "label": "White papers & reports", "recency": "",
     "blurb": "Authoritative studies, published research, and white papers.",
     "query": lambda t: (
         f'Find the most authoritative white papers, academic studies, '
         f'government reports, and published research about "{t}". For each, '
         f'summarize the key findings, data, methodology, and conclusions. '
         f'Prioritize primary sources and recent, credible publications. '
         f'Cite every source.')},
    {"key": "latest", "label": "Latest projects & news", "recency": "year",
     "blurb": "Recent developments, active projects, and current news.",
     "query": lambda t: (
         f'What are the latest developments, active and announced projects, '
         f'deals, and news about "{t}"? Focus on the most recent activity, '
         f'include dates and specifics, and identify what is currently in '
         f'motion versus rumored. Cite every source.')},
    {"key": "issues", "label": "Open & pending issues", "recency": "year",
     "blurb": "Unresolved questions, pending decisions, risks, controversies.",
     "query": lambda t: (
         f'What are the open questions, pending decisions, unresolved '
         f'regulatory/legal issues, risks, bottlenecks, and live controversies '
         f'around "{t}"? Be specific about what is undecided and why it '
         f'matters. Cite every source.')},
    {"key": "players", "label": "Key players & funding", "recency": "",
     "blurb": "Organizations, stakeholders, grants, and money flowing in.",
     "query": lambda t: (
         f'Who are the key players, companies, agencies, investors, and '
         f'stakeholders involved in "{t}"? Map out the funding landscape: '
         f'grants, programs, dollar amounts, and who controls the money. '
         f'Describe each player\'s role and influence. Cite every source.')},
    {"key": "hidden", "label": "Hidden / non-obvious insights", "recency": "",
     "blurb": "Under-reported angles, second-order effects, what consensus misses.",
     "query": lambda t: (
         f'What are the non-obvious, under-reported, contrarian, or hidden '
         f'insights about "{t}" that most observers miss? Surface second-order '
         f'effects, overlooked risks and opportunities, structural realities, '
         f'and where the conventional wisdom is wrong or incomplete. Be sharp '
         f'and specific, not generic. Cite every source.')},
]

_PLANNER_SYSTEM = """You are a world-class intelligence research strategist working for the principal of the brand. Given a TOPIC, decide the specific, high-value investigations that will produce decision-grade intelligence.

Use real judgment about what actually matters for THIS kind of topic — the angles a sharp analyst would prioritize, not generic buckets. A regulatory topic needs different angles than a market topic, a technology topic, or a financing topic.

For each directive provide:
- label: a 2-4 word category (it becomes a filing sub-category)
- query: a precise, web-search-optimized research question that will return substantive, citable results
- recency: how time-sensitive this angle is — exactly one of "", "day", "week", "month", "year". Use "year"/"month" for fast-moving or news-like angles; "" for foundational/structural angles.
- rationale: ONE sentence on why this matters for the decision

Principles:
- Cover the decision space that's relevant to this topic — typically some mix of: the real demand/economics, the competitive landscape, the constraints/risks/regulation, the money/incentives/funding, and the non-obvious second-order factors. Include ONLY the ones that matter here, and add topic-specific angles a generic checklist would miss.
- Prefer specific, answerable investigations over vague themes.
- If "what we already know" is provided, do NOT repeat it — target the gaps.
- Produce between 3 and 6 directives. Quality over quantity.

Output STRICT JSON, no prose outside it:
{"directives":[{"label":"...","query":"...","recency":"","rationale":"..."}]}"""

_SYNTHESIS_SYSTEM = """You are the brand's chief intelligence analyst. You are handed a corpus of research gathered on a single topic — a mix of external web research and the brand's own internal documents. Your job is to turn raw material into decision-grade intelligence for the principal.

Produce a briefing with these sections, using Markdown headers:

## Executive summary
3-5 sentences. The single most important takeaways.

## Key findings
The substantive facts, organized by theme. Be specific — figures, dates, names, dollar amounts.

## Connecting the dots
This is the most important section. Identify patterns, contradictions, and second-order implications ACROSS the sources that no single document states on its own. What does the combination reveal? Where do sources agree, disagree, or leave a gap?

## Opportunities & risks
Concrete, actionable. What should we lean into, and what should we watch out for?

## Open questions
What's still unknown or unverified, and what would be worth researching next.

Rules:
- Ground every claim in the corpus. Cite inline as [n] using the doc indices.
- Do not invent facts. If the corpus is thin on something, say so in Open questions.
- Be direct and concrete. No filler, no hedging boilerplate."""

_VALID_RECENCY = {"", "day", "week", "month", "year"}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())[:32]


async def plan_research(
    topic: str, known_context: str = "", max_directives: int = 6,
) -> list[dict]:
    """The 'common sense' layer: 3-6 gap-targeted, search-optimized research
    directives for the topic. [] on any failure (callers fall back to the
    fixed angles)."""
    max_n = min(max_directives, 8)
    user = "\n".join([
        f"TOPIC: {topic}",
        "",
        "WHAT WE ALREADY KNOW (do not re-research this — target the gaps):",
        (known_context or "").strip()
        or "Nothing indexed yet — plan a comprehensive investigation from "
           "the ground up.",
        "",
        f"Produce up to {max_n} research directives as JSON now.",
    ])
    try:
        out = await get_llm().complete_json(
            system=_PLANNER_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500, temperature=0.3,
        )
    except Exception:  # noqa: BLE001
        return []
    directives = []
    for d in (out.get("directives") or []) if isinstance(out, dict) else []:
        if not isinstance(d, dict):
            continue
        label = str(d.get("label") or "").strip()[:40]
        query = str(d.get("query") or "").strip()[:500]
        if not label or not query:
            continue
        recency = str(d.get("recency") or "").strip()
        if recency not in _VALID_RECENCY:
            recency = ""
        directives.append({
            "label": label, "query": query, "recency": recency,
            "rationale": str(d.get("rationale") or "").strip()[:300],
        })
        if len(directives) >= max_n:
            break
    return directives


async def _web_research(query: str) -> tuple[str, list[dict]]:
    """One live web-research call → (content, citations). Raises on failure."""
    from .research import get_research_provider

    provider = get_research_provider()
    if getattr(provider, "name", "stub") == "stub":
        raise RuntimeError(
            "Research is not configured — set RESEARCH_PROVIDER=perplexity "
            "and a PERPLEXITY_API_KEY.")
    res = await provider.research(query)
    text = (res.summary or "").strip()
    if res.findings:
        text += "\n" + "\n".join(f"- {f}" for f in res.findings)
    cites = [
        {"url": s.url, "title": s.title or None}
        for s in (res.sources or []) if getattr(s, "url", "")
    ]
    return text.strip(), cites


async def _save_brief(
    *, title: str, markdown: str, descriptor: str,
    tenant_id: UUID | None,
) -> dict | None:
    """Persist a gathered brief / synthesis into the Knowledge Base so the
    finds become permanent, queryable corpus. Best-effort."""
    try:
        from .knowledge import ingest_knowledge_document

        safe = re.sub(r"[^A-Za-z0-9-_ ]+", "", descriptor).strip()
        safe = safe.replace(" ", "-")[:60] or "brief"
        date = datetime.now(UTC).date().isoformat()
        saved = await ingest_knowledge_document(
            data=markdown.encode("utf-8"),
            original_name=f"{safe}-{date}.md",
            mime="text/markdown",
            category="research",
            notes=title,
            auto_classify=False,
            tenant_id=tenant_id,
        )
        if saved.get("ok"):
            return {"id": saved.get("fileId"), "filename": saved.get("filename")}
    except Exception:  # noqa: BLE001
        pass
    return None


def _brief_markdown(
    *, title: str, body: str, citations: list[dict], topic: str,
    subtopic: str, date: str,
) -> str:
    cite_lines = "\n".join(
        f"{i + 1}. [{(c.get('title') or c['url'])}]({c['url']})"
        for i, c in enumerate(citations)
    )
    return "\n".join([
        f"# {title}",
        "",
        f"> Research brief gathered {date} — topic: {topic} / {subtopic}.",
        "",
        body.strip(),
        "",
        "## Sources" if cite_lines else "",
        cite_lines,
        "",
    ])


async def build_topic_intelligence(
    *,
    topic: str,
    auto_plan: bool = True,
    force: bool = False,
    tenant_id: UUID | None = None,
) -> dict:
    """The full topic-intelligence pipeline (coverage → plan → gather →
    synthesize). Every gathered brief AND the synthesis are saved into the
    Knowledge Base, so intelligence compounds."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required")
    if len(topic) > MAX_TOPIC_CHARS:
        raise ValueError(f"topic too long (max {MAX_TOPIC_CHARS} chars)")
    date = datetime.now(UTC).date().isoformat()

    # ── 1. Coverage: what do we already know? ──
    hits = await search(topic, tenant_id=tenant_id, top_k_per_index=COVERAGE_VECTOR_K)
    # NDA-Protected docs never feed a persisted brief (it's saved back as
    # ordinary research — reproducing NDA data would launder it out of its tier).
    from .sensitivity import drop_nda_protected
    hits, _nda_dropped = await drop_nda_protected(hits, tenant_id)
    strong = [h for h in hits if h.score >= COVERAGE_MIN_SIM]
    strong_files = {
        (h.payload or {}).get("filename") or str(h.event_id) for h in strong
    }
    top_sim = max((h.score for h in hits), default=0.0)
    had_enough = len(strong_files) >= COVERAGE_MIN_DOCS and top_sim >= COVERAGE_MIN_SIM

    known_lines: list[str] = []
    seen = set()
    for h in hits:
        fn = (h.payload or {}).get("filename") or ""
        if not fn or fn in seen:
            continue
        seen.add(fn)
        excerpt = re.sub(r"\s+", " ", (h.raw_content or "")[:160]).strip()
        known_lines.append(f"- {fn}: {excerpt}")
        if len(known_lines) >= 8:
            break

    # ── 2. Plan + gather (only if thin, or forced) ──
    gathered: list[dict] = []
    planned = False
    should_research = force or not had_enough
    if should_research:
        run_items: list[dict] = []
        if auto_plan:
            directives = await plan_research(topic, "\n".join(known_lines))
            if directives:
                planned = True
                used = set()
                for d in directives:
                    key = _slugify(d["label"]) or "angle"
                    while key in used:
                        key += "x"
                    used.add(key)
                    run_items.append({**d, "key": key})
        if not run_items:
            run_items = [
                {"key": a["key"], "label": a["label"], "query": a["query"](topic),
                 "recency": a["recency"], "rationale": a["blurb"]}
                for a in ANGLES
            ]

        # Run all searches in parallel — the slow part.
        results = await asyncio.gather(
            *(_web_research(it["query"]) for it in run_items),
            return_exceptions=True,
        )
        # Persist sequentially (avoids version-walk races on the same topic).
        for it, r in zip(run_items, results, strict=True):
            if isinstance(r, BaseException):
                gathered.append({
                    "angle": it["key"], "label": it["label"],
                    "rationale": it["rationale"], "content": "",
                    "citations": [], "file": None, "error": str(r)[:300],
                })
                continue
            content, citations = r
            md = _brief_markdown(
                title=f"{it['label']} — {topic}", body=content,
                citations=citations, topic=topic, subtopic=it["key"], date=date,
            )
            file_info = await _save_brief(
                title=f"{it['key']} {topic}", markdown=md,
                descriptor=f"ResearchBrief-{it['key']}-{topic}",
                tenant_id=tenant_id,
            )
            gathered.append({
                "angle": it["key"], "label": it["label"],
                "rationale": it["rationale"], "content": content,
                "citations": citations, "file": file_info,
            })

    # ── 3. Build the synthesis corpus (existing docs + fresh briefs) ──
    corpus: list[dict] = []
    if hits:
        kept = await rerank(topic, hits, top_k=CORPUS_EXISTING_K)
        for ev in kept:
            text = (ev.raw_content or "").strip()
            if not text:
                continue
            corpus.append({
                "n": len(corpus) + 1,
                "filename": (ev.payload or {}).get("filename") or ev.event_type,
                "origin": "existing",
                "text": text[:ANGLE_EXCERPT_MAX],
            })
    for g in gathered:
        if not g["content"]:
            continue
        corpus.append({
            "n": len(corpus) + 1,
            "filename": (g["file"] or {}).get("filename") or f"{g['label']} (not saved)",
            "origin": g["label"],
            "text": g["content"][:ANGLE_EXCERPT_MAX],
        })

    coverage = {
        "had_enough": had_enough,
        "existing_docs": len(strong_files),
        "top_similarity": round(top_sim, 3),
        "researched": should_research,
    }
    if not corpus:
        return {
            "ok": True, "topic": topic, "coverage": coverage, "planned": planned,
            "gathered": gathered,
            "synthesis": {
                "answer": "No material could be gathered or found for this "
                          "topic. Try a more specific topic, or check that "
                          "research is configured.",
                "file": None,
            },
            "sources": [],
        }

    # ── 4. Synthesize the intelligence brief ──
    corpus_block = "\n\n".join(
        f'<doc index="{d["n"]}" filename="{d["filename"]}" origin="{d["origin"]}">\n'
        f"{d['text']}\n</doc>"
        for d in corpus
    )
    system = (
        _SYNTHESIS_SYSTEM
        + f'\n\nCorpus for topic "{topic}" ({len(corpus)} documents — a mix of '
          f"the brand's own docs and freshly gathered web research). "
          f"Cite by index as [n].\n\n{corpus_block}"
    )
    # BM's LLM interface is JSON-only — carry the markdown briefing in an
    # envelope field.
    out = await get_llm().complete_json(
        system=system + '\n\nReturn STRICT JSON: {"briefing_markdown": "<the full briefing in Markdown>"}',
        messages=[{"role": "user", "content":
                   f"Topic: {topic}\n\nProduce the decision-grade "
                   f"intelligence briefing now."}],
        max_tokens=4096, temperature=0.3,
    )
    answer = str((out or {}).get("briefing_markdown") or "") if isinstance(out, dict) else ""

    synth_md = "\n".join([
        f"# Intelligence Brief: {topic}",
        "",
        f"> Synthesized {date} by the brand intelligence engine across "
        f"{len(corpus)} sources.",
        "",
        (answer or "").strip(),
        "",
        "## Corpus",
        "\n".join(f"{d['n']}. {d['filename']} ({d['origin']})" for d in corpus),
        "",
    ])
    synth_file = await _save_brief(
        title=f"Intelligence Brief {topic}", markdown=synth_md,
        descriptor=f"IntelligenceBrief-{topic}", tenant_id=tenant_id,
    )

    return {
        "ok": True, "topic": topic, "coverage": coverage, "planned": planned,
        "gathered": [
            {"angle": g["angle"], "label": g["label"], "rationale": g["rationale"],
             "citations": len(g["citations"]), "saved": bool(g["file"]),
             "file": g["file"], "error": g.get("error")}
            for g in gathered
        ],
        "synthesis": {"answer": answer, "file": synth_file},
        "sources": [
            {"n": d["n"], "filename": d["filename"], "origin": d["origin"]}
            for d in corpus
        ],
    }


# ── Cross-silo portfolio synthesis ──

PER_SILO_KEEP = 5
SILO_EXCERPT_MAX = 900
POINT_TYPES = ["pattern", "synergy", "tension", "risk", "opportunity", "signal"]

_PORTFOLIO_SYSTEM = """You are the brand's portfolio intelligence analyst. You are given documents drawn from MULTIPLE project silos (each silo is a distinct project or domain). Each <doc> is tagged with the silo it came from.

Your job is to surface the analytical points that ONLY emerge when you look ACROSS projects — the connections, patterns, and tensions no single document states on its own. Think like a chief of staff briefing the principal on the whole portfolio at once.

For each analytical point, classify it as one of:
- "pattern"      — a recurring theme/dynamic showing up in multiple silos
- "synergy"      — where two or more projects could reinforce or feed each other
- "tension"      — where projects conflict, compete for the same resource, or contradict
- "risk"         — a portfolio-level threat or exposure
- "opportunity"  — a portfolio-level opening worth pursuing
- "signal"       — a notable standout from one silo that's relevant to the others

Rules:
- Ground every point in the provided documents. Cite evidence by the doc index numbers.
- STRONGLY prefer cross-silo points (involving 2+ silos). Include a single-silo "signal" only if it has clear portfolio relevance.
- Be specific and concrete — name figures, places, programs. No vague generalities.
- Do not invent facts. If the corpus is thin, return fewer, higher-quality points.
- Order points by importance (most decision-relevant first). Aim for 4-8 points.

Respond with STRICT JSON, no prose outside it, in exactly this shape:
{
  "executive_summary": "2-4 sentences on the portfolio-level picture.",
  "points": [
    {
      "type": "pattern|synergy|tension|risk|opportunity|signal",
      "title": "short headline",
      "insight": "1-3 sentences of specific analysis",
      "silos": ["silo_id", "..."],
      "evidence": [1, 4]
    }
  ]
}"""


async def synthesize_portfolio(
    *, theme: str = "", silo_ids: list[str] | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Cross-silo synthesis: per-silo retrieval → one portfolio pass."""
    # Resolve silos in scope — those that actually have indexed content.
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT d.silo_id, s.name
                 FROM document_metadata d
                 JOIN silos s ON s.id = d.silo_id
                WHERE d.silo_id IS NOT NULL AND d.chunks > 0""",
        )
    in_scope = [
        {"id": r["silo_id"], "name": r["name"]}
        for r in rows
        if not silo_ids or r["silo_id"] in silo_ids
    ]
    if len(in_scope) < 2:
        raise ValueError(
            "Cross-silo synthesis needs at least 2 silos with indexed "
            "documents. Assign documents to silos first.")

    query = (theme or "").strip() or "key facts, plans, risks, and status"
    corpus: list[dict] = []
    for silo in in_scope:
        # Silo-scoped corpus: this silo's documents' filenames gate the hits.
        async with acquire(tenant_id) as conn:
            fn_rows = await conn.fetch(
                "SELECT filename FROM document_metadata "
                "WHERE silo_id = $1 AND chunks > 0", silo["id"],
            )
        filenames = {r["filename"] for r in fn_rows}
        if not filenames:
            continue
        hits = await search(query, tenant_id=tenant_id, top_k_per_index=30)
        silo_hits = [
            h for h in hits
            if (h.payload or {}).get("filename") in filenames
        ]
        from .sensitivity import drop_nda_protected
        silo_hits, _ = await drop_nda_protected(silo_hits, tenant_id)
        kept = await rerank(query, silo_hits, top_k=PER_SILO_KEEP)
        for ev in kept:
            text = (ev.raw_content or "").strip()
            if not text:
                continue
            corpus.append({
                "n": len(corpus) + 1,
                "filename": (ev.payload or {}).get("filename") or "",
                "silo": silo["id"],
                "text": text[:SILO_EXCERPT_MAX],
            })
    if not corpus:
        raise ValueError("No indexed content found in the selected silos.")

    corpus_block = "\n\n".join(
        f'<doc index="{d["n"]}" silo="{d["silo"]}" filename="{d["filename"]}">\n'
        f"{d['text']}\n</doc>"
        for d in corpus
    )
    user = (f"THEME: {query}\n\nSILOS IN SCOPE: "
            f"{', '.join(s['id'] for s in in_scope)}\n\n{corpus_block}\n\n"
            f"Produce the portfolio synthesis as JSON now.")
    out = await get_llm().complete_json(
        system=_PORTFOLIO_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=3000, temperature=0.3,
    )
    if not isinstance(out, dict):
        raise RuntimeError("The model did not return a valid synthesis.")
    points = []
    for p in (out.get("points") or []):
        if not isinstance(p, dict):
            continue
        ptype = str(p.get("type") or "")
        points.append({
            "type": ptype if ptype in POINT_TYPES else "signal",
            "title": str(p.get("title") or "")[:160],
            "insight": str(p.get("insight") or "")[:800],
            "silos": [str(s) for s in (p.get("silos") or [])][:8],
            "evidence": [int(e) for e in (p.get("evidence") or [])
                         if isinstance(e, (int, float))][:8],
        })
    return {
        "ok": True,
        "executive_summary": str(out.get("executive_summary") or "")[:1500],
        "points": points,
        "silos": in_scope,
        "sources": [
            {"n": d["n"], "filename": d["filename"], "silo": d["silo"]}
            for d in corpus
        ],
    }


# ── background jobs (an intelligence build runs many web searches + a
# synthesis pass — minutes, not seconds; the gateway kills ~50s sync calls) ──

_JOBS: dict[str, dict] = {}
_JOB_OWNER: dict[str, str] = {}   # job_id -> tenant, for the cross-tenant poll guard
_TASKS: set = set()
_JOBS_MAX = 40


def _prune() -> None:
    if len(_JOBS) <= _JOBS_MAX:
        return
    done = [k for k, v in _JOBS.items() if v.get("status") != "running"]
    for k in done[: len(_JOBS) - _JOBS_MAX]:
        _JOBS.pop(k, None)
        _JOB_OWNER.pop(k, None)


def start_intelligence_job(
    *, topic: str, auto_plan: bool = True, force: bool = False,
    tenant_id: UUID | None = None,
) -> str:
    # Bind the tenant EXPLICITLY while the request context still exists — a
    # detached job must never rely on the request contextvar (multi-tenant
    # correctness; same posture as the whitepaper job).
    from .db import _request_tenant
    tenant_id = tenant_id or _request_tenant.get()
    job_id = _uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"status": "running", "topic": topic}
    _JOB_OWNER[job_id] = str(tenant_id or "")
    _prune()

    async def _run() -> None:
        try:
            result = await build_topic_intelligence(
                topic=topic, auto_plan=auto_plan, force=force,
                tenant_id=tenant_id,
            )
            _JOBS[job_id] = {"status": "done", "topic": topic, "result": result}
        except Exception as e:  # noqa: BLE001
            _JOBS[job_id] = {"status": "failed", "topic": topic,
                             "error": str(e)[:500]}

    task = asyncio.create_task(_run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job_id


def get_intelligence_job(job_id: str, tenant_id=None) -> dict | None:
    job = _JOBS.get(job_id)
    if job is None:
        return None
    owner = _JOB_OWNER.get(job_id)
    if tenant_id is not None and owner and owner != str(tenant_id):
        return None   # cross-tenant poll — pretend it doesn't exist
    return job


__all__ = [
    "plan_research", "build_topic_intelligence", "synthesize_portfolio",
    "start_intelligence_job", "get_intelligence_job", "ANGLES",
]
