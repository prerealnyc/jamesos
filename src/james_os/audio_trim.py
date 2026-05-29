"""ffmpeg-backed silence detection + trim.

Why: HeyGen avatar renders typically pad ~0.3-1.0s of silence after the
last spoken word; Runway clips can have trailing visual without speech.
When Creatomate stitches scenes back-to-back, those tails become audible
gaps. We detect where speech actually ends and trim the clip — then snap
the scene's duration to the trimmed length so the next scene cuts in
exactly when the last one finishes.

Conservative defaults: only trims tails of >= 0.3s, never cuts a clip
below 0.5s, leaves leading silence alone (HeyGen often opens with a
breath that reads as natural pacing).
"""

import asyncio
import re

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|")


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "ignore")


def _parse_total_duration(log: str) -> float:
    m = _DUR_RE.search(log)
    if not m:
        return 0.0
    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mn * 60 + s


async def detect_speech_end(file_path: str,
                            threshold_db: int = -40,
                            min_silence_s: float = 0.3) -> tuple[float, float]:
    """Returns (speech_end_seconds, total_duration_seconds).

    speech_end = the timestamp where the LAST trailing silence begins. If the
    file has no trailing silence (speech runs to the end), returns
    (total, total). Falls back to (total, total) on detection failure.
    """
    cmd = [
        "ffmpeg", "-i", file_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_s}",
        "-f", "null", "-",
    ]
    _, log = await _run(cmd)
    total = _parse_total_duration(log)
    if total <= 0:
        return 0.0, 0.0
    starts = [float(m.group(1)) for m in _SILENCE_START.finditer(log)]
    ends = [float(m.group(1)) for m in _SILENCE_END.finditer(log)]
    if not starts:
        return total, total
    last_start = starts[-1]
    last_end = ends[-1] if ends else 0.0
    # Two ways the file ends in silence:
    #   (a) silence_start with no matching silence_end (cut mid-silence)
    #   (b) the last silence_end reaches the file's tail (within 0.2s)
    file_ends_in_silence = (
        len(starts) > len(ends)
        or (last_end >= total - 0.2)
    )
    if file_ends_in_silence and total - last_start >= 0.3:
        return max(0.5, last_start), total
    return total, total


async def trim_to(in_path: str, out_path: str, duration_s: float) -> bool:
    """Trim a video to exactly duration_s seconds. Keeps the video stream
    intact (-c:v copy when possible); re-encodes audio so the trim is sample-
    accurate (the start of a frame, not a keyframe boundary)."""
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-t", f"{duration_s:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def extract_audio_mp3(
    video_path: str, out_path: str, bitrate: str = "96k"
) -> bool:
    """Strip a video file to a mono-128k MP3. Used by the story_audio mode
    to pull James's voice out of a HeyGen render so Whisper can transcribe
    it with word timestamps. 96 kbps keeps a 60s render comfortably under
    Whisper's 25 MB cap and is fine for STT — no listener ever hears it."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn",                          # drop video
        "-ac", "1",                     # mono
        "-ar", "16000",                 # 16 kHz — STT-quality
        "-b:a", bitrate,
        "-acodec", "libmp3lame",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def slice_video_silent(
    in_path: str, out_path: str, start_s: float, end_s: float
) -> bool:
    """Cut [start_s, end_s] out of a video and drop the audio track.

    Used by the avatar_story_mix mode to extract per-beat windows from
    the HeyGen render. We strip the audio so it can't collide with the
    master voice track on Creatomate (which carries the FULL HeyGen
    audio across the whole timeline) — playing both would produce a
    perfect echo of James's voice.

    Re-encodes video (-c:v libx264) instead of stream-copying so the
    cut is sample-accurate — `-c:v copy` snaps to the nearest keyframe
    which can be ±2 seconds off the requested start.
    """
    dur = max(0.05, end_s - start_s)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", in_path,
        "-t", f"{dur:.3f}",
        "-an",                          # drop audio
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


__all__ = [
    "detect_speech_end", "trim_to", "extract_audio_mp3",
    "slice_video_silent",
]
