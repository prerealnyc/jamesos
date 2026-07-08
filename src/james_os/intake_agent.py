"""Agentic intake — bm2.0's onboarding interview system on the james-os substrate.

Roy's spec (kept): "a brand comes in, it signs up, it should be asked
questions. If the user doesn't want to fill, it should be auto-answered by
the agent… an agent to ASK the questions and an agent to FIND the answers."

v2 (this file) replaces the dormant v1's LLM-invented 10-dimension question
generator with bm2.0's curated interview system (donors: backend/app/agents/
{interviewer,answerer}.py and services/onboarding.py):

  QUESTION BANK  manager/question_bank.py — the 119 seeded templates.
                 generate_questions() now MATERIALIZES the templates matching
                 the brand's entity type into brand_questions rows (D3:
                 dimension via profile._SECTION_DIMENSION, field_key set,
                 status='open') instead of asking an LLM to invent questions.

  INTERVIEWER    interview_next() — the donor's 3-tier selection (D10):
                 (1) contradiction-driven, (2) must-asks (info_value 5,
                 non-researchable), (3) backlog ranked by info_value x
                 confidence_gap (D2: missing field gap=1.0, stale counts at
                 0.5 x confidence). The <=25 onboarding cap applies while
                 tenants.config['onboarding_status'] == 'onboarding'; the
                 settle rule (maybe_advance_onboarding, single source of
                 truth) flips it to 'active' once every must-ask is settled
                 or the cap is consumed.

  ANSWERER       suggest_answer() / run_answer_batch() — researched DRAFT
                 answers. Never written to the profile: batch drafts live on
                 the answerer_batch job_runs row for the review screen; only
                 the human accept (answer_question/confirm_answer) records
                 them, via profile.write_field source=user_stated (D2 —
                 an AI guess is never silently a user statement).

  LEDGER         brand_questions rows (051 + field_key from 053).
                 open → asked (interviewer picked it) → answered
                 (source='research': settled by the auto-answer sweep) →
                 confirmed (human accepted; profile envelope updated, and
                 the answer still files into memory as a citable event —
                 kept from v1 so Ask/voice/strategy keep getting smarter).

Aspirational-peer answers additionally seed the manager watchlist (D4):
pre-approved entries (status='tracked', platform='unknown'); candidates the
Peer Agent already discovered are upgraded, never duplicated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from . import db
from .manager import profile, runs
from .manager.contracts import FieldStatus, FieldWrite, Source
from .manager.peers import _entry_keys, _handle_norm
from .manager.profile import _SECTION_DIMENSION
from .manager.providers import Providers, get_providers
from .manager.question_bank import template_for, templates_for
from .trends import get_watchlist, set_watchlist

# brand_questions.dimension vocabulary (051_brand_questions.sql) — kept.
DIMENSIONS = ("identity", "story", "audience", "offerings", "proof",
              "voice", "style", "competitors", "pov", "operations")

AGENT_INTERVIEWER = "interviewer"      # donor agent names (runs.start_run)
AGENT_ANSWERER = "answerer"
BATCH_AGENT = "answerer_batch"

ONBOARDING_QUESTION_CAP = 25           # spec §2.3: <=25 questions at onboarding
ASPIRATIONAL_FIELD_KEY = "competitors.aspirational"

_HUMAN_SETTLED_SOURCES = {Source.USER_STATED, Source.NEGOTIATED}
_STALE_DISCOUNT = 0.5                  # D2
_MAX_SEARCHES = 3
_PROFILE_CHARS = 4000
_BATCH_CAP = 30                        # never draft more than this per batch
_BATCH_CONCURRENCY = 5

_SPLIT_RE = re.compile(r"[\n,;]+")
_LEAD_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


# ── tenant config (onboarding_status, research_seed) ──────────────────

async def _tenant_config(tenant_id: UUID | None = None) -> dict:
    async with db.acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return cfg or {}


async def _set_config_key(key: str, value: object, tenant_id: UUID | None = None) -> None:
    async with db.acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), $1::text[], $2::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            [key], json.dumps(value),
        )


async def onboarding_status(tenant_id: UUID | None = None) -> str:
    """'onboarding' until the settle rule flips it to 'active' (the donor's
    Brand.status, kept in tenants.config so no schema change is needed)."""
    return str((await _tenant_config(tenant_id)).get("onboarding_status") or "onboarding")


async def get_research_seed(tenant_id: UUID | None = None) -> dict:
    return (await _tenant_config(tenant_id)).get("research_seed") or {}


async def set_research_seed(seed: dict, tenant_id: UUID | None = None) -> dict:
    """Persist the discover seed {name, entity_type, website, socials} so the
    research fan-out can rebuild its primary-source sets without re-asking
    the client (donor: brand.settings['research_seed'])."""
    stored = await get_research_seed(tenant_id)
    merged = {
        "name": str(seed.get("name") or stored.get("name") or "").strip(),
        "entity_type": str(seed.get("entity_type") or stored.get("entity_type") or "").strip(),
        "website": seed.get("website") or stored.get("website"),
        "socials": list(seed.get("socials") or stored.get("socials") or []),
    }
    await _set_config_key("research_seed", merged, tenant_id)
    return merged


# ── D3: question materialization (replaces the v1 LLM generator) ──────

async def _entity_type(tenant_id: UUID | None = None) -> str:
    """The brand's entity type: the profile envelope first, then the intake
    proposal's kind (brand_profiles), then the persisted research seed."""
    async with db.acquire(tenant_id) as conn:
        rows = await profile.current_fields(conn, "identity")
    for r in rows:
        if r["field_key"] == "identity.entity_type":
            v = r["value"].get("v") if isinstance(r["value"], dict) else r["value"]
            if v:
                return str(v)
    from .brands import get_brand_profile
    prof = await get_brand_profile(tenant_id)
    if prof and prof.get("kind"):
        return str(prof["kind"])
    return str((await get_research_seed(tenant_id)).get("entity_type") or "")


