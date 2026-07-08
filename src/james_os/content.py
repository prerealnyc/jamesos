"""The content engine — the half that consumes memory to produce voice.

ask() refuses unless memory answers a question. This does the opposite
job: it WRITES. But it writes in the brand's voice, and the voice is not
the LLM's — it's assembled from the memory substrate:

    brief
      │
      ▼
  assemble_memory ── category-weighted retrieval
      │   voice_exemplars + thesis  → how the brand sounds / believes
      │   research + reference      → facts it may cite
      │   frustration ledger        → hard "never do this" guardrails
      │   plug-in rules (verbatim)  → non-negotiable constraints
      ▼
  generate (LLM #1)  → on-voice draft + grounded event ids
      │
      ▼
  voice-QA (LLM #2, independent)  → 0-1 voice score + drift notes
      │
      ▼
  actions table (status=pending)  → a human approves before anything ships

Honesty rules baked in:
  * If there is no voice/thesis material in memory, the engine REFUSES
    rather than emit generic text dressed up as on-voice. Nothing is
    queued in that case.
  * The QA pass is a real second LLM call that did not write the draft —
    not a hardcoded score. Below the floor the draft is still queued for
    a human but flagged, never silently shipped.
  * Nothing auto-publishes. The pending row in `actions` is the gate the
    existing approval queue already enforces.
  * Quality is bounded by what's ingested. `memory_used` is returned so
    the human sees exactly how thin/rich the voice grounding was.
"""

import json
import re
import time
from uuid import UUID

from .brand_kit import get_brand_kit
from .config import settings
from .db import acquire
from .llm import get_llm
from .manager import review as manager_review
from .models import ContentBrief, ContentDraft, QAVerdict, RetrievedEvent
from .prompts import (
    build_content_system_prompt,
    build_qa_messages,
    format_content_memory,
    VOICE_QA_PROMPT,
)
from .rerank import rerank
from .retrieval import search

# Internal vocabulary that must NEVER reach audience-facing copy. James:
# "'just james clip' is a vertical of content we talk about in the backend —
# not something james says out loud to the audience." Catches separated,
# token and CONCATENATED/hashtag forms (#JustJamesClip) — but the bare
# spoken bigram "James clips …" only when it carries a label signal
# (quotes / colon / hashtag), so verb phrases are never mangled.
_INTERNAL_LABEL_RE = re.compile(
    r"""
      [\(\[\"'“”‘’#]*\b(?:
          just[\s_-]*james[\s_-]*clips?     # Just James Clip / JustJamesClip
        | james[_-]clips?                   # james_clip / james-clip tokens
        | jamesclips?                       # JamesClip / jamesclip run-together
      )\b[\)\]\"'“”‘’]*[:,]?
    | [\(\[\"'“”‘’]\s*james\s+clips?\s*[\)\]\"'“”‘’][:,]?   # “James Clips”
    | \bjames\s+clips?\s*:                                    # James Clips: prefix
    | \#\s*james\s+clips?\b                                   # '# james clip'
    """,
    re.IGNORECASE | re.VERBOSE,
)


def strip_internal_labels(text: str) -> str:
    """Remove backend/vertical labels from audience-facing copy and tidy the
    whitespace/punctuation the removal leaves behind."""
    if not text:
        return text
    out = _INTERNAL_LABEL_RE.sub(" ", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?;:])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ── bm2.0 text-hands MERGE (additive; exercised only for the new formats) ──
# Ported verbatim from bm2.0 backend/app/agents/hands.py: the long-form text
# formats james-os never built (blog, email). When a brief asks for one of
# these, its donor instruction block is appended to the existing system
# prompt; every other format's prompt is byte-identical to pre-merge.

