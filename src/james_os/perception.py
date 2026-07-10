"""Perception layer — make the reference library actually *see* a video.

On upload (or on demand) we watch the clip and turn it into a structured
"style fingerprint" the scene-plan generator can replicate:

    ffmpeg ──┬── audio  → Whisper  → transcript (the hook + script)
             └── frames → GPT-4o vision → structure / pacing / captions / framing
                                   │
                                   ▼
                         style fingerprint (JSON)

Honesty rules:
  * Replication targets FORMAT, never verbatim content — the fingerprint
    describes how a video is built (hook pattern, cut rhythm, caption style),
    not words to copy. The brand-voice QA gate downstream rejects copies.
  * No OpenAI key, or an un-downloadable URL reference → status is reported
    plainly ("unsupported"), never a faked analysis.
  * Frame sampling covers the opening window (short-form is the target);
    that scope limit is stated, not hidden.
"""

import asyncio
import base64
import json
import re
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from .config import settings

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_MAX_FRAMES = 8
_WHISPER_MODEL = "whisper-1"


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "ignore")


def _parse_duration(ffmpeg_log: str) -> int:
    m = _DUR_RE.search(ffmpeg_log)
    if not m:
        return 0
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return int(h * 3600 + mnt * 60 + s)


async def _extract_audio(src: Path, dst: Path) -> bool:
    rc, _ = await _run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", str(dst)]
    )
    return rc == 0 and dst.is_file() and dst.stat().st_size > 0


async def _extract_frames(src: Path, outdir: Path) -> tuple[list[Path], int]:
    rc, log = await _run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", "fps=1/2",
         "-frames:v", str(_MAX_FRAMES), "-q:v", "4",
         str(outdir / "f_%02d.jpg")]
    )
    frames = sorted(outdir.glob("f_*.jpg"))
    return frames, _parse_duration(log)


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


async def _transcribe(client: AsyncOpenAI, audio: Path) -> str:
    try:
        with audio.open("rb") as fh:
            res = await client.audio.transcriptions.create(
                model=_WHISPER_MODEL, file=fh
            )
        return (getattr(res, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — a missing/odd audio track must not sink analysis
        return ""


_VISION_SYSTEM = (
    "You are a short-form video analyst. Given sampled frames (in order) and "
    "the transcript of a clip, describe HOW it is built so another creator "
    "could replicate the FORMAT in their own voice — never to copy its words. "
    "Return STRICT JSON with keys: hook (what grabs attention in the first "
    "seconds), structure (the beat-by-beat flow), pacing (cut rhythm / energy), "
    "captions (on-screen text style, if any), visual_style (framing, setting, "
    "look), replication_tips (3-5 concrete, voice-agnostic tips). Be specific "
    "and concise. If the frames are uninformative, say so honestly in each field."
)


_FACE_X_SYSTEM = (
    "You locate the MAIN speaking person in frames sampled from ONE short video "
    "clip. Return STRICT JSON {\"found\": boolean, \"center_x\": number}. center_x "
    "is the horizontal center of that person's FACE/HEAD as a fraction from 0.0 "
    "(far LEFT edge of frame) to 1.0 (far RIGHT edge), AVERAGED across the frames. "
    "If there is no single clear person (empty frame, crowd, pure B-roll), set "
    "found=false. This is used to re-center a vertical crop, so be precise."
)

_SUBJECT_X_SYSTEM = (
    "You locate the MAIN VISUAL SUBJECT in frames sampled from ONE short video "
    "clip that has NO speaking person — it shows scenery, a place, a property, a "
    "product, or an object. Return STRICT JSON {\"found\": boolean, \"center_x\": "
    "number, \"subject\": string}. center_x is the horizontal center of the single "
    "most important subject — the building, room, product, landmark, sign, vehicle, "
    "or focal element the shot is ABOUT — as a fraction 0.0 (far LEFT edge) to 1.0 "
    "(far RIGHT edge), AVERAGED across the frames. Pick the ONE element a viewer's "
    "eye is drawn to and the shot is composed around; if it drifts across frames, "
    "average its position. subject is a 2-5 word label of it. If the frame is an "
    "even texture with no focal subject (open sky, flat water, blank wall, abstract "
    "pattern), set found=false. This re-centers a vertical crop, so be precise."
)


async def detect_speaker_center_x(
    video_path: str, duration_s: float = 0.0, samples: int = 3,
) -> float | None:
    """Best-effort: sample a few mid-clip frames and ask vision for the main
    speaker's horizontal face-center as a fraction 0..1. ONE vision call.

    Returns None on ANY failure (no key, ffmpeg/vision error, no clear face) so
    the caller falls back to a centered crop — this must never break a render."""
    client = _client()
    if client is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            frames = await _extract_center_frames(
                video_path, Path(td), duration_s, samples,
            )
            if not frames:
                return None
            content: list[dict] = [{
                "type": "text",
                "text": (f"{len(frames)} frames from ONE clip, in order. Give the "
                         "main speaker's face horizontal center (0=left, 1=right), "
                         "averaged across them."),
            }]
            for f in frames:
                b64 = base64.b64encode(f.read_bytes()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                  "detail": "low"},
                })
            res = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": _FACE_X_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=120, temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(res.choices[0].message.content or "{}")
            if not data.get("found"):
                return None
            x = float(data.get("center_x"))
            return x if 0.0 <= x <= 1.0 else None
    except Exception:  # noqa: BLE001 — detection is best-effort; never break a render
        return None