async def generate_questions(tenant_id: UUID | None = None, n: int | None = None) -> int:
    """Materialize the question bank (D3): one open brand_questions row per
    template whose entity_types is empty or includes the brand's type —
    dimension mapped from the template section, field_key set. Idempotent:
    a question already materialized (any status) is never re-inserted, and
    typed templates join in later once the entity type becomes known.
    (Signature kept from v1 for main.py; n optionally caps a run.)"""
    entity_type = await _entity_type(tenant_id)
    made = 0
    async with db.acquire(tenant_id) as conn:
        existing = {
            r["question"] for r in await conn.fetch("SELECT question FROM brand_questions")
        }
        for tmpl in templates_for(entity_type):
            if n is not None and made >= n:
                break
            if tmpl["text"] in existing:
                continue
            await conn.execute(
                "INSERT INTO brand_questions (dimension, question, source, status, field_key) "
                "VALUES ($1, $2, 'open', 'open', $3)",
                _SECTION_DIMENSION.get(tmpl["section"], "identity"),
                tmpl["text"],
                tmpl["field_key"],
            )
            existing.add(tmpl["text"])
            made += 1
    return made


# ── onboarding lifecycle (donor: services.onboarding) ─────────────────

async def asked_count(tenant_id: UUID | None = None) -> int:
    """Questions no longer open — what the onboarding cap is measured against."""
    async with db.acquire(tenant_id) as conn:
        return int(
            await conn.fetchval("SELECT count(*) FROM brand_questions WHERE status <> 'open'") or 0
        )


async def maybe_advance_onboarding(tenant_id: UUID | None = None) -> bool:
    """Onboarding completion rule (single source of truth): the tenant moves
    'onboarding' -> 'active' as soon as EITHER (a) every must-ask question
    (info_value 5, non-researchable) is settled — answered, auto-answered,
    or dismissed — or (b) the <=25 onboarding cap has been consumed. After
    that the Interviewer's cap no longer applies and ambient interviewing is
    governed by the per-session budget alone."""
    if await onboarding_status(tenant_id) != "onboarding":
        return False
    if await asked_count(tenant_id) >= ONBOARDING_QUESTION_CAP:
        advanced = True
    else:
        async with db.acquire(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT question, field_key FROM brand_questions "
                "WHERE status IN ('open','asked')"
            )
            total = await conn.fetchval("SELECT count(*) FROM brand_questions")
        if not int(total or 0):
            return False  # nothing materialized yet — no basis to advance
        outstanding = 0
        for r in rows:
            tmpl = template_for(r["question"], r["field_key"] or "")
            if tmpl and tmpl["info_value"] == 5 and not tmpl["researchable"]:
                outstanding += 1
        advanced = outstanding == 0
    if advanced:
        await _set_config_key("onboarding_status", "active", tenant_id)
    return advanced


