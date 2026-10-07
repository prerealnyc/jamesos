"""Final touches on a video that has already been rendered.

A reel that is 90% right has had exactly one lever until now: regenerate. That
spends another render, takes minutes, and comes back a DIFFERENT video — the 90%
that was fine is thrown away with the 10% that wasn't. Seven of the last eight
renders on this tenant were rejected.

So this edits the FILE that already exists. Trim the dead air off the front, fix
the words on screen, put the brand's logo on it, reframe it for the network it
is going to, choose the frame people see before they press play. One ffmpeg pass
over the mp4, saved to durable storage — what is saved is exactly what plays.

THE DOC. Positions and sizes are FRACTIONS of the output frame, never pixels, so
a layout made against a small preview renders identically at 1080x1920 — the
lesson the card editor learned. Times are seconds from the start of the TRIMMED
video, which is what the owner sees on the scrubber.

    {
      "version": 1,
      "trim":  {"start": 1.5, "end": 28.0},          # end null = to the end
      "frame": {"aspect": "9:16",                     # or 1:1 / 16:9 / source
                "crop": {"x":0.1,"y":0,"w":0.8,"h":1}},   # null = centred cover
      "texts": [{"text":"…","x":0.5,"y":0.72,"size":0.06,"font":"display",
                 "color":"#FFFFFF","align":"center","start":0,"end":4,
                 "box":true,"box_color":"#000000","box_opacity":0.45}],
      "logo":  {"x":0.72,"y":0.04,"w":0.2,"start":0,"end":null},
      "cover": {"t": 2.5},
      "audio": {"mute": false}
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

# The shapes a network actually wants. "source" keeps whatever the video is.
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}
MAX_TEXTS = 8
MIN_DURATION = 0.5
# A cut longer than this is not a "final touch" — and a 10-minute re-encode is
# not something to hold a web request (or a Railway container) open for.
MAX_DURATION = 15 * 60


def _f(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(lo, min(hi, n))


def _hex(v: Any, default: str) -> str:
    s = str(v or "").strip()
    if len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:]):
        return s
    return default


def normalize(doc: Any) -> dict:
    """The doc, clamped to what can actually be rendered. Everything unknown is
    dropped rather than guessed at — a doc is replayed in the owner's browser as
    well as here, and the two have to agree."""
    d = doc if isinstance(doc, dict) else {}
    trim = d.get("trim") if isinstance(d.get("trim"), dict) else {}
    start = _f(trim.get("start"), 0.0, MAX_DURATION, 0.0)
    end_raw = trim.get("end")
    end = None if end_raw in (None, "") else _f(end_raw, 0.0, MAX_DURATION, 0.0)

    frame = d.get("frame") if isinstance(d.get("frame"), dict) else {}
    aspect = str(frame.get("aspect") or "source").strip()
    if aspect not in ASPECTS and aspect != "source":
        aspect = "source"
    crop = frame.get("crop") if isinstance(frame.get("crop"), dict) else None
    if crop is not None:
        w = _f(crop.get("w"), 0.05, 1.0, 1.0)
        h = _f(crop.get("h"), 0.05, 1.0, 1.0)
        crop = {
            "x": _f(crop.get("x"), 0.0, 1.0 - w, 0.0),
            "y": _f(crop.get("y"), 0.0, 1.0 - h, 0.0),
            "w": w, "h": h,
        }

    texts = []
    for t in (d.get("texts") or [])[:MAX_TEXTS]:
        if not isinstance(t, dict):
            continue
        body = str(t.get("text") or "").strip()
        if not body:
            continue
        t_end = t.get("end")
        texts.append({
            "text": body[:400],
            "x": _f(t.get("x"), 0.0, 1.0, 0.5),
            "y": _f(t.get("y"), 0.0, 1.0, 0.8),
            "size": _f(t.get("size"), 0.015, 0.30, 0.06),
            "font": "body" if str(t.get("font")) == "body" else "display",
            "color": _hex(t.get("color"), "#FFFFFF"),
            "align": (str(t.get("align")) if str(t.get("align")) in ("left", "center", "right") else "center"),
            "start": _f(t.get("start"), 0.0, MAX_DURATION, 0.0),
            "end": None if t_end in (None, "") else _f(t_end, 0.0, MAX_DURATION, 0.0),
            "box": bool(t.get("box")),
            "box_color": _hex(t.get("box_color"), "#000000"),
            "box_opacity": _f(t.get("box_opacity"), 0.0, 1.0, 0.45),
        })

    logo = d.get("logo") if isinstance(d.get("logo"), dict) else None
    if logo is not None:
        l_end = logo.get("end")
        logo = {
            "x": _f(logo.get("x"), 0.0, 1.0, 0.72),
            "y": _f(logo.get("y"), 0.0, 1.0, 0.04),
            "w": _f(logo.get("w"), 0.03, 0.6, 0.18),
            "start": _f(logo.get("start"), 0.0, MAX_DURATION, 0.0),
            "end": None if l_end in (None, "") else _f(l_end, 0.0, MAX_DURATION, 0.0),
        }

    cover = d.get("cover") if isinstance(d.get("cover"), dict) else None
    cover = {"t": _f(cover.get("t"), 0.0, MAX_DURATION, 0.0)} if cover else None
    audio = d.get("audio") if isinstance(d.get("audio"), dict) else {}
    return {
        "version": 1,
        "trim": {"start": start, "end": end},
        "frame": {"aspect": aspect, "crop": crop},
        "texts": texts,
        "logo": logo,
        "cover": cover,
        "audio": {"mute": bool(audio.get("mute"))},
    }


def is_noop(doc: dict) -> bool:
    """Nothing to do — the owner opened the editor and changed their mind. Worth
    knowing: a re-encode that changes nothing still costs quality and minutes."""
    d = normalize(doc)
    return (d["trim"]["start"] == 0.0 and d["trim"]["end"] is None
            and d["frame"]["aspect"] == "source" and not d["frame"]["crop"]
            and not d["texts"] and not d["logo"] and not d["audio"]["mute"])


def out_size(doc: dict, src_w: int, src_h: int) -> tuple[int, int]:
    """The frame the edit renders into. 'source' keeps the video's own size (made
    even — h264 refuses odd dimensions)."""
    aspect = normalize(doc)["frame"]["aspect"]
    if aspect in ASPECTS:
        return ASPECTS[aspect]
    return (max(2, src_w - src_w % 2), max(2, src_h - src_h % 2))


def _esc_path(p: str) -> str:
    """A path inside a filter argument: ffmpeg eats backslashes and treats ':' as
    an option separator."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _crop_expr(doc: dict, src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    """Crop the source down to the piece that fills the output frame.

    An explicit crop is the owner dragging the frame around ("keep his face, lose
    the empty wall"). Without one, the largest CENTRED region of the target shape
    is taken — turning a 16:9 cut into a 9:16 reel without squashing anyone."""
    d = normalize(doc)
    crop = d["frame"]["crop"]
    if crop:
        cw = max(2, int(src_w * crop["w"]))
        ch = max(2, int(src_h * crop["h"]))
        cx = int(src_w * crop["x"])
        cy = int(src_h * crop["y"])
    else:
        target = out_w / out_h
        if src_w / src_h > target:                 # too wide: take a tall slice
            ch = src_h
            cw = max(2, int(round(src_h * target)))
        else:                                      # too tall: take a wide slice
            cw = src_w
            ch = max(2, int(round(src_w / target)))
        cx, cy = (src_w - cw) // 2, (src_h - ch) // 2
    cw -= cw % 2
    ch -= ch % 2
    return f"crop={cw}:{ch}:{cx}:{cy}"


def _enable(start: float, end: float | None) -> str:
    if start <= 0 and end is None:
        return ""
    lo = max(0.0, start)
    hi = end if end is not None else MAX_DURATION
    return f":enable='between(t\\,{lo:.3f}\\,{hi:.3f})'"


def build_filters(
    doc: dict, src_w: int, src_h: int, *, fonts: dict[str, str],
    text_files: list[str], has_logo: bool, out_w: int, out_h: int,
) -> str:
    """The whole edit as one filter graph: reframe, then the words, then the logo.

    `text_files` holds each overlay's copy on disk — drawtext reads it with
    `textfile=`, which sidesteps escaping a colon, a quote or a percent sign in
    the owner's own words."""
    d = normalize(doc)
    chain = [_crop_expr(d, src_w, src_h, out_w, out_h),
             f"scale={out_w}:{out_h}", "setsar=1"]
    for i, t in enumerate(d["texts"]):
        if i >= len(text_files):
            break
        size = max(8, int(round(t["size"] * out_h)))
        x = {"left": f"{int(t['x'] * out_w)}",
             "center": f"{int(t['x'] * out_w)}-text_w/2",
             "right": f"{int(t['x'] * out_w)}-text_w"}[t["align"]]
        y = f"{int(t['y'] * out_h)}-text_h/2"
        parts = [
            f"drawtext=fontfile='{_esc_path(fonts.get(t['font']) or fonts.get('display') or '')}'",
            f"textfile='{_esc_path(text_files[i])}'",
            f"fontsize={size}", f"fontcolor={t['color']}",
            f"x={x}", f"y={y}", "line_spacing=8",
            # The copy is LITERAL. drawtext expands %-sequences even out of a
            # textfile, so "50% off" logged "Stray %" and drew mangled text —
            # caught rendering it on the deployed ffmpeg.
            "expansion=none",
        ]
        if t["box"]:
            parts += [f"box=1", f"boxcolor={t['box_color']}@{t['box_opacity']:.2f}",
                      f"boxborderw={max(6, size // 4)}"]
        chain.append(":".join(parts) + _enable(t["start"], t["end"]))
    graph = f"[0:v]{','.join(chain)}[v]"
    if has_logo and d["logo"]:
        lg = d["logo"]
        lw = max(8, int(round(lg["w"] * out_w)))
        lx, ly = int(lg["x"] * out_w), int(lg["y"] * out_h)
        graph += (f";[1:v]scale={lw}:-1[lg]"
                  f";[v][lg]overlay={lx}:{ly}{_enable(lg['start'], lg['end'])}[vout]")
    else:
        graph += ";[v]null[vout]"
    return graph


def build_args(
    doc: dict, src: str, dst: str, *, src_w: int, src_h: int, fonts: dict[str, str],
    text_files: list[str], logo_path: str | None, duration: float | None = None,
) -> list[str]:
    """The exact ffmpeg invocation. Pure, so a test can read the command instead
    of rendering a video to find out what it would have done."""
    d = normalize(doc)
    out_w, out_h = out_size(d, src_w, src_h)
    start = d["trim"]["start"]
    end = d["trim"]["end"]
    if end is not None and duration:
        end = min(end, duration)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", src]
    if logo_path:
        args += ["-i", logo_path]
    if end is not None and end > start:
        args += ["-t", f"{end - start:.3f}"]
    args += [
        "-filter_complex",
        build_filters(d, src_w, src_h, fonts=fonts, text_files=text_files,
                      has_logo=bool(logo_path), out_w=out_w, out_h=out_h),
        "-map", "[vout]",
    ]
    if d["audio"]["mute"]:
        args += ["-an"]
    else:
        # `?` so a silent source (a cut with no audio track) renders instead of
        # failing on a stream that isn't there.
        args += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst,
    ]
    return args


