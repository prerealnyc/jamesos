"""Draw caption flashes onto a finished cut — locally, with libass.

Captions are burned into a reel during assembly, so changing where they sit has
always meant paying for the whole pipeline again: re-cut, re-transcribe,
regenerate B-roll, re-assemble. That is a lot of money and a different video to
move some words down forty pixels.

Everything a re-draw actually needs is now kept on the production (migration
062): `clean_cut_url`, the captionless cut the words were drawn on, and
`caption_cues`, the flashes with their timings. This module puts the second back
onto the first in one local ffmpeg pass — seconds, no provider spend, and the
footage is the same pixels the owner already approved.

ASS rather than drawtext on purpose. drawtext needs one filter per flash (a reel
runs 35-40 of them), has no real box/outline model, and escapes badly. libass
takes the whole track as one file, positions to the pixel, and is already
compiled into the image (`--enable-libass`).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from .caption_styles import clamp_caption_y, get_preset

# A reel's worth of flashes with room to spare. Past this something has gone
# wrong upstream, and a 10,000-line subtitle file is not worth rendering.
MAX_CUES = 600
MIN_FLASH = 0.10          # a flash shorter than this is a flicker, not a caption
_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")

# Preset font_family → the family name libass will match inside _FONT_DIR.
# Every face a preset can ask for is listed, because libass resolves by NAME and
# a name with no file behind it falls back SILENTLY — the reel comes back in the
# wrong typeface with nothing in the logs. Inter is the one preset face we do
# not ship; Montserrat is the closest thing on disk (both geometric sans), so
# clean_white/subtle_minimal re-draw close to how they rendered rather than in a
# heavy display face.
_FAMILIES = {
    "archivo black": "Archivo Black",
    "archivo": "Archivo Black",
    "anton": "Anton",
    "playfair display": "Playfair Display",
    "playfair": "Playfair Display",
    "poppins": "Poppins",
    "montserrat": "Montserrat",
    "inter": "Montserrat",
}
_FALLBACK_FAMILY = "Archivo Black"


def _family_for(preset: dict) -> str:
    name = str(preset.get("font_family") or "").strip().lower()
    for key, fam in _FAMILIES.items():
        if key in name:
            return fam
    return _FALLBACK_FAMILY


def _px(value: Any, height: int, *, relative_to: float = 0.0) -> float:
    """A preset length in pixels.

    Presets speak several units: "0.6 vh" (percent of frame HEIGHT, the unit
    Creatomate renders in), "0.5%" (percent of the font size, used for letter
    spacing), "12px", and bare numbers. Anything unreadable is 0 — a missing
    outline is a caption that still reads, a crashed render is not."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if not s:
        return 0.0
    try:
        if s.endswith("vh"):
            return float(s[:-2].strip()) / 100.0 * float(height)
        if s.endswith("%"):
            return float(s[:-1].strip()) / 100.0 * float(relative_to or height)
        if s.endswith("px"):
            return float(s[:-2].strip())
        return float(s)
    except ValueError:
        return 0.0


def _ass_color(hex_color: str | None, *, default: str = "&H00FFFFFF&",
               opacity: float = 1.0) -> str:
    """#RRGGBB → ASS &HAABBGGRR. ASS stores BGR, and its alpha is inverted
    (00 = opaque, FF = invisible) — both are easy to get backwards, which is
    why this is one function with one test."""
    s = str(hex_color or "").strip()
    if not s or s.lower() in ("transparent", "none"):
        return "&H00000000&"
    if s.lower().startswith("rgba(") or s.lower().startswith("rgb("):
        nums = [n.strip() for n in s[s.index("(") + 1:].rstrip(") ").split(",")]
        try:
            r, g, b = (int(float(nums[i])) for i in range(3))
            a_in = float(nums[3]) if len(nums) > 3 else 1.0
        except (ValueError, IndexError):
            return default
        a = int(round((1.0 - max(0.0, min(1.0, a_in * opacity))) * 255))
        return f"&H{a:02X}{b & 255:02X}{g & 255:02X}{r & 255:02X}&"
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return default
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return default
    a = int(round((1.0 - max(0.0, min(1.0, opacity))) * 255))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}&"


