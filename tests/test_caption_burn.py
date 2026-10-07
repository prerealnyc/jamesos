"""Re-drawing the captions on a reel that is already made.

The engine burns captions in during assembly, so "move them down a bit" used to
cost a whole new pipeline run — and came back a different video. These pin the
local re-draw: the stashed flashes onto the stashed captionless cut, positioned
where the owner asked.
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from james_os import caption_burn as cb
from james_os.caption_styles import get_preset

pytestmark = pytest.mark.nodb

CUES = [
    {"start": 0.0, "end": 1.2, "text": "CAN JACKSON", "raw_text": "Can Jackson"},
    {"start": 1.2, "end": 2.4, "text": "BIRDIE THE", "raw_text": "birdie the"},
    {"start": 2.4, "end": 3.6, "text": "COURSE RECORD", "raw_text": "course record"},
]


def _has_filter(name: str) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


# --- colour, time and text conversion ---------------------------------------

def test_a_hex_colour_becomes_ass_bgr_with_inverted_alpha():
    """ASS stores BGR, not RGB, and 00 alpha means OPAQUE. Both are easy to get
    backwards and the result is a caption in the wrong colour or invisible."""
    assert cb._ass_color("#FFFFFF") == "&H00FFFFFF&"
    assert cb._ass_color("#FF0000") == "&H000000FF&"   # red → BB=00 GG=00 RR=FF
    assert cb._ass_color("#0000FF") == "&H00FF0000&"   # blue → BB=FF
    assert cb._ass_color("#123456") == "&H00563412&"
    assert cb._ass_color("#fff") == "&H00FFFFFF&"
    assert cb._ass_color("#FFFFFF", opacity=0.0).startswith("&HFF")


def test_a_missing_or_transparent_colour_does_not_paint_a_black_box():
    assert cb._ass_color("transparent") == "&H00000000&"
    assert cb._ass_color("") == "&H00000000&"
    assert cb._ass_color(None) == "&H00000000&"
    assert cb._ass_color("nonsense", default="&H00FFFFFF&") == "&H00FFFFFF&"


def test_times_are_written_in_ass_centiseconds():
    assert cb._ass_time(0) == "0:00:00.00"
    assert cb._ass_time(3.6) == "0:00:03.60"
    assert cb._ass_time(75.25) == "0:01:15.25"
    assert cb._ass_time(3671.5) == "1:01:11.50"
    assert cb._ass_time(-4) == "0:00:00.00"


def test_caption_text_cannot_break_out_of_its_dialogue_line():
    """`{}` opens an ASS override block and a newline ends the event — a caption
    containing either would corrupt every line after it."""
    assert "{" not in cb._ass_escape("a {\\an8} b")
    assert "}" not in cb._ass_escape("a {\\an8} b")
    assert "\n" not in cb._ass_escape("two\nlines")
    assert "\\N" in cb._ass_escape("two\nlines")


# --- what gets drawn --------------------------------------------------------

def test_the_owners_position_is_where_the_caption_lands():
    """y is the block's CENTRE, same as in the assembler, so the same number
    means the same place in both."""
    doc = cb.build_ass(CUES, width=1080, height=1920, y_pct=40.0,
                       preset=get_preset("bold_pop"))
    assert r"\pos(540,768)" in doc          # 40% of 1920
    assert r"\an5" in doc                   # anchored on its middle
    assert doc.count("Dialogue:") == len(CUES)


def test_moving_the_captions_changes_only_the_position():
    a = cb.build_ass(CUES, width=1080, height=1920, y_pct=78.0,
                     preset=get_preset("bold_pop"))
    b = cb.build_ass(CUES, width=1080, height=1920, y_pct=35.0,
                     preset=get_preset("bold_pop"))
    strip = lambda d: "\n".join(
        l.split("}", 1)[-1] if l.startswith("Dialogue:") else l for l in d.splitlines())
    assert strip(a) == strip(b)
    assert a != b


def test_the_style_carries_the_presets_look():
    doc = cb.build_ass(CUES, width=1080, height=1920, y_pct=78.0,
                       preset=get_preset("bold_pop"))
    style = [l for l in doc.splitlines() if l.startswith("Style: Cap,")][0]
    assert "Anton" in style or "Archivo" in style
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc


def test_an_uppercase_preset_shouts_and_a_mixed_case_one_does_not():
    """The flash builder bakes per-word ALL-CAPS emphasis into `text`. Mixed-case
    presets read as normal sentences and must show `raw_text` instead — the same
    rule the assembler follows, so a re-draw doesn't change the words."""
    upper = cb.build_ass(CUES, width=1080, height=1920, y_pct=78.0,
                         preset=dict(get_preset("bold_pop"), transform="uppercase"))
    mixed = cb.build_ass(CUES, width=1080, height=1920, y_pct=78.0,
                         preset=dict(get_preset("clean_white"), transform="none"))
    assert "CAN JACKSON" in upper
    assert "Can Jackson" in mixed
    assert "CAN JACKSON" not in mixed