# ── field-settlement helpers (donor: agents.interviewer) ──────────────

def _settled_row(rows: list[dict]) -> dict | None:
    """Human answers settle when confirmed; audited values settle while they
    are current (the Auditor writes status='unconfirmed' per D2, but a fresh
    0.95-confidence audit answers the question — asking the user would waste
    onboarding budget)."""
    for r in rows:
        if r["status"] == FieldStatus.CONFIRMED.value and Source(r["source"]) in _HUMAN_SETTLED_SOURCES:
            return r
    for r in rows:
        if Source(r["source"]) == Source.AUDITED and r["status"] in (
            FieldStatus.UNCONFIRMED.value,
            FieldStatus.CONFIRMED.value,
        ):
            return r
    return None


def _gap(rows: list[dict]) -> float:
    """confidence_gap = 1 - effective confidence; missing field = 1.0 (D2)."""
    best = 0.0
    for r in rows:
        conf = float(r["confidence"] or 0.0)
        if r["status"] == FieldStatus.STALE.value:
            conf *= _STALE_DISCOUNT
        best = max(best, conf)
    return round(max(1.0 - best, 0.0), 3)


def _field_value(row: dict) -> object:
    return row["value"].get("v") if isinstance(row["value"], dict) else row["value"]


async def _fields_by_key(tenant_id: UUID | None) -> dict[str, list[dict]]:
    async with db.acquire(tenant_id) as conn:
        rows = await profile.current_fields(conn)
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["field_key"], []).append(r)
    return out


async def _sweep(
    tenant_id: UUID | None, fields_by_key: dict[str, list[dict]], rows: list[dict]
) -> tuple[int, list[dict]]:
    """Auto-answer sweep: a question whose field is already settled by a
    confirmed user value or a current audit is answered without burning
    interview budget — status='answered', source='research', the settled
    value as the answer (the human can still confirm/correct it)."""
    swept = 0
    remaining: list[dict] = []
    async with db.acquire(tenant_id) as conn:
        for q in rows:
            settled = _settled_row(fields_by_key.get(q["field_key"] or "", [])) if q["field_key"] else None
            if settled is None:
                remaining.append(q)
                continue
            await conn.execute(
                """UPDATE brand_questions
                      SET status='answered', source='research', answer=$2,
                          confidence=$3, answered_at=now()
                    WHERE id=$1 AND status IN ('open','asked')""",
                q["id"],
                str(_field_value(settled))[:2000],
                float(settled["confidence"] or 0.0),
            )
            swept += 1
    return swept, remaining


# ── INTERVIEWER: which questions reach the human (D10) ────────────────

def _template(q: dict) -> dict | None:
    if "tmpl" not in q:
        q["tmpl"] = template_for(q["question"], q["field_key"] or "")
    return q["tmpl"]


def _info_value(q: dict) -> int:
    tmpl = _template(q)
    return int(tmpl["info_value"]) if tmpl else 5  # non-template = contradiction row


def _ask_priority(q: dict) -> int:
    tmpl = _template(q)
    return int(tmpl["ask_priority"]) if tmpl else 0


def _topic(q: dict) -> str:
    key = q["field_key"] or q["question"]
    suffix = key.split(".", 1)[-1].replace("_", " ")
    tmpl = _template(q)
    section = tmpl["section"] if tmpl else q["dimension"]
    return f"{suffix} (in your {section} profile)"


def _why_contradiction(q: dict) -> str:
    return f"Our sources disagree about {_topic(q)} — your answer settles the record."


