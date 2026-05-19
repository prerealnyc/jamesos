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


# ─────────────────────────────────── Content generation ──

class ContentBrief(BaseModel):
    platform: str = "instagram"   # instagram | linkedin | x | youtube | ...
    format: str = "post"          # post | reel_script | caption | thread | ...
    pillar: str = ""              # which brand pillar this serves
    topic: str                    # what the piece is about
    research_subject: str = ""    # optional: ground facts in research on this
    extra_instructions: str = ""  # optional one-off steer


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
