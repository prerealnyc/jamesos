"""PreReal Intelligence (PRI) plug — a read-only client for another platform's
sensitivity-gated intelligence API.

PRI (the PreReal Intelligence OS) exposes two service-key endpoints:

  * GET  /api/plug/intelligence?silo=<slug>[&full=1]  → a project's living
    brief + portfolio insights + a tier-filtered document manifest.
  * POST /api/plug/retrieve  {silo, query, k}         → ranked passages for
    grounding generated content on real PRI material.

The PRI side enforces the sensitivity BORDER: NDA-Protected content never
crosses, and a brief/insights set is withheld when its project holds NDA
material. This client trusts that border and does not attempt to widen it.

Design mirrors `research.py` on purpose:

  * Provider-abstracted. `StubPriPlugProvider` proves the pull→memory loop
    with NO network and NO key — it returns an obviously-labelled placeholder,
    never invented intelligence dressed up as real. `HttpPriPlugProvider` is
    the real client.
  * Nothing is asserted that the plug didn't return. We only structure and
    carry the plug's own brief / insights / docs / passages.
  * Selection is by configuration presence: with PRI_PLUG_URL + PRI_PLUG_KEY
    set you get the real client; otherwise the stub. So tests (and a fresh
    install) exercise the whole path deterministically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from ..config import settings

_INTELLIGENCE_PATH = "/api/plug/intelligence"
_RETRIEVE_PATH = "/api/plug/retrieve"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


# ─────────────────────────────────────────────────────────── result types ──
@dataclass
class PlugBrief:
    goal: str | None = None
    status: str | None = None
    narrative: str | None = None
    blockers: list = field(default_factory=list)
    next_actions: list = field(default_factory=list)
    key_facts: dict | list | None = None
    open_questions: list = field(default_factory=list)
    max_sensitivity: str | None = None
    as_of: str | None = None
    stale: bool = False


@dataclass
class PlugInsight:
    kind: str = ""
    title: str = ""
    detail: str = ""
    projects: list = field(default_factory=list)
    suggested_action: str = ""
    run_at: str | None = None


@dataclass
class PlugDoc:
    id: str
    filename: str
    doc_type: str | None = None
    file_date: str | None = None
    sensitivity: str = "Restricted"
    notes: str | None = None
    text: str = ""            # full text (full=1) or a preview


@dataclass
class PlugIntelligence:
    silo: str
    brief: PlugBrief | None = None
    insights: list[PlugInsight] = field(default_factory=list)
    docs: list[PlugDoc] = field(default_factory=list)
    withheld: dict = field(default_factory=dict)
    provider: str = "stub"

    def is_empty(self) -> bool:
        return self.brief is None and not self.insights and not self.docs


@dataclass
class PlugChunk:
    content: str
    filename: str = ""
    doc_type: str | None = None
    file_id: str = ""
    silo_id: str | None = None
    entity_id: str | None = None
    file_date: str | None = None
    sensitivity: str = "Restricted"
    relevance: float = 0.0


# ───────────────────────────────────────────────────────────── providers ──
class PriPlugProvider(ABC):
    name: str

    @abstractmethod
    async def fetch_intelligence(self, silo: str, *, full: bool = True) -> PlugIntelligence:
        """A project's brief + insights + documents for one PRI silo."""
        ...

    @abstractmethod
    async def retrieve(self, silo: str, query: str, k: int = 10) -> list[PlugChunk]:
        """Ranked passages from that silo for grounding generated content."""
        ...


class StubPriPlugProvider(PriPlugProvider):
    """No network. Proves pull→memory→ground without a key or the PRI service.

    It does NOT fabricate intelligence — the body says plainly it is a
    placeholder, so stub-sourced memory can never be mistaken for real PRI
    intelligence downstream.
    """

    name = "stub"

    async def fetch_intelligence(self, silo: str, *, full: bool = True) -> PlugIntelligence:
        note = (
            f"[STUB PRI PLUG] No PRI plug is connected, so JAMES OS has not "
            f"actually pulled intelligence for silo '{silo}'. This placeholder "
            f"exists only to prove the pull → memory → grounding pipeline. Set "
            f"PRI_PLUG_URL and PRI_PLUG_KEY to pull real PreReal Intelligence."
        )
        return PlugIntelligence(
            silo=silo,
            brief=PlugBrief(
                goal=f"(stub) goal for {silo}",
                status="(stub) no live PRI plug connected",
                narrative=note,
                max_sensitivity="Restricted",
            ),
            insights=[],
            docs=[PlugDoc(
                id=f"stub-{silo}-1", filename=f"{silo}_stub_note.md",
                doc_type="Note", sensitivity="Restricted",
                notes="stub", text=note,
            )],
            withheld={"nda_docs": 0, "docs_hidden": 0, "stub": True},
            provider=self.name,
        )

    async def retrieve(self, silo: str, query: str, k: int = 10) -> list[PlugChunk]:
        return []


