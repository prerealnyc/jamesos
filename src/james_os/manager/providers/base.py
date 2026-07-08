"""Provider interfaces (D8). Every external dependency sits behind one of
these; each has a real implementation and a deterministic mock. Agents depend
on the interface only — vendor swaps are config changes.

get_providers() is the single wiring point: APP_ENV=mock returns fixtures,
live returns real vendors.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = "serper"


@dataclass
class PageContent:
    url: str
    title: str
    text: str
    meta: dict = field(default_factory=dict)


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: str
    source: str
    snippet: str = ""


@dataclass
class SocialProfile:
    platform: str
    handle: str
    display_name: str = ""
    followers: int | None = None
    bio: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class SocialPost:
    platform: str
    post_id: str
    url: str
    text: str
    published_at: str
    metrics: dict = field(default_factory=dict)


@dataclass
class WikiPage:
    title: str
    url: str
    summary: str
    facts: dict = field(default_factory=dict)


@dataclass
class ChannelOverview:
    platform: str
    channel_id: str
    title: str
    url: str
    subscribers: int | None = None
    video_count: int | None = None
    recent_titles: list[str] = field(default_factory=list)


@dataclass
class VideoRef:
    """A single upload on the brand's own channel — the unit the voice
    harvester transcribes (own channel => the brand is the speaker, D6)."""

    video_id: str
    url: str
    title: str = ""
    published_at: str = ""


@dataclass
class TranscriptResult:
    """What the brand actually said in one source, cited back to its URL so an
    exemplar is verifiable ('somebody of the brand has spoken this'), never
    fabricated. method records HOW we got it (captions vs audio transcription)."""

    url: str
    text: str
    method: str = ""  # captions | assemblyai | direct | mock | none
    title: str = ""
    duration_sec: int | None = None
    note: str = ""


@dataclass
class PlaceInfo:
    name: str
    address: str = ""
    rating: float | None = None
    reviews_count: int | None = None
    categories: list[str] = field(default_factory=list)
    website: str = ""
    url: str = ""


@dataclass
class DeepResearchResult:
    question: str
    synthesis: str
    citations: list[str] = field(default_factory=list)


class SearchProvider(Protocol):
    async def search(self, query: str, num: int = 10) -> list[SearchResult]: ...


class KnowledgeProvider(Protocol):
    """Encyclopedic entity lookup (Wikipedia v0). None = no page — itself a
    finding (feeds the 'needs a Wikipedia page' recommendation)."""

    async def lookup(self, name: str) -> WikiPage | None: ...


class VideoProvider(Protocol):
    """Channel-level overview. D9: never search.list — locate the channel via
    web search, then stat it with 1-unit endpoints."""

    async def channel_overview(self, query: str) -> ChannelOverview | None: ...

    async def recent_uploads(self, channel_query: str, limit: int = 6) -> list[VideoRef]:
        """The brand's own recent uploads (via the uploads playlist, 1-unit, D9)
        — the source videos the voice harvester transcribes."""
        ...


class PlacesProvider(Protocol):
    """Local/maps presence for physical assets and institutions."""

    async def place(self, query: str) -> PlaceInfo | None: ...


class DeepResearchProvider(Protocol):
    """Synthesized research (Perplexity Sonar v0). Supplementary lane only:
    output is source=researched SECONDARY confidence, never primary."""

    async def research(self, question: str) -> DeepResearchResult: ...


class ScrapeProvider(Protocol):
    async def scrape(self, url: str) -> PageContent: ...


class NewsProvider(Protocol):
    async def search(self, query: str, days: int = 30) -> list[NewsItem]: ...


class PeerDataProvider(Protocol):
    """Public data about accounts we do NOT own (per-platform routing per D8)."""

    async def profile(self, platform: str, handle: str) -> SocialProfile: ...

    async def recent_posts(self, platform: str, handle: str, limit: int = 20) -> list[SocialPost]: ...


@dataclass
class AggregatorGroup:
    """An aggregator profile group the brand could bind to, with the accounts
    already connected in it (so the user can pick the one holding their data)."""

    group_id: str
    name: str
    accounts: list[SocialProfile] = field(default_factory=list)


class SocialConnector(Protocol):
    """Aggregator (Ayrshare primary). One aggregator profile per brand."""

    async def create_profile(self, brand_name: str) -> str: ...  # -> profile_key

    async def list_groups(self) -> list[AggregatorGroup]: ...  # existing groups + their accounts

    # white-label OAuth page/link; platform matters for per-platform flows
    # (PostProxy) and is ignored by all-network connect pages (Ayrshare)
    async def connect_url(self, profile_key: str, platform: str = "instagram") -> str: ...

    async def list_accounts(self, profile_key: str) -> list[SocialProfile]: ...

    async def account_analytics(self, profile_key: str, platform: str) -> dict: ...

    async def post_history(self, profile_key: str, platform: str, months: int = 12) -> list[SocialPost]: ...

    async def publish(self, profile_key: str, platforms: list[str], text: str, media_urls: list[str]) -> dict: ...


class TranscriptionProvider(Protocol):
    async def transcribe(self, media_url: str) -> str: ...

    async def transcribe_source(self, url: str, *, title: str = "") -> TranscriptResult:
        """Turn any spoken source into text: a YouTube URL (captions first, then
        audio extraction), a podcast/direct-media URL (transcription), etc. Best
        effort — a source we cannot transcribe returns empty text + a note, never
        raises, so one bad video never sinks the harvest."""
        ...


@dataclass
class PublishResult:
    """What an execution 'hand' returns after doing the work. ok=False carries
    the reason in detail so the owner sees an honest failure, not a fake done."""

    ok: bool
    ref: str = ""  # provider message-id / post-id
    url: str = ""  # public URL when the surface has one (blog post, social post)
    detail: dict = field(default_factory=dict)
    provider: str = "mock"


class EmailProvider(Protocol):
    """Outbound email / newsletter — an execution 'hand', never an 'eye'. The
    manager drafts the email, a human approves, and only then does send() fire
    (nothing auto-publishes). james-os has no email of any kind (D8 gap fill)."""

    async def send(
        self, *, to: list[str], subject: str, html: str, from_name: str = "", preheader: str = ""
    ) -> PublishResult: ...


class PublishProvider(Protocol):
    """Publish long-form text (a blog post / article) to a crawlable surface —
    own hosted blog in v0; Substack/Ghost/WordPress later is a config swap. The
    SEO/AEO distribution 'hand' james-os deliberately left unbuilt (its PRD R8.1)."""

    async def publish(
        self, *, title: str, body_markdown: str, slug: str = "", meta: dict | None = None
    ) -> PublishResult: ...


class LLMRouter(Protocol):
    """Tiered routing (D8): extract=Haiku, content=Sonnet, strategy=Opus.
    complete() returns text; complete_json() enforces a JSON response parsed
    to dict. Implementations must record token usage on the active AgentRun.
    NOTE: web search happens through SearchProvider only — never the model's
    native web tool (D8 cost guardrail)."""

    async def complete(self, tier: str, system: str, prompt: str, max_tokens: int = 2000) -> str: ...

    async def complete_json(self, tier: str, system: str, prompt: str, max_tokens: int = 4000) -> dict: ...


@dataclass
class Providers:
    search: SearchProvider
    scrape: ScrapeProvider
    news: NewsProvider
    peers: PeerDataProvider
    social: SocialConnector
    transcription: TranscriptionProvider
    llm: LLMRouter
    # parallel research lanes
    wiki: KnowledgeProvider
    video: VideoProvider
    places: PlacesProvider
    deep: DeepResearchProvider
    # execution 'hands' — publish the work the manager drafted (D8 gap fill)
    email: EmailProvider
    blog: PublishProvider


def get_providers() -> Providers:
    """MANAGER_ENV=mock -> all mocks (deterministic, keyless). live ->
    per-provider: live where its key is configured, mock fallback where not,
    so go-live is incremental (see live.live_providers)."""
    from ...config import settings

    from . import mocks

    mock = mocks.mock_providers()
    if settings.manager_env != "live":
        return mock
    from . import live

    return live.live_providers(fallback=mock)
