"""Final touches on a finished video, instead of paying for another one.

Seven of the last eight renders on James's tenant were REJECTED — each one a
fresh credit for a video that came back different rather than fixed. These pin
the edit that replaces that loop: trim, words, logo, reframe, cover.
"""

import asyncio
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from james_os import video_edit as ve

pytestmark = pytest.mark.nodb

FONTS = {"display": "/fonts/Anton-Regular.ttf", "body": "/fonts/Archivo.ttf"}


def _args(doc, **kw):
    kw.setdefault("src_w", 1920)
    kw.setdefault("src_h", 1080)
    kw.setdefault("fonts", FONTS)
    kw.setdefault("text_files", [])
    kw.setdefault("logo_path", None)
    return ve.build_args(doc, "in.mp4", "out.mp4", **kw)


def test_a_reframe_takes_a_slice_of_the_picture_rather_than_squashing_it():
    """A 16:9 cut posted as a reel must not become a squashed 9:16 — the frame
    keeps its proportions and the sides are left out."""
    args = _args({"frame": {"aspect": "9:16"}})
    graph = args[args.index("-filter_complex") + 1]
    # 1080 tall source, 9:16 → a 607-wide centred slice, then scaled to 1080x1920
    assert "crop=606:1080:657:0" in graph or "crop=608:1080:656:0" in graph, graph
    assert "scale=1080:1920" in graph
    assert "setsar=1" in graph


def test_the_owner_can_say_which_part_of_the_frame_to_keep():
    """"Keep him, lose the empty wall" — an explicit crop wins over the centre."""
    graph = _args({"frame": {"aspect": "9:16", "crop": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}}})
    g = graph[graph.index("-filter_complex") + 1]
    assert "crop=960:1080:0:0" in g, g


def test_trimming_cuts_the_dead_air_off_the_front_and_the_tail():
    args = _args({"trim": {"start": 2.5, "end": 12.0}})
    assert args[args.index("-ss") + 1] == "2.500"
    assert args[args.index("-t") + 1] == "9.500", "duration, not an end stamp"
    assert args.index("-ss") < args.index("-i"), "seek before the input, or it decodes the lot"


def test_the_words_come_from_a_file_so_the_owner_s_own_punctuation_survives():
    """A colon, an apostrophe or a percent sign in a headline is ordinary copy and
    a filter-graph metacharacter. textfile= sidesteps every one of them."""
    doc = {"texts": [{"text": "50% off: it's real", "x": 0.5, "y": 0.8, "size": 0.06}]}
    args = _args(doc, text_files=["/tmp/t0.txt"])
    g = args[args.index("-filter_complex") + 1]
    assert "textfile='/tmp/t0.txt'" in g
    assert "50%" not in g, "the copy itself must never reach the graph"
    assert "fontfile='/fonts/Anton-Regular.ttf'" in g
    # drawtext expands %-sequences even from a file: "50% off" drew mangled text
    # and logged "Stray %" until this was set. Found on the deployed ffmpeg.
    assert "expansion=none" in g


def test_text_is_sized_and_placed_against_the_OUTPUT_frame():
    """The doc is fractions, so a layout made on a small preview lands in the same
    place at 1080x1920 — the card editor's lesson, applied to video."""
    doc = {"frame": {"aspect": "9:16"},
           "texts": [{"text": "hi", "x": 0.5, "y": 0.75, "size": 0.05, "align": "center"}]}
    args = _args(doc, text_files=["/tmp/t.txt"])
    g = args[args.index("-filter_complex") + 1]
    assert "fontsize=96" in g, "5% of 1920"
    assert "x=540-text_w/2" in g and "y=1440-text_h/2" in g


def test_a_caption_can_appear_for_part_of_the_video_only():
    doc = {"texts": [{"text": "hook", "start": 0.0, "end": 3.0}]}
    args = _args(doc, text_files=["/tmp/t.txt"])
    graph = args[args.index("-filter_complex") + 1]
    assert "enable='between(t\\,0.000\\,3.000)'" in graph, graph