def _why_must_ask(q: dict) -> str:
    return (
        f"Only you can answer this — {_topic(q)} can't be researched, "
        "and it directly shapes what we create."
    )


def _why_backlog(q: dict, gap: float) -> str:
    if gap >= 1.0:
        return f"We have nothing yet on {_topic(q)}, and it ranks high for shaping your content."
    return (
        f"Our picture of {_topic(q)} is only partial — "
        f"your answer raises our confidence from {1.0 - gap:.0%}."
    )


async def interview_next(
    tenant_id: UUID | None = None, *, session_budget: int = 3, trigger: str = "manual"
) -> dict:
    """The Interviewer agent: materialize on demand, sweep already-settled
    questions, then pick the next batch — contradictions first, must-asks,
    then the ranked backlog. 'why' strings are template-based (no LLM)."""
    handle = await runs.start_run(
        AGENT_INTERVIEWER, trigger=trigger,
        input={"session_budget": session_budget}, tenant_id=tenant_id,
    )
    try:
        await generate_questions(tenant_id)  # D3: on-demand materialization
        result, auto_answered = await _select(tenant_id, session_budget)
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle,
        output={
            "asked_question_ids": [q["id"] for q in result["questions"]],
            "auto_answered": auto_answered,
        },
    )
    return result


async def _select(tenant_id: UUID | None, session_budget: int) -> tuple[dict, int]:
    fields_by_key = await _fields_by_key(tenant_id)
    async with db.acquire(tenant_id) as conn:
        rows = [
            dict(r)
            for r in await conn.fetch(
                "SELECT id, dimension, question, field_key FROM brand_questions "
                "WHERE status='open' ORDER BY created_at ASC"
            )
        ]

    auto_answered, pending = await _sweep(tenant_id, fields_by_key, rows)

    # the sweep may have settled the last outstanding must-ask; advance the
    # tenant before the cap is applied so ambient interviewing kicks in
    # immediately (donor rule)
    await maybe_advance_onboarding(tenant_id)

    budget = session_budget
    status = await onboarding_status(tenant_id)
    if status == "onboarding":
        # cap applies ONLY during onboarding; once active, session_budget governs
        budget = min(budget, max(ONBOARDING_QUESTION_CAP - await asked_count(tenant_id), 0))

    contradicted_keys = {
        key
        for key, rows_ in fields_by_key.items()
        if any(r["status"] == FieldStatus.CONTRADICTED.value for r in rows_)
    }

    chosen: list[tuple[dict, str]] = []
    chosen_ids: set[str] = set()

    def _take(bucket: list[dict], why_of) -> None:
        for q in bucket:
            if len(chosen) >= budget:
                return
            if str(q["id"]) in chosen_ids:
                continue
            chosen_ids.add(str(q["id"]))
            chosen.append((q, why_of(q)))

    contradictions = [q for q in pending if q["field_key"] and q["field_key"] in contradicted_keys]
    contradictions.sort(key=lambda q: (-_info_value(q), _ask_priority(q)))
    _take(contradictions, _why_contradiction)

    must_asks = [
        q for q in pending
        if (t := _template(q)) and t["info_value"] == 5 and not t["researchable"]
    ]
    must_asks.sort(key=_ask_priority)
    _take(must_asks, _why_must_ask)

    backlog = [
        (q, _info_value(q) * _gap(fields_by_key.get(q["field_key"] or "", [])))
        for q in pending
        if str(q["id"]) not in chosen_ids
    ]
    backlog.sort(key=lambda t: (-t[1], _ask_priority(t[0])))
    for q, _score in backlog:
        if len(chosen) >= budget:
            break
        chosen_ids.add(str(q["id"]))
        chosen.append((q, _why_backlog(q, _gap(fields_by_key.get(q["field_key"] or "", [])))))

    questions = []
    if chosen:
        async with db.acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE brand_questions SET status='asked' WHERE id = ANY($1::uuid[])",
                [q["id"] for q, _ in chosen],
            )
    for q, why in chosen:
        questions.append({
            "id": str(q["id"]),
            "text": q["question"],
            "dimension": q["dimension"],
            "field_key": q["field_key"] or "",
            "why": why,
        })
    return {"questions": questions, "onboarding_status": status}, auto_answered


