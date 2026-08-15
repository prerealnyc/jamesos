"""Academy — dump lessons + documents, generate a campaign (doc → campaign).

A James-only content engine (the app is single-tenant, so "James-only" is
inherent). James dumps lessons and source documents; they land in the 'academy'
silo of the brand's memory. Then, for a topic, the engine generates a multi-piece
content campaign GROUNDED on those lessons, on any PreReal Intelligence pulled
via the plug, and on the brand's own voice — never invented. The campaign is
returned and filed back to memory (category:campaign) so it is itself recallable.

Honest by construction: generation is strictly from retrieved grounding; when
there is nothing to ground on (or no real LLM is connected), it says so instead
of fabricating a campaign.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire
from .ingestion import ingest_many
from .knowledge import ingest_knowledge_document
from .llm import get_llm
from .models import EventCreate, EventSource
from .retrieval import search

ACADEMY_SILO = "academy"
CAMPAIGN_CATEGORY = "campaign"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "lesson"


async def add_academy_source(*, title: str, content: str, tenant_id: UUID | None = None) -> dict:
    """Dump one lesson / source doc (text) into the academy silo — filed + indexed
    into memory so campaigns can ground on it."""
    body = (content or "").strip()
    if not body:
        raise ValueError("content is required")
    name = f"academy_{_slug(title or 'lesson')}.txt"
    return await ingest_knowledge_document(
        data=body.encode("utf-8"),
        original_name=name,
        mime="text/plain",
        category="company_doc",
        notes=f"academy_source:{(title or '').strip()}",
        auto_classify=False,
        tenant_id=tenant_id,
        silo_id=ACADEMY_SILO,
    )


async def list_academy_sources(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, filename, chunks, created_at FROM document_metadata "
            "WHERE silo_id = $1 ORDER BY created_at DESC LIMIT 200",
            ACADEMY_SILO,
        )
    return [dict(r) for r in rows]


async def _brand_voice(tenant_id: UUID | None) -> str:
    try:
        from .brands import get_brand_profile
        prof = await get_brand_profile(tenant_id)
    except Exception:  # noqa: BLE001
        prof = None
    if not prof:
        return ""
    bits = []
    for k in ("voice", "tone", "positioning", "audience", "about", "tagline", "display_name"):
        v = prof.get(k) if isinstance(prof, dict) else None
        if v:
            bits.append(f"{k}: {v}")
    return "\n".join(bits)


_SYSTEM = (
    "You are the content strategist for a single brand. From the brand's OWN lessons, "
    "intelligence, and voice provided below, produce a concrete content campaign on the "
    "requested topic. Ground everything in the provided material — do NOT invent facts, "
    "statistics, or claims. If the grounding is too thin to build a real campaign, say so "
    "in the note and return few or no pieces rather than fabricating. Match the brand voice "
    "when one is given.\n\n"
    "Respond as JSON:\n"
    '{\n'
    '  "concept": "<one-line campaign concept>",\n'
    '  "theme": "<the through-line tying the pieces together>",\n'
    '  "pieces": [\n'
    '    {"title":"","angle":"","hook":"","body":"","format":"<post|reel-script|email|carousel|blog>","cta":""}\n'
    '  ],\n'
    '  "note": "<grounding caveats, or what more would strengthen it>"\n'
    '}\n'
    "No preamble; JSON only."
)


async def generate_campaign(
    topic: str,
    *,
    silo: str | None = None,
    pieces: int = 5,
    channel: str = "mixed",
    extra_context: str = "",
    tenant_id: UUID | None = None,
) -> dict:
    """Generate a grounded content campaign from the academy lessons + intelligence."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required")
    n = max(1, min(pieces, 12))

    # Ground on the brand's memory: the topic pulls in academy lessons + (if a silo
    # is named) that project's pulled PRI intelligence, all from the same substrate.
    hits = await search(f"{topic} {silo or ''}".strip(), tenant_id=tenant_id)
    context = "\n\n".join(h.raw_content[:900] for h in hits[:8])
    voice = await _brand_voice(tenant_id)

    user = (
        f"TOPIC: {topic}\nCHANNEL: {channel}\nNUMBER OF PIECES: {n}\n\n"
        f"OUR LESSONS & INTELLIGENCE (ground on this):\n{context or '(nothing found in memory yet)'}\n\n"
        f"BRAND VOICE:\n{voice or '(no voice profile set)'}"
        + (f"\n\nADDITIONAL DIRECTION:\n{extra_context}" if extra_context.strip() else "")
    )

    try:
        out = await get_llm().complete_json(
            system=_SYSTEM, messages=[{"role": "user", "content": user}],
            max_tokens=2500, temperature=0.4,
        )
    except Exception as e:  # noqa: BLE001
        return {"topic": topic, "silo": silo, "grounded_on": len(hits),
                "campaign": None, "filed": False, "error": str(e)}

    raw_pieces = out.get("pieces") if isinstance(out.get("pieces"), list) else []
    campaign = {
        "concept": (out.get("concept") or out.get("answer") or "").strip(),
        "theme": (out.get("theme") or "").strip(),
        "pieces": [p for p in raw_pieces if isinstance(p, dict)][:n],
        "note": (out.get("note") or out.get("refusal_reason") or "").strip(),
    }

    # File the campaign back to memory so it is itself recallable.
    filed = False
    if campaign["pieces"] or campaign["concept"]:
        text = f"Campaign — {topic}\nConcept: {campaign['concept']}\nTheme: {campaign['theme']}\n\n" + "\n\n".join(
            f"[{p.get('format','piece')}] {p.get('title','')}\nHook: {p.get('hook','')}\n{p.get('body','')}\nCTA: {p.get('cta','')}"
            for p in campaign["pieces"]
        )
        digest = hashlib.sha256(f"campaign|{topic}|{text}".encode()).hexdigest()[:16]
        ev = EventCreate(
            event_type="document",
            payload={"text": text, "subject": topic, "category": CAMPAIGN_CATEGORY,
                     "silo": silo, "kind": "campaign"},
            raw_content=text,
            source=EventSource(adapter="academy", uri=None, dedupe_key=f"campaign-{digest}",
                               raw_metadata={"category": CAMPAIGN_CATEGORY, "topic": topic, "silo": silo}),
            entities=[f"subject:{topic}", f"category:{CAMPAIGN_CATEGORY}",
                      *([f"silo:{silo}"] if silo else [])],
            effective_at=datetime.now(UTC), confidence=0.7,
        )
        stored = await ingest_many([ev], tenant_id=tenant_id)
        filed = bool(stored)

    return {"topic": topic, "silo": silo, "grounded_on": len(hits),
            "campaign": campaign, "filed": filed}
