"""Pull PreReal Intelligence into a brand's memory.

Takes one PRI silo (e.g. 'spaceport') and lands its synthesized intelligence —
the living brief, portfolio insights, and every tier-allowed document — into
THIS brand's knowledge base, filed under a silo of the same slug. Once landed
it is ordinary memory: Ask, retrieval, press monitoring and Academy lessons
ground on it exactly like an uploaded company doc.

Honest by construction:
  * The sensitivity TIER rides along — a 'Restricted' PRI doc is filed
    Restricted here. NDA-Protected never crosses (the PRI side strips it),
    so it can never arrive to be re-filed at a lower tier.
  * Re-pulls are idempotent. `pri_pull_log` holds a content hash per pulled
    item: unchanged items are skipped, changed items are re-ingested (a new
    version), so pulling twice does not duplicate the corpus.
  * With no PRI plug configured the provider is the stub, so this whole path
    runs (and is testable) offline — it just lands a clearly-labelled
    placeholder instead of real intelligence.
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

from .adapters.pri_plug import (
    PlugBrief,
    PlugChunk,
    PlugDoc,
    PlugInsight,
    PriPlugProvider,
    get_pri_plug_provider,
)
from .db import acquire
from .knowledge import ingest_knowledge_document
from .silos import create_silo, list_silos
from .vocab import normalize_sensitivity


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "doc"


# ─────────────────────────────────────────────────────── rendering to text ──
def _one(x: object, *keys: str) -> str:
    """A blocker/action item may be a plain string or a dict — render either."""
    if isinstance(x, dict):
        main = str(x.get(keys[0], "") if keys else "").strip()
        extra = [f"{k}: {x[k]}" for k in keys[1:] if x.get(k)]
        return f"{main}" + (f" ({'; '.join(extra)})" if extra else "")
    return str(x).strip()


def _render_brief(silo: str, b: PlugBrief) -> str:
    out: list[str] = [f"# PRI living brief — {silo}"]
    if b.goal:
        out.append(f"\n**Goal:** {b.goal}")
    if b.status:
        out.append(f"\n**Status:** {b.status}")
    if b.narrative:
        out.append(f"\n{b.narrative}")
    if b.blockers:
        out.append("\n## Blockers")
        out += [f"- {_one(x, 'text', 'needs')}" for x in b.blockers]
    if b.next_actions:
        out.append("\n## Next actions")
        out += [f"- {_one(x, 'text', 'owner', 'due')}" for x in b.next_actions]
    if b.open_questions:
        out.append("\n## Open questions")
        out += [f"- {_one(x, 'text')}" for x in b.open_questions]
    if b.key_facts:
        out.append("\n## Key facts")
        kf = b.key_facts
        if isinstance(kf, dict):
            out += [f"- **{k}:** {v}" for k, v in kf.items()]
        elif isinstance(kf, list):
            out += [f"- {_one(x, 'text')}" for x in kf]
    return "\n".join(out).strip()


def _render_insights(silo: str, insights: list[PlugInsight]) -> str:
    out: list[str] = [f"# PRI portfolio insights touching {silo}"]
    for i in insights:
        out.append(f"\n## [{i.kind}] {i.title}")
        if i.detail:
            out.append(i.detail)
        if i.suggested_action:
            out.append(f"\n→ Suggested next step: {i.suggested_action}")
        if i.projects:
            out.append(f"\n_across: {', '.join(str(p) for p in i.projects)}_")
    return "\n".join(out).strip()


def _render_doc(d: PlugDoc) -> str:
    meta = f"_type: {d.doc_type or 'Doc'} · date: {d.file_date or '—'} · sensitivity: {d.sensitivity}_"
    header = f"# {d.filename}\n{meta}"
    if d.notes:
        header += f"\n_notes: {d.notes}_"
    return f"{header}\n\n{d.text}".strip()


# ───────────────────────────────────────────────────────────── silo helper ──
async def _ensure_silo(silo: str, tenant_id: UUID | None) -> None:
    existing = await list_silos(tenant_id)
    if any((s.get("id") == silo) for s in existing):
        return
    try:
        await create_silo(
            name=silo.replace("-", " ").replace("_", " ").title(),
            silo_id=silo,
            description=f"PreReal Intelligence for {silo}, pulled via the PRI plug.",
            tenant_id=tenant_id,
        )
    except ValueError:
        pass  # created concurrently — fine


# ─────────────────────────────────────────────────────────────── the pull ──
async def pull_silo_into_memory(
    silo: str,
    *,
    tenant_id: UUID | None = None,
    provider: PriPlugProvider | None = None,
) -> dict:
    """Pull one PRI silo's intelligence into this brand's memory. Idempotent."""
    silo = (silo or "").strip()
    if not silo:
        raise ValueError("silo is required")

    prov = provider or get_pri_plug_provider()
    intel = await prov.fetch_intelligence(silo, full=True)
    await _ensure_silo(silo, tenant_id)

    # (pri_id, kind, sensitivity, filename, text) for every item worth landing.
    items: list[tuple[str, str, str, str, str]] = []
    if intel.brief and (intel.brief.narrative or intel.brief.goal or intel.brief.status):
        items.append((
            f"{silo}::brief", "brief",
            normalize_sensitivity(intel.brief.max_sensitivity),
            f"pri_{silo}_living_brief.txt", _render_brief(silo, intel.brief),
        ))
    for d in intel.docs:
        text = _render_doc(d)
        if text.strip():
            items.append((
                str(d.id), "doc", normalize_sensitivity(d.sensitivity),
                f"pri_{silo}_{_slug(d.filename)}.txt", text,
            ))
    if intel.insights:
        items.append((
            f"{silo}::insights", "insights", "Restricted",
            f"pri_{silo}_portfolio_insights.txt", _render_insights(silo, intel.insights),
        ))

    res: dict = {
        "silo": silo, "provider": intel.provider,
        "ingested": 0, "updated": 0, "skipped": 0, "failed": 0,
        "withheld": intel.withheld, "items": [],
    }

    async with acquire(tenant_id) as conn:
        seen = {
            r["pri_id"]: r["content_hash"]
            for r in await conn.fetch(
                "SELECT pri_id, content_hash FROM pri_pull_log WHERE silo_id = $1", silo
            )
        }

    for pri_id, kind, sens, filename, text in items:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        prev = seen.get(pri_id)
        if prev == digest:
            res["skipped"] += 1
            res["items"].append({"pri_id": pri_id, "kind": kind, "status": "unchanged"})
            continue
        try:
            out = await ingest_knowledge_document(
                data=text.encode("utf-8"),
                original_name=filename,
                mime="text/plain",
                category="research",
                notes=f"pri_ref:{silo}:{pri_id}",
                auto_classify=False,       # deterministic filing — we pin silo + tier
                tenant_id=tenant_id,
                silo_id=silo,
                sensitivity=sens,
            )
            if not out.get("ok"):
                res["failed"] += 1
                res["items"].append({"pri_id": pri_id, "kind": kind,
                                     "status": "failed", "reason": out.get("reason")})
                continue
            bm_doc_id = UUID(out["fileId"]) if out.get("fileId") else None
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    """INSERT INTO pri_pull_log
                         (silo_id, pri_id, kind, bm_doc_id, content_hash)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (tenant_id, silo_id, pri_id) DO UPDATE
                         SET kind = excluded.kind,
                             bm_doc_id = excluded.bm_doc_id,
                             content_hash = excluded.content_hash,
                             pulled_at = now()""",
                    silo, pri_id, kind, bm_doc_id, digest,
                )
            status = "updated" if prev is not None else "ingested"
            res["updated" if prev is not None else "ingested"] += 1
            res["items"].append({
                "pri_id": pri_id, "kind": kind, "status": status,
                "fileId": out.get("fileId"), "chunks": out.get("chunks"),
            })
        except Exception as e:  # noqa: BLE001 — one bad item never kills the pull
            res["failed"] += 1
            res["items"].append({"pri_id": pri_id, "kind": kind, "status": "error", "reason": str(e)})

    return res


async def retrieve_from_pri(
    silo: str, query: str, k: int = 10, *, provider: PriPlugProvider | None = None,
) -> list[PlugChunk]:
    """Live passage retrieval from a PRI silo (grounding, no ingest). The PRI
    side gates NDA/locked/superseded before returning anything."""
    prov = provider or get_pri_plug_provider()
    return await prov.retrieve(silo, query, k)