# ── ANSWERER: researched draft answers (donor: agents.answerer) ───────

PROMPT_SYSTEM = (
    "You are a brand-research assistant helping fill out a brand profile. You "
    "draft a concise, plausible answer to one interview question, grounded in "
    "the brand facts provided and any research snippets. You never invent "
    "specific numbers or names you cannot support; when the question is a "
    "matter of the owner's intent (goals, aspirations, preferences), you "
    "propose sensible options they can accept or change."
)
PROMPT = (
    "Draft a suggested answer to the interview question for this brand.\n\n"
    "QUESTION: {question}\n"
    "FIELD: {field_key}\n"
    "RESEARCHABLE: {researchable}\n\n"
    "BRAND FACTS (source-of-truth, already researched):\n{profile}\n\n"
    "RESEARCH SNIPPETS (may be empty):\n{research}\n\n"
    'Return JSON only: {{"suggestion": str, "rationale": str, '
    '"grounded": bool}}. suggestion = the proposed answer, phrased as the '
    "brand owner would state it (1-3 sentences, or a short comma-separated "
    "list for list questions). rationale = one sentence on why. grounded = "
    "true only if the suggestion is supported by the brand facts or research "
    "snippets (false when it is a reasoned proposal for an intent question)."
)


def _profile_digest(fields: list[dict]) -> str:
    lines: list[str] = []
    for f in fields:
        lines.append(f"- {f['field_key']}: {str(_field_value(f))[:200]}")
    text = "\n".join(lines)
    return text[:_PROFILE_CHARS] if text else "(no profile fields yet)"


async def _brand_name(tenant_id: UUID | None, fields: list[dict]) -> str:
    for f in fields:
        if f["field_key"] == "identity.display_name":
            v = _field_value(f)
            if v:
                return str(v)
    seed_name = (await get_research_seed(tenant_id)).get("name")
    if seed_name:
        return str(seed_name)
    from .brands import get_brand_profile
    prof = await get_brand_profile(tenant_id)
    return str(((prof or {}).get("identity") or {}).get("name") or "")


async def _draft(providers: Providers, brand_name: str, q: dict, profile_digest: str) -> dict:
    """Draft a single suggestion. Pure (no run bookkeeping, no DB writes) so
    the batch path can call it concurrently over a shared profile snapshot."""
    tmpl = _template(q)
    researchable = bool(tmpl["researchable"]) if tmpl else False
    research_lines: list[str] = []
    citations: list[str] = []
    if researchable and brand_name:
        try:
            results = await providers.search.search(
                f"{brand_name} {q['question']}", num=_MAX_SEARCHES
            )
            for r in results[:_MAX_SEARCHES]:
                research_lines.append(f"- {r.title}: {r.snippet} ({r.url})")
                if r.url:
                    citations.append(r.url)
        except Exception as exc:  # noqa: BLE001 — research is best-effort; profile still grounds it
            research_lines.append(f"(research unavailable: {str(exc)[:120]})")

    prompt = PROMPT.format(
        question=q["question"],
        field_key=q["field_key"] or "(none)",
        researchable=researchable,
        profile=profile_digest,
        research="\n".join(research_lines) or "(none)",
    )
    raw = await providers.llm.complete_json("extract", PROMPT_SYSTEM, prompt)
    suggestion = str(raw.get("suggestion") or "").strip()
    if not suggestion:
        raise RuntimeError("answerer produced no suggestion")
    return {
        "question_id": str(q["id"]),
        "field_key": q["field_key"] or "",
        "question": q["question"],
        "suggestion": suggestion,
        "rationale": str(raw.get("rationale") or "").strip(),
        "grounded": bool(raw.get("grounded")),
        "citations": citations,
    }