def test_a_caption_stays_inside_the_same_side_margins_as_the_assembler():
    """The assembler caps a caption at 60% width (20% each side). A re-draw that
    ran to the frame edge would re-wrap every line."""
    doc = cb.build_ass(CUES, width=1080, height=1920, y_pct=78.0,
                       preset=get_preset("bold_pop"))
    style = [l for l in doc.splitlines() if l.startswith("Style: Cap,")][0]
    assert ",216,216," in style             # 20% of 1080 each side


# --- the cues we are handed -------------------------------------------------

def test_junk_cues_are_dropped_rather_than_drawn():
    cues = cb.normalize_cues([
        {"start": 1.0, "end": 2.0, "text": "real"},
        {"start": 0.0, "end": 1.0, "text": "   "},        # blank
        "not a dict",
        {"start": "x", "end": "y", "text": "bad times"},
        {"start": 3.0, "end": 3.0, "text": "zero length"},
    ])
    assert [c["text"] for c in cues] == ["real", "zero length"]
    assert cues[1]["end"] > cues[1]["start"]   # given a floor, not dropped


def test_cues_are_drawn_in_time_order():
    cues = cb.normalize_cues([
        {"start": 5.0, "end": 6.0, "text": "last"},
        {"start": 1.0, "end": 2.0, "text": "first"},
    ])
    assert [c["text"] for c in cues] == ["first", "last"]


def test_an_absurd_cue_list_is_capped():
    many = [{"start": i, "end": i + 0.5, "text": f"w{i}"} for i in range(5000)]
    assert len(cb.normalize_cues(many)) == cb.MAX_CUES


def test_nothing_usable_is_not_an_empty_subtitle_file():
    assert cb.normalize_cues(None) == []
    assert cb.normalize_cues("captions") == []
    assert cb.normalize_cues([]) == []


# --- the ffmpeg call --------------------------------------------------------

def test_the_burn_reuses_the_audio_untouched():
    """Re-drawing captions must never re-encode the audio — that is a second
    generation of loss for a change that is entirely visual."""
    args = cb.build_args("in.mp4", "out.mp4", "/tmp/c.ass")
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "copy"


def test_captions_off_renders_with_no_subtitle_filter():
    args = cb.build_args("in.mp4", "out.mp4", None)
    assert not any(a == "-vf" for a in args)
    assert "ass=" not in " ".join(args)


def test_the_font_directory_travels_with_the_filter():
    """libass resolves faces by NAME through fontconfig; without fontsdir the
    brand's face silently becomes whatever the box has."""
    args = cb.build_args("in.mp4", "out.mp4", "/tmp/c.ass")
    vf = args[args.index("-vf") + 1]
    assert "fontsdir=" in vf
    assert os.path.isdir(cb._FONT_DIR)


