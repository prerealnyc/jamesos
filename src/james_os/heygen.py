"""Talking-head avatar provider (HeyGen).

Turns a scene's voiceover text into an avatar clip of James speaking it.
Provider-abstracted with a stub so the production pipeline runs end-to-end
without spending HeyGen credits and never returns a fake mp4.

Honest scope: HeyGen v2 needs both an avatar_id and a voice_id. We ship the
real call shaped correctly; if a voice_id isn't configured the real provider
says so plainly rather than guessing. The stub path always works.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import uuid4

import httpx

from .config import settings

_BASE = "https://api.heygen.com"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class AvatarSubmit:
    job_id: str
    status: str            # processing | failed
    error: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AvatarPoll:
    status: str            # processing | succeeded | failed
    url: str | None = None
    error: str | None = None


class AvatarProvider(ABC):
    name: str

    @abstractmethod
    async def submit(self, text: str, aspect: str, *, captions: bool = False) -> AvatarSubmit: ...
    @abstractmethod
    async def poll(self, job_id: str) -> AvatarPoll: ...


class StubAvatarProvider(AvatarProvider):
    name = "stub"

    async def submit(self, text: str, aspect: str, *, captions: bool = False) -> AvatarSubmit:
        return AvatarSubmit(job_id=f"stub-avatar-{uuid4()}", status="processing")

    async def poll(self, job_id: str) -> AvatarPoll:
        return AvatarPoll(status="succeeded", url=f"stub://avatar/{job_id}")

    async def upload_talking_photo(self, image_bytes: bytes, mime: str = "image/png") -> tuple[str | None, str]:
        return f"stub-tp-{uuid4()}", ""

    async def submit_talking_photo(
        self, talking_photo_id: str, text: str, aspect: str, *, captions: bool = False
    ) -> AvatarSubmit:
        return AvatarSubmit(job_id=f"stub-avatar-{uuid4()}", status="processing")


def _dims(aspect: str) -> dict:
    return {"width": 720, "height": 1280} if aspect == "9:16" else {"width": 1280, "height": 720}


class HeyGenAvatarProvider(AvatarProvider):
    name = "heygen"

    def __init__(self, api_key: str, avatar_id: str, voice_id: str):
        if not api_key:
            raise ValueError("HEYGEN_API_KEY is required for HeyGen avatar")
        self.api_key = api_key
        self.avatar_id = avatar_id
        self.voice_id = voice_id

    def _h(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    async def submit(self, text: str, aspect: str, *, captions: bool = False) -> AvatarSubmit:
        if not self.avatar_id:
            return AvatarSubmit("", "failed", error="HEYGEN_AVATAR_ID not set")
        if not self.voice_id:
            return AvatarSubmit(
                "", "failed",
                error="HeyGen needs a voice_id (set HEYGEN_VOICE_ID) to speak text",
            )
        body = {
            "video_inputs": [{
                "character": {"type": "avatar", "avatar_id": self.avatar_id,
                              "avatar_style": "normal"},
                "voice": {"type": "text", "input_text": text[:1500],
                          "voice_id": self.voice_id},
            }],
            "dimension": _dims(aspect),
        }
        # HeyGen burns in subtitles of exactly what's spoken when this flag
        # is on — used in avatar-only mode where the user wants the spoken
        # words as the only on-screen text. Mixed mode leaves this off so
        # Creatomate's per-scene captions stay the only overlay.
        if captions:
            body["caption"] = True
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(f"{_BASE}/v2/video/generate", headers=self._h(), json=body)
        except httpx.HTTPError as e:
            return AvatarSubmit("", "failed", error=f"HeyGen submit transport error: {e}")
        if r.status_code in (401, 403):
            return AvatarSubmit("", "failed", error=f"HeyGen auth failed ({r.status_code})")
        if r.status_code >= 400:
            return AvatarSubmit("", "failed", error=f"HeyGen HTTP {r.status_code}: {r.text[:160]}")
        vid = (r.json().get("data") or {}).get("video_id")
        if not vid:
            return AvatarSubmit("", "failed", error=f"HeyGen returned no video_id: {r.text[:160]}")
        return AvatarSubmit(job_id=str(vid), status="processing", raw=r.json())

    async def poll(self, job_id: str) -> AvatarPoll:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.get(f"{_BASE}/v1/video_status.get",
                                headers=self._h(), params={"video_id": job_id})
        except httpx.HTTPError as e:
            return AvatarPoll("failed", error=f"HeyGen poll transport error: {e}")
        if r.status_code >= 400:
            return AvatarPoll("failed", error=f"HeyGen poll HTTP {r.status_code}")
        data = r.json().get("data") or {}
        st = str(data.get("status", "")).lower()
        if st in ("processing", "pending", "waiting"):
            return AvatarPoll("processing")
        if st == "completed":
            url = data.get("video_url")
            return AvatarPoll("succeeded", url=url) if url else AvatarPoll("failed", error="no url")
        return AvatarPoll("failed", error=str(data.get("error") or f"status={st}"))

    # ── Talking Photo (clone the hero from a still) ──────────────────
    # Animate a real PHOTO into a lip-synced talking clip in James's voice.
    # Upload host differs from the API host; the v2 generate + v1 status
    # poll above are reused verbatim (talking_photo is just a character type).
    async def upload_talking_photo(
        self, image_bytes: bytes, mime: str = "image/png"
    ) -> tuple[str | None, str]:
        headers = {"X-Api-Key": self.api_key, "Content-Type": mime}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as c:
                r = await c.post(
                    "https://upload.heygen.com/v1/talking_photo",
                    headers=headers, content=image_bytes,
                )
        except Exception as e:  # noqa: BLE001 — surface honestly
            return None, f"HeyGen talking-photo upload error: {e}"
        if r.status_code in (401, 403):
            return None, f"HeyGen auth failed ({r.status_code})"
        if r.status_code >= 400:
            return None, f"HeyGen talking-photo upload HTTP {r.status_code}: {r.text[:160]}"
        tp = (r.json().get("data") or {}).get("talking_photo_id")
        return (str(tp), "") if tp else (None, f"no talking_photo_id returned: {r.text[:160]}")

    async def submit_talking_photo(
        self, talking_photo_id: str, text: str, aspect: str, *, captions: bool = False
    ) -> AvatarSubmit:
        if not self.voice_id:
            return AvatarSubmit(
                "", "failed",
                error="HeyGen needs a voice_id (set HEYGEN_VOICE_ID) to speak text",
            )
        body = {
            "video_inputs": [{
                "character": {"type": "talking_photo", "talking_photo_id": talking_photo_id},
                "voice": {"type": "text", "input_text": text[:1500], "voice_id": self.voice_id},
            }],
            "dimension": _dims(aspect),
        }
        if captions:
            body["caption"] = True
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(f"{_BASE}/v2/video/generate", headers=self._h(), json=body)
        except httpx.HTTPError as e:
            return AvatarSubmit("", "failed", error=f"HeyGen submit transport error: {e}")
        if r.status_code in (401, 403):
            return AvatarSubmit("", "failed", error=f"HeyGen auth failed ({r.status_code})")
        if r.status_code >= 400:
            return AvatarSubmit("", "failed", error=f"HeyGen HTTP {r.status_code}: {r.text[:160]}")
        vid = (r.json().get("data") or {}).get("video_id")
        if not vid:
            return AvatarSubmit("", "failed", error=f"HeyGen returned no video_id: {r.text[:160]}")
        return AvatarSubmit(job_id=str(vid), status="processing", raw=r.json())


def make_avatar_provider() -> AvatarProvider:
    if (settings.avatar_provider or "stub").lower() == "heygen" and settings.heygen_api_key.strip():
        return HeyGenAvatarProvider(
            api_key=settings.heygen_api_key,
            avatar_id=settings.heygen_avatar_id,
            voice_id=getattr(settings, "heygen_voice_id", ""),
        )
    return StubAvatarProvider()


_provider: AvatarProvider | None = None


def get_avatar_provider() -> AvatarProvider:
    global _provider
    if _provider is None:
        _provider = make_avatar_provider()
    return _provider


__all__ = ["get_avatar_provider", "make_avatar_provider", "AvatarProvider"]