async def suggest_answer(tenant_id: UUID | None = None, *, question_id: UUID | str) -> dict:
    """Answerer agent: a researched suggestion for ONE question. Writes
    nothing — the human accepts/edits via answer_question."""
    handle = await runs.start_run(
        AGENT_ANSWERER, trigger="manual",
        input={"question_id": str(question_id)}, tenant_id=tenant_id,
    )
    try:
        async with db.acquire(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT id, dimension, question, field_key FROM brand_questions WHERE id=$1",
                question_id,
            )
            fields = await profile.current_fields(conn)
        if row is None:
            raise ValueError(f"question {question_id} not found")
        q = dict(row)
        result = await _draft(
            get_providers(), await _brand_name(tenant_id, fields), q, _profile_digest(fields)
        )
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle, output={"field_key": result["field_key"], "grounded": result["grounded"]}
    )
    return result


async def run_answer_batch(tenant_id: UUID | None = None) -> dict:
    """Draft suggestions for every OPEN question (status open|asked) up to a
    cap, concurrently. Writes nothing to the profile — drafts are stored on
    the batch job_runs row for the review screen; the human commits each via
    the answer endpoint. (start_run commits its own row, so a status poller
    in another request sees 'running' before the batch finishes.)"""
    handle = await runs.start_run(BATCH_AGENT, trigger="manual", tenant_id=tenant_id)
    try:
        async with db.acquire(tenant_id) as conn:
            fields = await profile.current_fields(conn)
            rows = [
                dict(r)
                for r in await conn.fetch(
                    "SELECT id, dimension, question, field_key FROM brand_questions "
                    "WHERE status IN ('open','asked')"
                )
            ]
        brand_name = await _brand_name(tenant_id, fields)
        digest = _profile_digest(fields)
        # highest-value first, capped
        rows.sort(key=lambda q: (-_info_value(q), _ask_priority(q)))
        rows = rows[:_BATCH_CAP]

        providers = get_providers()
        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def one(q: dict) -> dict:
            async with sem:
                try:
                    return await _draft(providers, brand_name, q, digest)
                except Exception as exc:  # noqa: BLE001 — one failed draft never sinks the batch
                    return {"question_id": str(q["id"]), "question": q["question"],
                            "error": str(exc)[:160]}

        drafted = await asyncio.gather(*(one(q) for q in rows))
        drafts = [d for d in drafted if "error" not in d]
        failures = [d for d in drafted if "error" in d]
        output = {
            "drafts": drafts, "count": len(drafts),
            "failures": failures, "considered": len(rows),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output=output)
    return output


# ── human answers (donor: services.onboarding.answer_question) ────────

def _parse_entries(answer: str) -> list[str]:
    """Splits a free-text list on newlines/commas/semicolons; strips bullets
    and dedupes."""
    out: list[str] = []
    seen: set[str] = set()
    for part in _SPLIT_RE.split(answer):
        cleaned = _LEAD_RE.sub("", part).strip()
        if not cleaned:
            continue
        key = cleaned.lstrip("@").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:120] or "item"


async def _seed_aspirational_peers(entries: list[str], tenant_id: UUID | None) -> list[dict]:
    """D4: the aspirational-peers answer seeds the manager watchlist with
    zero circularity. Platform 'unknown' — the Peer Agent resolves handles
    from the first successful snapshot. Names typed with spaces get a slug
    handle (display keeps the raw name); entries discovery already proposed
    are upgraded to aspirational/tracked instead of duplicated (the user's
    framing outranks the inferred kind, and naming it IS the approval gate)."""
    watchlist = await get_watchlist(tenant_id)
    existing: dict[str, dict] = {}
    for e in watchlist:
        for k in _entry_keys(e):
            existing.setdefault(k, e)
    created: list[dict] = []
    changed = False
    now = datetime.now(UTC).isoformat()
    for entry in entries:
        display = entry.lstrip("@").strip()
        if not display:
            continue
        # An account/brand name is short; a prose sentence is not. Skip
        # entries that are clearly sentences (the AI sometimes answers the
        # aspirational question in prose) so we never seed a 100-char slug
        # the Peer Agent can't resolve.
        if len(display) > 60 or len(display.split()) > 7:
            continue
        key = _handle_norm(display)
        if not key:
            continue
        if key in existing:
            existing[key]["kind"] = "aspirational"
            existing[key]["status"] = "tracked"  # user naming it approves it past the gate
            changed = True
            continue
        handle = _slug(display) if re.search(r"\s", display) else display
        peer = {
            "handle": handle,
            "platform": "unknown",
            "display_name": display,
            "status": "tracked",  # human-named = pre-approved past the discovery gate
            "kind": "aspirational",
            "reason": "named by the user as an aspirational peer in the onboarding interview",
            "discovered_at": now,
        }
        watchlist.append(peer)
        created.append(peer)
        for k in _entry_keys(peer):
            existing.setdefault(k, peer)
        changed = True
    if changed:
        await set_watchlist(watchlist, tenant_id)
    return created


