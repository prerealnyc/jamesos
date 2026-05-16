"""Manual entry adapter.

Used by the /events POST endpoint and tests. Bypasses fetch_since; the API
hands EventCreate payloads directly to the ingestion service.
"""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from ..models import EventCreate, EventSource
from .base import Adapter, RawDoc


class ManualAdapter(Adapter):
    name = "manual"

    async def fetch_since(self, since: datetime | None) -> AsyncIterator[RawDoc]:
        # Manual entry has no pull-based fetch; ingestion happens via API push.
        if False:
            yield  # pragma: no cover

    def normalize(self, raw: RawDoc) -> list[EventCreate]:
        dedupe = hashlib.sha256(
            f"{raw.source_uri}|{raw.content}".encode()
        ).hexdigest()[:32]
        return [
            EventCreate(
                event_type="note",
                payload={"text": raw.content},
                raw_content=raw.content,
                source=EventSource(
                    adapter=self.name,
                    uri=raw.source_uri,
                    dedupe_key=dedupe,
                    raw_metadata=raw.metadata,
                ),
                effective_at=raw.fetched_at or datetime.now(UTC),
            )
        ]
