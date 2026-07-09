"""AI auto-filing — ported from the PreReal Intelligence platform's
classifier. Given a document's name + a text excerpt, the model assigns the
metadata needed to file it (silo, entity, doc type, business unit, descriptor,
date, sensitivity) choosing ONLY from the real vocabularies and the known
silos/entities. Low confidence or no match leaves entity/silo null so the doc
lands flagged for review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from .llm import get_llm
from .naming import safe_descriptor
from .vocab import (
    ASSET_CLASSES,
    BU_CODES,
    DEFAULT_SENSITIVITY,
    DOC_TYPES,
    ENTITY_TYPE_CODES,
    SENSITIVITIES,
    STATUSES,
)

LOW_CONFIDENCE = 0.45


@dataclass
class Classification:
    business_unit: str
    asset_class: str
    doc_type: str
    descriptor: str
    date: str | None
    status: str
    sensitivity: str
    silo_id: str | None
    entity_id: str | None       # matched an existing registered entity
    entity_name: str | None     # proposed name for a NEW entity when nothing matched
    entity_type_code: str
    confidence: float
    reason: str


_SYSTEM = """You are the filing clerk for the brand's knowledge base. Given a document's filename and a text excerpt, assign the metadata needed to file it so it can be retrieved later.

Choose values ONLY from the provided vocabularies. Match the silo to a KNOWN silo only when the document clearly belongs to it; otherwise leave it null. Pick the single best doc type.

ENTITY — the specific subject the document is about (a project, property, deal, company, or person):
- If it clearly matches one of the KNOWN ENTITIES, set "entityId" to that id and leave "entityName" null.
- Otherwise, if it clearly concerns a specific, nameable subject NOT in the known list, set "entityName" to a concise canonical name for it (e.g. "Spaceport America") and "entityType" to the best-fitting type code — leave "entityId" null. The system will register it.
- Only if there is no specific nameable subject at all, leave BOTH "entityId" and "entityName" null.
Entity type codes: {type_codes} (H=Hotel, P=Parcel, L=Listing, D=Deal/Project, C=Contact/Person, E=Entity/LLC, PR=PressMention, S=Speech, B=Bio, Pod=Podcast, Art=Article, Cse=Course). When unsure, use D.

Output STRICT JSON, no prose:
{{"businessUnit":"<BU code>","assetClass":"<asset class>","docType":"<doc type>","descriptor":"<<=30 chars, letters/numbers/hyphens, no spaces>","date":"YYYY-MM-DD or null","status":"<status>","sensitivity":"<sensitivity>","siloId":"<silo id or null>","entityId":"<known entity id or null>","entityName":"<name for a new entity, or null>","entityType":"<entity type code>","confidence":0.0,"reason":"one short sentence"}}

confidence is your overall certainty (0-1). Use the document's own date if stated; else null. descriptor should be a short, specific label for the document's subject."""


def _one_of(value, allowed: list[str], fallback: str) -> str:
    s = str(value or "").strip()
    return s if s in allowed else fallback


def _clamp_sensitivity(tier: str) -> str:
    """The auto-classifier may RAISE sensitivity but must never LOWER it below
    the safe default without human sign-off — a document whose own (attacker-
    controllable) text says 'cleared for public release' cannot silently
    downgrade itself. SENSITIVITIES is ordered least->most restrictive."""
    try:
        return SENSITIVITIES[max(SENSITIVITIES.index(tier),
                                 SENSITIVITIES.index(DEFAULT_SENSITIVITY))]
    except ValueError:
        return DEFAULT_SENSITIVITY


async def classify_document(
    *,
    filename: str,
    text_snippet: str,
    notes: str = "",
    silos: list[dict] | None = None,
    entities: list[dict] | None = None,
    tenant_id: UUID | None = None,
) -> Classification | None:
    """One LLM call → vocabulary-constrained filing metadata; None on any
    model failure (caller falls back to defaults + review flag)."""
    silos = silos or []
    entities = entities or []
    silos_list = ", ".join(f"{s['id']} ({s['name']})" for s in silos) or "(none)"
    ents_list = "\n".join(
        f"{e['id']} [{e.get('business_unit', '')}] {e.get('display_name', '')}"
        for e in entities[:120]
    ) or "(none)"

    user = "\n".join([
        "VOCABULARIES",
        f"- businessUnit (codes): {', '.join(BU_CODES)}",
        f"- assetClass: {', '.join(ASSET_CLASSES)}",
        f"- docType: {', '.join(DOC_TYPES)}",
        f"- status: {', '.join(STATUSES)}",
        f"- sensitivity: {', '.join(SENSITIVITIES)}",
        "",
        f"KNOWN SILOS (siloId options): {silos_list}",
        "",
        "KNOWN ENTITIES (entityId options):",
        ents_list,
        "",
        f"DOCUMENT FILENAME: {filename}",
        (f"USER NOTE — the user's own description of this document "
         f"(trust it strongly):\n{notes.strip()}") if (notes or "").strip() else "",
        "",
        "DOCUMENT EXCERPT (first part):",
        (text_snippet or "")[:4000]
        or "(no extractable text — classify from the filename and user note "
           "alone, lower your confidence)",
        "",
        "Classify now.",
    ])

    try:
        p = await get_llm().complete_json(
            system=_SYSTEM.format(type_codes=", ".join(ENTITY_TYPE_CODES)),
            messages=[{"role": "user", "content": user}],
            max_tokens=500,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(p, dict):
        return None

    silo_ids = {s["id"] for s in silos}
    ent_ids = {e["id"] for e in entities}

    date_raw = str(p.get("date") or "").strip()
    date = date_raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_raw) else None

    silo_id = str(p["siloId"]) if p.get("siloId") and str(p["siloId"]) in silo_ids else None
    entity_id = str(p["entityId"]) if p.get("entityId") and str(p["entityId"]) in ent_ids else None
    entity_name = (str(p.get("entityName") or "").strip()[:120] or None) if not entity_id else None

    try:
        conf = float(p.get("confidence"))
        confidence = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        confidence = 0.5

    from .vocab import canonical_doc_type

    return Classification(
        business_unit=_one_of(p.get("businessUnit"), BU_CODES, "PRI"),
        asset_class=_one_of(p.get("assetClass"), ASSET_CLASSES, "Other"),
        doc_type=canonical_doc_type(_one_of(p.get("docType"), DOC_TYPES, "Other")),
        descriptor=safe_descriptor(str(p.get("descriptor") or "")) or "Doc",
        date=date,
        status=_one_of(p.get("status"), STATUSES, "Current"),
        sensitivity=_clamp_sensitivity(
            _one_of(p.get("sensitivity"), SENSITIVITIES, DEFAULT_SENSITIVITY)),
        silo_id=silo_id,
        entity_id=entity_id,
        entity_name=entity_name,
        entity_type_code=_one_of(p.get("entityType"), ENTITY_TYPE_CODES, "D"),
        confidence=confidence,
        reason=str(p.get("reason") or "")[:200],
    )


__all__ = ["classify_document", "Classification", "LOW_CONFIDENCE"]
