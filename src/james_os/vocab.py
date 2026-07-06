"""Controlled vocabularies for the Knowledge Base — ported from the PreReal
Intelligence platform's Naming Convention v1.0 (source-of-truth spec).

These ship as the DEFAULT vocabulary (the first tenant's real vocab); the
module is the single place a future per-tenant vocabulary would plug in.
Sensitivity answers ONE question: how far can a document travel? It governs
how the AI may USE a document — stored as metadata, never in the filename.
"""

from __future__ import annotations

import re

# ── Business Units ──
BUSINESS_UNITS: list[dict] = [
    {"code": "PRI", "label": "PreReal Investments (parent)"},
    {"code": "PRE", "label": "PreReal Brokerage"},
    {"code": "TBM", "label": "Turtleback Mountain Resort (Sales)"},
    {"code": "TBG", "label": "Turtleback Golf Course"},
    {"code": "LND", "label": "Land Portfolio"},
    {"code": "DEV", "label": "Development Projects"},
    {"code": "ACA", "label": "The Prendamano Academy"},
    {"code": "ALL", "label": "The Alliance"},
    {"code": "JPB", "label": "James Prendamano Brand"},
    {"code": "IES", "label": "LB Philips Construction"},
]
BU_CODES = [b["code"] for b in BUSINESS_UNITS]

# ── Asset Classes ──
ASSET_CLASSES = [
    "Brand", "Brokerage", "Hotel", "Golf", "Land", "Residential",
    "Commercial", "Course", "Entity", "Podcast", "Editorial", "PR", "Other",
]

# ── Doc Types (grouped for UX, flat list for validation) ──
DOC_TYPE_GROUPS: list[dict] = [
    {"label": "Brand & Editorial", "types": [
        "Article", "OpEd", "PressMention", "PressRelease", "SocialCaption",
        "PodcastEpisode", "PodcastTranscript", "Speech", "Quote", "Bio",
        "BoilerPlate", "MediaSummary", "KeyMessagePoints", "BackgrounderFAQ",
        "MediaKit", "Curriculum", "GuestExpert",
        "VoiceProfile", "VoiceSample", "Transcript", "AnnotatedTranscript",
        "StyleGuide", "DraftForApproval", "ApprovedDraft",
        # BM-native additions (generated intelligence artifacts).
        "WhitePaper", "ResearchBrief",
    ]},
    {"label": "Real Estate / Brokerage", "types": [
        "ListingAgreement", "BuyerRepAgreement", "PurchaseContract", "Disclosure",
        "Survey", "TaxMap", "TaxLevy", "Deed", "Title", "Closing",
        "CommissionStatement", "MLSSheet",
    ]},
    {"label": "Legal / Entity", "types": [
        "OperatingAgreement", "Bylaws", "FormationDoc", "Resolution", "Amendment",
    ]},
    {"label": "Operations", "types": [
        "Invoice", "Reservation", "VendorContract", "MaintenanceLog", "Schedule",
    ]},
    {"label": "Construction (LB Philips)", "types": [
        "ProjectDocuments", "AcceptedRejectedSummary", "ProjectAbstract",
        "RequestForInformation", "OwnerBidForm", "BidStatusReport",
        "ChangeOrder", "DailyFieldReport", "ApplicationForPayment",
    ]},
    {"label": "Other", "types": ["Other"]},
]
DOC_TYPES = [t for g in DOC_TYPE_GROUPS for t in g["types"]]

# Naming-variant aliases → canonical form (keeps the vocabulary clean without
# making people memorize singular vs plural).
DOC_TYPE_ALIASES: dict[str, str] = {
    "Biography": "Bio", "Biographies": "Bio",
    "PressCoverage": "PressMention", "PressMentions": "PressMention",
    "Backgrounder": "BackgrounderFAQ", "FAQ": "BackgrounderFAQ",
    "FAQs": "BackgrounderFAQ",
    "SocialCaptions": "SocialCaption", "Quotes": "Quote",
    "BrandVoice": "StyleGuide", "ToneGuide": "StyleGuide",
    "VoiceGuide": "StyleGuide",
    "Transcripts": "Transcript", "RawTranscript": "Transcript",
    "TaggedTranscript": "AnnotatedTranscript",
    "MarkedUpTranscript": "AnnotatedTranscript",
    "AudioSample": "VoiceSample", "VoiceClip": "VoiceSample",
    "RawAudio": "VoiceSample",
    "Draft": "DraftForApproval", "PendingDraft": "DraftForApproval",
    "ForReview": "DraftForApproval",
    "SignedOffDraft": "ApprovedDraft", "Approved": "ApprovedDraft",
}


