"""Canonical 8-slot document filename — ported from the PreReal Intelligence
platform's naming module (Naming Convention v1.0):

  [BusinessUnit]_[AssetClass]_[EntityID]_[DocType]_[Descriptor]_[YYYYMMDD]_[vN]_[Status].ext
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .vocab import (
    DESCRIPTOR_MAX,
    STATUSES,
    UNKNOWN_DATE_FILENAME,
    canonical_doc_type,
)

_TOKEN_RE = re.compile(r"[^A-Za-z0-9-]+")


def _safe_token(value: str) -> str:
    """Strip anything that would break a filename (hyphens kept — EntityIDs
    need them)."""
    return _TOKEN_RE.sub("", (value or "").strip())


def safe_descriptor(value: str) -> str:
    """Descriptor: max 30 chars, alphanumeric + hyphens."""
    return _safe_token(value)[:DESCRIPTOR_MAX]


def _compact_date(iso: str | None) -> str:
    if not iso or iso == UNKNOWN_DATE_FILENAME:
        return UNKNOWN_DATE_FILENAME
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return iso.replace("-", "")
    # Anything weird: don't fabricate a date — flag with the unknown sentinel.
    return UNKNOWN_DATE_FILENAME


def _safe_status(value: str) -> str:
    # The spec bans 'Final'. If callers somehow pass it, refuse.
    if (value or "").lower() == "final":
        raise ValueError(
            "Status 'Final' is banned by the naming convention. "
            "Use 'Executed' or 'Current'.")
    if value not in STATUSES:
        raise ValueError(
            f"Status '{value}' is not in the approved vocabulary: "
            f"{', '.join(STATUSES)}")
    return value


def generate_filename(
    *,
    business_unit: str,
    asset_class: str,
    entity_id: str = "",
    doc_type: str,
    descriptor: str,
    date: str | None,
    version: int,
    status: str,
    extension: str,
) -> str:
    segments = [
        _safe_token(business_unit),
        _safe_token(asset_class),
        _safe_token(entity_id or "UNASSIGNED"),
        _safe_token(doc_type),
        safe_descriptor(descriptor or "Doc") or "Doc",
        _compact_date(date),
        f"v{max(1, int(version or 1))}",
        _safe_status(status),
    ]
    ext = (extension or "bin").lstrip(".").lower()
    return "_".join(segments) + f".{ext}"


@dataclass
class ParsedFilename:
    business_unit: str
    asset_class: str
    entity_id: str
    doc_type: str
    descriptor: str
    date: str | None
    version: int
    status: str
    extension: str


def parse_filename(name: str) -> ParsedFilename | None:
    """Parse a filename that already follows the v1.0 8-slot pattern; None if
    it doesn't look conformant. Shape-only — values validate downstream."""
    dot = name.rfind(".")
    if dot < 1:
        return None
    stem, ext = name[:dot], name[dot + 1:].lower()
    parts = stem.split("_")
    if len(parts) != 8 or not all(parts):
        return None
    bu, ac, entity_id, doc_type, descriptor, date_raw, v_raw, status = parts
    if not re.fullmatch(r"\d{8}", date_raw):
        return None
    date = (None if date_raw == UNKNOWN_DATE_FILENAME
            else f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}")
    m = re.fullmatch(r"v(\d+)", v_raw)
    if not m:
        return None
    return ParsedFilename(
        business_unit=bu, asset_class=ac, entity_id=entity_id,
        doc_type=canonical_doc_type(doc_type), descriptor=descriptor,
        date=date, version=int(m.group(1)), status=status, extension=ext,
    )


__all__ = ["generate_filename", "parse_filename", "safe_descriptor", "ParsedFilename"]
