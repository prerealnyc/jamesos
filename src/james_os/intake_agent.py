"""Agentic intake — two agents build the brand's identity together.

Roy's spec: "a brand comes in, it signs up, it should be asked questions.
If the user doesn't want to fill, it should be auto-answered by the agent…
give their name and a few details and the brand manager pulls the
information from online, does the research themselves and shows them: IS
THIS YOUR BRAND? … Then it should be able to ask ten thousand or more
questions regarding the brand — an agent to ASK the questions and an agent
to FIND the answers — both bring the intelligence to the platform so the
user finds it very easy."

Three cooperating pieces:

  RESEARCHER  research_brand(name, hints) → a PROPOSED brand profile with
              sources ("is this your brand?"), so typing a name is enough
              to start. Also answers open interview questions from the web
              + the tenant's own corpus, with confidence + citations.

  INTERVIEWER generate_questions() → the next batch of deep-dive questions
              across ten dimensions, aware of everything already asked and
              answered — the 10,000-question interview, run in batches.

  LEDGER      brand_questions rows. research-answered ≥ threshold →
              'answered' (user can confirm/correct); confirmed answers are
              FILED INTO MEMORY as citable events, so Ask, the voice
              engine, and the strategy engine all get smarter with every
              answer.

The scheduled job 'brand_interview' keeps the loop running in the
background after intake, bounded per run so costs stay sane.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .llm import get_llm

DIMENSIONS = ("identity", "story", "audience", "offerings", "proof",
              "voice", "style", "competitors", "pov", "operations")

_ANSWERED_THRESHOLD = 0.55     # research below this stays open for the human
_QUESTIONS_PER_BATCH = 20
_ANSWERS_PER_RUN = 15
_OPEN_TARGET = 40              # keep this many open at most — never a wall

_PROPOSE_SYSTEM = """You are a brand researcher. From the research briefing
below, propose the brand profile for "{name}". Only state what the research
supports — leave unknown fields as empty strings/lists. This will be shown
to the brand's own operator with the question "Is this your brand?", so be
accurate, specific, and neutral.

Return STRICT JSON:
{{"kind": "person"|"asset"|"institution"|"politician",
  "identity": {{"name": str, "mission": str, "positioning": str, "audience": str}},
  "goals": [str, ...], "pillars": [str, ...], "peers": [str, ...],
  "summary": str}}    // 3-5 sentence "here's what we found" for the operator
"""

_INTERVIEWER_SYSTEM = """You are the INTERVIEWER agent building a complete
picture of the brand in <brand_profile>. Generate the NEXT {n} deep-dive
questions — the questions an elite brand manager would ask in week one.

Rules:
* Spread across these dimensions (favor the least-covered): {dims}.
* NEVER repeat or trivially rephrase anything in <already_asked>.
* Each question must be answerable in 1-4 sentences, specific to THIS
  brand (not generic marketing quiz questions), and useful for producing
  content or strategy later.
* Move from foundational → specific as coverage grows: origin story,
  customers' exact words, named proof points, opinions on live industry
  debates, style likes/dislikes, operational facts.

