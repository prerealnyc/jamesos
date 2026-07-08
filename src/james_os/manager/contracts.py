"""Shared value sets and inter-agent payload contracts (ported from bm2.0
backend/app/schemas/contracts.py — the merge keeps these semantics intact).

Every manager module imports from here; nothing here imports from agents.
Changes to this file require updating docs/build-decisions.md (in the bm2.0
repo) or its successor here.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    PERSON = "person"
    COMPANY = "company"
    PHYSICAL_ASSET = "physical_asset"
    INSTITUTION = "institution"


class Source(StrEnum):
    USER_STATED = "user_stated"
    NEGOTIATED = "negotiated"
    AUDITED = "audited"
    RESEARCHED = "researched"
    INFERRED = "inferred"
    DERIVED = "derived"
    QUEUE_SIGNAL = "queue_signal"


class FieldStatus(StrEnum):
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    SUPERSEDED = "superseded"


class WorkOrderStatus(StrEnum):
    """The D5 lifecycle, extended onto the james-os actions queue. The
    pre-merge james-os states (pending/approved/rejected/executed/failed)
    remain valid rows in the table; manager work orders use these."""

    QUEUED = "queued"
    GENERATING = "generating"
    REVIEW = "review"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    MEASURED = "measured"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


# D2 confidence rubric — the only place these numbers live
CONFIDENCE_BASE: dict[Source, float] = {
    Source.USER_STATED: 1.0,
    Source.NEGOTIATED: 1.0,
    Source.AUDITED: 0.95,
    Source.RESEARCHED: 0.6,  # secondary; primary-source research passes primary=True
    Source.INFERRED: 0.4,
    Source.DERIVED: 0.6,
    Source.QUEUE_SIGNAL: 0.7,
}
CONFIDENCE_RESEARCHED_PRIMARY = 0.8
CONFIDENCE_MULTI_CITATION_BONUS = 0.1
CONFIDENCE_NON_HUMAN_CAP = 0.95

# Profile sections (bm2.0 spec §2.2)
SECTIONS = [
    "identity",
    "audience",
    "voice",
    "positioning",
    "products",
    "competitors",
    "goals",
    "guardrails",
    "channels",
    "performance",
]

# Staleness TTLs (D2): past this age a current field flips to 'stale'.
STALENESS_TTL_DAYS = {"performance": 7, "channels": 7, "competitors": 30, "audience": 90}
STALENESS_DEFAULT_DAYS = 365


def staleness_ttl_days(section: str) -> int:
    return STALENESS_TTL_DAYS.get(section, STALENESS_DEFAULT_DAYS)


def compute_confidence(source: Source, citations: list[str], primary: bool = False) -> float:
    """D2: deterministic, never LLM-self-reported."""
    if source in (Source.USER_STATED, Source.NEGOTIATED):
        return 1.0
    base = CONFIDENCE_RESEARCHED_PRIMARY if (source == Source.RESEARCHED and primary) else CONFIDENCE_BASE[source]
    if len(citations) >= 2:
        base = min(base + CONFIDENCE_MULTI_CITATION_BONUS, CONFIDENCE_NON_HUMAN_CAP)
    return round(base, 2)


# ---------------------------------------------------------------- payloads


class Citation(BaseModel):
    url: str = ""
    ref: str = ""  # asset id / internal ref when not a URL
    note: str = ""


class FieldWrite(BaseModel):
    """What an agent hands the profile service to record a fact."""

    section: str
    field_key: str
    item_key: str | None = None
    value: object
    source: Source
    citations: list[Citation] = Field(default_factory=list)
    primary: bool = False  # researched-from-brand's-own-property
    updated_by: str


class EntityCandidate(BaseModel):
    """Researcher output for the 'Is this your brand?' disambiguation step."""

    name: str
    description: str
    urls: list[str] = Field(default_factory=list)
    score: float = 0.0


class ResearchReport(BaseModel):
    brand_id: str
    candidates: list[EntityCandidate] = Field(default_factory=list)
    fields: list[FieldWrite] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)  # partial-failure visibility


class BaselineReport(BaseModel):
    """Auditor output. Lookback capped at 12 months (D9)."""

    brand_id: str
    captured_at: datetime
    platforms: dict[str, dict] = Field(default_factory=dict)  # platform -> metrics
    top_posts: list[dict] = Field(default_factory=list)
    bottom_posts: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # degraded-mode explanations


class PeerDigest(BaseModel):
    """Peer Agent weekly output -> Strategist input."""

    brand_id: str
    period: str
    peers: list[dict] = Field(default_factory=list)  # handle, kind, followers, cadence, top_topics
    benchmarks: dict = Field(default_factory=dict)  # cadence_per_week, avg_engagement, formats
    observations: list[str] = Field(default_factory=list)  # cited, human-readable


class PlanItem(BaseModel):
    content_type: str
    platform: str
    topic: str
    count: int = 1
    format_spec: dict = Field(default_factory=dict)
    rationale: str
    evidence: list[Citation] = Field(default_factory=list)
    predicted_metrics: dict = Field(default_factory=dict)  # mandatory per D5; crude v0 ok


class WeeklyPlan(BaseModel):
    """Strategist output; items become queue work orders on activation."""

    brand_id: str
    period_start: datetime
    period_end: datetime
    rationale: str
    goals_snapshot: dict = Field(default_factory=dict)
    items: list[PlanItem]


class MorningBrief(BaseModel):
    brand_id: str
    date: datetime
    headline: str
    sections: list[dict] = Field(default_factory=list)  # {title, body, citations}
    recommended_actions: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """Reviewer gate output (D7)."""

    passed: bool
    lint_violations: list[str] = Field(default_factory=list)
    guardrail_violations: list[str] = Field(default_factory=list)
    # PRD R3.2 'vet if it's accurate': specific factual claims in the draft not
    # supported by the work order's evidence/context. Any entry fails review.
    fact_violations: list[str] = Field(default_factory=list)
    voice_score: float | None = None  # None = cold start, voice unverified
    judge_notes: str = ""