# The Reviewer's D7 lint is zero-tolerance (manager/ai_isms): no em dashes and
# no marketing/AI clichés. Steer every hand to write lint-clean on the first
# pass so it clears the gate instead of bouncing to the revise loop.
_HANDS_HOUSE_STYLE = (
    " House style (mandatory): plain, direct, specific language, like a sharp human expert. "
    "Do NOT use em dashes (—); use commas, periods, or parentheses. Never use clichés or AI-isms "
    "such as: delve, seamless, leverage, elevate, unlock, supercharge, robust, holistic, synergy, "
    "game-changer, cutting-edge, world-class, unparalleled, transformative, actionable insights, "
    "key takeaways, thought leadership, 'in today's ... world', 'it's worth noting', 'in conclusion', "
    "'at the end of the day', 'at its core'. Ground every claim in the facts given; never invent numbers. "
    "Write STRICTLY in the brand's own voice exactly as specified in the prompt — match it, and never "
    "fall back to a generic or AI voice."
)

# format -> donor per-format instruction block (hands.py _SYSTEM), appended to
# the system prompt ONLY when that format is requested.
_HANDS_FORMAT_SYSTEM: dict[str, str] = {
    "blog": (
        "You are the brand's blog writer. Write the article exactly as it should be published: a clear "
        "headline on the first line, then the body in the brand's voice, concrete and grounded in the "
        "facts given. Never write placeholders or meta commentary that describes an article instead of "
        "being one." + _HANDS_HOUSE_STYLE
    ),
    "email": (
        "You are the brand's email writer. Write the email body exactly as it should be sent: a warm, "
        "concrete note in the brand's voice that pays off the subject and gives the reader one real thing. "
        "Never write placeholders or meta commentary." + _HANDS_HOUSE_STYLE
    ),
}

# PRD R5.2 — the 'rewrite this' door (hands.py, verbatim): an external draft
# goes in, the brand's voice comes out. Facts stay; the voice changes.
_REWRITE_INSTRUCTION = (
    "REWRITE the following external draft. Keep its facts, claims, and intent; replace the "
    "voice entirely so it reads like the brand wrote it (never like AI or a ghostwriter):\n"
)


def _hands_slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "post")[:60]


def _format_fields(fmt: str, topic: str, body: str) -> dict:
    """Format-specific fields the publish step needs (donor hands.py _media):
    blog = title + body_markdown + meta (slug/meta_description/tags), email =
    subject + preheader + body. Kept alongside the queued draft."""
    if fmt == "blog":
        title = (body.splitlines()[0].strip() if body.strip() else topic) or topic
        title = title.lstrip("# ").strip()[:200]
        rest = body.split("\n", 1)[1].strip() if "\n" in body else body
        return {
            "title": title,
            "slug": _hands_slug(title or topic),
            "meta_description": rest[:155],
            "tags": [],
            "links": [],
        }
    if fmt == "email":
        subject = topic.strip()
        return {"subject": subject[:150], "preheader": subject[:120], "links": []}
    return {}


def _extract_rewrite(extra_instructions: str) -> tuple[str | None, str]:
    """Pull a rewrite_of payload out of brief.extra_instructions (ContentBrief
    is frozen upstream, so the payload rides the existing free-text field).
    Accepted shapes: a JSON object {"rewrite_of": "...", "extra_instructions":
    "..."} or a 'rewrite_of:' prefix followed by the external draft. Returns
    (rewrite_text | None, remaining extra instructions). Anything else passes
    through untouched — the pre-merge path stays byte-identical."""
    raw = (extra_instructions or "").strip()
    if not raw:
        return None, extra_instructions
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None, extra_instructions
        if isinstance(data, dict) and str(data.get("rewrite_of") or "").strip():
            return (
                str(data["rewrite_of"]).strip(),
                str(data.get("extra_instructions") or data.get("instructions") or "").strip(),
            )
        return None, extra_instructions
    prefix = "rewrite_of:"
    if raw.lower().startswith(prefix):
        rewrite = raw[len(prefix):].strip()
        return (rewrite or None), ""
    return None, extra_instructions


