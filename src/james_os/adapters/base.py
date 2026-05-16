"""Adapter protocol — every input source plugs into the substrate via this.

Adding a new ingestion source = writing one Adapter implementation.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import EventCreate


@dataclass
class RawDoc:
    """Raw payload from a source, before normalization into events."""

    source_uri: str
    content: str
    fetched_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    """Base contract for any ingestion source.

    Adapters are stateless apart from their config; durable state (cursors,
    last-run timestamps) is read from and written to the `adapters` table by
    the orchestration layer.
    """

    name: str
    schema_version: int = 1

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    async def fetch_since(self, since: datetime | None) -> AsyncIterator[RawDoc]:
        """Yield raw documents created or updated since the given cursor.

        Implementations must be idempotent — re-running with the same `since`
        is safe; downstream dedupe handles repeat events via dedupe_key.
        """
        ...

    @abstractmethod
    def normalize(self, raw: RawDoc) -> list[EventCreate]:
        """Convert one raw doc into one or more events.

        Must produce a deterministic dedupe_key in source.dedupe_key so that
        re-ingestion is idempotent.
        """
        ...