class HttpPriPlugProvider(PriPlugProvider):
    """The real client — service-key auth against the PRI plug endpoints."""

    name = "pri"

    def __init__(self, base_url: str, api_key: str):
        if not base_url or not api_key:
            raise ValueError("PRI_PLUG_URL and PRI_PLUG_KEY are required for the PRI plug")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def fetch_intelligence(self, silo: str, *, full: bool = True) -> PlugIntelligence:
        params = {"silo": silo}
        if full:
            params["full"] = "1"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}{_INTELLIGENCE_PATH}", params=params, headers=self._headers,
            )
            if resp.status_code == 401:
                raise RuntimeError("PRI plug rejected the API key (401)")
            if resp.status_code == 403:
                raise RuntimeError(f"PRI plug refused silo '{silo}' (403)")
            resp.raise_for_status()
            data = resp.json()

        b = data.get("brief")
        brief = PlugBrief(
            goal=b.get("goal"), status=b.get("status"), narrative=b.get("narrative"),
            blockers=b.get("blockers") or [], next_actions=b.get("next_actions") or [],
            key_facts=b.get("key_facts"), open_questions=b.get("open_questions") or [],
            max_sensitivity=b.get("max_sensitivity"), as_of=b.get("as_of"),
            stale=bool(b.get("stale")),
        ) if isinstance(b, dict) else None

        insights = [
            PlugInsight(
                kind=i.get("kind", ""), title=i.get("title", ""), detail=i.get("detail", ""),
                projects=i.get("projects") or [], suggested_action=i.get("suggested_action", ""),
                run_at=i.get("run_at"),
            )
            for i in (data.get("insights") or []) if isinstance(i, dict)
        ]
        docs = [
            PlugDoc(
                id=str(d.get("id", "")), filename=d.get("filename", "") or "untitled",
                doc_type=d.get("doc_type"), file_date=d.get("file_date"),
                sensitivity=d.get("sensitivity") or "Restricted", notes=d.get("notes"),
                # full=1 returns `text`; otherwise `preview`.
                text=(d.get("text") if d.get("text") is not None else d.get("preview", "")) or "",
            )
            for d in (data.get("docs") or []) if isinstance(d, dict)
        ]
        return PlugIntelligence(
            silo=silo, brief=brief, insights=insights, docs=docs,
            withheld=data.get("withheld") or {}, provider=self.name,
        )

    async def retrieve(self, silo: str, query: str, k: int = 10) -> list[PlugChunk]:
        payload = {"silo": silo, "query": query, "k": max(1, min(k, 30))}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}{_RETRIEVE_PATH}", json=payload, headers=self._headers,
            )
            if resp.status_code == 401:
                raise RuntimeError("PRI plug rejected the API key (401)")
            if resp.status_code == 503:
                raise RuntimeError("PRI plug retrieval unavailable (503)")
            resp.raise_for_status()
            data = resp.json()
        return [
            PlugChunk(
                content=c.get("content", ""), filename=c.get("filename", ""),
                doc_type=c.get("doc_type"), file_id=str(c.get("file_id", "")),
                silo_id=c.get("silo_id"), entity_id=c.get("entity_id"),
                file_date=c.get("file_date"), sensitivity=c.get("sensitivity") or "Restricted",
                relevance=float(c.get("relevance") or 0.0),
            )
            for c in (data.get("chunks") or []) if isinstance(c, dict)
        ]


# ─────────────────────────────────────────────────────────────── factory ──
def make_pri_plug_provider() -> PriPlugProvider:
    """Real client when both URL + key are configured; stub otherwise."""
    url = (settings.pri_plug_url or "").strip()
    key = (settings.pri_plug_key or "").strip()
    if url and key:
        return HttpPriPlugProvider(base_url=url, api_key=key)
    return StubPriPlugProvider()


_provider: PriPlugProvider | None = None


def get_pri_plug_provider() -> PriPlugProvider:
    global _provider
    if _provider is None:
        _provider = make_pri_plug_provider()
    return _provider


def reset_pri_plug_provider() -> None:
    """Drop the cached provider (tests that flip settings between stub/http)."""
    global _provider
    _provider = None