def test_a_path_with_a_colon_cannot_break_the_filter_graph():
    esc = cb._escape_filter_path("/tmp/a:b/c,d[e].ass")
    for ch in (":", ",", "[", "]"):
        assert f"\\{ch}" in esc


# --- end to end -------------------------------------------------------------

def _make_clip(path: str, seconds: float = 4.0) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=360x640:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.exists(path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_captions_off_really_produces_a_playable_video(tmp_path):
    """The 'off' path needs no libass, so it runs anywhere ffmpeg does."""
    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src):
        pytest.skip("could not build a test clip")
    out = asyncio.run(cb.render(src, CUES, y_pct=78, style="bold_pop",
                                off=True, work_dir=str(tmp_path)))
    assert out["ok"], out.get("reason")
    assert os.path.getsize(out["path"]) > 0
    assert 3.5 < out["duration"] < 4.5
    assert (out["width"], out["height"]) == (360, 640)


@pytest.mark.skipif(not _has_filter("ass"), reason="this ffmpeg has no libass")
def test_a_real_reel_is_recaptioned_in_one_pass(tmp_path):
    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src):
        pytest.skip("could not build a test clip")
    out = asyncio.run(cb.render(src, CUES, y_pct=35, style="bold_pop",
                                work_dir=str(tmp_path)))
    assert out["ok"], out.get("reason")
    assert os.path.getsize(out["path"]) > 0
    assert 3.5 < out["duration"] < 4.5


def test_a_reel_with_no_stashed_captions_says_so_instead_of_shipping_a_blank(tmp_path):
    """Every reel rendered before the stash existed lands here. The owner has to
    be told it needs a full re-render, not handed a silent no-op."""
    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src):
        pytest.skip("could not build a test clip")
    out = asyncio.run(cb.render(src, [], y_pct=40, style="bold_pop",
                                work_dir=str(tmp_path)))
    assert out["ok"] is False
    assert "full re-render" in out["reason"]


def test_a_missing_cut_is_refused_before_ffmpeg_runs(tmp_path):
    out = asyncio.run(cb.render(str(tmp_path / "nope.mp4"), CUES, y_pct=40,
                                style="bold_pop", work_dir=str(tmp_path)))
    assert out["ok"] is False
    assert "not available" in out["reason"]


# --- the headline -----------------------------------------------------------
#
# The big boxed hook over the first few seconds is a SEPARATE element from the
# captions, with its own hard-coded position (57%). The owner asked to move
# "captions and headlines"; moving only the captions answers half of it.

HOOK = {"text": "WHY WEEKENDS FEEL ENDLESS AT TURTLEBACK", "hold": 3.2}


def _hook_doc(**kw):
    opts = {"width": 1080, "height": 1920, "y_pct": 78.0,
            "preset": get_preset("bold_pop"), "hook": HOOK}
    opts.update(kw)
    return cb.build_ass(list(CUES), **opts)


def _hook_line(doc):
    return [l for l in doc.splitlines() if ",Hook," in l][0]


def test_the_headline_is_drawn_and_lands_where_asked():
    doc = _hook_doc(hook_y=40.0)
    hook_line = [l for l in doc.splitlines() if ",Hook," in l][0]
    assert r"\pos(540,768)" in hook_line          # 40% of 1920
    assert "WHY WEEKENDS FEEL" in hook_line


def test_the_headline_defaults_to_where_it_has_always_been():
    doc = _hook_doc()
    hook_line = [l for l in doc.splitlines() if ",Hook," in l][0]
    assert r"\pos(540,1094)" in hook_line          # 57% of 1920


def test_the_headline_holds_then_clears():
    doc = _hook_doc()
    hook_line = [l for l in doc.splitlines() if ",Hook," in l][0]
    assert "0:00:00.00,0:00:03.20" in hook_line


