from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ─────────────────────────────────────────── Events ──

EventType = Literal[
    "commitment",
    "decision",
    "document",
    "message",
    "voice_memo",
    "ai_output",
    "note",
]


class EventSource(BaseModel):
    adapter: str
    uri: str | None = None
    dedupe_key: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class EventCreate(BaseModel):
    event_type: EventType
    payload: dict[str, Any]
    raw_content: str | None = None
    source: EventSource
    participants: list[UUID] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    parent_event_id: UUID | None = None
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    id: UUID
    tenant_id: UUID
    event_type: str
    payload: dict[str, Any]
    raw_content: str | None
    embedding_model: str | None
    source: dict[str, Any]
    participants: list[UUID]
    entities: list[str]
    parent_event_id: UUID | None
    superseded_by: UUID | None
    effective_at: datetime
    expires_at: datetime | None
    confidence: float
    created_at: datetime
    metadata: dict[str, Any]


# ────────────────────────────────────────── Plug-ins ──

PluginSlot = Literal["framework", "guideline", "protocol", "frustration", "identity"]


class PlugInCreate(BaseModel):
    slot: PluginSlot
    name: str
    content: dict[str, Any]
    applies_to: list[str] = Field(default_factory=list)


class PlugIn(BaseModel):
    id: UUID
    tenant_id: UUID
    slot: str
    name: str
    content: dict[str, Any]
    applies_to: list[str]
    version: int
    active: bool
    created_at: datetime


# ────────────────────────────────── Retrieval / Ask ──

class RetrievedEvent(BaseModel):
    event_id: UUID
    event_type: str
    raw_content: str | None
    payload: dict[str, Any]
    effective_at: datetime
    score: float
    source_signal: list[str]  # ["vector", "fts", "structured"]


class Citation(BaseModel):
    event_id: UUID
    span: str  # the cited fragment
    confidence: float


class AskRequest(BaseModel):
    question: str
    user_id: UUID | None = None
    event_types: list[EventType] | None = None
    since: datetime | None = None
    until: datetime | None = None


class AskResponse(BaseModel):
    response: str
    citations: list[Citation]
    refused: bool
    refusal_reason: str | None = None
    confidence: float
    retrieved_event_ids: list[UUID]
    model: str
    latency_ms: int


# ──────────────────────────────────────── Research ──

class ResearchRequest(BaseModel):
    subject: str  # a company or a person to research
    focus: str = ""  # optional angle, e.g. "their content style"


class ResearchSourceOut(BaseModel):
    url: str
    title: str = ""


class ResearchResponse(BaseModel):
    subject: str
    provider: str
    summary: str
    findings: list[str]
    sources: list[ResearchSourceOut]
    stored_event_ids: list[UUID]
    ingested_into_memory: bool
    note: str | None = None


# ─────────────────────────────────── Video generation ──

class VideoGenerateRequest(BaseModel):
    prompt: str
    prompt_image: str = ""        # still URL — required by Runway's dev API
    source_action_id: UUID | None = None  # if rendered from an approved draft


# ─────────────────────────────────── Content generation ──

class ContentBrief(BaseModel):
    platform: str = "instagram"   # instagram | linkedin | x | youtube | ...
    format: str = "post"          # post | reel_script | caption | thread | ...
    pillar: str = ""              # which brand pillar this serves
    topic: str                    # what the piece is about
    research_subject: str = ""    # optional: ground facts in research on this
    extra_instructions: str = ""  # optional one-off steer


# ─────────────────────────────────── Trend radar ──

class TrendDiscoverRequest(BaseModel):
    topic: str                            # keyword / hashtag to discover around
    platforms: list[str] = Field(default_factory=lambda: ["instagram", "tiktok", "youtube"])
    limit: int = 20                       # results per platform


class TrendCreator(BaseModel):
    platform: str
    handle: str
    # Optional context, preserved on the watchlist so the UI can render a
    # human label and the content engine can prefer creators whose interests
    # overlap with the brief.
    name: str = ""
    interests: list[str] = Field(default_factory=list)


class WatchlistUpdate(BaseModel):
    creators: list[TrendCreator] = Field(default_factory=list)
    # Wholesale-replace semantics make {"creators": []} destructive — one
    # bad client call wipes the curated list. Require an explicit flag.
    confirm_clear: bool = False


class WatchlistRefreshRequest(BaseModel):
    limit: int = 15                       # recent posts per creator


class ScriptRequest(BaseModel):
    event_id: UUID                        # a trend event to model a script on
    platform: str = ""                    # override target platform (else the trend's)
    extra_instructions: str = ""


# ─────────────────────────────────── Reference / media library ──

class MediaLinkRequest(BaseModel):
    url: str
    role: str = "style_reference"         # style_reference | james_clip | broll
    title: str = ""
    platform: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class MediaUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    platform: str | None = None
    tags: list[str] | None = None
    mute_audio: bool | None = None


class VideoPlanRequest(BaseModel):
    script: str
    platform: str = "instagram"
    aspect: str = "9:16"


class VideoComposeRequest(BaseModel):
    topic_hint: str = ""
    platform: str = "instagram"
    aspect: str = "9:16"


class SceneRenderRequest(BaseModel):
    scene: dict
    aspect: str = "9:16"


class VideoProduceRequest(BaseModel):
    script: str = ""
    platform: str = "instagram"
    aspect: str = "9:16"
    title: str = ""
    scenes: list[dict] = Field(default_factory=list)  # edited plan from the editor
    mode: str = "mixed"  # mixed | avatar_only | timeline | story_audio | avatar_story_mix
    caption_style: str = ""              # blank → AI picks (see caption_styles.py)


class MultiGenerateRequest(BaseModel):
    topic: str
    pillar: str = ""
    platforms: list[str] = Field(default_factory=lambda: ["linkedin", "facebook", "instagram"])
    carousel: bool = True                 # also produce a multi-slide carousel
    carousel_slides: int = 6
    research_subject: str = ""
    extra_instructions: str = ""


class QAVerdict(BaseModel):
    voice_score: float            # 0-1 independent reviewer score
    passed: bool                  # met the hard voice floor
    drift: list[str]              # specific ways it drifts off-voice (if any)


class ContentDraft(BaseModel):
    status: str                   # generated | flagged | not_generated
    draft: str                    # the content itself ("" if not generated)
    platform: str
    format: str
    pillar: str
    angle: str                    # the POV the engine took
    voice_score: float            # final score surfaced to the human
    qa: QAVerdict | None
    grounded_event_ids: list[UUID]  # memory the draft leaned on
    memory_used: dict[str, int]   # category -> # of events available
    action_id: UUID | None        # the pending queue row (None if not queued)
    model: str
    latency_ms: int
    note: str | None = None       # honest caveat (e.g. stub LLM, thin corpus)


class PostImageRequest(BaseModel):
    """Generate a shareable hero image for a post (LinkedIn/Twitter/IG).

    `topic` is the subject line. `brief` is optional extra context — the
    draft body, a specific angle, or a reference to a fact the image
    should evoke. `style` picks the aesthetic prefix (editorial /
    photoreal / minimal / bw_photo); same topic + different style =
    different render. Aspect override beats the per-platform default.
    """
    topic: str
    platform: str = "linkedin"
    brief: str = ""
    aspect: str = ""                 # blank → per-platform default
    style: str = "editorial"          # editorial | photoreal | minimal | bw_photo
    title: str = ""                  # human label for the library
    tags: list[str] = Field(default_factory=list)
