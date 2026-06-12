"""Final-cut assembly provider (Creatomate / Shotstack).

Stitches the rendered scene clips into one MP4 with sequential timing and
on-screen captions. Provider-abstracted with a stub so the pipeline proves
out without spending render credits and never emits a fake mp4.

`scenes` passed in are dicts with at least: url (clip), duration, on_screen_text.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from .caption_styles import caption_element, get_preset, styled_caption_elements
from .audio_library import resolve_music_url, resolve_sfx_url
from .brand_kit import get_brand_kit
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

    async def render_story(self, **kwargs) -> RenderResult:
        beats = kwargs.get("beats") or []
        return RenderResult(
            status="succeeded", render_id="stub-story",
            url=f"stub://assembled/story-{len(beats)}-beats",
        )

    async def render_avatar_story_mix(self, **kwargs) -> RenderResult:
        beats = kwargs.get("beats") or []
        return RenderResult(
            status="succeeded", render_id="stub-mix",
            url=f"stub://assembled/mix-{len(beats)}-beats",
        )

    async def render_engaging_avatar(self, **kwargs) -> RenderResult:
        inserts = kwargs.get("inserts") or []
        return RenderResult(
            status="succeeded", render_id="stub-engaging",
            url=f"stub://assembled/engaging-{len(inserts)}-inserts",
        )

    async def render_split_horizontal(self, **kwargs) -> RenderResult:
        inserts = kwargs.get("inserts") or []
        return RenderResult(
            status="succeeded", render_id="stub-split",
            url=f"stub://assembled/split-{len(inserts)}-inserts",
        )

    async def render_split_vertical(self, **kwargs) -> RenderResult:
        inserts = kwargs.get("inserts") or []
        return RenderResult(
            status="succeeded", render_id="stub-split-v",
            url=f"stub://assembled/splitv-{len(inserts)}-inserts",
        )

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


def _ken_burns(duration: float, kind: str = "in") -> list[dict]:
    """Subtle scale animation so a still image doesn't read as a freeze
    frame. Direction alternates between beats to keep the slideshow
    visually alive. Conservative — 100%→106% in 'in' / 106%→100% in
    'out' — anything stronger feels like a 90s real-estate ad."""
    if duration <= 0:
        return []
    if kind == "out":
        return [{"time": 0, "duration": duration, "type": "scale",
                 "scope": "element", "easing": "linear",
                 "start_scale": "106%", "end_scale": "100%"}]
    return [{"time": 0, "duration": duration, "type": "scale",
             "scope": "element", "easing": "linear",
             "start_scale": "100%", "end_scale": "106%"}]


def _zoom_punches(total: float, period: float = 7.0, hold: float = 2.6) -> list[dict]:
    """Retention punch-ins on the speaker: a quick 100→107% zoom every ~period
    seconds that snaps back after `hold` — reads as a pro jump-cut edit."""
    anims: list[dict] = []
    t = period * 0.6
    while t + hold + 0.4 < total:
        anims.append({"time": round(t, 2), "duration": 0.18, "type": "scale",
                      "scope": "element", "easing": "quadratic-out",
                      "start_scale": "100%", "end_scale": "107%"})
        anims.append({"time": round(t + hold, 2), "duration": 0.14, "type": "scale",
                      "scope": "element", "easing": "quadratic-in",
                      "start_scale": "107%", "end_scale": "100%"})
        t += period
    return anims


def _watermark_element(logo_url: str, total: float, track: int = 8) -> list[dict]:
    """Small brand logo pinned top-right for the whole video."""
    if not (logo_url or "").startswith("http"):
        return []
    return [{
        "type": "image", "source": logo_url,
        "track": track, "time": 0, "duration": total,
        "width": "12%", "x": "calc(100% - 4%)", "y": "4%",
        "x_anchor": "100%", "y_anchor": "0%", "fit": "contain",
    }]


def _nameplate_elements(name: str, tagline: str) -> list[dict]:
    """Lower-third identity plate for the first ~3s."""
    if not (name or "").strip():
        return []
    fade = [{"time": 0, "duration": 0.35, "type": "fade"}]
    shadow = {"shadow_color": "rgba(0,0,0,0.7)", "shadow_blur": "1.0 vh",
              "shadow_x": "0 vh", "shadow_y": "0.25 vh"}
    out = [{
        "type": "text", "text": name.strip().upper(),
        "track": 6, "time": 0.7, "duration": 3.0,
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        "y": "80%", "y_anchor": "50%", "width": "86%",
        "font_family": "Montserrat", "font_weight": "800",
        "font_size": "3.6 vh", "fill_color": "#FFFFFF",
        "letter_spacing": "2%", "animations": fade, **shadow,
    }]
    if (tagline or "").strip():
        out.append({
            "type": "text", "text": tagline.strip(),
            "track": 7, "time": 0.7, "duration": 3.0,
            "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
            "y": "84.5%", "y_anchor": "50%", "width": "86%",
            "font_family": "Montserrat", "font_weight": "600",
            "font_size": "2.4 vh", "fill_color": "#E8E8E8",
            "animations": fade, **shadow,
        })
    return out


def _endcard_elements(handle: str, logo_url: str, total: float) -> list[dict]:
    """Last ~2.6s: FOLLOW FOR MORE + handle + logo over the footage."""
    if total < 8:
        return []
    start = round(max(0.0, total - 2.6), 2)
    dur = round(total - start, 2)
    fade = [{"time": 0, "duration": 0.3, "type": "fade"}]
    shadow = {"shadow_color": "rgba(0,0,0,0.75)", "shadow_blur": "1.2 vh",
              "shadow_x": "0 vh", "shadow_y": "0.3 vh"}
    out = [{
        "type": "text", "text": "FOLLOW FOR MORE",
        "track": 6, "time": start, "duration": dur,
        "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
        "y": "40%", "y_anchor": "50%", "width": "88%",
        "font_family": "Archivo Black", "font_weight": "900",
        "font_size": "6.2 vh", "fill_color": "#FFFFFF",
        "animations": fade, **shadow,
    }]
    if (handle or "").strip():
        out.append({
            "type": "text", "text": handle.strip(),
            "track": 7, "time": start, "duration": dur,
            "x": "50%", "x_anchor": "50%", "x_alignment": "50%",
            "y": "49%", "y_anchor": "50%", "width": "88%",
            "font_family": "Montserrat", "font_weight": "700",
            "font_size": "4.2 vh", "fill_color": "#FFE600",
            "animations": fade, **shadow,
        })
    if (logo_url or "").startswith("http"):
        out.append({
            "type": "image", "source": logo_url,
            "track": 9, "time": start, "duration": dur,
            "width": "20%", "x": "50%", "y": "61%",
            "x_anchor": "50%", "y_anchor": "50%", "fit": "contain",
            "animations": fade,
        })
    return out


def _progress_bar_element(total: float, color: str = "#C8102E") -> list[dict]:
    """Thin top progress bar — background-only text element whose width is
    keyframed 0→100% across the video (Creatomate keyframe-array form)."""
    if total <= 0:
        return []
    return [{
        "type": "text", "text": " ",
        "track": 10, "time": 0, "duration": total,
        "x": "0%", "x_anchor": "0%", "y": "0%", "y_anchor": "0%",
        "width": [
            {"time": 0, "value": "0%"},
            {"time": round(total, 2), "value": "100%"},
        ],
        "font_size": "0.7 vh",
        "background_color": color,
    }]


def _polish_elements(
    brand: dict | None, total: float,
    sfx_hit_url: str = "", sfx_riser_url: str = "",
) -> list[dict]:
    """The shared brand + retention layer appended by every builder."""
    b = brand or {}
    out: list[dict] = []
    out += _nameplate_elements(b.get("display_name", ""), b.get("tagline", ""))
    out += _watermark_element(b.get("logo_url", ""), total)
    out += _endcard_elements(b.get("handle", ""), b.get("logo_url", ""), total)
    out += _progress_bar_element(total)
    if (sfx_hit_url or "").startswith("http"):
        out.append({"type": "audio", "source": sfx_hit_url, "track": 11,
                    "time": 0.1, "duration": 1.0, "volume": 65})
    if (sfx_riser_url or "").startswith("http") and total > 10:
        out.append({"type": "audio", "source": sfx_riser_url, "track": 11,
                    "time": round(max(0.0, total - 3.4), 2), "duration": 3.0,
                    "volume": 55})
    return out



class CreatomateAssemblyProvider(AssemblyProvider):
    name = "creatomate"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("CREATOMATE_API_KEY is required")
        self.api_key = api_key

    def build_story_source(
        self, *,
        audio_url: str,
        audio_duration: float,
        beats: list[dict],            # [{start, end, image_url, text}, …]
        captions: list[dict],          # [{start, end, text}, …]
        aspect: str,
        music_mood: str = "none",
        caption_style: str | None = None,
        music_track_url: str | None = None,   # library override (audio_library)
        sfx_url: str = "",                     # whoosh at cutaway starts; '' = no layer
        brand: dict | None = None,             # brand kit (nameplate/watermark/endcard)
        sfx_hit_url: str = "",
        sfx_riser_url: str = "",
    ) -> dict:
        """Build a Creatomate source for the story_audio mode.

        Track layout:
          * track 1 — the voice MP3 (whole duration, full volume)
          * track 2 — one image per beat, pinned to its [start, end]
            window, with alternating Ken-Burns zoom so consecutive
            stills don't feel static
          * track 3 — burned-in word-group captions on top of the image,
            timed to the original Whisper word timestamps
          * track 4 — optional background music ducked under the voice

        Beats without `image_url` are silently dropped — story_audio
        already refuses to assemble when more than half are missing, so
        this is just the "1-2 individual misses" fallback path.
        """
        w, h = _dims(aspect)
        elements: list[dict] = []
        total = max(audio_duration, beats[-1]["end"] if beats else 0.0)

        # 1) voice
        if audio_url and audio_url.startswith("http"):
            elements.append({
                "type": "audio", "source": audio_url,
                "track": 1, "time": 0, "duration": total,
            })

        # 2) image stills (per beat, with Ken Burns)
        for i, b in enumerate(beats):
            url = (b.get("image_url") or "").strip()
            if not url or not url.startswith("http"):
                continue
            start = float(b.get("start") or 0.0)
            end = float(b.get("end") or start)
            dur = max(0.1, end - start)
            elements.append({
                "type": "image", "source": url,
                "track": 2, "time": start, "duration": dur, "fit": "cover",
                "animations": _ken_burns(dur, "out" if i % 2 else "in"),
            })

        # 3) captions — preset-driven + beat-aware safe zone. story_audio
        # is faceless, so every beat is broll; the role lookup below
        # still runs so the two source builders stay symmetric.
        preset = get_preset(caption_style)
        _styled = styled_caption_elements(caption_style, captions, track=3)
        if _styled is not None:
            # Designer styles (viral_hook, magenta_blocks, editorial_serif,
            # gradient_mint) emit their complete multi-element track here;
            # blank the list so the standard loop below no-ops.
            elements.extend(_styled)
            captions = []
        for c in captions:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            start = float(c.get("start") or 0.0)
            end = float(c.get("end") or start)
            midpoint = (start + end) / 2
            role = "broll"
            for b in beats:
                bs = float(b.get("start") or 0.0)
                be = float(b.get("end") or bs)
                if bs <= midpoint < be:
                    role = (b.get("role") or "broll").lower()
                    break
            elements.append(caption_element(
                text=text, start=start, end=end, preset=preset, track=3,
                role=role,
            ))

        # 4) optional music
        music_url = music_track_url or _music_url_for(music_mood)
        if music_url and total > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total,
                "volume": 18,
            })

        elements += _polish_elements(brand, total, sfx_hit_url, sfx_riser_url)
        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    def build_avatar_story_mix_source(
        self, *,
        audio_url: str,
        audio_duration: float,
        beats: list[dict],            # [{start, end, role, image_url|video_url, …}]
        captions: list[dict],
        aspect: str,
        music_mood: str = "none",
        caption_style: str | None = None,
        music_track_url: str | None = None,   # library override (audio_library)
        sfx_url: str = "",                     # whoosh at cutaway starts; '' = no layer
        brand: dict | None = None,             # brand kit (nameplate/watermark/endcard)
        sfx_hit_url: str = "",
        sfx_riser_url: str = "",
    ) -> dict:
        """Mixed avatar-on-camera + AI-still source.

        Same 4-track layout as build_story_source, but track 2 alternates
        between video and image elements depending on each beat's role:

          * role='avatar' + video_url present → video element pinned to
            the beat window, volume=0 (master audio carries the voice
            on track 1, so playing the slice's own audio would echo).
          * role='broll' + image_url present → image element with Ken
            Burns, same as story_audio.

        A beat with neither URL is silently skipped (the upstream
        builder already refused to assemble when too many were missing).
        """
        w, h = _dims(aspect)
        elements: list[dict] = []
        total = max(audio_duration, beats[-1]["end"] if beats else 0.0)

        # track 1 — continuous HeyGen voice
        if audio_url and audio_url.startswith("http"):
            elements.append({
                "type": "audio", "source": audio_url,
                "track": 1, "time": 0, "duration": total,
            })

        # track 2 — alternating avatar slice / B-roll still
        for i, b in enumerate(beats):
            role = (b.get("role") or "broll").lower()
            start = float(b.get("start") or 0.0)
            end = float(b.get("end") or start)
            dur = max(0.1, end - start)
            if role == "avatar":
                vurl = (b.get("video_url") or "").strip()
                if not vurl or not vurl.startswith("http"):
                    continue
                elements.append({
                    "type": "video", "source": vurl,
                    "track": 2, "time": start, "duration": dur,
                    "fit": "cover",
                    "volume": 0,            # mute — master audio carries voice
                })
            else:
                iurl = (b.get("image_url") or "").strip()
                if not iurl or not iurl.startswith("http"):
                    continue
                elements.append({
                    "type": "image", "source": iurl,
                    "track": 2, "time": start, "duration": dur,
                    "fit": "cover",
                    "animations": _ken_burns(dur, "out" if i % 2 else "in"),
                })

        # track 3 — captions (preset-driven + beat-aware safe zone)
        #
        # For each caption flash, find the beat it falls in by midpoint
        # match, then pass that beat's role to caption_element so the y
        # avoids the speaker's face on avatar beats. See the SAFE_ZONES
        # table in caption_styles.py for the actual offsets.
        preset = get_preset(caption_style)
        _styled = styled_caption_elements(caption_style, captions, track=3)
        if _styled is not None:
            # Designer styles (viral_hook, magenta_blocks, editorial_serif,
            # gradient_mint) emit their complete multi-element track here;
            # blank the list so the standard loop below no-ops.
            elements.extend(_styled)
            captions = []
        for c in captions:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            start = float(c.get("start") or 0.0)
            end = float(c.get("end") or start)
            midpoint = (start + end) / 2
            role = "default"
            for b in beats:
                bs = float(b.get("start") or 0.0)
                be = float(b.get("end") or bs)
                if bs <= midpoint < be:
                    role = (b.get("role") or "default").lower()
                    break
            elements.append(caption_element(
                text=text, start=start, end=end, preset=preset, track=3,
                role=role,
            ))

        # track 4 — optional background music (ducked further than story
        # because the master voice is louder/more present in this mode)
        music_url = music_track_url or _music_url_for(music_mood)
        if music_url and total > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total,
                "volume": 15,
            })

        elements += _polish_elements(brand, total, sfx_hit_url, sfx_riser_url)
        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    def build_engaging_avatar_source(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict],          # [{start, end, image_url, text, …}]
        captions: list[dict],
        aspect: str,
        music_mood: str = "none",
        caption_style: str | None = None,
        music_track_url: str | None = None,   # library override (audio_library)
        sfx_url: str = "",                     # whoosh at cutaway starts; '' = no layer
        brand: dict | None = None,             # brand kit (nameplate/watermark/endcard)
        sfx_hit_url: str = "",
        sfx_riser_url: str = "",
    ) -> dict:
        """engaging_avatar layout. The avatar video carries its own
        audio across the whole timeline; B-roll images overlay on top
        for short windows where the LLM wants visual punctuation.

        Tracks:
          1 — HeyGen avatar video (full duration, includes audio)
          2 — B-roll image overlays at each insert window, with a
              short fade in/out so the cut doesn't feel jarring
          3 — word-pinned captions (safe-zone aware: when an insert
              overlaps, captions take the broll y position; otherwise
              avatar y to clear his face)
          4 — optional background music ducked under the avatar voice
        """
        w, h = _dims(aspect)
        elements: list[dict] = []
        total = max(audio_duration, inserts[-1]["end"] if inserts else 0.0)

        # track 1 — the avatar video carries its own audio
        if avatar_video_url and avatar_video_url.startswith("http"):
            elements.append({
                "type": "video", "source": avatar_video_url,
                "track": 1, "time": 0, "duration": total, "fit": "cover",
                "animations": _zoom_punches(total),
            })

        # track 2 — insert overlays with short fade in/out. Prefer the
        # Runway-animated video clip when available so the cutaway has
        # real motion; fall back to the static gpt-image-1 still when
        # only the image rendered. Video elements are muted (volume=0)
        # because the avatar's voice on track 1 is the master audio.
        for ins in inserts:
            video_url = (ins.get("video_url") or "").strip()
            image_url = (ins.get("image_url") or "").strip()
            url = video_url if video_url.startswith("http") else image_url
            if not url or not url.startswith("http"):
                continue
            start = float(ins.get("start") or 0.0)
            end = float(ins.get("end") or start)
            dur = max(0.4, end - start)
            common = {
                "track": 2, "time": start, "duration": dur, "fit": "cover",
                "animations": [
                    {"time": 0, "duration": 0.15, "type": "fade"},
                    {"time": max(0, dur - 0.15), "duration": 0.15,
                     "type": "fade", "reversed": True},
                ],
            }
            if video_url.startswith("http"):
                elements.append({
                    "type": "video", "source": video_url,
                    "volume": 0,        # avatar audio carries the voice
                    **common,
                })
            else:
                elements.append({
                    "type": "image", "source": image_url,
                    **common,
                })

        # track 3 — captions with safe-zone awareness. For each flash,
        # treat as broll-zone iff an insert overlays its midpoint.
        preset = get_preset(caption_style)
        _styled = styled_caption_elements(caption_style, captions, track=3)
        if _styled is not None:
            # Designer styles (viral_hook, magenta_blocks, editorial_serif,
            # gradient_mint) emit their complete multi-element track here;
            # blank the list so the standard loop below no-ops.
            elements.extend(_styled)
            captions = []
        for c in captions:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            start = float(c.get("start") or 0.0)
            end = float(c.get("end") or start)
            midpoint = (start + end) / 2
            role = "avatar"
            for ins in inserts:
                ins_start = float(ins.get("start") or 0.0)
                ins_end = float(ins.get("end") or ins_start)
                if ins_start <= midpoint < ins_end:
                    role = "broll"
                    break
            elements.append(caption_element(
                text=text, start=start, end=end, preset=preset, track=3,
                role=role,
            ))

        # track 5 — transition whoosh at each cutaway start (only when the
        # audio library has one; '' skips the layer, never a broken element).
        if sfx_url.startswith("http"):
            for ins in inserts:
                if not ((ins.get("video_url") or ins.get("image_url") or "").strip().startswith("http")):
                    continue
                _t = float(ins.get("start") or 0.0)
                elements.append({
                    "type": "audio", "source": sfx_url,
                    "track": 5, "time": round(max(0.0, _t - 0.15), 2),
                    "duration": 0.7, "volume": 60,
                })

        # track 4 — optional music heavily ducked under avatar voice
        music_url = music_track_url or _music_url_for(music_mood)
        if music_url and total > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total,
                "volume": 12,
            })

        elements += _polish_elements(brand, total, sfx_hit_url, sfx_riser_url)
        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    def build_split_horizontal_source(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict],          # [{start, end, image_url, video_url, …}]
        captions: list[dict],
        aspect: str,
        music_mood: str = "none",
        caption_style: str | None = None,
        music_track_url: str | None = None,   # library override (audio_library)
        sfx_url: str = "",                     # whoosh at cutaway starts; '' = no layer
        brand: dict | None = None,             # brand kit (nameplate/watermark/endcard)
        sfx_hit_url: str = "",
        sfx_riser_url: str = "",
    ) -> dict:
        """split_horizontal layout — reproduces the "speaker on top, text /
        visuals on the bottom" reel composition the Design Inspector captures
        as layout.type == 'split_horizontal'.

        Unlike engaging_avatar (a FULL-FRAME speaker with transient B-roll
        cutaways), here the two regions are CO-PRESENT for the whole video —
        the speaker never leaves the top, the bottom is always the content
        panel. This is the structural difference the audit flagged as the
        reason replication "gave the same style": engaging_avatar can't emit
        two stacked regions, this can.

          track 1 — speaker video, constrained to the TOP half (carries audio)
          track 2 — B-roll image/video, constrained to the BOTTOM half, pinned
                    to its insert window; muted (speaker is the master audio)
          track 3 — bold captions pinned LOW (role='broll' → ~75% y) so the
                    text lives in the bottom panel, never over the face
          track 4 — optional background music ducked under the voice

        Bottom-region gaps fall back to the composition's dark canvas, reading
        as a clean text panel — a faithful v1 of the reference's bottom strip.
        A persistent decorated panel (brand color / UI chrome) is a later pass.
        """
        w, h = _dims(aspect)
        elements: list[dict] = []
        total = max(audio_duration, inserts[-1]["end"] if inserts else 0.0)

        # Region geometry. Top element pinned by its TOP edge to y=0%; bottom
        # element pinned by its BOTTOM edge to y=100%. Each is exactly half the
        # canvas height, full width — the two halves tile the frame with no gap.
        TOP = {"x": "50%", "y": "0%", "width": "100%", "height": "50%",
               "x_anchor": "50%", "y_anchor": "0%"}
        BOTTOM = {"x": "50%", "y": "100%", "width": "100%", "height": "50%",
                  "x_anchor": "50%", "y_anchor": "100%"}

        # track 1 — speaker pinned to the TOP half, carries the master audio.
        # 'cover' fills the panel edge-to-edge; the caller renders the split
        # speaker at 16:9 so cover keeps the FULL face height and only trims the
        # empty sides (a 9:16 source here would crop the forehead/eyes).
        if avatar_video_url and avatar_video_url.startswith("http"):
            elements.append({
                "type": "video", "source": avatar_video_url,
                "track": 1, "time": 0, "duration": total, "fit": "cover",
                "animations": _zoom_punches(total),
                **TOP,
            })

        # track 2 — B-roll in the BOTTOM half, pinned to each insert window.
        # Prefer the Runway-animated clip; fall back to the still. Video is
        # muted because the speaker on track 1 is the master audio.
        for ins in inserts:
            video_url = (ins.get("video_url") or "").strip()
            image_url = (ins.get("image_url") or "").strip()
            url = video_url if video_url.startswith("http") else image_url
            if not url or not url.startswith("http"):
                continue
            start = float(ins.get("start") or 0.0)
            end = float(ins.get("end") or start)
            dur = max(0.4, end - start)
            common = {
                "track": 2, "time": start, "duration": dur, "fit": "cover",
                **BOTTOM,
                "animations": [
                    {"time": 0, "duration": 0.15, "type": "fade"},
                    {"time": max(0, dur - 0.15), "duration": 0.15,
                     "type": "fade", "reversed": True},
                ],
            }
            if video_url.startswith("http"):
                elements.append({"type": "video", "source": video_url,
                                 "volume": 0, **common})
            else:
                elements.append({"type": "image", "source": image_url, **common})

        # track 3 — bold captions in the MIDDLE band, on the seam between the
        # top speaker and the bottom B-roll. Spec: top 50% speaker, bottom 50%
        # B-roll, captions across the centre (not buried in the bottom panel).
        preset = get_preset(caption_style)
        _styled = styled_caption_elements(caption_style, captions, track=3)
        if _styled is not None:
            # Designer styles (viral_hook, magenta_blocks, editorial_serif,
            # gradient_mint) emit their complete multi-element track here;
            # blank the list so the standard loop below no-ops.
            elements.extend(_styled)
            captions = []
        for c in captions:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            start = float(c.get("start") or 0.0)
            end = float(c.get("end") or start)
            elem = caption_element(
                text=text, start=start, end=end, preset=preset, track=3,
                role="broll",
            )
            elem["y"] = "50%"          # centre on the horizontal seam
            elem["y_anchor"] = "50%"
            elements.append(elem)

        # track 5 — transition whoosh at each cutaway start (only when the
        # audio library has one; '' skips the layer, never a broken element).
        if sfx_url.startswith("http"):
            for ins in inserts:
                if not ((ins.get("video_url") or ins.get("image_url") or "").strip().startswith("http")):
                    continue
                _t = float(ins.get("start") or 0.0)
                elements.append({
                    "type": "audio", "source": sfx_url,
                    "track": 5, "time": round(max(0.0, _t - 0.15), 2),
                    "duration": 0.7, "volume": 60,
                })

        # track 4 — optional music heavily ducked under the speaker voice
        music_url = music_track_url or _music_url_for(music_mood)
        if music_url and total > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total,
                "volume": 12,
            })

        elements += _polish_elements(brand, total, sfx_hit_url, sfx_riser_url)
        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    async def render_split_horizontal(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict], captions: list[dict],
        aspect: str, music_mood: str = "none",
        caption_style: str | None = None,
    ) -> RenderResult:
        """Submit a split_horizontal render. Same submit/poll contract as
        render_engaging_avatar — only the source composition differs."""
        if not (avatar_video_url or "").startswith("http"):
            return RenderResult("failed", error="no avatar video URL")
        source = self.build_split_horizontal_source(
            avatar_video_url=avatar_video_url,
            audio_duration=audio_duration,
            inserts=inserts, captions=captions,
            aspect=aspect, music_mood=music_mood,
            caption_style=caption_style,
            music_track_url=await resolve_music_url(music_mood),
            sfx_url=await resolve_sfx_url("whoosh"),
            brand=await get_brand_kit(),
            sfx_hit_url=await resolve_sfx_url("hit"),
            sfx_riser_url=await resolve_sfx_url("riser"),
        )
        body = {"source": source}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.creatomate.com/v1/renders",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
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

    def build_split_vertical_source(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict],
        captions: list[dict],
        aspect: str,
        music_mood: str = "none",
        caption_style: str | None = None,
        music_track_url: str | None = None,   # library override (audio_library)
        sfx_url: str = "",                     # whoosh at cutaway starts; '' = no layer
        brand: dict | None = None,             # brand kit (nameplate/watermark/endcard)
        sfx_hit_url: str = "",
        sfx_riser_url: str = "",
    ) -> dict:
        """split_vertical layout — speaker on the LEFT half, B-roll + bold text
        on the RIGHT half (divided by a VERTICAL line). Mirror of
        build_split_horizontal_source but left|right instead of top|bottom.

          track 1 — speaker video, constrained to the LEFT half (carries audio).
                    A 9:16 source cover-cropped into the tall-narrow column keeps
                    the full face height and trims the sides.
          track 2 — B-roll image/video, constrained to the RIGHT half, pinned to
                    its insert window; muted (speaker is the master audio).
          track 3 — bold captions constrained to the RIGHT column (the content
                    side), so they read as the panel text, clear of the speaker.
          track 4 — optional background music ducked under the voice.
        """
        w, h = _dims(aspect)
        elements: list[dict] = []
        total = max(audio_duration, inserts[-1]["end"] if inserts else 0.0)

        # Left element pinned by its LEFT edge to x=0%; right element by its
        # RIGHT edge to x=100%. Each is half-width, full-height — they tile the
        # frame with a vertical seam down the middle.
        LEFT = {"x": "0%", "y": "50%", "width": "50%", "height": "100%",
                "x_anchor": "0%", "y_anchor": "50%"}
        RIGHT = {"x": "100%", "y": "50%", "width": "50%", "height": "100%",
                 "x_anchor": "100%", "y_anchor": "50%"}

        # track 1 — speaker pinned to the LEFT half, carries the master audio.
        if avatar_video_url and avatar_video_url.startswith("http"):
            elements.append({
                "type": "video", "source": avatar_video_url,
                "track": 1, "time": 0, "duration": total, "fit": "cover",
                "animations": _zoom_punches(total),
                **LEFT,
            })

        # track 2 — B-roll in the RIGHT half, pinned to each insert window.
        for ins in inserts:
            video_url = (ins.get("video_url") or "").strip()
            image_url = (ins.get("image_url") or "").strip()
            url = video_url if video_url.startswith("http") else image_url
            if not url or not url.startswith("http"):
                continue
            start = float(ins.get("start") or 0.0)
            end = float(ins.get("end") or start)
            dur = max(0.4, end - start)
            common = {
                "track": 2, "time": start, "duration": dur, "fit": "cover",
                **RIGHT,
                "animations": [
                    {"time": 0, "duration": 0.15, "type": "fade"},
                    {"time": max(0, dur - 0.15), "duration": 0.15,
                     "type": "fade", "reversed": True},
                ],
            }
            if video_url.startswith("http"):
                elements.append({"type": "video", "source": video_url,
                                 "volume": 0, **common})
            else:
                elements.append({"type": "image", "source": image_url, **common})

        # track 3 — captions constrained to the RIGHT column (content side).
        preset = get_preset(caption_style)
        _styled = styled_caption_elements(caption_style, captions, track=3)
        if _styled is not None:
            # Designer styles (viral_hook, magenta_blocks, editorial_serif,
            # gradient_mint) emit their complete multi-element track here;
            # blank the list so the standard loop below no-ops.
            elements.extend(_styled)
            captions = []
        for c in captions:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            start = float(c.get("start") or 0.0)
            end = float(c.get("end") or start)
            elem = caption_element(
                text=text, start=start, end=end, preset=preset, track=3,
                role="broll",
            )
            # Pull the caption into the right half: centre on the right column,
            # narrow the box so it stays clear of the speaker on the left.
            elem["x"] = "75%"
            elem["width"] = "44%"
            elements.append(elem)

        # track 5 — transition whoosh at each cutaway start (only when the
        # audio library has one; '' skips the layer, never a broken element).
        if sfx_url.startswith("http"):
            for ins in inserts:
                if not ((ins.get("video_url") or ins.get("image_url") or "").strip().startswith("http")):
                    continue
                _t = float(ins.get("start") or 0.0)
                elements.append({
                    "type": "audio", "source": sfx_url,
                    "track": 5, "time": round(max(0.0, _t - 0.15), 2),
                    "duration": 0.7, "volume": 60,
                })

        # track 4 — optional music ducked under the speaker voice
        music_url = music_track_url or _music_url_for(music_mood)
        if music_url and total > 0:
            elements.append({
                "type": "audio", "source": music_url,
                "track": 4, "time": 0, "duration": total,
                "volume": 12,
            })

        elements += _polish_elements(brand, total, sfx_hit_url, sfx_riser_url)
        return {"output_format": "mp4", "width": w, "height": h, "elements": elements}

    async def render_split_vertical(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict], captions: list[dict],
        aspect: str, music_mood: str = "none",
        caption_style: str | None = None,
    ) -> RenderResult:
        """Submit a split_vertical render. Same submit/poll contract as
        render_split_horizontal — only the source composition differs."""
        if not (avatar_video_url or "").startswith("http"):
            return RenderResult("failed", error="no avatar video URL")
        source = self.build_split_vertical_source(
            avatar_video_url=avatar_video_url,
            audio_duration=audio_duration,
            inserts=inserts, captions=captions,
            aspect=aspect, music_mood=music_mood,
            caption_style=caption_style,
            music_track_url=await resolve_music_url(music_mood),
            sfx_url=await resolve_sfx_url("whoosh"),
            brand=await get_brand_kit(),
            sfx_hit_url=await resolve_sfx_url("hit"),
            sfx_riser_url=await resolve_sfx_url("riser"),
        )
        body = {"source": source}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.creatomate.com/v1/renders",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
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

    async def render_engaging_avatar(
        self, *,
        avatar_video_url: str,
        audio_duration: float,
        inserts: list[dict], captions: list[dict],
        aspect: str, music_mood: str = "none",
        caption_style: str | None = None,
    ) -> RenderResult:
        """Submit an engaging_avatar render. Same poll contract."""
        if not (avatar_video_url or "").startswith("http"):
            return RenderResult("failed", error="no avatar video URL")
        source = self.build_engaging_avatar_source(
            avatar_video_url=avatar_video_url,
            audio_duration=audio_duration,
            inserts=inserts, captions=captions,
            aspect=aspect, music_mood=music_mood,
            caption_style=caption_style,
            music_track_url=await resolve_music_url(music_mood),
            sfx_url=await resolve_sfx_url("whoosh"),
            brand=await get_brand_kit(),
            sfx_hit_url=await resolve_sfx_url("hit"),
            sfx_riser_url=await resolve_sfx_url("riser"),
        )
        body = {"source": source}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.creatomate.com/v1/renders",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
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

    async def render_avatar_story_mix(
        self, *,
        audio_url: str, audio_duration: float,
        beats: list[dict], captions: list[dict],
        aspect: str, music_mood: str = "none",
        caption_style: str | None = None,
    ) -> RenderResult:
        """Submit a mixed avatar+still render. Same submit/poll contract
        as render() / render_story()."""
        has_any = any(
            ((b.get("role") == "avatar" and (b.get("video_url") or "").startswith("http"))
             or ((b.get("image_url") or "").startswith("http")))
            for b in beats
        )
        if not has_any:
            return RenderResult("failed", error="no real beat clips to assemble")
        source = self.build_avatar_story_mix_source(
            audio_url=audio_url, audio_duration=audio_duration,
            beats=beats, captions=captions,
            aspect=aspect, music_mood=music_mood,
            caption_style=caption_style,
            music_track_url=await resolve_music_url(music_mood),
            sfx_url=await resolve_sfx_url("whoosh"),
            brand=await get_brand_kit(),
            sfx_hit_url=await resolve_sfx_url("hit"),
            sfx_riser_url=await resolve_sfx_url("riser"),
        )
        body = {"source": source}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.creatomate.com/v1/renders",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
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

    async def render_story(
        self, *,
        audio_url: str, audio_duration: float,
        beats: list[dict], captions: list[dict],
        aspect: str, music_mood: str = "none",
        caption_style: str | None = None,
    ) -> RenderResult:
        """Submit a story_audio render. Identical polling contract to
        the existing render() / poll() pair — the production worker
        treats the returned render_id the same way."""
        if not any((b.get("image_url") or "").startswith("http") for b in beats):
            return RenderResult("failed", error="no real beat images to assemble")
        source = self.build_story_source(
            audio_url=audio_url, audio_duration=audio_duration,
            beats=beats, captions=captions,
            aspect=aspect, music_mood=music_mood,
            caption_style=caption_style,
            music_track_url=await resolve_music_url(music_mood),
            sfx_url=await resolve_sfx_url("whoosh"),
            brand=await get_brand_kit(),
            sfx_hit_url=await resolve_sfx_url("hit"),
            sfx_riser_url=await resolve_sfx_url("riser"),
        )
        body = {"source": source}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.creatomate.com/v1/renders",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
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
                # Per-scene mute: a james_clip flagged mute_audio in the
                # Reference Library plays visually-only (the user has a
                # narrator/voiceover track carrying the audio elsewhere).
                if s.get("mute_native_audio"):
                    elem["volume"] = 0
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

            # SFX: if the planner produced a URL, drop it in at scene start
            # (free-form keywords without a URL stay as planning notes only).
            sfx = (s.get("audio_sfx") or "").strip()
            if sfx.startswith("http"):
                elements.append({
                    "type": "audio", "source": sfx,
                    "track": 5, "time": t, "duration": min(2.0, dur),
                    "volume": 60,
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