async def detect_subject_center_x(
    video_path: str, duration_s: float = 0.0, samples: int = 3,
) -> tuple[float, str] | None:
    """Best-effort: when a clip has NO speaking face, sample a few mid-clip frames
    and ask vision for the MAIN VISUAL SUBJECT's horizontal center 0..1 (the
    object / scenery the shot is about), so a vertical crop pans to IT instead of a
    blind center crop. ONE vision call. Returns (center_x, subject_label) or None
    on ANY failure — must never break a render."""
    client = _client()
    if client is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            frames = await _extract_center_frames(
                video_path, Path(td), duration_s, samples,
            )
            if not frames:
                return None
            content: list[dict] = [{
                "type": "text",
                "text": (f"{len(frames)} frames from ONE scenery/b-roll clip, in "
                         "order. Give the main visual subject's horizontal center "
                         "(0=left, 1=right), averaged across them."),
            }]
            for f in frames:
                b64 = base64.b64encode(f.read_bytes()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                  "detail": "low"},
                })
            res = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": _SUBJECT_X_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=120, temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(res.choices[0].message.content or "{}")
            if not data.get("found"):
                return None
            x = float(data.get("center_x"))
            if not (0.0 <= x <= 1.0):
                return None
            return x, str(data.get("subject") or "subject")[:60]
    except Exception:  # noqa: BLE001 — detection is best-effort; never break a render
        return None


async def _extract_frames_at(
    video_path: str, outdir: Path, times: list[float], prefix: str = "fx",
) -> list[Path]:
    """Extract ONE JPEG per timestamp in `times` (single-frame ffmpeg seeks —
    cheap on a local file). Returns the frames that actually extracted."""
    frames: list[Path] = []
    for i, t in enumerate(times):
        p = outdir / f"{prefix}_{i:02d}.jpg"
        rc, _ = await _run([
            "ffmpeg", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "4", str(p),
        ])
        if rc == 0 and p.is_file() and p.stat().st_size > 0:
            frames.append(p)
    return frames


async def _extract_center_frames(
    video_path: str, outdir: Path, duration_s: float, samples: int,
) -> list[Path]:
    """Pull `samples` JPEG frames spread across the MIDDLE 60% of the clip
    (skips intro/outro framing)."""
    if duration_s <= 0:
        from .audio_trim import probe_duration
        duration_s = await probe_duration(video_path)
    if duration_s <= 0:
        times = [1.0]
    else:
        lo, hi = duration_s * 0.2, duration_s * 0.8
        n = max(1, samples)
        times = [(lo + hi) / 2.0] if n == 1 else [
            lo + (hi - lo) * i / (n - 1) for i in range(n)
        ]
    return await _extract_frames_at(video_path, outdir, times, prefix="fx")


_FACE_MAP_SYSTEM = (
    "Every frame shows the SAME one person — the person who is speaking. Return "
    "STRICT JSON {\"found\": boolean, \"center_x\": number}, where center_x is the "
    "horizontal center of THAT person's face as a fraction from 0.0 (far LEFT of "
    "the frame) to 1.0 (far RIGHT), averaged across the frames. If there is no "
    "single clear person, set found=false."
)