# event payload.category (and event_type) → memory bucket.
_VOICE_CATS = {"voice_corpus", "guideline"}
_THESIS_CATS = {"thesis"}
_FRUSTRATION_CATS = {"frustration"}
_FACT_CATS = {"research", "reference", "trend"}


def _bucket_of(ev: RetrievedEvent) -> str:
    cat = (ev.payload or {}).get("category", "")
    if ev.event_type == "voice_memo" or cat in _VOICE_CATS:
        return "voice"
    if cat in _THESIS_CATS:
        return "thesis"
    if cat in _FRUSTRATION_CATS:
        return "frustration"
    if cat in _FACT_CATS:
        return "facts"
    # Uncategorised / older memory is still usable as grounding context.
    return "facts"


async def assemble_memory(
    brief: ContentBrief, tenant_id: UUID | None
) -> tuple[dict[str, list[RetrievedEvent]], dict[str, int]]:
    """Two retrievals: topic-relevant facts, and voice-anchored material.
    Merged, deduped, bucketed by category."""
    topic_q = " ".join(
        x for x in (brief.topic, brief.research_subject, brief.pillar) if x
    ).strip()
    voice_q = (
        f"brand voice tone style point of view how it sounds "
        f"{brief.pillar} {brief.topic}"
    ).strip()

    topic_hits = await rerank(topic_q, await search(topic_q, tenant_id=tenant_id))
    # Voice/thesis must not be reranked against the topic — it would bury
    # the voice signal. Take it on retrieval score.
    voice_hits = (await search(voice_q, tenant_id=tenant_id))[
        : settings.retrieval_top_k_after_rerank
    ]

    seen: set[UUID] = set()
    buckets: dict[str, list[RetrievedEvent]] = {
        "voice": [], "thesis": [], "frustration": [], "facts": []
    }
    # Recent human rejections are authoritative guardrails — always include
    # them, even if semantic retrieval didn't surface them, so the engine
    # cannot repeat a freshly-flagged mistake.
    for ev in await _recent_frustrations(tenant_id):
        if ev.event_id in seen:
            continue
        seen.add(ev.event_id)
        buckets["frustration"].append(ev)
    # Diverse, real voice exemplars (random voice-corpus samples) so the
    # model sees James's actual cadence — not just near-duplicates of a
    # generic "brand voice" query.
    exemplars = await _voice_exemplars(tenant_id, settings.content_voice_exemplars)
    # Brand guideline docs (e.g. BRAND GUIDELINES.docx) are RULES that must
    # apply to every draft — text post AND video script — not just when
    # semantic search happens to surface them. Always anchor them (placed
    # first so the per-bucket cap never crowds them out), exactly like the
    # frustration ledger is always included.
    guides = await _brand_guidelines(tenant_id)
    for ev in [*guides, *exemplars, *voice_hits, *topic_hits]:
        if ev.event_id in seen:
            continue
        seen.add(ev.event_id)
        buckets[_bucket_of(ev)].append(ev)

    # Cap each bucket so one huge category can't crowd the prompt.
    buckets["voice"] = buckets["voice"][: settings.content_voice_k]
    buckets["thesis"] = buckets["thesis"][: settings.content_voice_k]
    buckets["facts"] = buckets["facts"][: settings.content_facts_k]
    buckets["frustration"] = buckets["frustration"][: settings.content_voice_k]

    used = {k: len(v) for k, v in buckets.items()}
    return buckets, used