def _ass_time(t: float) -> str:
    """Seconds → H:MM:SS.cc (ASS centisecond precision)."""
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    """Make a caption safe inside a dialogue line. `{}` opens an override block
    and a raw newline ends the event, so both have to go."""
    out = (str(text or "")
           .replace("\\", "\\\\")
           .replace("{", "(")
           .replace("}", ")"))
    out = out.replace("\r\n", "\\N").replace("\n", "\\N").replace("\r", "\\N")
    return out.strip()


def normalize_cues(cues: Any) -> list[dict]:
    """Take whatever was stashed and return flashes we can actually draw:
    in time order, non-empty, with a sane duration."""
    if not isinstance(cues, (list, tuple)):
        return []
    out: list[dict] = []
    for c in cues:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        raw = str(c.get("raw_text") or "").strip()
        if not (text or raw):
            continue
        try:
            start = max(0.0, float(c.get("start") or 0.0))
            end = float(c.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if end - start < MIN_FLASH:
            end = start + MIN_FLASH
        out.append({"start": start, "end": end, "text": text, "raw_text": raw})
    out.sort(key=lambda c: c["start"])
    return out[:MAX_CUES]


def build_ass(cues: list[dict], *, width: int, height: int,
              y_pct: float, preset: dict,
              hook: dict | None = None, hook_y: float | None = None) -> str:
    """The whole caption track as one ASS document.

    Positioning is explicit `\\pos` with `\\an5` (middle-centre), so `y_pct` is
    the block's CENTRE — exactly what it means in the assembler, so a reel
    re-captioned here lands where the same number would have put it there.
    """
    font_px = max(12, int(round(float(preset.get("font_size_vh") or 8.0)
                                / 100.0 * height)))
    fill = _ass_color(preset.get("fill_color"), default="&H00FFFFFF&")
    stroke_w = _px(preset.get("stroke_width"), height)
    has_stroke = bool(preset.get("stroke_color")) and \
        str(preset.get("stroke_color")).lower() != "transparent" and stroke_w > 0
    outline = _ass_color(preset.get("stroke_color")) if has_stroke else "&H00000000&"

    box = preset.get("background_color")
    has_box = bool(box) and str(box).lower() not in ("transparent", "none")
    # BorderStyle 3 = opaque box behind the text; 1 = outline + drop shadow.
    border_style = 3 if has_box else 1
    back = _ass_color(box) if has_box else _ass_color(
        preset.get("shadow_color"), default="&H80000000&", opacity=0.5)
    shadow = 0.0 if has_box else round(
        _px(preset.get("shadow_y"), height) or (1.0 if preset.get("shadow_color") else 0.0), 1)
    outline_px = max(0.0, stroke_w if has_stroke
                     else (0.0 if has_box else max(1.0, font_px * 0.03)))

    x = width // 2
    y = int(round(max(0.0, min(100.0, y_pct)) / 100.0 * height))
    upper = str(preset.get("transform") or "").lower() == "uppercase"
    family = _family_for(preset)
    # Letter spacing is a percentage OF THE FONT SIZE in the presets, not of
    # the frame — 0.5% of 1920px would be a caption with gaps between letters.
    spacing = _px(preset.get("letter_spacing"), height, relative_to=font_px)

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        (f"Style: Cap,{family},{font_px},{fill},{fill},{outline},{back},"
         f"{-1 if int(preset.get('font_weight') or 400) >= 600 else 0},0,0,0,"
         f"100,100,{spacing:.1f},0,{border_style},{outline_px:.1f},{shadow},"
         # Horizontal margins keep a long flash inside the same 60% box the
         # assembler uses, so wrapping matches what the owner saw.
         f"5,{int(width * 0.20)},{int(width * 0.20)},0,1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    hook_style, hook_line = ("", "")
    if hook and str(hook.get("text") or "").strip():
        hook_style, hook_line = build_hook_ass(
            str(hook["text"]), hold=float(hook.get("hold") or 0.0),
            width=width, height=height,
            y_pct=HOOK_DEFAULT_Y_PCT if hook_y is None else hook_y)
    if hook_style:
        # Directly after the Cap style, inside [V4+ Styles] — not after the blank
        # line that closes the section.
        cap_at = next(i for i, ln in enumerate(head) if ln.startswith("Style: Cap,"))
        head.insert(cap_at + 1, hook_style)

    lines: list[str] = []
    if hook_line:
        lines.append(hook_line)
    for c in cues:
        # Mixed-case presets must not show the per-word ALL-CAPS emphasis the
        # flash builder bakes into `text` — same rule as caption_element.
        body = c["text"] if upper else (c.get("raw_text") or c["text"])
        body = body.upper() if upper else body
        text = _ass_escape(body)
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Cap,,0,0,0,,"
            f"{{\\an5\\pos({x},{y})}}{text}")
    return "\n".join(head + lines) + "\n"


# The headline block, matched to what the assembler draws: Archivo Black, white
# with a thin black edge, on a dark translucent pill. It is deliberately a
# DIFFERENT shape from the captions — bigger, boxed, two lines at most — because
# it is a title card, not a subtitle.
HOOK_FONT_VH = 9.0
HOOK_MAX_LINES = 2
HOOK_DEFAULT_Y_PCT = 57.0


def _hook_split(text: str) -> list[str]:
    """The headline as at most two balanced lines. The assembler caps it at two
    for a reason — a three-line pill reaches down into the caption band."""
    words = [w for w in str(text or "").split() if w]
    if not words:
        return []
    if len(words) <= 3:
        return [" ".join(words)]
    best = (10 ** 9, [" ".join(words)])
    for cut in range(1, len(words)):
        a, b = " ".join(words[:cut]), " ".join(words[cut:])
        m = max(len(a), len(b))
        if m < best[0]:
            best = (m, [a, b])
    return best[1][:HOOK_MAX_LINES]


def build_hook_ass(text: str, *, hold: float, width: int, height: int,
                   y_pct: float) -> tuple[str, str]:
    """(style line, dialogue line) for the headline, or ("", "") if there is none."""
    lines = _hook_split(text)
    if not lines or hold <= 0:
        return "", ""
    longest = max(len(ln) for ln in lines)
    # Same fit rule as the assembler: the TEXT sits in 76% so the pill's padding
    # still lands inside the safe margin and the line never wraps.
    max_vh = (0.76 * float(width)) / (max(1, longest) * 0.74 * (float(height) / 100.0))
    vh = round(min(HOOK_FONT_VH, max(3.2, max_vh * 0.94)), 1)
    font_px = max(12, int(round(vh / 100.0 * height)))
    y = int(round(max(0.0, min(100.0, y_pct)) / 100.0 * height))
    style = (f"Style: Hook,Archivo Black,{font_px},&H00FFFFFF&,&H00FFFFFF&,"
             f"&H00000000&,&H38141408&,-1,0,0,0,100,100,0,0,3,"
             f"{max(1.0, font_px * 0.04):.1f},0,5,"
             f"{int(width * 0.09)},{int(width * 0.09)},0,1")
    body = "\\N".join(_ass_escape(ln.upper()) for ln in lines)
    dialogue = (f"Dialogue: 1,{_ass_time(0.0)},{_ass_time(hold)},Hook,,0,0,0,,"
                f"{{\\an5\\pos({width // 2},{y})}}{body}")
    return style, dialogue


def _escape_filter_path(path: str) -> str:
    """A path inside an ffmpeg filter argument. Windows-style colons and the
    filter-graph separators all need escaping or the graph fails to parse."""
    return (path.replace("\\", "/")
                .replace(":", r"\:")
                .replace("'", r"\'")
                .replace(",", r"\,")
                .replace("[", r"\[")
                .replace("]", r"\]"))


async def probe_size(path: str) -> tuple[int, int, float]:
    """(width, height, duration) of a local file."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    vals = [v for v in out.decode().split() if v.strip()]
    try:
        return int(float(vals[0])), int(float(vals[1])), float(vals[2])
    except (IndexError, ValueError):
        return 0, 0, 0.0


def build_args(src: str, dst: str, ass_path: str | None,
               size: tuple[int, int] | None = None) -> list[str]:
    """ffmpeg argv for the burn. With no track, this is still a re-encode: that
    is what "captions off" means when the alternative has them baked in.

    `size` is the FINISHED reel's frame. The cut we keep is the footage as it was
    cut from the source — 360x640 on this brand — while the assembly upscaled its
    output to 1080x1920, so burning onto the cut and stopping there handed the
    owner a third of the resolution they had. Scaling comes BEFORE the subtitle
    filter so the words are drawn at full size rather than drawn small and then
    enlarged with the picture.
    """
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src]
    chain = []
    if size and size[0] > 0 and size[1] > 0:
        chain.append(f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1")
    if ass_path:
        chain.append(f"ass='{_escape_filter_path(ass_path)}':"
                     f"fontsdir='{_escape_filter_path(_FONT_DIR)}'")
    if chain:
        args += ["-vf", ",".join(chain)]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-c:a", "copy", dst]
    return args


async def render(cut_path: str, cues: Any, *, y_pct: float | str | None,
                 style: str | None, off: bool = False,
                 hook: dict | None = None, hook_y: float | str | None = None,
                 hook_off: bool = False, target_size: tuple[int, int] | None = None,
                 work_dir: str) -> dict:
    """Burn `cues` (and the headline) onto `cut_path`.

    Returns {ok, path, duration, width, height} or {ok: False, reason}.

    `off` renders the cut with no captions at all — the one thing neither the
    assembler nor the existing video editor could do (a blank caption style
    means "pick one for me", and the editor can only draw ON TOP of words
    already baked in). `hook_off` does the same for the headline.
    """
    if not os.path.exists(cut_path):
        return {"ok": False, "reason": "the captionless cut is not available"}
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "reason": "ffmpeg is not available"}

    width, height, duration = await probe_size(cut_path)
    if width <= 0 or height <= 0:
        return {"ok": False, "reason": "could not read the cut"}
    # Author the caption track for the frame it will be SHOWN in, not the frame
    # the cut happens to be stored at, or the words come out a third of the size.
    scale_to: tuple[int, int] | None = None
    if target_size and target_size[0] > 0 and target_size[1] > 0 \
            and (target_size[0], target_size[1]) != (width, height):
        scale_to = (target_size[0], target_size[1])
        width, height = scale_to

    the_hook = None if hook_off else (hook if isinstance(hook, dict) else None)
    hold = float((the_hook or {}).get("hold") or 0.0)

    flashes = [] if off else normalize_cues(cues)
    if off and not the_hook:
        flashes = []
    elif not off and not flashes and not the_hook:
        return {"ok": False, "reason":
                "this reel has no stored captions to move — it was rendered "
                "before they were kept, so it needs a full re-render"}
    # The assembler holds the caption track back until the headline clears, so
    # the two never share the screen. The cues we stashed are the UNFILTERED
    # list, so the same rule has to be applied here or a re-draw would put
    # captions under a headline that was never sharing the frame with them.
    if the_hook and hold > 0:
        flashes = [c for c in flashes if c["start"] >= hold]

    ass_path: str | None = None
    if flashes or the_hook:
        preset = get_preset(style)
        y = float((clamp_caption_y(y_pct) or "78%").rstrip("%"))
        hy = clamp_caption_y(hook_y)
        ass_path = os.path.join(work_dir, "captions.ass")
        with open(ass_path, "w", encoding="utf-8") as fh:
            fh.write(build_ass(flashes, width=width, height=height,
                               y_pct=y, preset=preset, hook=the_hook,
                               hook_y=None if hy is None else float(hy.rstrip("%"))))

    dst = os.path.join(work_dir, "recaptioned.mp4")
    proc = await asyncio.create_subprocess_exec(
        *build_args(cut_path, dst, ass_path, scale_to),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(dst):
        return {"ok": False,
                "reason": (err.decode()[-400:] or "the caption pass failed")}
    _, _, out_dur = await probe_size(dst)
    return {"ok": True, "path": dst, "duration": out_dur or duration,
            "width": width, "height": height}