async def answer_question(
    tenant_id: UUID | None = None, *, question_id: UUID | str, answer: str
) -> dict:
    """Records a human answer: profile write(s) via profile.write_field
    (source=user_stated — the only path that makes a draft a user statement),
    aspirational-peer seeding, ledger row -> 'confirmed', memory filing."""
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("no answer to confirm")
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, dimension, question, field_key, source, confidence, sources "
            "FROM brand_questions WHERE id=$1",
            question_id,
        )
    if row is None:
        raise ValueError(f"question {question_id} not found")
    q = dict(row)
    tmpl = template_for(q["question"], q["field_key"] or "")
    field_key = (tmpl["field_key"] if tmpl else q["field_key"]) or ""
    # contradiction questions carry a field_key but no template; the section
    # is the key's prefix (bm2.0 keys are '<section>.<field>')
    section = tmpl["section"] if tmpl else (field_key.split(".", 1)[0] if "." in field_key else "")

    entries = _parse_entries(answer) if field_key == ASPIRATIONAL_FIELD_KEY else []
    async with db.acquire(tenant_id) as conn:
        if field_key == ASPIRATIONAL_FIELD_KEY:
            for entry in entries:  # list field: one row per item (D1)
                await profile.write_field(
                    conn,
                    FieldWrite(
                        section=section, field_key=field_key, item_key=_slug(entry),
                        value=entry, source=Source.USER_STATED, updated_by="user",
                    ),
                )
        elif field_key and section:
            await profile.write_field(
                conn,
                FieldWrite(
                    section=section, field_key=field_key, value=answer,
                    source=Source.USER_STATED, updated_by="user",
                ),
            )
        # legacy v1 rows (no field_key) skip the envelope and just confirm
        await conn.execute(
            """UPDATE brand_questions
                  SET answer=$2, source='user', status='confirmed',
                      confidence=1.0, answered_at=now()
                WHERE id=$1""",
            question_id, answer[:2000],
        )
    if entries:
        await _seed_aspirational_peers(entries, tenant_id)

    qrow = {**q, "answer": answer, "source": "user", "confidence": 1.0}
    try:
        await _file_answer(qrow, tenant_id)
    except Exception as e:  # noqa: BLE001 — confirm never fails on filing
        print(f"[intake_agent] filing failed: {e}")

    await maybe_advance_onboarding(tenant_id)
    return {"id": str(question_id), "status": "confirmed", "field_key": field_key}


async def confirm_answer(
    question_id: UUID, answer: str | None = None, tenant_id: UUID | None = None
) -> dict:
    """Human confirms (optionally corrects) an answer — kept for main.py.
    Confirming as-is accepts the researched answer; either way the human
    action is what records it (source=user_stated in the envelope)."""
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, answer FROM brand_questions WHERE id=$1", question_id
        )
    if row is None:
        raise ValueError("question not found")
    final = (answer or "").strip() or (row["answer"] or "").strip()
    if not final:
        raise ValueError("no answer to confirm")
    return await answer_question(tenant_id, question_id=question_id, answer=final)


# ── confirmed answers become brand memory (kept from v1) ──────────────