def canonical_doc_type(value: str) -> str:
    return DOC_TYPE_ALIASES.get(value, value)


# ── Status ──  ("Final", "Signed", "Expired", "Review" are NOT in the spec.)
STATUSES = ["Draft", "Executed", "Current", "Superseded", "Archived", "Pending"]

# ── Sensitivity (metadata only — never in the filename) ──
SENSITIVITIES = ["Public", "Shareable", "Restricted", "NDA-Protected"]
# Safe default: a document stays inside the company until someone deliberately
# clears it to travel further.
DEFAULT_SENSITIVITY = "Restricted"

SENSITIVITY_INFO: dict[str, dict] = {
    "Public": {
        "travel": "Already public or cleared for release.",
        "ai": "AI can use it anywhere, including public-facing material.",
    },
    "Shareable": {
        "travel": "Not public yet, but meant for outward use after review.",
        "ai": "AI can use it in drafts for external release, pending approval.",
    },
    "Restricted": {
        "travel": "Sensitive — internal only.",
        "ai": "AI uses it in internal answers, but it's kept out of "
              "public-facing output.",
    },
    "NDA-Protected": {
        "travel": "Covered by a third-party NDA.",
        "ai": "AI reads it only to understand — its data is never quoted, "
              "cited, or shared in any output.",
    },
}

_LEGACY_SENSITIVITY_MAP = {
    "Internal": "Restricted", "Confidential": "Restricted", "Legal": "Restricted",
}


def normalize_sensitivity(value: str | None) -> str:
    """Coerce any stored value into a valid current tag: new values pass,
    legacy values remap, anything unknown falls back to the safe default."""
    if not value:
        return DEFAULT_SENSITIVITY
    if value in SENSITIVITIES:
        return value
    return _LEGACY_SENSITIVITY_MAP.get(value, DEFAULT_SENSITIVITY)


# ── Entity Type codes ──
ENTITY_TYPES: list[dict] = [
    {"code": "H", "label": "Hotel"},
    {"code": "P", "label": "Parcel"},
    {"code": "L", "label": "Listing"},
    {"code": "D", "label": "Deal"},
    {"code": "C", "label": "Contact / Person"},
    {"code": "E", "label": "Entity (LLC)"},
    {"code": "PR", "label": "Press mention"},
    {"code": "S", "label": "Speech"},
    {"code": "B", "label": "Bio"},
    {"code": "Pod", "label": "Podcast episode"},
    {"code": "Art", "label": "Article / OpEd"},
    {"code": "Cse", "label": "Course module"},
]
ENTITY_TYPE_CODES = [t["code"] for t in ENTITY_TYPES]

# EntityID = [BU]-[TypeCode]-[Number]; 3-6 digits (LND-P-0472, PRE-L-000123).
ENTITY_ID_REGEX = re.compile(
    r"^(PRI|PRE|TBM|TBG|LND|DEV|ACA|ALL|JPB|IES)-"
    r"(H|P|L|D|C|E|PR|S|B|Pod|Art|Cse)-[0-9]{3,6}$"
)

DESCRIPTOR_MAX = 30
UNKNOWN_DATE_FILENAME = "00000000"

__all__ = [
    "BUSINESS_UNITS", "BU_CODES", "ASSET_CLASSES",
    "DOC_TYPE_GROUPS", "DOC_TYPES", "DOC_TYPE_ALIASES", "canonical_doc_type",
    "STATUSES", "SENSITIVITIES", "DEFAULT_SENSITIVITY", "SENSITIVITY_INFO",
    "normalize_sensitivity",
    "ENTITY_TYPES", "ENTITY_TYPE_CODES", "ENTITY_ID_REGEX",
    "DESCRIPTOR_MAX", "UNKNOWN_DATE_FILENAME",
]
