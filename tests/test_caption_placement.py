"""Where the captions sit — and whether they are drawn at all.

Until this, `caption_y_for_role` deliberately ignored every preset's own
`y_position` and hard-returned "78%". That was the right fix for the bug it was
written for (captions riding the speaker's face) and the wrong answer for an
owner who looks at a finished reel and wants the words somewhere else: there was
no parameter, column or control anywhere that moved a caption.

These pin the new contract: automatic placement is unchanged, the owner can
override it, and the override is still clamped to the band where a caption is
readable rather than covered by the platform's own UI.
"""

import pytest

from james_os.assembly import CreatomateAssemblyProvider
from james_os.caption_styles import (
    CAPTION_Y_MAX,
    CAPTION_Y_MIN,
    SAFE_ZONES,
    caption_element,
    caption_y_for_role,
    clamp_caption_y,
    get_preset,
)

pytestmark = pytest.mark.nodb

CAPTIONS = [
    {"start": 0.0, "end": 1.2, "text": "CAN JACKSON", "raw_text": "Can Jackson"},
    {"start": 1.2, "end": 2.4, "text": "BIRDIE THE", "raw_text": "birdie the"},
    {"start": 2.4, "end": 3.6, "text": "COURSE RECORD", "raw_text": "course record"},
]


def _reel(**kw):
    return CreatomateAssemblyProvider(api_key="test").build_engaging_avatar_source(
        avatar_video_url="https://example.com/cut.mp4",
        audio_duration=12.2, inserts=[], captions=list(CAPTIONS),
        aspect="9:16", caption_style="bold_pop", **kw)


def _caption_texts(source: dict) -> list[dict]:
    return [e for e in source["elements"]
            if e.get("type") == "text" and e.get("track") == 3]


# --- the automatic placement is unchanged -----------------------------------

def test_with_no_instruction_captions_still_sit_off_the_face():
    """The bug this hard-coding fixed must stay fixed: say nothing, and every
    role still lands in the lower third, not on the speaker."""
    preset = get_preset("bold_pop")
    for role in ("avatar", "broll", "default", "unknown-role"):
        assert caption_y_for_role(preset, role) == "78%"


def test_a_high_preset_still_cannot_put_itself_on_the_face():
    """A preset's own y_position is still ignored — only the OWNER may move
    captions, because a preset moving them was the original face bug."""
    preset = dict(get_preset("bold_pop"), y_position="20%")
    assert caption_y_for_role(preset, "avatar") == "78%"


# --- the owner can move them ------------------------------------------------

def test_the_owner_can_move_the_captions():
    preset = get_preset("bold_pop")
    assert caption_y_for_role(preset, "avatar", y_override=45) == "45.0%"
    assert caption_y_for_role(preset, "avatar", y_override="62%") == "62.0%"
    assert caption_y_for_role(preset, "broll", y_override="30") == "30.0%"


def test_a_moved_caption_is_still_kept_out_of_the_platforms_own_chrome():
    """Clamped, not obeyed literally: a caption at 95% is under the TikTok UI
    and a caption at 2% is under the status bar. Either is a caption the viewer
    never reads, so the band holds even when the owner asks past it."""
    preset = get_preset("bold_pop")
    assert caption_y_for_role(preset, "avatar", y_override=95) == f"{CAPTION_Y_MAX}%"
    assert caption_y_for_role(preset, "avatar", y_override=2) == f"{CAPTION_Y_MIN}%"
    assert CAPTION_Y_MIN < 78.0 < CAPTION_Y_MAX  # today's default is inside it


def test_junk_falls_back_to_automatic_rather_than_off_frame():
    preset = get_preset("bold_pop")
    for junk in ("", "abc", "%", None, float("nan"), float("inf")):
        assert caption_y_for_role(preset, "avatar", y_override=junk) == "78%"
    assert clamp_caption_y(None) is None


# --- it reaches the rendered element ----------------------------------------

def test_the_position_reaches_the_caption_element():
    preset = get_preset("bold_pop")
    auto = caption_element(text="HELLO", start=0.0, end=1.0, preset=preset, role="avatar")
    moved = caption_element(text="HELLO", start=0.0, end=1.0, preset=preset, role="avatar",
                            y_override="40%")
    assert auto["y"] == "78%"
    assert moved["y"] == "40.0%"
    # Only the position changes — the look is the preset's business.
    assert moved["fill_color"] == auto["fill_color"]
    assert moved["font_family"] == auto["font_family"]


def test_every_caption_in_a_reel_moves_together():
    src = _reel(caption_y="35%")
    texts = _caption_texts(src)
    assert len(texts) == len(CAPTIONS)
    assert {e["y"] for e in texts} == {"35.0%"}


def test_a_reel_that_says_nothing_renders_exactly_where_it_used_to():
    assert {e["y"] for e in _caption_texts(_reel())} == {"78%"}


# --- captions off -----------------------------------------------------------

def test_the_owner_can_turn_captions_off_entirely():
    """A blank caption_style means 'pick one for me', so turning captions OFF
    needed its own switch — there was previously no way to ask for a reel with
    no words burned onto it."""
    src = _reel(captions_off=True)
    assert _caption_texts(src) == []


def test_captions_off_leaves_the_rest_of_the_reel_alone():
    """Off means no caption track, not a different video."""
    off = _reel(captions_off=True)
    on = _reel()
    non_caption = lambda s: [e for e in s["elements"]
                             if not (e.get("type") == "text" and e.get("track") == 3)]
    assert non_caption(off) == non_caption(on)
    assert off["width"] == on["width"] and off["height"] == on["height"]


def test_captions_off_also_silences_the_designer_styles():
    """Four presets bypass caption_element and emit their own element tree;
    'off' has to beat those too or it only works for some styles."""
    src = CreatomateAssemblyProvider(api_key="test").build_engaging_avatar_source(
        avatar_video_url="https://example.com/cut.mp4", audio_duration=12.2,
        inserts=[], captions=list(CAPTIONS), aspect="9:16",
        caption_style="magenta_blocks", captions_off=True)
    assert _caption_texts(src) == []


def test_the_safe_zone_table_is_still_what_the_automatic_path_reads():
    assert SAFE_ZONES["avatar"][0][0] == "78%"