def test_the_logo_rides_on_top_at_its_own_proportions():
    doc = {"frame": {"aspect": "9:16"}, "logo": {"x": 0.7, "y": 0.05, "w": 0.2}}
    args = _args(doc, logo_path="/tmp/logo.png")
    assert args.count("-i") == 2, "the logo is a second input"
    g = args[args.index("-filter_complex") + 1]
    assert "[1:v]scale=216:-1[lg]" in g, "width fixed, height follows — never stretched"
    assert "overlay=756:96" in g


def test_a_silent_cut_still_renders():
    """Some clips carry no audio track at all; mapping it unconditionally failed
    the whole render on a stream that wasn't there."""
    args = _args({})
    assert "0:a?" in args


def test_muting_drops_the_audio_rather_than_silencing_it():
    args = _args({"audio": {"mute": True}})
    assert "-an" in args and "0:a?" not in args


def test_a_junk_doc_cannot_make_a_junk_render():
    d = ve.normalize({"trim": {"start": -5, "end": "soon"}, "frame": {"aspect": "17:3"},
                      "texts": [{"text": ""}, {"text": "ok", "size": 99, "color": "red; rm -rf"}],
                      "logo": {"w": 40}})
    assert d["trim"]["start"] == 0.0
    assert d["frame"]["aspect"] == "source"
    assert [t["text"] for t in d["texts"]] == ["ok"], "empty copy draws nothing"
    assert d["texts"][0]["size"] <= 0.30 and d["texts"][0]["color"] == "#FFFFFF"
    assert d["logo"]["w"] <= 0.6


def test_an_untouched_video_is_recognised_as_untouched():
    """A re-encode that changes nothing still costs quality and minutes."""
    assert ve.is_noop({}) is True
    assert ve.is_noop({"trim": {"start": 0.4}}) is False
    assert ve.is_noop({"texts": [{"text": "hi"}]}) is False


def test_source_keeps_the_video_s_own_shape_and_never_an_odd_size():
    """h264 refuses odd dimensions — a 1281-wide source must not fail the render."""
    assert ve.out_size({}, 1281, 721) == (1280, 720)


def _has_filter(name: str) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return f" {name} " in out.stdout


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on this machine")
def test_it_really_renders_a_trim_a_reframe_and_a_cover(tmp_path):
    """No text here, so this runs on any ffmpeg — the graph is only right if
    ffmpeg accepts it."""
    src = str(tmp_path / "plain.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=24:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
        check=True)
    import asyncio
    out = asyncio.run(ve.render(
        src, {"trim": {"start": 0.5, "end": 2.5}, "frame": {"aspect": "9:16"}, "cover": {"t": 0.5}},
        fonts=FONTS, logo_path=None, work_dir=str(tmp_path)))
    assert out["ok"] is True, out
    assert (out["width"], out["height"]) == (1080, 1920)
    assert 1.9 <= out["duration"] <= 2.1
    assert out["cover_path"]
    info = asyncio.run(ve.probe(out["path"]))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["has_audio"] is True


@pytest.mark.skipif(not _has_filter("drawtext"),
                    reason="this ffmpeg has no drawtext (the deployed image does)")
