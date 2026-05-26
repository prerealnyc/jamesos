"""Final-cut assembly provider (Creatomate / Shotstack).

Stitches the rendered scene clips into one MP4 with sequential timing and
on-screen captions. Provider-abstracted with a stub so the pipeline proves
out without spending render credits and never emits a fake mp4.

`scenes` passed in are dicts with at least: url (clip), duration, on_screen_text.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from .config import settings

_TIMEOUT = httpx.Timeout(45.0, connect=10.0)


@dataclass
class RenderResult:
    status: str            # processing | succeeded | failed
    render_id: str = ""
    url: str | None = None
    error: str | None = None


class AssemblyProvider(ABC):
    name: str

    @abstractmethod
    async def render(self, scenes: list[dict], aspect: str) -> RenderResult: ...
    @abstractmethod
    async def poll(self, render_id: str) -> RenderResult: ...


class StubAssemblyProvider(AssemblyProvider):
    name = "stub"

    async def render(self, scenes: list[dict], aspect: str) -> RenderResult:
        # Honest marker — not a real mp4.
        return RenderResult(status="succeeded", render_id="stub",
                            url=f"stub://assembled/{len(scenes)}-scenes")

    async def poll(self, render_id: str) -> RenderResult:
        return RenderResult(status="succeeded", url=f"stub://assembled/{render_id}")


def _dims(aspect: str) -> tuple[int, int]:
    return (1080, 1920) if aspect == "9:16" else (1920, 1080)


def _entry_animation(kind: str | None) -> list[dict]:
    """Plan's transition_in → Creatomate entry animation. 'cut' = no
    animation (the default in Creatomate is an instant cut)."""
    k = (kind or "cut").lower()
    if k == "fade":
        return [{"time": 0, "duration": 0.4, "easing": "linear", "type": "fade"}]
    if k == "slide":
        return [{"time": 0, "duration": 0.4, "easing": "quadratic-out",
                 "type": "slide", "direction": "+100% 0%"}]
    return []  # 'cut' or unknown


def _logo_position(pos: str | None) -> dict:
    """Plan's branding_position → x/y/anchor for a Creatomate image element."""
    p = (pos or "").lower()
    margin = "6%"
    if p == "bottom-right":
        return {"x": f"calc(100% - {margin})", "y": f"calc(100% - {margin})",
                "x_anchor": "100%", "y_anchor": "100%"}
    if p == "bottom-center":
        return {"x": "50%", "y": f"calc(100% - {margin})",
                "x_anchor": "50%", "y_anchor": "100%"}
    if p == "top-right":
        return {"x": f"calc(100% - {margin})", "y": margin,
                "x_anchor": "100%", "y_anchor": "0%"}
    return {}  # 'none' or unknown — caller will skip


def _music_url_for(mood: str) -> str:
    """Returns the configured music URL for the mood, or '' if none set."""
    if not mood or mood == "none":
        return ""
    return {
        "upbeat": settings.music_url_upbeat,
        "calm": settings.music_url_calm,
        "dramatic": settings.music_url_dramatic,
        "tension": settings.music_url_tension,
    }.get(mood, "")


class CreatomateAssemblyProvider(AssemblyProvider):
    name = "creatomate"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("CREATOMATE_API_KEY is required")
        self.api_key = api_key

    def _build_source(self, scenes: list[dict], aspect: str) -> dict:
        """Build a Creatomate source applying the full production layer:
        clip + caption + transition_in + per-scene logo overlay + a single
        background music track. Anything not configured (no logo URL, no
        music URL for the chosen mood) is honestly skipped, not faked."""
        w, h = _dims(aspect)
        elements: list[dict] = []
        total_duration = 0.0
        t = 0.0
        first_mood = ""

        for s in scenes:
            dur = float(s.get("duration") or 5)
            total_duration += dur
            url = s.get("url") or ""
            anim = _entry_animation(s.get("transition_in"))

            if url and not url.startswith("stub://"):
                elem = {
                    "type": "video", "source": url,
                    "track": 1, "time": t, "duration": dur, "fit": "cover",
                }
                if anim:
                    elem["animations"] = anim
                elements.append(elem)

            cap = (s.get("on_screen_text") or "").strip()
            if cap:
                elements.append({
                    "type": "text", "text": cap, "track": 2, "time": t,
                    "duration": dur, "y": "82%", "width": "86%",
                    "font_family": "Montserrat", "font_weight": "700",
                    "font_size": "6.5 vh", "fill_color": "#ffffff",
                    "background_color": "rgba(0,0,0,0.55)", "x_alignment": "50%",
                })

            # Logo overlay — per-scene (so it can come/go per segment).
            if s.get("branding_logo") and settings.brand_logo_url:
                pos = _logo_position(s.get("branding_position"))
                if pos:
                    elements.append({
                        "type": "image", "source": settings.brand_logo_url,
                        "track": 3, "time": t, "duration": dur,
                        "width": "14%", **pos,
                    })

            if not first_mood:
                first_mood = (s.get("audio_music") or "").lower()
            t += dur

        # Background music: one track, full duration, mood from the first scene.
        music_url = _music_url_for(first_mood)
        if music_url and total_duration > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total_duration,
                "volume": 22,  # ducked under voiceover
            })

        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    async def render(self, scenes: list[dict], aspect: str) -> RenderResult:
        playable = [s for s in scenes if (s.get("url") or "").startswith("http")]
        if not playable:
            return RenderResult("failed", error="no real clip URLs to assemble (all stub)")
        body = {"source": self._build_source(scenes, aspect)}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post("https://api.creatomate.com/v1/renders",
                             headers={"Authorization": f"Bearer {self.api_key}",
                                      "Content-Type": "application/json"},
                             json=body)
        if r.status_code in (401, 403):
            return RenderResult("failed", error=f"Creatomate auth failed ({r.status_code})")
        if r.status_code >= 400:
            return RenderResult("failed", error=f"Creatomate HTTP {r.status_code}: {r.text[:160]}")
        data = r.json()
        item = data[0] if isinstance(data, list) and data else data
        rid = item.get("id")
        url = item.get("url")
        st = str(item.get("status", "")).lower()
        if st == "succeeded" and url:
            return RenderResult("succeeded", render_id=str(rid), url=url)
        return RenderResult("processing", render_id=str(rid))

    async def poll(self, render_id: str) -> RenderResult:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"https://api.creatomate.com/v1/renders/{render_id}",
                            headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            return RenderResult("failed", error=f"Creatomate poll HTTP {r.status_code}")
        data = r.json()
        st = str(data.get("status", "")).lower()
        if st in ("planned", "rendering", "transcribing", "waiting"):
            return RenderResult("processing", render_id=render_id)
        if st == "succeeded" and data.get("url"):
            return RenderResult("succeeded", render_id=render_id, url=data["url"])
        return RenderResult("failed", error=str(data.get("error") or f"status={st}"))


def make_assembly_provider() -> AssemblyProvider:
    p = (settings.assembly_provider or "stub").lower()
    if p == "creatomate" and settings.creatomate_api_key.strip():
        return CreatomateAssemblyProvider(api_key=settings.creatomate_api_key)
    return StubAssemblyProvider()


_provider: AssemblyProvider | None = None


def get_assembly_provider() -> AssemblyProvider:
    global _provider
    if _provider is None:
        _provider = make_assembly_provider()
    return _provider


__all__ = ["get_assembly_provider", "make_assembly_provider", "AssemblyProvider"]