Return STRICT JSON:
{{"questions": [{{"dimension": str, "question": str}}, ...]}}
"""

_ANSWERER_SYSTEM = """You are the RESEARCHER agent. Answer the interview
question about the brand using ONLY the material below (web briefing +
the brand's own corpus excerpts). If the material doesn't answer it,
say so — never guess.

Return STRICT JSON:
{{"answer": str,            // "" when the material can't answer
  "confidence": float,      // 0-1, honest
  "used_sources": [str]}}   // URLs/filenames actually used
"""


# ── RESEARCHER: "is this your brand?" ─────────────────────────────────

async def research_brand(
    name: str, hints: str = "", tenant_id: UUID | None = None,
) -> dict:
    """Type a name (+ optional hints: website, socials, city) → a proposed
    profile with sources. Nothing is saved — the operator confirms first."""
    name = (name or "").strip()
    if not name:
        raise ValueError("brand name is required")
    from .research import get_research_provider
    res = await get_research_provider().research(
        subject=f"{name} {hints}".strip()[:220],
        focus="who/what this brand is: mission, positioning, audience, "
              "goals, main topics, notable competitors or peers, and "
              "credibility signals. Facts with sources only.",
    )
    briefing = (res.summary + "\n" + "\n".join(f"- {f}" for f in res.findings[:15]))[:7000]
    sources = [s.url for s in res.sources][:10]
    out = await get_llm().complete_json(
        system=_PROPOSE_SYSTEM.format(name=name[:120]),
        messages=[{"role": "user", "content":
                   f"<briefing>\n{briefing or '(no research available)'}\n</briefing>"}],
        max_tokens=1200, temperature=0.3,
    )
    if not isinstance(out, dict):
        out = {}
    ident = out.get("identity") or {}
    ident["name"] = ident.get("name") or name
    return {
        "proposal": {
            "kind": out.get("kind") or "person",
            "identity": {k: str(ident.get(k) or "")[:400]
                         for k in ("name", "mission", "positioning", "audience")},
            "goals": [str(g)[:160] for g in (out.get("goals") or [])][:6],
            "pillars": [str(p)[:100] for p in (out.get("pillars") or [])][:8],
            "peers": [str(p)[:80] for p in (out.get("peers") or [])][:8],
        },
        "summary": str(out.get("summary") or "")[:1200],
        "sources": sources,
    }


# ── INTERVIEWER: the 10,000-question interview, batch by batch ───────

async def generate_questions(
    tenant_id: UUID | None = None, n: int = _QUESTIONS_PER_BATCH,
) -> int:
    from .brands import brand_profile_block
    block = await brand_profile_block(tenant_id)
    async with acquire(tenant_id) as conn:
        asked = await conn.fetch(
            "SELECT question, dimension FROM brand_questions "
            "ORDER BY created_at DESC LIMIT 300")
        open_count = await conn.fetchval(
            "SELECT count(*) FROM brand_questions WHERE status='open'")
    room = max(0, _OPEN_TARGET - int(open_count or 0))
    n = min(n, room)
    if n <= 0:
        return 0
    asked_block = "\n".join(f"- [{r['dimension']}] {r['question']}" for r in asked)
    out = await get_llm().complete_json(
        system=_INTERVIEWER_SYSTEM.format(n=n, dims=", ".join(DIMENSIONS)),
        messages=[{"role": "user", "content":
                   f"{block or '(profile not filled yet — start foundational)'}\n\n"
                   f"<already_asked>\n{asked_block or '(none yet)'}\n</already_asked>"}],
        max_tokens=1800, temperature=0.6,
    )
    made = 0
    seen = {r["question"].strip().lower() for r in asked}
    async with acquire(tenant_id) as conn:
        for q in (out or {}).get("questions") or []:
            if not isinstance(q, dict):
                continue
            text = str(q.get("question") or "").strip()[:400]
            if not text or text.lower() in seen:
                continue
            dim = str(q.get("dimension") or "identity")
            if dim not in DIMENSIONS:
                dim = "identity"
            await conn.execute(
                "INSERT INTO brand_questions (dimension, question) VALUES ($1, $2)",
                dim, text)
            seen.add(text.lower())
            made += 1
    return made


# ── RESEARCHER: answer open questions from web + corpus ───────────────

async def research_answers(
    tenant_id: UUID | None = None, limit: int = _ANSWERS_PER_RUN,
) -> dict:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, dimension, question FROM brand_questions "
            "WHERE status='open' ORDER BY created_at ASC LIMIT $1", limit)
    if not rows:
        return {"answered": 0, "left_for_user": 0}

    from .brands import get_brand_profile
    profile = await get_brand_profile(tenant_id)
    ident = (profile or {}).get("identity") or {}
    brand_name = ident.get("name") or ""

    # ONE web pass covering this batch (not one per question — cost control).
    briefing = ""
    try:
        from .research import get_research_provider
        joined = "; ".join(r["question"][:120] for r in rows[:8])
        res = await get_research_provider().research(
            subject=(brand_name or "the brand")[:120],
            focus=f"answering these questions about them: {joined}"[:500],
        )
        briefing = (res.summary + "\n" + "\n".join(
            f"- {f}" for f in res.findings[:15]))[:6000]
        briefing_sources = [s.url for s in res.sources][:10]
    except Exception as e:  # noqa: BLE001 — corpus-only answering still works
        print(f"[intake_agent] web pass skipped: {e}")
        briefing_sources = []

    answered = 0
    from .rerank import rerank
    from .retrieval import search
    for r in rows:
        # Corpus excerpts relevant to THIS question.
        corpus = ""
        try:
            hits = await search(r["question"], tenant_id=tenant_id,
                                top_k_per_index=6)
            kept = await rerank(r["question"], hits, top_k=4)
            corpus = "\n\n".join(
                f"[{(h.payload or {}).get('filename') or h.event_type}] "
                f"{(h.raw_content or '')[:600]}" for h in kept)[:3500]
        except Exception:  # noqa: BLE001
            corpus = ""
        try:
            out = await get_llm().complete_json(
                system=_ANSWERER_SYSTEM,
                messages=[{"role": "user", "content":
                           f"BRAND: {brand_name}\nQUESTION [{r['dimension']}]: "
                           f"{r['question']}\n\n<web_briefing>\n"
                           f"{briefing or '(none)'}\n</web_briefing>\n\n"
                           f"<corpus>\n{corpus or '(none)'}\n</corpus>"}],
                max_tokens=500, temperature=0.2,
            )
        except Exception:  # noqa: BLE001 — leave the question open
            continue
        ans = str((out or {}).get("answer") or "").strip()
        try:
            conf = max(0.0, min(1.0, float((out or {}).get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        if not ans or conf < _ANSWERED_THRESHOLD:
            continue
        used = [str(u)[:300] for u in (out or {}).get("used_sources") or []][:6]
        async with acquire(tenant_id) as conn:
            await conn.execute(
                """UPDATE brand_questions
                      SET answer=$2, source='research', confidence=$3,
                          sources=$4::jsonb, status='answered', answered_at=now()
                    WHERE id=$1 AND status='open'""",
                r["id"], ans[:2000], conf,
                json.dumps(used or briefing_sources[:4]))
        answered += 1
    left = len(rows) - answered
    return {"answered": answered, "left_for_user": left}


# ── confirmed answers become brand memory ─────────────────────────────

async def _file_answer(qrow: dict, tenant_id: UUID | None) -> None:
    from .ingestion import ingest_many
    from .models import EventCreate, EventSource
    text = (f"Brand interview [{qrow['dimension']}]\n"
            f"Q: {qrow['question']}\nA: {qrow['answer']}")
    digest = hashlib.sha256(str(qrow["id"]).encode()).hexdigest()[:16]
    ev = EventCreate(
        event_type="note",
        payload={"text": text, "category": "reference",
                 "kind": "brand_interview", "dimension": qrow["dimension"],
                 "sources": qrow.get("sources") or []},
        raw_content=text,
        source=EventSource(
            adapter="intake_agent", uri=None,
            dedupe_key=f"brandq-{digest}",
            raw_metadata={"question_id": str(qrow["id"]),
                          "category": "reference"},
        ),
        entities=["category:reference", f"dimension:{qrow['dimension']}"],
        effective_at=datetime.now(UTC),
        confidence=1.0 if qrow["source"] == "user" else float(qrow["confidence"] or 0.6),
    )
    await ingest_many([ev], tenant_id=tenant_id)


async def confirm_answer(
    question_id: UUID, answer: str | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Human confirms (optionally corrects) an answer → status confirmed →
    filed into memory. Also the path for the human ANSWERING an open one."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, dimension, question, answer, source, confidence, sources "
            "FROM brand_questions WHERE id=$1", question_id)
    if not row:
        raise ValueError("question not found")
    final = (answer or "").strip() or (row["answer"] or "").strip()
    if not final:
        raise ValueError("no answer to confirm")
    src = "user" if (answer or "").strip() else row["source"]
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """UPDATE brand_questions
                  SET answer=$2, source=$3, status='confirmed',
                      confidence=GREATEST(confidence, $4), answered_at=now()
                WHERE id=$1""",
            question_id, final[:2000], src, 1.0 if src == "user" else 0.0)
    qrow = dict(row)
    qrow["answer"] = final
    qrow["source"] = src
    srcs = qrow.get("sources")
    if isinstance(srcs, str):
        qrow["sources"] = json.loads(srcs)
    try:
        await _file_answer(qrow, tenant_id)
    except Exception as e:  # noqa: BLE001 — confirm never fails on filing
        print(f"[intake_agent] filing failed: {e}")
    return {"id": str(question_id), "status": "confirmed"}


async def list_questions(
    status: str = "", tenant_id: UUID | None = None, limit: int = 40,
) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, dimension, question, answer, source, confidence,
                      sources, status, created_at
                 FROM brand_questions
                WHERE ($1 = '' OR status = $1)
                ORDER BY (status='answered') DESC, created_at ASC
                LIMIT $2""",
            status or "", limit)
    out = []
    for r in rows:
        srcs = r["sources"]
        if isinstance(srcs, str):
            srcs = json.loads(srcs)
        out.append({
            "id": str(r["id"]), "dimension": r["dimension"],
            "question": r["question"], "answer": r["answer"],
            "source": r["source"], "confidence": float(r["confidence"] or 0),
            "sources": srcs or [], "status": r["status"],
        })
    return out


async def interview_stats(tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n FROM brand_questions GROUP BY status")
    d = {r["status"]: int(r["n"]) for r in rows}
    return {"open": d.get("open", 0), "answered": d.get("answered", 0),
            "confirmed": d.get("confirmed", 0), "dismissed": d.get("dismissed", 0),
            "total": sum(d.values())}


# ── the background interview loop (scheduler job) ─────────────────────

async def run_brand_interview(tenant_id: UUID, config: dict | None = None) -> None:
    """Each run: top up the open-question pool, then research-answer a
    bounded batch. Runs on a cadence after intake completes — the interview
    that never really ends, without ever spamming the operator."""
    from .brands import get_brand_profile
    profile = await get_brand_profile(tenant_id)
    if not profile or not profile.get("intake_done"):
        return
    made = await generate_questions(tenant_id)
    res = await research_answers(tenant_id)
    print(f"[intake_agent] tenant {tenant_id}: +{made} questions, "
          f"{res['answered']} research-answered")


__all__ = [
    "research_brand", "generate_questions", "research_answers",
    "confirm_answer", "list_questions", "interview_stats",
    "run_brand_interview", "DIMENSIONS",
]