def test_it_really_renders(tmp_path):
    """The graph is only right if ffmpeg accepts it. Builds a 3-second clip, then
    trims, reframes, captions and covers it for real."""
    src = str(tmp_path / "src.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=24:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
        check=True)

    import asyncio
    doc = {"trim": {"start": 0.5, "end": 2.5}, "frame": {"aspect": "9:16"},
           "texts": [{"text": "It's 50% — really", "x": 0.5, "y": 0.8, "size": 0.05,
                      "box": True, "start": 0, "end": 1.5}],
           "cover": {"t": 0.5}}
    out = asyncio.run(ve.render(
        src, doc,
        fonts={"display": "src/james_os/assets/fonts/Anton-Regular.ttf",
               "body": "src/james_os/assets/fonts/ArchivoBlack-Regular.ttf"},
        logo_path=None, work_dir=str(tmp_path)))

    assert out["ok"] is True, out
    assert out["width"] == 1080 and out["height"] == 1920
    assert 1.9 <= out["duration"] <= 2.1
    assert out["cover_path"] and tmp_path.joinpath("cover.jpg").exists()
    info = asyncio.run(ve.probe(out["path"]))
    assert info["width"] == 1080 and info["height"] == 1920
    assert 1.8 <= info["duration"] <= 2.2, info
    assert info["has_audio"] is True


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on this machine")
def test_a_video_too_short_to_cut_is_refused_not_rendered(tmp_path):
    src = str(tmp_path / "s.mp4")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=320x240:rate=12:duration=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", src], check=True)
    import asyncio
    out = asyncio.run(ve.render(src, {"trim": {"start": 1.0, "end": 1.2}},
                                fonts=FONTS, logo_path=None, work_dir=str(tmp_path)))
    assert out["ok"] is False and "0.5" in out["reason"]


# ------------------------------------------------- the door the editor knocks on


def test_only_a_public_https_source_is_fetched():
    """The editor hands the engine a URL and the engine downloads it. Unguarded,
    that is an SSRF hole: a crafted url would have the server fetch its own
    metadata service or something behind the network boundary."""
    import asyncio

    from james_os.api_v1 import _fetchable

    for bad in ("http://cdn.test/a.mp4",            # not https
                "https://127.0.0.1/a.mp4",          # loopback
                "https://10.0.0.5/a.mp4",           # private
                "https://169.254.169.254/latest",   # the metadata service
                "https:///a.mp4",                   # no host
                "not a url"):
        assert asyncio.run(_fetchable(bad)) is False, bad


def test_an_edit_that_changes_nothing_is_refused_before_it_costs_anything():
    """is_noop is what the endpoint checks — a re-encode that changes nothing
    still costs quality and minutes."""
    assert ve.is_noop({"version": 1, "trim": {"start": 0, "end": None}}) is True
    assert ve.is_noop({"frame": {"aspect": "9:16"}}) is False, "a reframe IS a change"
    assert ve.is_noop({"logo": {"x": 0.5}}) is False


# --- the job the editor hands back must be pollable -------------------------

def test_an_edit_job_carries_what_the_poller_reads_back():
    """`GET /v1/jobs/{id}` dereferences job["tenant_id"] and job["type"] directly.
    A job missing either is not an odd-looking job — it is a 500 on every poll,
    so BM2 could start an edit and never be able to collect it."""
    from james_os.api_v1 import _new_job

    job = _new_job("video_edit", "6febd4bb-f062-4f53-936b-802d648fc060")
    for key in ("id", "type", "tenant_id", "status", "result", "error",
                "created_at", "updated_at"):
        assert key in job, f"the poller reads {key!r}"
    assert job["type"] == "video_edit"
    assert job["tenant_id"] == "6febd4bb-f062-4f53-936b-802d648fc060"


def test_the_poller_refuses_another_tenants_edit_job():
    """The tenant on the job is the whole guard: without it every tenant could
    poll every other tenant's render. This runs the poller's own check."""
    from uuid import UUID

    from james_os.api_v1 import _JOBS, _new_job, _put_job, v1_job

    mine = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    theirs = UUID("bbbbbbbb-0000-0000-0000-000000000002")
    job = _new_job("video_edit", mine)
    _put_job(job)
    try:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(v1_job(job["id"], theirs))
        assert caught.value.status_code == 404

        # ...and the owner gets it back, with the fields the poller reads.
        out = asyncio.run(v1_job(job["id"], mine))
        assert out["job_id"] == job["id"]
        assert out["type"] == "video_edit"
        assert out["status"] == "running"
    finally:
        _JOBS.pop(job["id"], None)
