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
import tempfile

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


async def trim_video(in_path: str, out_path: str, start_s: float, end_s: float) -> bool:
    """Cut [start_s, end_s] out of a finished reel, KEEPING audio + video,
    frame-accurate (decodes from the start, then re-encodes). Used to trim a
    rendered output's excess head/tail footage."""
    start = max(0.0, float(start_s))
    end = float(end_s)
    if end - start < 0.2:
        return False
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-movflags", "+faststart",
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


async def slice_video(
    in_path: str, out_path: str, start_s: float, end_s: float
) -> bool:
    """Cut [start_s, end_s] out of a video keeping the audio track.

    Used by the long_form_cutter mode to extract a 30-45s reel window
    out of a 50-60 min source. Same sample-accurate cut policy as
    slice_video_silent — re-encodes rather than stream-copying so the
    cut starts exactly on the requested timestamp, not the nearest
    keyframe. AAC audio for portable mp4 playback.
    """
    dur = max(0.05, end_s - start_s)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", in_path,
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def slice_video_compact(
    in_path: str, out_path: str, start_s: float, end_s: float,
    max_height: int = 1920,
) -> bool:
    """Same as slice_video but kept reasonably compact for storage.

    IMPORTANT: max_height is 1920, NOT 1080. A vertical 9:16 phone clip
    is 1080w x 1920h; an 1080 height cap silently DOWNSCALED it to
    608x1080 (~44% of the pixels), then Creatomate re-encoded on top —
    delivered reels looked soft. 1920 preserves full vertical res; the
    scale filter only kicks in for genuinely taller-than-1920 sources.

    CRF 20 (was 26) keeps the talking-head crisp. A 2-min 1080x1920 cut
    lands around 40-60 MB — well within the TUS upload path's headroom.
    """
    dur = max(0.05, end_s - start_s)
    # -2 in scale keeps even pixel dimensions (libx264 requires that).
    # Only downscale when the source is TALLER than max_height; never
    # upscale (min()).
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", in_path,
        "-t", f"{dur:.3f}",
        "-vf", f"scale=-2:'min({max_height},ih)'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ac", "1",
        "-movflags", "+faststart",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def extract_audio_lowbit(
    in_path: str, out_path: str, bitrate: str = "32k"
) -> bool:
    """Strip a video file to a low-bitrate mono mp3 sized for chunked
    Whisper transcription of long sources (50-60 min podcasts).

    32 kbps mono 16 kHz keeps a 60-min file at roughly 14 MB — well
    inside Whisper's 25 MB upload cap, with audio quality fine for
    STT. Use extract_audio_mp3 (the higher-quality 96 kbps variant)
    for shorter clips that don't need the compression headroom.
    """
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", bitrate, "-acodec", "libmp3lame",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def split_audio_chunks(
    in_path: str, out_dir: str, chunk_seconds: int = 600,
) -> list[str]:
    """Split a long mp3 into fixed-length chunks for Whisper.

    Whisper's 25 MB cap is per-file. A 60-min mp3 at 32 kbps fits
    under it; longer or higher-quality sources need splitting. We
    output `chunk_000.mp3 chunk_001.mp3 …` in `out_dir` and return
    the list of paths in playback order. The caller stitches the
    transcripts with time offsets of N × chunk_seconds.
    """
    from pathlib import Path as _P
    _P(out_dir).mkdir(parents=True, exist_ok=True)
    pattern = f"{out_dir}/chunk_%03d.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-c", "copy", pattern,
    ]
    rc, _ = await _run(cmd)
    if rc != 0:
        return []
    return sorted(
        str(p) for p in _P(out_dir).glob("chunk_*.mp3")
    )


async def probe_duration(in_path: str) -> float:
    """Return the duration of a video/audio file in seconds, or 0.0
    when ffmpeg can't parse it. Uses ffmpeg -i since ffprobe isn't
    guaranteed to be on PATH on every dev machine."""
    cmd = ["ffmpeg", "-i", in_path]
    _, log = await _run(cmd)
    return _parse_total_duration(log)


_DIMS_RE = re.compile(r"Video:.*?,\s(\d{2,5})x(\d{2,5})[\s,\[]")


