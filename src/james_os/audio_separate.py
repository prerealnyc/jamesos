"""Music-only extraction — pull the instrumental bed out from under a voice.

Recovering the bed from a finished reel is source separation, not filtering.
ffmpeg alone cannot do it: centre-channel cancellation only works when the
voice sits dead-centre in a stereo mix, and it takes the music's centre with it.
So this runs Demucs (`htdemucs`) in `--two-stems=vocals` mode, which splits the
mix into `vocals` and `no_vocals` — and `no_vocals` IS the bed.

Demucs drags PyTorch in, which is a heavyweight dependency for a feature most
renders never touch. So it is an OPTIONAL import behind `available()`: the app
boots, and every other feature works, whether or not torch installed. A missing
install surfaces as an honest message on this one endpoint instead of a failed
container.

It is also SLOW — tens of seconds per clip on Railway CPU — so every caller must
treat it as a background job, never an inline request.

LICENSING: this recovers audio from whatever file it is handed. That the tool
can separate a track says nothing about who may reuse it — a bed lifted from
someone else's reel still belongs to whoever made or licensed it. Recovering a
bed from your own previously-mixed footage is the clean case.
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

# Separation is CPU-bound and long; refuse anything that would tie a worker up
# for minutes rather than silently grinding.
MAX_SECONDS = 600
_DEMUCS_TIMEOUT_S = 900
_MODEL = "htdemucs"


class SeparationError(RuntimeError):
    """Raised with a message meant for the user, not a stack trace."""


async def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise SeparationError(
            f"separation timed out after {timeout}s — the clip is too long "
            "or the worker is too small"
        ) from None
    return proc.returncode or 0, out.decode("utf-8", "ignore")


def demucs_installed() -> bool:
    """True when the optional Demucs dependency is importable."""
    try:
        import demucs  # noqa: F401
    except Exception:  # noqa: BLE001 — a broken torch install must read as "absent"
        return False
    return True


def available() -> tuple[bool, str]:
    """(usable, why-not). Checked before any job is queued so the UI can say
    what's missing instead of failing halfway through."""
    if shutil.which("ffmpeg") is None:
        return False, "ffmpeg is not installed on this host"
    if not demucs_installed():
        return False, (
            "the Demucs separation model isn't installed on this deployment "
            "(optional dependency: pip install 'james-os[separate]')"
        )
    return True, ""


async def probe_seconds(src: Path) -> float:
    """Clip length via ffprobe. 0.0 when it can't be read."""
    rc, out = await _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(src),
    ], timeout=60)
    if rc != 0:
        return 0.0
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


async def extract_instrumental(src: Path, *, mp3_bitrate: int = 192) -> tuple[bytes, dict]:
    """Separate `src` (video OR audio) and return the instrumental bed as MP3.

    Raises SeparationError with a readable message on every failure path —
    missing dependency, unreadable input, too long, model failure — so the
    caller never has to guess why a job produced nothing.
    """
    ok, why = available()
    if not ok:
        raise SeparationError(why)
    if not src.is_file():
        raise SeparationError("the source file could not be read")

    seconds = await probe_seconds(src)
    if seconds > MAX_SECONDS:
        raise SeparationError(
            f"that clip is {seconds:.0f}s — separation is capped at "
            f"{MAX_SECONDS}s to keep a worker free"
        )

    with tempfile.TemporaryDirectory(prefix="jos-sep-") as tmp:
        tmpdir = Path(tmp)
        # Demucs wants audio; decode to 44.1k stereo WAV first so a video
        # container (or an odd codec) can't confuse the model.
        wav = tmpdir / "mix.wav"
        rc, log = await _run([
            "ffmpeg", "-y", "-i", str(src), "-vn",
            "-ac", "2", "-ar", "44100", str(wav),
        ], timeout=300)
        if rc != 0 or not wav.is_file() or wav.stat().st_size == 0:
            raise SeparationError(
                "could not decode any audio from that file"
                + (f" — {log.strip().splitlines()[-1]}" if log.strip() else "")
            )

        outdir = tmpdir / "out"
        # `--two-stems=vocals` gives exactly vocals + no_vocals: half the work
        # of a full 4-stem split, and no_vocals is precisely what we want.
        rc, log = await _run([
            sys.executable, "-m", "demucs",
            "--two-stems=vocals", "-n", _MODEL,
            "--mp3", f"--mp3-bitrate={int(mp3_bitrate)}",
            "-o", str(outdir), str(wav),
        ], timeout=_DEMUCS_TIMEOUT_S)
        if rc != 0:
            tail = "\n".join(log.strip().splitlines()[-3:])
            raise SeparationError(f"separation failed: {tail or 'unknown error'}")

        beds = list(outdir.rglob("no_vocals.mp3"))
        if not beds:
            raise SeparationError(
                "separation produced no instrumental stem — the clip may be "
                "voice-only with no music under it"
            )
        data = beds[0].read_bytes()

    if not data:
        raise SeparationError("the extracted bed was empty")
    return data, {
        "model": _MODEL,
        "source_seconds": round(seconds, 2),
        "bytes": len(data),
        "bitrate_kbps": int(mp3_bitrate),
    }


__all__ = [
    "available", "demucs_installed", "extract_instrumental",
    "probe_seconds", "SeparationError", "MAX_SECONDS",
]