async def _voice_exemplars(
    tenant_id: UUID | None, limit: int = 4
) -> list[RetrievedEvent]:
    """Voice-corpus samples that ANCHOR the prompt on the brand's explicit
    voice profile, then add real, varied cadence from the rest of the corpus.

    Why not pure random: the corpus mixes a distilled voice-profile spec
    (the canonical 'how this brand sounds') with long-form material (e.g.
    academy transcripts). A flat `ORDER BY random()` surfaces the profile
    only as often as its share of rows, so most drafts get grounded on
    random mid-paragraphs and drift to generic voice. We always include a
    few profile chunks as the anchor, then fill with cadence samples.
    """
    if limit <= 0:
        return []
    # Heuristic: a doc whose filename marks it as the voice profile/spec.
    _PROFILE = (
        "(coalesce(payload->>'filename','') ILIKE '%voice_profile%' "
        "OR coalesce(payload->>'filename','') ILIKE '%brand_voice%' "
        "OR coalesce(payload->>'filename','') ILIKE '%voice_spec%')"
    )
    async with acquire(tenant_id) as conn:
        # Approved exemplars — drafts a human blessed (positive feedback loop).
        # Strongest "make more like this" signal; newest first.
        approved = await conn.fetch(
            """
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'voice_corpus'
              AND superseded_by IS NULL
              AND payload ->> 'source' = 'approved_exemplar'
              AND length(raw_content) > 60
            ORDER BY created_at DESC LIMIT 3
            """
        )
        anchors = await conn.fetch(
            f"""
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'voice_corpus'
              AND superseded_by IS NULL
              AND length(raw_content) > 120
              AND {_PROFILE}
            ORDER BY random() LIMIT 3
            """
        )
        cadence = await conn.fetch(
            f"""
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'voice_corpus'
              AND superseded_by IS NULL
              AND length(raw_content) > 200
              AND coalesce(payload ->> 'source','') <> 'approved_exemplar'
              AND NOT {_PROFILE}
            ORDER BY random() LIMIT $1
            """,
            limit,
        )
    # Approved exemplars + profile anchors FIRST so they win the bucket cap;
    # cadence fills the rest. Falls back to all-random when neither exists.
    rows = [*approved, *anchors, *cadence]
    out: list[RetrievedEvent] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            RetrievedEvent(
                event_id=r["id"], event_type=r["event_type"],
                raw_content=r["raw_content"], payload=payload,
                effective_at=r["effective_at"], score=1.0,
                source_signal=["voice_exemplar"],
            )
        )
    return out


async def _brand_guidelines(
    tenant_id: UUID | None, limit: int = 4
) -> list[RetrievedEvent]:
    """The brand's uploaded guideline docs (category='guideline'), fetched by
    recency and ALWAYS included — so every draft (post text AND video script)
    follows the brand rules, not only when semantic retrieval surfaces them.
    Mirrors _recent_frustrations: authoritative, not search-dependent."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'guideline'
              AND superseded_by IS NULL
              AND length(raw_content) > 80
            ORDER BY created_at DESC LIMIT $1
            """,
            limit,
        )
    out: list[RetrievedEvent] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            RetrievedEvent(
                event_id=r["id"], event_type=r["event_type"],
                raw_content=r["raw_content"], payload=payload,
                effective_at=r["effective_at"], score=1.0,
                source_signal=["brand_guideline"],
            )
        )
    return out


async def _recent_frustrations(
    tenant_id: UUID | None, limit: int = 8
) -> list[RetrievedEvent]:
    """The newest human-rejection guardrails, fetched by recency (not
    semantic match) so a just-recorded rejection always reaches the prompt."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, event_type, raw_content, payload, effective_at
            FROM events
            WHERE payload ->> 'category' = 'frustration'
              AND superseded_by IS NULL
            -- Explicit human rejections outrank edit-derived rules so the
            -- more-frequent edits can never evict a rejection guardrail from
            -- this capped window; recency breaks ties within each group.
            ORDER BY (payload ->> 'source' = 'rejection_feedback') DESC,
                     created_at DESC
            LIMIT $1
            """,
            limit,
        )
    out: list[RetrievedEvent] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            RetrievedEvent(
                event_id=r["id"],
                event_type=r["event_type"],
                raw_content=r["raw_content"],
                payload=payload,
                effective_at=r["effective_at"],
                score=1.0,
                source_signal=["recency"],
            )
        )
    return out