def test_the_headline_is_two_balanced_lines_at_most():
    """A three-line pill reaches down into the caption band — the assembler caps
    it at two for exactly that reason."""
    doc = _hook_doc()
    hook_line = [l for l in doc.splitlines() if ",Hook," in l][0]
    body = hook_line.split("}", 1)[1]
    assert body.count("\\N") <= cb.HOOK_MAX_LINES - 1
    assert cb._hook_split("one two three four five six seven eight") == [
        "one two three four", "five six seven eight"]
    assert cb._hook_split("short hook") == ["short hook"]
    assert cb._hook_split("") == []


def test_the_headline_gets_its_own_style_inside_the_styles_section():
    """A Style line after the blank that closes [V4+ Styles] is not guaranteed
    to be read — it has to sit with the other styles."""
    doc = _hook_doc()
    lines = doc.splitlines()
    styles_at = lines.index("[V4+ Styles]")
    events_at = lines.index("[Events]")
    hook_at = [i for i, l in enumerate(lines) if l.startswith("Style: Hook,")][0]
    assert styles_at < hook_at < events_at
    assert lines[hook_at - 1].startswith("Style: Cap,")


def test_captions_under_the_headline_are_held_back_exactly_as_the_assembler_does(tmp_path):
    """The stashed cues are the UNFILTERED list. Without re-applying the hold, a
    re-draw shows captions under a headline they never shared the frame with."""
    early = [{"start": 0.5, "end": 1.5, "text": "TOO EARLY"},
             {"start": 4.0, "end": 5.0, "text": "AFTER THE HOOK"}]
    doc = cb.build_ass(cb.normalize_cues(early), width=1080, height=1920, y_pct=78.0,
                       preset=get_preset("bold_pop"), hook=HOOK)
    # build_ass draws what it is given; the hold is applied in render().
    assert "TOO EARLY" in doc

    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src, 8.0):
        pytest.skip("could not build a test clip")
    captured = {}
    real = cb.build_ass

    def spy(cues, **kw):
        captured["texts"] = [c["text"] for c in cues]
        return real(cues, **kw)

    cb.build_ass = spy
    try:
        asyncio.run(cb.render(src, early, y_pct=78, style="bold_pop",
                              hook=HOOK, work_dir=str(tmp_path)))
    finally:
        cb.build_ass = real
    assert captured["texts"] == ["AFTER THE HOOK"]


def test_the_headline_can_be_removed_on_its_own(tmp_path):
    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src):
        pytest.skip("could not build a test clip")
    captured = {}
    real = cb.build_ass

    def spy(cues, **kw):
        captured["hook"] = kw.get("hook")
        return real(cues, **kw)

    cb.build_ass = spy
    try:
        asyncio.run(cb.render(src, CUES, y_pct=78, style="bold_pop",
                              hook=HOOK, hook_off=True, work_dir=str(tmp_path)))
    finally:
        cb.build_ass = real
    assert captured["hook"] is None


@pytest.mark.skipif(not _has_filter("ass"), reason="this ffmpeg has no libass")
def test_a_reel_with_only_a_headline_and_no_cues_still_redraws(tmp_path):
    """Captions off, headline moved — that combination must not be refused as
    'nothing stored to move'."""
    src = str(tmp_path / "cut.mp4")
    if not _make_clip(src):
        pytest.skip("could not build a test clip")
    out = asyncio.run(cb.render(src, [], y_pct=78, style="bold_pop", off=True,
                                hook=HOOK, hook_y=35, work_dir=str(tmp_path)))
    assert out["ok"] is True, out.get("reason")


def test_every_preset_resolves_to_a_typeface_we_actually_ship():
    """libass resolves by NAME and falls back SILENTLY — a preset asking for a
    face with no file behind it comes back in the wrong typeface with nothing in
    the logs. Inter is the one preset face we do not ship."""
    import os
    from james_os.caption_styles import CAPTION_PRESETS

    shipped = {f.lower() for f in os.listdir(cb._FONT_DIR) if f.endswith(".ttf")}
    for name, preset in CAPTION_PRESETS.items():
        fam = cb._family_for(preset).replace(" ", "").lower()
        assert any(fam in f.replace("-", "") for f in shipped), f"{name} → {fam}"


