"""Video generation — durable, async, human-gated.

Generative clips (Runway Gen-3/4) take minutes and are asynchronous, so a
render must survive a restart:

    submit  → row in video_jobs (status=submitted), provider job id stored
    poll    → GET the provider task; update the row
    succeed → clip routed into the existing approval queue (actions,
              status=pending) — nothing publishes without a human

Provider-abstracted exactly like research/llm/embedder. `StubVideoProvider`
proves the whole pipeline WITHOUT spending render credits and never returns
a fake mp4 — it returns an obviously-labelled stub marker. `RunwayVideoProvider`
is the real engine.

Honest scope: Runway's public dev API is image-conditioned
(`image_to_video`). We do NOT fake a text-only endpoint — if no still is
supplied the Runway provider says so plainly. Higgsfield/Descript/MiniMax
are not wired (no usable public REST API / no key) and are not faked.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from .config import settings
from .db import acquire

_RUNWAY_BASE = "https://api.dev.runwayml.com/v1"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class SubmitResult:
    provider_job_id: str
    status: str            # submitted | processing | failed
    error: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class PollResult:
    status: str            # processing | succeeded | failed
    result_url: str | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


class VideoProvider(ABC):
    name: str

    @abstractmethod
    async def submit(
        self, prompt: str, image_url: str | None, *,
        model: str, ratio: str, duration: int,
    ) -> SubmitResult: ...

    @abstractmethod
    async def poll(self, provider_job_id: str) -> PollResult: ...


class StubVideoProvider(VideoProvider):
    """Proves the durable submit→poll→queue pipeline without a real render.

    It never emits a fake mp4 — the result is an explicit stub marker so a
    stub-sourced clip can't be mistaken for a real one downstream.
    """

    name = "stub"

    async def submit(
        self, prompt: str, image_url: str | None, *,
        model: str, ratio: str, duration: int,
    ) -> SubmitResult:
        return SubmitResult(provider_job_id=f"stub-{uuid4()}", status="processing")

    async def poll(self, provider_job_id: str) -> PollResult:
        return PollResult(
            status="succeeded",
            result_url=f"stub://no-render/{provider_job_id}",
            error=None,
            raw={"note": "VIDEO_PROVIDER=stub — no real clip was rendered"},
        )


class RunwayVideoProvider(VideoProvider):
    """Runway Gen-3/4 dev API (image-conditioned image_to_video + task poll)."""

    name = "runway"

    def __init__(self, api_key: str, api_version: str):
        if not api_key:
            raise ValueError("RUNWAY_API_KEY is required for Runway video")
        self.api_key = api_key
        self.api_version = api_version

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Runway-Version": self.api_version,
            "Content-Type": "application/json",
        }

    async def submit(
        self, prompt: str, image_url: str | None, *,
        model: str, ratio: str, duration: int,
    ) -> SubmitResult:
        if not image_url:
            # Honest: Runway's public dev API conditions on a still image.
            # We refuse rather than pretend a text-only endpoint exists.
            return SubmitResult(
                provider_job_id="",
                status="failed",
                error=(
                    "Runway's dev API is image-conditioned (image_to_video). "
                    "Supply prompt_image (a still URL) — text-only video is "
                    "not available on this API."
                ),
            )
        body = {
            "promptImage": image_url,
            "promptText": prompt[:1000],
            "model": model,
            "ratio": ratio,
            "duration": duration,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{_RUNWAY_BASE}/image_to_video",
                headers=self._headers(),
                json=body,
            )
        if r.status_code in (401, 403):
            return SubmitResult("", "failed", error=f"Runway auth failed (HTTP {r.status_code})")
        if r.status_code == 429:
            return SubmitResult("", "failed", error="Runway rate/credit limit (HTTP 429)")
        if r.status_code >= 400:
            return SubmitResult("", "failed", error=f"Runway HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        jid = data.get("id")
        if not jid:
            return SubmitResult("", "failed", error=f"Runway returned no task id: {data}")
        return SubmitResult(provider_job_id=str(jid), status="processing", raw=data)

    async def poll(self, provider_job_id: str) -> PollResult:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_RUNWAY_BASE}/tasks/{provider_job_id}",
                headers=self._headers(),
            )
        if r.status_code >= 400:
            return PollResult("failed", error=f"Runway poll HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        rs = str(data.get("status", "")).upper()
        if rs in ("PENDING", "THROTTLED", "RUNNING"):
            return PollResult("processing", raw=data)
        if rs == "SUCCEEDED":
            out = data.get("output") or []
            url = out[0] if isinstance(out, list) and out else None
            if not url:
                return PollResult("failed", error="Runway succeeded but no output url", raw=data)
            return PollResult("succeeded", result_url=str(url), raw=data)
        return PollResult(
            "failed",
            error=str(data.get("failure") or data.get("failureCode") or "Runway task failed"),
            raw=data,
        )


def make_video_provider() -> VideoProvider:
    p = settings.video_provider.lower()
    if p == "stub":
        return StubVideoProvider()
    if p == "runway":
        return RunwayVideoProvider(
            api_key=settings.runway_api_key,
            api_version=settings.runway_api_version,
        )
    raise ValueError(f"Unknown video provider: {p}")


_provider: VideoProvider | None = None


def get_video_provider() -> VideoProvider:
    global _provider
    if _provider is None:
        _provider = make_video_provider()
    return _provider


# ─────────────────────────────────── durable job orchestration ──

def _row_to_job(row) -> dict:
    d = dict(row)
    for k in ("id", "tenant_id", "source_action_id", "queued_action_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    if isinstance(d.get("payload"), str):
        d["payload"] = json.loads(d["payload"])
    for k in ("created_at", "updated_at", "completed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def submit_video_job(
    prompt: str,
    image_url: str | None = None,
    source_action_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Persist the job FIRST (durable), then submit to the provider."""
    provider = get_video_provider()
    async with acquire(tenant_id) as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO video_jobs (provider, prompt, prompt_image,
                                    status, source_action_id, payload)
            VALUES ($1,$2,$3,'queued',$4,$5::jsonb)
            RETURNING id
            """,
            provider.name, prompt, image_url, source_action_id,
            json.dumps({
                "model": settings.runway_model,
                "ratio": settings.runway_video_ratio,
                "duration": settings.runway_video_duration,
            }),
        )

    res = await provider.submit(
        prompt, image_url,
        model=settings.runway_model,
        ratio=settings.runway_video_ratio,
        duration=settings.runway_video_duration,
    )
    status = "failed" if res.status == "failed" else "submitted"
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """
            UPDATE video_jobs
            SET status=$2, provider_job_id=$3, error=$4, updated_at=now(),
                completed_at = CASE WHEN $2='failed' THEN now() ELSE NULL END
            WHERE id=$1
            """,
            job_id, status, res.provider_job_id or None, res.error,
        )
        row = await conn.fetchrow("SELECT * FROM video_jobs WHERE id=$1", job_id)
    return _row_to_job(row)


async def refresh_video_job(job_id: UUID, tenant_id: UUID | None = None) -> dict:
    """Poll the provider for an in-flight job; on success route the clip
    into the approval queue (once)."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT * FROM video_jobs WHERE id=$1", job_id)
    if row is None:
        raise KeyError("video job not found")
    job = dict(row)

    if job["status"] not in ("submitted", "processing") or not job["provider_job_id"]:
        return _row_to_job(row)

    provider = get_video_provider()
    poll = await provider.poll(job["provider_job_id"])

    if poll.status == "processing":
        async with acquire(tenant_id) as conn:
            await conn.execute(
                "UPDATE video_jobs SET status='processing', updated_at=now() WHERE id=$1",
                job_id,
            )
            row = await conn.fetchrow("SELECT * FROM video_jobs WHERE id=$1", job_id)
        return _row_to_job(row)

    if poll.status == "failed":
        async with acquire(tenant_id) as conn:
            await conn.execute(
                """UPDATE video_jobs SET status='failed', error=$2,
                   updated_at=now(), completed_at=now() WHERE id=$1""",
                job_id, poll.error,
            )
            row = await conn.fetchrow("SELECT * FROM video_jobs WHERE id=$1", job_id)
        return _row_to_job(row)

    # succeeded → land in the approval queue exactly once
    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """
            INSERT INTO actions (proposed_by, action_type, payload, status)
            VALUES ('video_engine', 'video', $1::jsonb, 'pending')
            RETURNING id
            """,
            json.dumps({
                "platform": job["payload"].get("platform", "—")
                if isinstance(job["payload"], dict) else "—",
                "format": "video",
                "content": job["prompt"],
                "caption": job["prompt"],
                "media_url": poll.result_url,
                "provider": job["provider"],
                "stub": str(poll.result_url or "").startswith("stub://"),
            }),
        )
        await conn.execute(
            """UPDATE video_jobs SET status='succeeded', result_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1""",
            job_id, poll.result_url, action_id,
        )
        row = await conn.fetchrow("SELECT * FROM video_jobs WHERE id=$1", job_id)
    return _row_to_job(row)


async def list_video_jobs(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM video_jobs ORDER BY created_at DESC LIMIT 100"
        )
    return [_row_to_job(r) for r in rows]


__all__ = [
    "submit_video_job", "refresh_video_job", "list_video_jobs",
    "get_video_provider",
]