def _coerce_ids(raw: object) -> list[UUID]:
    out: list[UUID] = []
    for x in raw or []:
        try:
            out.append(UUID(str(x)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


def apply_caption_signoff(text: str, signoff: str) -> str:
    """Append the brand caption sign-off as a final line — idempotently, so a
    re-run or an already-signed draft never doubles it. Empty text/sign-off is
    a no-op."""
    t = (text or "").rstrip()
    s = (signoff or "").strip()
    if not t or not s:
        return text
    # Idempotent across any trailing punctuation/quotes ('one?', 'one…', 'one"').
    _trail = " .!?…\"'’"
    if t.lower().rstrip(_trail).endswith(s.lower().rstrip(_trail)):
        return t
    return f"{t}\n\n{s}"


_VIDEO_CAPTION_SYSTEM = (
    "You write the SOCIAL CAPTION that accompanies a short video by a "
    "real-estate broker who teaches mindset, ownership and accountability. "
    "Given the video's spoken content, write ONE scroll-stopping caption in "
    "his first-person voice: a strong opening line, 1-3 short sentences total, "
    "no emoji spam, at most a couple of relevant hashtags, no surrounding "
    "quotes. Do NOT add a sign-off line — that is appended separately.\n\n"
    'Return STRICT JSON: {"caption": "<the caption>"}'
)


async def gen_video_caption(
    source_text: str, platform: str = "instagram", tenant_id: UUID | None = None
) -> str:
    """A relevant social caption for a finished VIDEO, derived from its spoken
    content/hook and ending with the brand sign-off. Best-effort: falls back to
    the first line of the source + sign-off if the LLM is unavailable."""
    kit = await get_brand_kit(tenant_id)
    signoff = kit.get("caption_signoff") or ""
    src = (source_text or "").strip()
    base = ""
    if src:
        try:
            out = await get_llm().complete_json(
                system=_VIDEO_CAPTION_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": f"Platform: {platform}\nVideo content:\n{src[:1800]}",
                }],
                max_tokens=300, temperature=0.6,
            )
            base = str((out or {}).get("caption") or "").strip().strip('"')
        except Exception:  # noqa: BLE001 — fall back to a trimmed source line
            base = ""
    if not base:
        base = (src.split(". ")[0] if src else "").strip()[:180]
    return apply_caption_signoff(strip_internal_labels(base), signoff)


_VIDEO_HOOK_SYSTEM = (
    "You write the 3-second ON-SCREEN HOOK for a short reel — the big bold text "
    "that stops the scroll. Given the video's spoken content, return ONE punchy "
    "hook of AT MOST 7 words: a bold question or claim that creates curiosity. "
    "No hashtags, no emoji, no surrounding quotes, no trailing period. Plain "
    "words.\n\n"
    'Return STRICT JSON: {"hook": "<the hook>"}'
)


async def gen_video_hook(source_text: str, tenant_id: UUID | None = None) -> str:
    """A SHORT punchy on-screen hook (<= 7 words) for a reel, from its spoken
    content. Best-effort: falls back to the first few words of the source."""
    src = (source_text or "").strip()
    if not src:
        return ""
    try:
        out = await get_llm().complete_json(
            system=_VIDEO_HOOK_SYSTEM,
            messages=[{"role": "user", "content": src[:1500]}],
            max_tokens=60, temperature=0.7,
        )
        hook = str((out or {}).get("hook") or "").strip().strip('"').rstrip(".")
        hook = strip_internal_labels(hook)
        if hook:
            return " ".join(hook.split()[:8])
    except Exception:  # noqa: BLE001 — fall back to a trimmed source line
        pass
    return strip_internal_labels(" ".join(src.split()[:6]))