def cover_args(src: str, dst: str, at: float) -> list[str]:
    """One frame, for the still people see before they press play."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at):.3f}", "-i", src, "-frames:v", "1",
            "-q:v", "3", dst]


async def _run(args: list[str], timeout: float = 900.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")[-4000:]


async def probe(path: str) -> dict:
    """What the file IS: how long, how big, whether it carries sound. The editor
    lays its scrubber and its frame out from this."""
    code, out = await _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", path], timeout=120)
    if code != 0:
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    info = {"duration": float((data.get("format") or {}).get("duration") or 0.0),
            "width": 0, "height": 0, "has_audio": False}
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not info["width"]:
            info["width"] = int(s.get("width") or 0)
            info["height"] = int(s.get("height") or 0)
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


async def render(
    src_path: str, doc: dict, *, fonts: dict[str, str], logo_path: str | None,
    work_dir: str,
) -> dict:
    """Apply the doc to the file. Returns {ok, path, cover_path, duration, width,
    height} or {ok: False, reason} — never a half-written file passed off as a
    render."""
    info = await probe(src_path)
    if not info.get("width") or not info.get("height"):
        return {"ok": False, "reason": "could not read that video"}
    d = normalize(doc)
    total = info.get("duration") or 0.0
    start = d["trim"]["start"]
    end = d["trim"]["end"] if d["trim"]["end"] is not None else total
    if total and end > total:
        end = total
    if total and end - start < MIN_DURATION:
        return {"ok": False, "reason": f"the trimmed video would be under {MIN_DURATION}s"}
    if end - start > MAX_DURATION:
        return {"ok": False, "reason": "that video is too long to edit here"}

    files: list[str] = []
    for i, t in enumerate(d["texts"]):
        p = os.path.join(work_dir, f"text-{i}.txt")
        Path(p).write_text(t["text"], encoding="utf-8")
        files.append(p)

    dst = os.path.join(work_dir, "edited.mp4")
    out_w, out_h = out_size(d, info["width"], info["height"])
    args = build_args(d, src_path, dst, src_w=info["width"], src_h=info["height"],
                      fonts=fonts, text_files=files, logo_path=logo_path, duration=total)
    code, log = await _run(args)
    if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        return {"ok": False, "reason": f"the render failed: {log[-300:] or code}"}

    cover_path = None
    if d["cover"]:
        cp = os.path.join(work_dir, "cover.jpg")
        # the cover is picked on the TRIMMED timeline, which is what was on screen
        c_code, _ = await _run(cover_args(dst, cp, d["cover"]["t"]), timeout=120)
        if c_code == 0 and os.path.exists(cp) and os.path.getsize(cp) > 0:
            cover_path = cp
    return {"ok": True, "path": dst, "cover_path": cover_path,
            "duration": round(max(0.0, end - start), 2), "width": out_w, "height": out_h}
