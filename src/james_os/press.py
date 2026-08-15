"""Press monitoring.

Scans the open web / news for recent MENTIONS of a brand, files each mention
into the SAME memory substrate (tagged `category:press`), and writes a short
press digest grounded on those mentions AND the brand's own memory (including
PreReal Intelligence pulled via the PRI plug). So "what's being said about us,
and what it means for our projects" is answerable from memory with citations.

Same honest, provider-abstracted shape as research.py:

  * `StubPressProvider` proves the scan → memory → digest loop with NO key and
    NO network. It returns an obviously-labelled placeholder — never invented
    press dressed up as real coverage.
  * `PerplexityPressProvider` is the real scanner: Perplexity `sonar` does live
    web retrieval and returns citations, which become the mentions.
  * Nothing is asserted that the provider didn't return. The digest is written
    by the LLM strictly from the scanned mentions + retrieved memory, and it is
    told to say so when coverage is thin rather than inventing any.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .config import settings
from .models import EventCreate, EventSource

PRESS_CATEGORY = "press"
_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@dataclass
class PressMention:
    url: str
    title: str = ""
    snippet: str = ""
    published: str = ""   # provider-reported date if any (else "")


@dataclass
class PressScan:
    brand: str
    summary: str                                    # the scanner's overview prose
    mentions: list[PressMention] = field(default_factory=list)
    provider: str = "stub"

    def is_empty(self) -> bool:
        return not self.summary.strip() and not self.mentions


class PressProvider(ABC):
    name: str

    @abstractmethod
    async def scan(self, brand: str, focus: str = "", days: int = 30) -> PressScan:
        """Find recent press/web mentions of `brand`. `focus` narrows the angle
        (a project, a topic); `days` bounds recency."""
        ...


class StubPressProvider(PressProvider):
    name = "stub"

    async def scan(self, brand: str, focus: str = "", days: int = 30) -> PressScan:
        focus_line = f" (focus: {focus})" if focus else ""
        summary = (
            f"[STUB PRESS] No live press provider is connected, so JAMES OS has "
            f"not actually scanned press for '{brand}'{focus_line}. This "
            f"placeholder exists only to prove the scan → memory → digest "
            f"pipeline. Set RESEARCH_PROVIDER=perplexity and add "
            f"PERPLEXITY_API_KEY to get real press monitoring."
        )
        return PressScan(brand=brand, summary=summary, mentions=[], provider=self.name)


class PerplexityPressProvider(PressProvider):
    """Perplexity `sonar` — live web retrieval, press-framed, returns citations."""

    name = "perplexity"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY is required for press monitoring")
        self.api_key = api_key
        self.model = model

    async def scan(self, brand: str, focus: str = "", days: int = 30) -> PressScan:
        focus_clause = f" Pay special attention to: {focus}." if focus else ""
        system = (
            "You are a press-monitoring analyst. Using ONLY current, citable "
            "web/news sources, find recent public mentions, coverage, articles, "
            "announcements, or notable social posts about the subject. Never "
            "speculate or invent coverage. If there is little or no recent "
            "coverage, say so plainly. Return a tight overview paragraph of what "
            "is being said, then a bullet list where each bullet is one distinct "
            "mention (what the source said), tied to its source."
        )
        user = (
            f"Subject: {brand}. Find press and web mentions from roughly the last "
            f"{days} days.{focus_clause} Base everything strictly on what sources "
            f"actually report."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "return_citations": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_PERPLEXITY_URL, json=payload, headers=headers)
            if resp.status_code == 401:
                raise RuntimeError("Perplexity rejected the API key (401)")
            if resp.status_code == 429:
                raise RuntimeError("Perplexity rate limit hit (429)")
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        summary = (choice.get("message") or {}).get("content", "").strip()

        mentions: list[PressMention] = []
        seen: set[str] = set()
        for sr in data.get("search_results") or []:
            if isinstance(sr, dict) and sr.get("url") and sr["url"] not in seen:
                seen.add(sr["url"])
                mentions.append(PressMention(
                    url=sr["url"], title=sr.get("title", ""),
                    published=sr.get("date", "") or "",
                ))
        for c in data.get("citations") or []:
            url = c if isinstance(c, str) else (c.get("url") if isinstance(c, dict) else "")
            if url and url not in seen:
                seen.add(url)
                title = c.get("title", "") if isinstance(c, dict) else ""
                mentions.append(PressMention(url=url, title=title))

        return PressScan(brand=brand, summary=summary, mentions=mentions, provider=self.name)


def make_press_provider() -> PressProvider:
    # Reuse the research provider selection — press scanning is the same live-web
    # capability, press-framed. Stub unless Perplexity is configured.
    provider = settings.research_provider.lower()
    if provider == "perplexity" and settings.perplexity_api_key:
        return PerplexityPressProvider(settings.perplexity_api_key, settings.perplexity_model)
    return StubPressProvider()


_provider: PressProvider | None = None


def get_press_provider() -> PressProvider:
    global _provider
    if _provider is None:
        _provider = make_press_provider()
    return _provider


def reset_press_provider() -> None:
    global _provider
    _provider = None


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def scan_to_events(scan: PressScan) -> list[EventCreate]:
    """One event for the scan overview, plus one per distinct mention, so a
    specific mention is retrievable and the digest can cite it. Provenance
    (brand, provider, source URL) rides on every event."""
    if scan.is_empty():
        return []

    now = datetime.now(UTC)
    urls = [m.url for m in scan.mentions]
    domains = sorted({_domain(u) for u in urls if _domain(u)})
    base_entities = [f"subject:{scan.brand}", f"category:{PRESS_CATEGORY}",
                     *[f"source:{d}" for d in domains]]
    digest = hashlib.sha256(f"{scan.brand}|{scan.summary}".encode()).hexdigest()[:16]

    def _src(idx: int, uri: str | None) -> EventSource:
        return EventSource(
            adapter=f"press:{scan.provider}", uri=uri,
            dedupe_key=f"press-{digest}-{idx}",
            raw_metadata={"subject": scan.brand, "provider": scan.provider,
                          "category": PRESS_CATEGORY, "sources": urls},
        )

    events: list[EventCreate] = []
    events.append(EventCreate(
        event_type="document",
        payload={"text": f"Press overview for {scan.brand} (via {scan.provider}):\n{scan.summary}",
                 "subject": scan.brand, "category": PRESS_CATEGORY, "provider": scan.provider,
                 "kind": "overview", "sources": urls},
        raw_content=f"Press overview for {scan.brand}:\n{scan.summary}",
        source=_src(0, urls[0] if urls else None),
        entities=base_entities, effective_at=now, confidence=0.6,
    ))
    for i, m in enumerate(scan.mentions, start=1):
        body = f"Press mention of {scan.brand}: {m.title or m.url}".strip()
        if m.snippet:
            body += f" — {m.snippet}"
        body += f"\nSource: {m.url}" + (f" ({m.published})" if m.published else "")
        events.append(EventCreate(
            event_type="document",
            payload={"text": body, "subject": scan.brand, "category": PRESS_CATEGORY,
                     "provider": scan.provider, "kind": "mention", "url": m.url,
                     "title": m.title, "published": m.published},
            raw_content=body,
            source=_src(i, m.url),
            entities=[*base_entities, f"source:{_domain(m.url)}"] if _domain(m.url) else base_entities,
            effective_at=now, confidence=0.6,
        ))
    return events


async def generate_press_digest(scan: PressScan, *, silo: str | None = None,
                                tenant_id=None) -> str:
    """A short, grounded press digest: what's being said + why it matters for us.

    Grounded on the scan's own mentions AND this brand's memory — which now
    includes PreReal Intelligence pulled via the plug — so the 'why it matters'
    is tied to real internal context, not invented. Never fabricates coverage.
    """
    from .llm import get_llm
    from .retrieval import search

    ctx_hits = await search(f"{scan.brand} {silo or ''}".strip(), tenant_id=tenant_id)
    context = "\n\n".join(h.raw_content[:800] for h in ctx_hits[:6])
    mentions_block = "\n".join(
        f"- {m.title or m.url} — {_domain(m.url)}" for m in scan.mentions[:20]
    ) or "(no specific mentions found)"

    system = (
        "You write a brief, factual press digest for a brand's own team. Use ONLY "
        "the press overview and mentions provided; use the internal context only to "
        "interpret what the coverage means for this brand's projects. NEVER invent "
        "coverage — if coverage is thin or absent, say so plainly. Respond as JSON: "
        '{"digest": "<markdown>"} where the markdown is a one-line pulse, then 2–5 '
        "bullets of what is being said (each naming its source domain), then one short "
        "'Why it matters for us' tied to the internal context. No preamble, no sources list."
    )
    user = (
        f"BRAND: {scan.brand}\n\nPRESS OVERVIEW:\n{scan.summary}\n\n"
        f"MENTIONS:\n{mentions_block}\n\n"
        f"INTERNAL CONTEXT (our own memory / PRI intelligence):\n{context or '(none)'}"
    )
    try:
        out = await get_llm().complete_json(
            system=system, messages=[{"role": "user", "content": user}],
            max_tokens=900, temperature=0.2,
        )
        return (out.get("digest") or "").strip()
    except Exception:  # noqa: BLE001 — a digest failure must not lose the filed mentions
        return scan.summary