async def generate_content(
    brief: ContentBrief, tenant_id: UUID | None = None
) -> ContentDraft:
    started = time.perf_counter()
    llm = get_llm()

    buckets, used = await assemble_memory(brief, tenant_id)

    def _draft(status: str, **kw) -> ContentDraft:
        return ContentDraft(
            status=status,
            draft=kw.get("draft", ""),
            platform=brief.platform,
            format=brief.format,
            pillar=brief.pillar,
            angle=kw.get("angle", ""),
            voice_score=kw.get("voice_score", 0.0),
            qa=kw.get("qa"),
            grounded_event_ids=kw.get("grounded_event_ids", []),
            memory_used=used,
            action_id=kw.get("action_id"),
            model=llm.model_name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            note=kw.get("note"),
        )

    # No voice grounding at all → refuse honestly, queue nothing.
    if not buckets["voice"] and not buckets["thesis"]:
        return _draft(
            "not_generated",
            note=(
                "No voice corpus or thesis in memory for this tenant — the "
                "engine will not fabricate a generic post and call it "
                "on-voice. Ingest voice_corpus / thesis material in Settings, "
                "then regenerate."
            ),
        )

    # bm2.0 rewrite door (R5.2): a rewrite_of payload in extra_instructions
    # switches this run into rewrite mode; the remaining instructions (if
    # any) still reach the brief. No payload → byte-identical to pre-merge.
    rewrite_of, extra_instructions = _extract_rewrite(brief.extra_instructions)

    # ── LLM #1: generate ──
    system = await build_content_system_prompt(
        brief.platform, brief.format, tenant_id
    )
    # bm2.0 text-hands formats (blog/email): append the donor's per-format
    # instruction block. Existing formats never enter this branch.
    if brief.format in _HANDS_FORMAT_SYSTEM:
        system = f"{system}\n\n{_HANDS_FORMAT_SYSTEM[brief.format]}"
    brief_block = (
        f"<brief>\n"
        f"platform: {brief.platform}\n"
        f"format: {brief.format}\n"
        f"pillar: {brief.pillar or '(none specified)'}\n"
        f"topic: {strip_internal_labels(brief.topic)}\n"
        f"extra_instructions: {extra_instructions or '(none)'}\n"
        f"</brief>"
    )
    if rewrite_of:
        brief_block += f"\n\n{_REWRITE_INSTRUCTION}{str(rewrite_of)[:4000]}"
    memory_block = format_content_memory(buckets)
    try:
        gen = await llm.complete_json(
            system=system,
            messages=[{"role": "user", "content": f"{memory_block}\n\n{brief_block}"}],
            max_tokens=2000,
            temperature=0.7,  # voice work needs some range; QA is the gate
        )
    except Exception as e:  # noqa: BLE001
        return _draft("not_generated", note=f"generation failed: {e}")

    draft_text = (gen.get("draft") or "").strip()
    if gen.get("refused") or not draft_text:
        return _draft(
            "not_generated",
            note=gen.get("refusal_reason")
            or "model did not return a draft (stub LLM, or refused)",
        )

    grounded = _coerce_ids(gen.get("grounded_event_ids"))
    angle = (gen.get("angle") or "").strip()

    # ── LLM #2: independent voice-QA ──
    floor = settings.content_voice_floor
    qa_raw: dict = {}
    try:
        qa_raw = await llm.complete_json(
            system=VOICE_QA_PROMPT.format(floor=floor),
            messages=build_qa_messages(draft_text, buckets),
            max_tokens=600,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 — QA failure must not lose the draft
        qa_raw = {}

    score = float(qa_raw.get("voice_score", 0.0) or 0.0)
    drift = [str(d) for d in (qa_raw.get("drift") or [])]
    passed = bool(qa_raw.get("passed", score >= floor)) and score >= floor

    # Brand caption sign-off — every caption ends on the same note. Applied
    # AFTER voice-QA so the boilerplate never sways the voice score, and it
    # flows into content, caption and the returned draft below. (Skipped for
    # the merged long-form formats — a caption sign-off is not a blog/email
    # signature; those formats never existed pre-merge.)
    if brief.format not in _HANDS_FORMAT_SYSTEM:
        try:
            _kit = await get_brand_kit(tenant_id)
            draft_text = apply_caption_signoff(draft_text, _kit.get("caption_signoff"))
        except Exception:  # noqa: BLE001 — never lose the draft over a brand read
            pass

    # Hard firewall: internal vocabulary must never reach the audience, no
    # matter which input carried it in (topic, memory, or the model itself).
    draft_text = strip_internal_labels(draft_text)

    # ── bm2.0 reviewer legs (D7): deterministic lint + profile guardrails ──
    # Runs on the FINAL text, after their voice-QA. A violation flags the
    # draft (never silently ships, matching content_voice_floor semantics)
    # but changes nothing else: pass/fail for existing formats is untouched
    # unless a violation actually fires.
    if brief.format in _HANDS_FORMAT_SYSTEM:
        # donor hands.py post-pass: strip em/en dashes so a good long-form
        # draft isn't bounced on punctuation alone (voice stays the gate)
        draft_text = manager_review.lint_clean(draft_text)
    lint_violations = manager_review.lint(draft_text)
    guardrail_violations: list[str] = []
    try:
        async with acquire(tenant_id) as conn:
            guardrail_violations = await manager_review.guardrail_check(conn, draft_text)
    except Exception:  # noqa: BLE001 — guardrail leg is best-effort here; lint still gates
        pass
    review_flags = [
        *(f"lint: {v}" for v in lint_violations),
        *(f"guardrail: {v}" for v in guardrail_violations),
    ]
    if review_flags:
        drift = [*drift, *review_flags]

    qa = QAVerdict(voice_score=score, passed=passed, drift=drift)
    flagged = (not passed) or bool(review_flags)
    status = "generated" if not flagged else "flagged"

    # ── queue as a pending action (the human gate) ──
    payload = {
        "platform": brief.platform,
        "pillar": brief.pillar,
        "format": brief.format,
        "content": draft_text,
        "caption": draft_text,
        "topic": strip_internal_labels(brief.topic),
        "angle": angle,
        "voice_score": round(score, 3),
        "self_voice_score": gen.get("self_voice_score"),
        "qa_passed": passed,
        "qa_drift": drift,
        "grounded_event_ids": [str(g) for g in grounded],
        "memory_used": used,
        "flagged": flagged,
    }
    # Additive annotations — present only when the new merge paths fired, so
    # pre-merge payloads keep their exact shape.
    if lint_violations:
        payload["lint_violations"] = lint_violations
    if guardrail_violations:
        payload["guardrail_violations"] = guardrail_violations
    if brief.format in _HANDS_FORMAT_SYSTEM:
        payload["format_fields"] = _format_fields(
            brief.format, strip_internal_labels(brief.topic), draft_text
        )
    if rewrite_of:
        payload["rewrite_of"] = str(rewrite_of)[:4000]
    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """
            INSERT INTO actions (proposed_by, action_type, payload, status)
            VALUES ('content_engine', 'content', $1::jsonb, 'pending')
            RETURNING id
            """,
            json.dumps(payload),
        )

    note = None
    if not passed:
        note = (
            f"Voice-QA scored {score:.2f} (floor {floor:.2f}) — queued but "
            f"flagged for revision. A human still decides in the queue."
        )
    elif used["voice"] + used["thesis"] <= 1:
        note = (
            "Thin voice grounding (≤1 voice/thesis event in memory). It "
            "passed QA, but ingest more voice corpus for stronger fidelity."
        )
    if review_flags:
        review_note = (
            f"Reviewer flagged {len(review_flags)} violation(s): "
            + "; ".join(review_flags[:5])
            + " — queued but flagged for revision, never silently shipped."
        )
        note = f"{note} {review_note}" if note else review_note

    return _draft(
        status,
        draft=draft_text,
        angle=angle,
        voice_score=score,
        qa=qa,
        grounded_event_ids=grounded,
        action_id=action_id,
        note=note,
    )


__all__ = ["generate_content", "assemble_memory"]
