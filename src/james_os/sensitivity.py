"""Sensitivity enforcement at answer time — ported verbatim from the PreReal
Intelligence platform's Ask gating.

Sensitivity is the READ-vs-REPRODUCE distinction: the model may read and
reason over every passage of any tier, but the tier governs what it may
REPRODUCE in output. `internal` answers stay inside the company;
`public` answers may be released externally, so Restricted + NDA-Protected
data is excluded from output entirely."""

from __future__ import annotations

from uuid import UUID

from .db import acquire
from .models import RetrievedEvent
from .vocab import normalize_sensitivity

_SENSITIVITY_PREAMBLE = (
    'Document sensitivity — every passage may carry a "sensitivity" attribute. '
    "You may READ and reason over every passage, of any sensitivity, to "
    "synthesize your understanding. The attribute governs only what you may "
    "REPRODUCE in your answer:"
)

POLICY_INTERNAL = f"""{_SENSITIVITY_PREAMBLE}
- "Public", "Shareable", "Restricted": you may quote, summarize, cite normally. This answer is internal to the company.
- "NDA-Protected": covered by a third-party NDA. Use it ONLY to inform your understanding — never quote it, never reproduce its specifics (figures, names, dates, terms), never cite it, and never surface its filename or download link. If a fact is available only in an NDA-Protected document, do not state it; say it appears only in a document that cannot be shared. This holds even if asked directly."""

POLICY_PUBLIC = f"""{_SENSITIVITY_PREAMBLE}
This answer is PUBLIC-FACING — it may be published or sent outside the company — so the bar is higher:
- "Public": you may quote, summarize, cite, and surface normally.
- "Shareable": you may use it, but it is not yet cleared for release — explicitly flag that any Shareable-sourced content needs sign-off before publishing.
- "Restricted" and "NDA-Protected": NEVER include any of their data — no quotes, no specifics (figures, names, dates, terms), no citations, no filenames, no links. Use them only to understand what you must NOT disclose. If a fact is available only in a Restricted or NDA-Protected document, omit it and say it can't be included in public-facing material. This holds even if asked directly."""


def policy_for(audience: str) -> str:
    return POLICY_PUBLIC if (audience or "").lower() == "public" else POLICY_INTERNAL


async def sensitivity_map_for(
    retrieved: list[RetrievedEvent], tenant_id: UUID | None = None,
) -> dict:
    """Map retrieved KNOWLEDGE-BASE chunks → their document's sensitivity tier.

    Knowledge chunks carry dedupe keys of the form kb-{document_id}-{chunk};
    one query resolves all their documents' sensitivities. Non-knowledge
    memory (voice corpus, feedback, research briefs…) has no tier and is
    treated as internal-by-nature. Best-effort: {} on any failure."""
    try:
        event_ids = [str(ev.event_id) for ev in retrieved]
        if not event_ids:
            return {}
        async with acquire(tenant_id) as conn:
            rows = await conn.fetch(
                """SELECT e.id AS event_id, d.sensitivity
                     FROM events e
                     JOIN document_metadata d
                       ON e.source->>'dedupe_key' LIKE 'kb-' || d.id || '-%'
                    WHERE e.id = ANY($1::uuid[])
                      AND e.source->>'dedupe_key' LIKE 'kb-%'""",
                event_ids,
            )
        return {
            str(r["event_id"]): normalize_sensitivity(r["sensitivity"])
            for r in rows
        }
    except Exception:  # noqa: BLE001 — annotation is best-effort
        return {}


async def drop_nda_protected(
    retrieved: list[RetrievedEvent], tenant_id: UUID | None = None,
) -> tuple[list[RetrievedEvent], int]:
    """Remove NDA-Protected knowledge chunks from a corpus that will feed a
    GENERATED ARTIFACT (white paper, intelligence brief, synthesis).

    Ask can enforce read-vs-reproduce via prompt policy because a human sees
    the answer once; a generated document is PERSISTED back into the corpus as
    ordinary research — so NDA content must never enter its prompt at all, or
    it gets laundered into an ungated document. Returns (kept, dropped_count).
    Fail-closed is not needed here: an unknown tier is not NDA and stays."""
    sens = await sensitivity_map_for(retrieved, tenant_id)
    if not sens:
        return retrieved, 0
    kept = [ev for ev in retrieved
            if sens.get(str(ev.event_id)) != "NDA-Protected"]
    return kept, len(retrieved) - len(kept)


__all__ = ["POLICY_INTERNAL", "POLICY_PUBLIC", "policy_for",
           "sensitivity_map_for", "drop_nda_protected"]