async def detect_speaker_face_map(
    video_path: str, turns: list[dict], *, per_speaker_samples: int = 3,
) -> dict[str, float] | None:
    """Map each diarization speaker label → their face's horizontal center
    (0..1), by sampling frames DURING that speaker's own turns and asking vision
    where the (single, speaking) person sits. Returns {speaker: center_x} for
    >=2 confidently-separated speakers, else None (caller falls back to the
    static single-face pan). ONE vision call per speaker. Never raises."""
    client = _client()
    if client is None or not turns:
        return None
    try:
        by_spk: dict[str, list[tuple[float, float]]] = {}
        for t in turns:
            spk = str(t.get("speaker") or "")
            if not spk:
                continue
            s = float(t.get("start") or 0.0)
            e = float(t.get("end") or s)
            if e - s > 0.4:
                by_spk.setdefault(spk, []).append((s, e))
        if len(by_spk) < 2:
            return None
        face_map: dict[str, float] = {}
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            for spk, windows in by_spk.items():
                windows.sort(key=lambda w: w[1] - w[0], reverse=True)
                times = [round((s + e) / 2.0, 2) for (s, e) in windows[:per_speaker_samples]]
                frames = await _extract_frames_at(video_path, outdir, times, prefix=f"sp{spk}")
                if not frames:
                    continue
                content: list[dict] = [{
                    "type": "text",
                    "text": (f"{len(frames)} frames, all of the SAME speaking "
                             "person. Their face's horizontal center 0..1, averaged."),
                }]
                for f in frames:
                    b64 = base64.b64encode(f.read_bytes()).decode()
                    content.append({"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
                try:
                    res = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": _FACE_MAP_SYSTEM},
                            {"role": "user", "content": content},
                        ],
                        max_tokens=120, temperature=0.0,
                        response_format={"type": "json_object"},
                    )
                    data = json.loads(res.choices[0].message.content or "{}")
                    if data.get("found"):
                        x = float(data.get("center_x"))
                        if 0.0 <= x <= 1.0:
                            face_map[spk] = round(x, 3)
                except Exception:  # noqa: BLE001 — drop this speaker, keep going
                    continue
        if len(face_map) < 2:
            return None
        xs = sorted(face_map.values())
        if xs[-1] - xs[0] < 0.12:            # faces too close → can't separate
            print(f"[speaker-follow] face-map ambiguous (spread {xs[-1] - xs[0]:.2f}) "
                  f"→ static pan. map={face_map}")
            return None
        print(f"[speaker-follow] face map: {face_map}")
        return face_map
    except Exception as e:  # noqa: BLE001 — best-effort, never break a render
        print(f"[speaker-follow] face-map failed: {e}")
        return None


_SOURCE_SPEAKERS_SYSTEM = (
    "You are given several frames sampled in order from ONE video (an interview "
    "or talk). Identify the DISTINCT PEOPLE who appear on camera as speakers "
    "(hosts/guests). Return STRICT JSON {\"people\": [{\"position\": number, "
    "\"label\": string, \"frame\": int}]} where: position is that person's typical "
    "horizontal center, 0.0 (far LEFT) to 1.0 (far RIGHT); label is a 3-6 word "
    "visual description (e.g. 'blonde woman in black top'); frame is the 0-based "
    "index of the frame where that person is shown most clearly. List each "
    "distinct person ONCE, left-to-right. Ignore background/B-roll people. If "
    "only one person speaks, return exactly one."
)


