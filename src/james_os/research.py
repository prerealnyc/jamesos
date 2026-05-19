"""Market research / internet intelligence.

Researches a subject (a company or a person) on the open internet and turns
the findings into events in the SAME memory substrate — tagged
`category:research` — so the rest of JAMES OS (Ask, the content engine,
retrieval) can use that intelligence exactly like any other memory, with
citations back to the sources it came from.

Design rules (kept honest on purpose):

  * Provider-abstracted. `StubResearchProvider` proves the whole loop
    (research → memory → cited answer) with no API key. It returns an
    obviously-labelled placeholder, never invented "facts" dressed up as
    real intelligence.
  * `PerplexityResearchProvider` is the real engine: Perplexity's `sonar`
    models do live web retrieval and return citations. We keep those
    citations and store them as provenance on every event.
  * Nothing is asserted that wasn't returned by the provider. The summary,
    findings and sources are all the provider's — we only structure and
    persist them.

Video/transcript scraping is intentionally NOT faked here. It's a later
phase (yt-dlp → Whisper → ingest) that reuses the existing transcription
path; this module is the text-intelligence spine it will hang off.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .config import settings
from .models import EventCreate, EventSource

RESEARCH_CATEGORY = "research"
_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@dataclass
class ResearchSource:
    url: str
    title: str = ""


@dataclass
class ResearchResult:
    subject: str
    summary: str
    findings: list[str] = field(default_factory=list)
    sources: list[ResearchSource] = field(default_factory=list)
    provider: str = "stub"

    def is_empty(self) -> bool:
        return not self.summary.strip() and not self.findings


class ResearchProvider(ABC):
    name: str

    @abstractmethod
    async def research(self, subject: str, focus: str = "") -> ResearchResult:
        """Research `subject` on the internet. `focus` narrows the angle
        (e.g. 'their content style and what they care about')."""
        ...


class StubResearchProvider(ResearchProvider):
    """No network. Proves the ingest→memory→cite loop without a key.

    It does NOT fabricate intelligence — the body explicitly says it is a
    placeholder so a stub-sourced answer can never be mistaken for real
    research downstream.
    """

    name = "stub"

    async def research(self, subject: str, focus: str = "") -> ResearchResult:
        focus_line = f" (focus: {focus})" if focus else ""
        summary = (
            f"[STUB RESEARCH] No live research provider is connected, so "
            f"JAMES OS has not actually researched '{subject}'{focus_line}. "
            f"This placeholder exists only to prove the research → memory → "
            f"cited-answer pipeline. Set RESEARCH_PROVIDER=perplexity and add "
            f"PERPLEXITY_API_KEY to get real internet intelligence."
        )
        return ResearchResult(
            subject=subject,
            summary=summary,
            findings=[
                f"Stub finding: real research on '{subject}' is not yet "
                f"connected — treat this as unverified.",
            ],
            sources=[ResearchSource(url="stub://no-provider", title="stub provider")],
            provider=self.name,
        )


class PerplexityResearchProvider(ResearchProvider):
    """Perplexity `sonar` — live web retrieval LLM that returns citations."""

    name = "perplexity"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY is required for Perplexity research")
        self.api_key = api_key
        self.model = model

    async def research(self, subject: str, focus: str = "") -> ResearchResult:
        focus_clause = (
            f" Focus specifically on: {focus}."
            if focus
            else " Focus on who/what they are, what they care about, their"
            " positioning, their content style, and recurring themes."
        )
        system = (
            "You are a research analyst. Research the subject ONLY from "
            "current, citable internet sources. Never speculate or invent. "
            "If something is unknown, say so. Be specific and concrete. "
            "Return: (1) a tight summary paragraph, then (2) a bullet list "
            "of concrete findings, each tied to what a source actually said."
        )
        user = (
            f"Research this subject: {subject}.{focus_clause} "
            f"Base everything strictly on what sources actually report."
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
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_PERPLEXITY_URL, json=payload, headers=headers)
            if resp.status_code == 401:
                raise RuntimeError("Perplexity rejected the API key (401)")
            if resp.status_code == 429:
                raise RuntimeError("Perplexity rate limit hit (429)")
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "").strip()

        # Perplexity returns citations as a top-level list of URLs (newer
        # responses also nest richer search_results). Handle both.
        sources: list[ResearchSource] = []
        for c in data.get("citations") or []:
            if isinstance(c, str):
                sources.append(ResearchSource(url=c))
            elif isinstance(c, dict) and c.get("url"):
                sources.append(ResearchSource(url=c["url"], title=c.get("title", "")))
        for sr in data.get("search_results") or []:
            if isinstance(sr, dict) and sr.get("url"):
                url = sr["url"]
                if not any(s.url == url for s in sources):
                    sources.append(ResearchSource(url=url, title=sr.get("title", "")))

        summary, findings = _split_summary_and_findings(content)
        return ResearchResult(
            subject=subject,
            summary=summary,
            findings=findings,
            sources=sources,
            provider=self.name,
        )


def _split_summary_and_findings(text: str) -> tuple[str, list[str]]:
    """First non-bullet block = summary; bullet lines = findings."""
    summary_lines: list[str] = []
    findings: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] in "-*•" or (len(line) > 2 and line[0].isdigit() and line[1] in ".)"):
            findings.append(line.lstrip("-*•0123456789.) ").strip())
        elif findings:
            # prose after bullets started — append to last finding
            findings[-1] = f"{findings[-1]} {line}"
        else:
            summary_lines.append(line)
    return " ".join(summary_lines).strip() or text.strip(), findings


def make_research_provider() -> ResearchProvider:
    provider = settings.research_provider.lower()
    if provider == "stub":
        return StubResearchProvider()
    if provider == "perplexity":
        return PerplexityResearchProvider(
            api_key=settings.perplexity_api_key,
            model=settings.perplexity_model,
        )
    raise ValueError(f"Unknown research provider: {provider}")


_provider: ResearchProvider | None = None


def get_research_provider() -> ResearchProvider:
    global _provider
    if _provider is None:
        _provider = make_research_provider()
    return _provider


def research_to_events(result: ResearchResult) -> list[EventCreate]:
    """Turn a ResearchResult into citation-grounded memory events.

    One event for the synthesized summary, plus one per discrete finding,
    so retrieval can surface a precise fact and the content engine can
    cite it. Provenance (subject, provider, source URLs) rides on every
    event's payload + entities + source.raw_metadata.
    """
    if result.is_empty():
        return []

    now = datetime.now(UTC)
    source_urls = [s.url for s in result.sources]
    domains = sorted({_domain(u) for u in source_urls if _domain(u)})
    base_entities = [
        f"subject:{result.subject}",
        f"category:{RESEARCH_CATEGORY}",
        *[f"source:{d}" for d in domains],
    ]
    digest = hashlib.sha256(
        f"{result.subject}|{result.summary}".encode()
    ).hexdigest()[:16]

    def _src(idx: int) -> EventSource:
        return EventSource(
            adapter=f"research:{result.provider}",
            uri=source_urls[0] if source_urls else None,
            dedupe_key=f"research-{digest}-{idx}",
            raw_metadata={
                "subject": result.subject,
                "provider": result.provider,
                "category": RESEARCH_CATEGORY,
                "sources": source_urls,
            },
        )

    blocks: list[str] = []
    sources_footer = (
        "\n\nSources:\n" + "\n".join(f"- {u}" for u in source_urls)
        if source_urls
        else ""
    )
    blocks.append(
        f"Research on {result.subject} (via {result.provider}):\n"
        f"{result.summary}{sources_footer}"
    )
    for finding in result.findings:
        blocks.append(
            f"Research finding on {result.subject}: {finding}{sources_footer}"
        )

    events: list[EventCreate] = []
    for idx, text in enumerate(blocks):
        events.append(
            EventCreate(
                event_type="document",
                payload={
                    "text": text,
                    "subject": result.subject,
                    "category": RESEARCH_CATEGORY,
                    "provider": result.provider,
                    "sources": source_urls,
                    "block": idx,
                    "total_blocks": len(blocks),
                },
                raw_content=text,
                source=_src(idx),
                entities=base_entities,
                effective_at=now,
                # Internet research is informative, not authoritative voice.
                confidence=0.6,
            )
        )
    return events


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""