# --- the owner writes the headline ------------------------------------------
#
# On a rebuilt reel the headline is a PARAPHRASE: the original was written at
# render time and never stored. So the owner gets the last word on what their
# own video says.

def test_the_headline_draws_whatever_words_it_is_given():
    doc = _hook_doc(hook={"text": "WORDS THE OWNER TYPED", "hold": 3.0}, hook_y=30.0)
    # Balanced over two lines, so compare the words rather than the raw string.
    body = _hook_line(doc).split("}", 1)[1]
    assert body.replace("\\N", " ") == "WORDS THE OWNER TYPED"


def test_a_typed_headline_is_still_capped_at_two_lines():
    """A long sentence typed into the box must not become a five-line pill that
    reaches down into the caption band."""
    long_text = "this is a very long headline that someone typed without stopping to think"
    doc = _hook_doc(hook={"text": long_text, "hold": 3.0})
    line = [l for l in doc.splitlines() if ",Hook," in l][0]
    assert line.split("}", 1)[1].count("\\N") <= cb.HOOK_MAX_LINES - 1


def test_headline_text_cannot_break_the_subtitle_file():
    """It is free text from a person, so it gets the same escaping as a caption."""
    doc = _hook_doc(hook={"text": "a {\\an8} b\nc", "hold": 3.0})
    line = [l for l in doc.splitlines() if ",Hook," in l][0]
    body = line.split("}", 1)[1]
    assert "{" not in body and "}" not in body
    assert "\n" not in body


def test_an_empty_headline_draws_nothing_rather_than_an_empty_box():
    for text in ("", "   ", None):
        style, line = cb.build_hook_ass(text, hold=3.0, width=1080, height=1920, y_pct=57)
        assert (style, line) == ("", "")


def test_the_recaption_request_carries_the_headline_words():
    from james_os.api_v1 import _HOOK_TEXT_MAX, RecaptionRequest

    req = RecaptionRequest(production_id="p", hook_text="  My own headline  ")
    assert req.hook_text.strip() == "My own headline"
    assert 40 <= _HOOK_TEXT_MAX <= 120


def test_the_recaption_row_only_selects_columns_that_exist():
    """`duration` is not a column on video_productions — selecting it 500s every
    open of the caption editor. Caught in review, not by a test, so here is one."""
    import inspect

    from james_os import api_v1

    sql = inspect.getsource(api_v1._recaption_row)
    selected = sql[sql.index("SELECT"):sql.index("FROM video_productions")]
    for bad in ("duration", "length_s", "duration_s"):
        assert bad not in selected, f"{bad} is not a column on video_productions"


# --- B-roll cutaways --------------------------------------------------------

def test_a_reel_with_broll_cutaways_is_refused_rather_than_stripped():
    """Cutaways are composited over the cut by the assembler, so they are NOT in
    the captionless cut we keep. Redrawing captions on that cut alone hands the
    owner their reel with every cutaway gone — 33 of James's 43 reels have
    them, so this is the common case, not the corner."""
    from james_os.api_v1 import _broll_inserts, _recaption_readiness

    window = {"source_url": "https://x/s.mp4", "start_s": 0.0, "end_s": 15.0}
    insert = {"start": 4.0, "end": 7.0, "image_url": "https://x/a.png",
              "video_url": "https://x/a.mp4", "text": "housing market"}
    assert _broll_inserts({"scenes": [window]}) == 0
    assert _broll_inserts({"scenes": [window, insert, insert]}) == 2
    import json as _json
    assert _broll_inserts({"scenes": _json.dumps([window, insert])}) == 1
    for junk in (None, "", "not json", {}, 7, [None, "x"]):
        assert _broll_inserts({"scenes": junk}) == 0

    ready = _recaption_readiness({
        "scenes": [window, insert], "clean_cut_url": "https://x/cut.mp4",
        "caption_cues": [{"start": 0, "end": 1, "text": "hi"}], "mode": "long_form_reel",
        "status": "succeeded"})
    assert ready["can_recaption"] is False
    assert "cutaway" in ready["reason"]
    assert ready["broll_inserts"] == 1