async def _file_answer(qrow: dict, tenant_id: UUID | None) -> None:
    from .ingestion import ingest_many
    from .models import EventCreate, EventSource
    text = (f"Brand interview [{qrow['dimension']}]\n"
            f"Q: {qrow['question']}\nA: {qrow['answer']}")
    digest = hashlib.sha256(str(qrow["id"]).encode()).hexdigest()[:16]
    sources = qrow.get("sources") or []
    if isinstance(sources, str):
        sources = json.loads(sources)
    ev = EventCreate(
        event_type="note",
        payload={"text": text, "category": "reference",
                 "kind": "brand_interview", "dimension": qrow["dimension"],
                 "sources": sources},
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


# ── the ledger surface (kept names for main.py) ───────────────────────

async def research_answers(tenant_id: UUID | None = None, limit: int | None = None) -> dict:
    """v2: the auto-answer sweep. Questions whose profile field is already
    settled (confirmed user value or a current audit) flip to 'answered'
    with source='research' — no LLM, no budget burned. (Researched DRAFTS
    are run_answer_batch; they stay off the ledger until a human accepts.)"""
    fields_by_key = await _fields_by_key(tenant_id)
    async with db.acquire(tenant_id) as conn:
        rows = [
            dict(r)
            for r in await conn.fetch(
                "SELECT id, dimension, question, field_key FROM brand_questions "
                "WHERE status IN ('open','asked') AND field_key <> ''"
            )
        ]
    if limit is not None:
        rows = rows[:limit]
    answered, _remaining = await _sweep(tenant_id, fields_by_key, rows)
    await maybe_advance_onboarding(tenant_id)
    async with db.acquire(tenant_id) as conn:
        left = await conn.fetchval(
            "SELECT count(*) FROM brand_questions WHERE status IN ('open','asked')"
        )
    return {"answered": answered, "left_for_user": int(left or 0)}


async def list_questions(
    status: str = "", tenant_id: UUID | None = None, limit: int = 40,
) -> list[dict]:
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, dimension, question, answer, source, confidence,
                      sources, status, field_key, created_at
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
            "field_key": r["field_key"] or "",
        })
    return out


async def interview_stats(tenant_id: UUID | None = None) -> dict:
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n FROM brand_questions GROUP BY status")
    d = {r["status"]: int(r["n"]) for r in rows}
    return {"open": d.get("open", 0), "asked": d.get("asked", 0),
            "answered": d.get("answered", 0), "confirmed": d.get("confirmed", 0),
            "dismissed": d.get("dismissed", 0), "total": sum(d.values())}


# ── RESEARCHER: "is this your brand?" (kept from v1, unchanged) ───────

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


async def research_brand(
    name: str, hints: str = "", tenant_id: UUID | None = None,
) -> dict:
    """Type a name (+ optional hints: website, socials, city) → a proposed
    profile with sources. Nothing is saved — the operator confirms first."""
    from .llm import get_llm
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


# ── the background interview loop (scheduler job, kept name) ──────────

async def run_brand_interview(tenant_id: UUID, config: dict | None = None) -> None:
    """Each run: materialize any not-yet-asked bank templates (cheap, no
    LLM), then sweep questions the envelope has since settled. Runs on a
    cadence after intake completes — costs stay near zero because the bank
    is finite and the sweep is pure SQL over the profile."""
    from .brands import get_brand_profile
    prof = await get_brand_profile(tenant_id)
    if not prof or not prof.get("intake_done"):
        return
    made = await generate_questions(tenant_id)
    res = await research_answers(tenant_id)
    print(f"[intake_agent] tenant {tenant_id}: +{made} questions materialized, "
          f"{res['answered']} auto-answered by the profile sweep")


__all__ = [
    # kept v1 surface (main.py wires these)
    "research_brand", "generate_questions", "research_answers",
    "confirm_answer", "list_questions", "interview_stats",
    "run_brand_interview", "DIMENSIONS",
    # v2 interview system
    "interview_next", "suggest_answer", "run_answer_batch", "answer_question",
    "asked_count", "maybe_advance_onboarding", "onboarding_status",
    "get_research_seed", "set_research_seed",
    "AGENT_INTERVIEWER", "AGENT_ANSWERER", "BATCH_AGENT",
    "ONBOARDING_QUESTION_CAP", "ASPIRATIONAL_FIELD_KEY",
]