async def probe_video_dims(in_path: str) -> tuple[int, int] | None:
    """(width, height) of a video's first video stream, or None if it can't be
    parsed. Parses `ffmpeg -i` output (ffprobe isn't guaranteed on PATH). Used to
    decide whether a long-form cut is wide enough to need a re-centering pan."""
    _, log = await _run(["ffmpeg", "-i", in_path])
    m = _DIMS_RE.search(log)
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None


async def concat_videos_normalized(paths: list[str], out_path: str) -> bool:
    """Concatenate several cuts into one file (topic edits stitch segments
    from DIFFERENT sources). Dims/fps/sample-rates can differ, so every
    input is scaled + padded onto the first cut's canvas and its audio
    normalized to mono 48 kHz before the concat filter — the concat demuxer
    would silently corrupt on mismatched streams."""
    if not paths:
        return False
    if len(paths) == 1:
        # Nothing to stitch; a stream copy keeps this cheap.
        rc, _ = await _run([
            "ffmpeg", "-y", "-i", paths[0], "-c", "copy",
            "-movflags", "+faststart", out_path,
        ])
        return rc == 0
    # Canvas = the highest-resolution input, so one low-res (or oddly
    # oriented) segment can't drag every other segment down with it.
    best: tuple[int, int] | None = None
    for p in paths:
        d = await probe_video_dims(p)
        if d and (best is None or d[0] * d[1] > best[0] * best[1]):
            best = d
    if not best:
        return False
    w, h = (best[0] // 2) * 2, (best[1] // 2) * 2   # libx264 needs even dims
    n = len(paths)
    chains: list[str] = []
    cmd: list[str] = ["ffmpeg", "-y"]
    for i, p in enumerate(paths):
        cmd += ["-i", p]
        chains.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];"
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=mono[a{i}];"
        )
    inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_graph = "".join(chains) + f"{inputs}concat=n={n}:v=1:a=1[v][a]"
    cmd += [
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ac", "1",
        "-movflags", "+faststart",
        out_path,
    ]
    rc, _ = await _run(cmd)
    return rc == 0


async def tighten_clip(
    in_bytes: bytes, intervals: list[tuple[float, float]], *, crossfade_ms: int = 0,
) -> bytes | None:
    """Re-encode `in_bytes` keeping ONLY the given (start,end) intervals, in
    order, concatenated — used to cut internal dead air out of a reel. ONE ffmpeg
    filter_complex pass (sample-accurate; no temp-file concat demuxer). Preserves
    the source's dimensions/fps (NO scale filter) so any detected face-position /
    crop math stays valid.

    Returns the tightened mp4 bytes, or None on any failure / nothing to cut
    (< 2 intervals) / absurd segment count — caller then keeps the original."""
    from pathlib import Path as _PathT
    from .drive import big_file_tmp_dir
    n = len(intervals)
    if n < 2 or n > 400:
        return None
    with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
        inp, outp = f"{td}/tin.mp4", f"{td}/tout.mp4"
        try:
            with open(inp, "wb") as fh:
                fh.write(in_bytes)
        except OSError:
            return None
        parts: list[str] = []
        for i, (s, e) in enumerate(intervals):
            parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
        vlabels = "".join(f"[v{i}]" for i in range(n))
        alabels = "".join(f"[a{i}]" for i in range(n))
        parts.append(f"{vlabels}concat=n={n}:v=1:a=0[v]")
        parts.append(f"{alabels}concat=n={n}:v=0:a=1[a]")
        cmd = [
            "ffmpeg", "-y", "-i", inp,
            "-filter_complex", ";".join(parts),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ac", "1",
            "-movflags", "+faststart", outp,
        ]
        rc, _ = await _run(cmd)
        if rc != 0:
            return None
        try:
            data = _PathT(outp).read_bytes()
        except OSError:
            return None
        return data if data else None


__all__ = [
    "detect_speech_end", "trim_to", "extract_audio_mp3",
    "slice_video_silent", "slice_video", "extract_audio_lowbit",
    "split_audio_chunks", "probe_duration", "probe_video_dims", "tighten_clip",
]