def test_the_editor_is_given_the_words_not_just_a_count():
    """A band labelled "CAPTIONS HERE" moved nothing on screen when dragged —
    the burned-in words are pixels until the re-draw runs — and the owner read
    that as the feature not working. The editor needs the actual cues so it can
    show them where they are being put."""
    from james_os.api_v1 import _PREVIEW_CUES, _recaption_readiness

    cues = [{"start": i * 1.0, "end": i * 1.0 + 0.9,
             "text": f"WORD {i}", "raw_text": f"word {i}"} for i in range(40)]
    ready = _recaption_readiness({
        "scenes": [{"source_url": "https://x/s.mp4", "start_s": 0.0, "end_s": 15.0}],
        "clean_cut_url": "https://x/cut.mp4", "caption_cues": cues,
        "caption_hook": {"text": "A headline", "hold": 2.6},
        "mode": "long_form_reel", "status": "succeeded"})

    assert ready["can_recaption"] is True
    assert len(ready["cues"]) == 40
    # Mixed case, as a reader sees them — the ALL-CAPS in `text` is the flash
    # builder's per-word emphasis, not the words themselves.
    assert ready["cues"][0]["text"] == "word 0"
    assert ready["cues"][0]["start"] == 0.0
    assert ready["hook_hold"] == 2.6
    assert _PREVIEW_CUES >= 100


def test_an_absurd_cue_list_does_not_flood_the_editor():
    from james_os.api_v1 import _PREVIEW_CUES, _recaption_readiness

    many = [{"start": i, "end": i + 0.5, "text": f"w{i}"} for i in range(5000)]
    ready = _recaption_readiness({
        "scenes": [{"source_url": "https://x/s.mp4", "start_s": 0.0, "end_s": 15.0}],
        "clean_cut_url": "https://x/cut.mp4", "caption_cues": many,
        "mode": "long_form_reel", "status": "succeeded"})

    assert len(ready["cues"]) == _PREVIEW_CUES


def test_the_redraw_keeps_the_resolution_the_owner_already_had():
    """The cut we keep is the footage as it was CUT — 360x640 on one brand —
    while the assembly upscaled its output to 1080x1920. Burning onto the cut and
    stopping there handed back a third of the resolution. Caught by probing a
    real re-render, not by a test: v1 was 1080x1920 and v3 was 360x640."""
    args = cb.build_args("in.mp4", "out.mp4", "/tmp/c.ass", (1080, 1920))
    vf = args[args.index("-vf") + 1]
    assert "scale=1080:1920" in vf
    # Scale BEFORE the subtitles, or the words are drawn small and then enlarged.
    assert vf.index("scale=") < vf.index("ass=")
    assert "setsar=1" in vf


def test_no_scale_is_added_when_the_cut_is_already_the_right_frame():
    args = cb.build_args("in.mp4", "out.mp4", "/tmp/c.ass", None)
    assert "scale=" not in args[args.index("-vf") + 1]


def test_the_caption_track_is_authored_for_the_frame_it_is_shown_in():
    """A track written for 360x640 and then enlarged gives fuzzy words at the
    wrong size — the ASS has to be built at the OUTPUT resolution."""
    import inspect

    src = inspect.getsource(cb.render)
    assert "width, height = scale_to" in src
    assert src.index("width, height = scale_to") < src.index("build_ass(")


def test_the_endpoint_measures_the_finished_reel_for_that_frame():
    import inspect

    from james_os import api_v1

    src = inspect.getsource(api_v1._run_recaption)
    assert "target_size=target" in src
    assert 'final_url' in src