async def _save_face_crop(frame_path: Path, pos: float, tenant_id, idx: int) -> str:
    """Crop a portrait slice centred on `pos` from a frame and store it as a
    speaker-preview image. Returns the served URL, or '' on any failure."""
    try:
        import uuid as _uuid
        from io import BytesIO

        from PIL import Image

        from .config import settings as _settings
        from .media import storage as media_storage

        img = Image.open(frame_path).convert("RGB")
        W, H = img.size
        cw = min(W, max(1, int(H * 9 / 16)))         # 9:16 portrait slice
        cx = int(min(1.0, max(0.0, pos)) * W)
        x0 = max(0, min(W - cw, cx - cw // 2))
        crop = img.crop((x0, 0, x0 + cw, H))
        crop.thumbnail((420, 760), Image.LANCZOS)
        buf = BytesIO()
        crop.save(buf, "JPEG", quality=85)
        tenant = str(tenant_id or _settings.default_tenant_id)
        url, _ = await asyncio.to_thread(
            media_storage().save, tenant, buf.getvalue(),
            f"speaker-preview-{idx}-{_uuid.uuid4().hex[:8]}.jpg",
        )
        return url
    except Exception as e:  # noqa: BLE001
        print(f"[speaker-detect] crop failed: {e}")
        return ""


async def detect_source_speakers(
    video_path: str, tenant_id=None, max_people: int = 4,
) -> list[dict]:
    """Enumerate the DISTINCT on-camera speakers in a source video for the
    'who is this?' step: sample frames → ONE vision call → each person's stable
    horizontal position + a cropped preview. Returns
    [{face_x, label, preview_url}] left-to-right, or [] on any failure (no key,
    ffmpeg/vision error). Position-based so it lines up with the render's
    per-speaker face map. Never raises."""
    client = _client()
    if client is None:
        return []
    try:
        from .audio_trim import probe_duration
        dur = await probe_duration(video_path)
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            frames = await _extract_center_frames(video_path, outdir, dur, samples=10)
            if not frames:
                return []
            content: list[dict] = [{
                "type": "text",
                "text": f"{len(frames)} frames from ONE video, in order (index 0..{len(frames) - 1}).",
            }]
            for f in frames:
                b64 = base64.b64encode(f.read_bytes()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            resp = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": _SOURCE_SPEAKERS_SYSTEM},
                    {"role": "user", "content": content},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            people = data.get("people") or []
            out: list[dict] = []
            for idx, p in enumerate(people[:max_people]):
                try:
                    pos = min(1.0, max(0.0, float(p.get("position", 0.5))))
                except (TypeError, ValueError):
                    pos = 0.5
                try:
                    fi = int(p.get("frame", 0))
                except (TypeError, ValueError):
                    fi = 0
                fi = min(max(0, fi), len(frames) - 1)
                preview = await _save_face_crop(frames[fi], pos, tenant_id, idx)
                out.append({
                    "face_x": round(pos, 3),
                    "label": str(p.get("label") or "")[:80],
                    "preview_url": preview,
                })
            # left-to-right for a predictable UI order
            out.sort(key=lambda d: d["face_x"])
            return out
    except Exception as e:  # noqa: BLE001 — detection is best-effort
        print(f"[speaker-detect] failed: {e}")
        return []


async def _describe(client: AsyncOpenAI, frames: list[Path], transcript: str) -> dict:
    if not frames:
        return {}
    content: list[dict] = [
        {"type": "text",
         "text": f"Transcript:\n{transcript[:4000] or '(no speech detected)'}\n\n"
                 f"{len(frames)} frames follow, in chronological order."}
    ]
    for f in frames:
        b64 = base64.b64encode(f.read_bytes()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    try:
        res = await client.chat.completions.create(
            model=settings.llm_model if "gpt-4o" in settings.llm_model else "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": content},
            ],
            max_tokens=900,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        return json.loads(res.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001
        return {"error": f"vision analysis failed: {e}"}


async def analyze_file(path: str) -> dict:
    """Watch a local video file → {status, transcript, duration, fingerprint}."""
    client = _client()
    if client is None:
        return {"status": "unsupported", "note": "No OpenAI key — add it in Settings to analyze videos."}
    src = Path(path)
    if not src.is_file():
        return {"status": "failed", "note": "file not found on disk"}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = tmp / "audio.mp3"
        have_audio = await _extract_audio(src, audio)
        frames, duration = await _extract_frames(src, tmp)
        transcript = await _transcribe(client, audio) if have_audio else ""
        fingerprint = await _describe(client, frames, transcript)

    if not frames and not transcript:
        return {"status": "failed", "note": "could not extract audio or frames (unsupported format?)"}

    return {
        "status": "done",
        "transcript": transcript,
        "duration": duration,
        "fingerprint": fingerprint,
        "frames_analyzed": len(frames),
    }


def fingerprint_to_notes(analysis: dict) -> str:
    """Flatten a fingerprint into readable notes for the card / generator."""
    fp = analysis.get("fingerprint") or {}
    if not fp:
        return ""
    order = ["hook", "structure", "pacing", "captions", "visual_style"]
    lines = []
    for k in order:
        v = fp.get(k)
        if v:
            lines.append(f"{k.replace('_', ' ').title()}: {v}")
    tips = fp.get("replication_tips")
    if isinstance(tips, list) and tips:
        lines.append("Replicate: " + "; ".join(str(t) for t in tips))
    elif tips:
        lines.append(f"Replicate: {tips}")
    return "\n".join(lines)


__all__ = ["analyze_file", "fingerprint_to_notes"]
