"""Tests for the Phase-2 template→render mapper (pure, no providers/DB)."""

from james_os.template_apply import apply_overrides_to_scenes, map_template_to_render


def test_engaging_avatar_tiktok_trending():
    tpl = {
        "production_mode": "engaging_avatar",
        "captions": {"preset_guess": "tiktok_yellow"},
        "audio": {"music": {"type": "trending"}},
        "aspect_ratio": "9:16",
        "logo": {"present": True, "position": "bottom-right"},
    }
    m = map_template_to_render(tpl)
    assert m["mode"] == "engaging_avatar"
    assert m["caption_style"] == "tiktok_yellow"
    assert m["music_mood"] == "upbeat"  # trending → upbeat substitution
    assert m["aspect"] == "9:16"
    assert m["structure"] is None  # engaging_avatar drives itself
    assert any("trending" in a.lower() for a in m["approximations"])


def test_caption_alias_minimal_to_subtle():
    m = map_template_to_render(
        {"production_mode": "engaging_avatar", "captions": {"preset_guess": "minimal"}}
    )
    assert m["caption_style"] == "subtle_minimal"


def test_unknown_caption_falls_back_to_auto():
    m = map_template_to_render({"captions": {"preset_guess": "glitchcore"}})
    assert m["caption_style"] == ""  # auto-pick, never an invalid preset key


def test_invalid_mode_defaults_to_engaging_avatar():
    m = map_template_to_render({"production_mode": "tiktok_dance"})
    assert m["mode"] == "engaging_avatar"


def test_music_none_is_empty_no_substitution_note():
    m = map_template_to_render({"audio": {"music": {"type": "none"}}})
    assert m["music_mood"] == ""
    # no substitution/trending note when there's simply no music
    assert not any(
        ("substitut" in a.lower() or "trending" in a.lower()) for a in m["approximations"]
    )


def test_mixed_segments_become_clamped_structure():
    segs = [
        {"role": "talking_head", "start": 0, "end": 3},
        {"role": "b_roll", "start": 3, "end": 5},
        {"role": "text_card", "start": 5, "end": 7},
    ]
    m = map_template_to_render(
        {"production_mode": "mixed", "segments": segs, "aspect_ratio": "1:1"}
    )
    assert m["aspect"] == "1:1"
    assert isinstance(m["structure"], list) and len(m["structure"]) == 3
    assert m["structure"][0]["kind"] == "talking_head"
    assert m["structure"][0]["source"] == "avatar"
    assert m["structure"][1]["kind"] == "broll" and m["structure"][1]["source"] is None
    assert all(s["duration"] >= 2 for s in m["structure"])  # clamped shootable


def test_mixed_caps_at_six_scenes():
    segs = [{"role": "b_roll", "start": i, "end": i + 1} for i in range(10)]
    m = map_template_to_render({"production_mode": "mixed", "segments": segs})
    assert len(m["structure"]) == 6


def test_apply_overrides_stamps_music_and_logo():
    scenes = [
        {"audio_music": "calm", "branding_logo": False},
        {"audio_music": "none"},
    ]
    apply_overrides_to_scenes(
        scenes, music_mood="upbeat", logo_on=True, logo_position="top-right"
    )
    assert all(s["audio_music"] == "upbeat" for s in scenes)
    assert all(s["branding_logo"] and s["branding_position"] == "top-right" for s in scenes)


def test_apply_overrides_noop_when_empty():
    scenes = [{"audio_music": "calm", "branding_logo": True, "branding_position": "x"}]
    apply_overrides_to_scenes(scenes)  # no music, no logo
    assert scenes[0]["audio_music"] == "calm"  # untouched
