"""Tests for hand-authored reel templates (pure — no DB, no providers).

The contract these pin down: a builder spec becomes the SAME template JSON the
Design Inspector emits, so everything downstream (map_template_to_render,
/replicate, the autopilot picker) works on an authored template unchanged; and
the builder can only ever express something the renderer can actually produce.
"""

import pytest

from james_os.caption_styles import CAPTION_PRESETS
from james_os.starter_templates import HOUSE_TEMPLATES, validate_house_library
from james_os.template_apply import _ALLOWED_MODES, _MUSIC_MOODS, map_template_to_render
from james_os.template_spec import (
    BEAT_MAX_SECONDS,
    LAYOUTS,
    MAX_BEATS,
    build_template,
    capabilities,
    preview_render,
    template_to_spec,
    validate_spec,
)


def _spec(**over) -> dict:
    base = {
        "name": "Talking head, bold subs",
        "summary": "Straight to camera with cutaways.",
        "layout": "full_frame",
        "production_mode": "engaging_avatar",
        "aspect": "9:16",
        "caption_preset": "magenta_blocks",
        "music": "upbeat",
        "format_type": "talking_head",
        "energy": "high",
    }
    base.update(over)
    return base


# ── the vocabulary is bounded by the renderer ────────────────────────

def test_capabilities_only_offers_renderable_choices():
    caps = capabilities()
    for layout in (c["value"] for c in caps["layouts"]):
        assert layout in LAYOUTS
    for mode in (c["value"] for c in caps["modes"]):
        assert mode in _ALLOWED_MODES
    for preset in (c["value"] for c in caps["caption_presets"]):
        assert preset == "" or preset in CAPTION_PRESETS
    for mood in (c["value"] for c in caps["music_moods"]):
        assert mood == "" or mood in _MUSIC_MOODS


def test_capabilities_excludes_split_modes_from_the_mode_picker():
    # A split layout IMPLIES its renderer; offering it as a separate mode is how
    # a template ends up claiming a full-frame mode for a stacked composition.
    modes = {c["value"] for c in capabilities()["modes"]}
    assert "split_horizontal" not in modes
    assert "split_vertical" not in modes


def test_capabilities_is_honest_about_where_beats_matter():
    assert capabilities()["beats_drive_render_in"] == ["mixed"]


# ── validation ───────────────────────────────────────────────────────

def test_valid_spec_has_no_errors():
    assert validate_spec(_spec()) == []


def test_name_is_required():
    assert any("name" in e for e in validate_spec(_spec(name="  ")))


def test_unrenderable_layout_is_refused():
    errs = validate_spec(_spec(layout="pip"))
    assert any("pip" in e for e in errs)


def test_unknown_caption_preset_is_refused():
    assert any("glitchcore" in e for e in validate_spec(_spec(caption_preset="glitchcore")))


def test_music_without_a_track_is_refused():
    assert any("dubstep" in e for e in validate_spec(_spec(music="dubstep")))


def test_every_problem_is_reported_not_just_the_first():
    errs = validate_spec({"name": "", "layout": "grid", "caption_preset": "nope",
                          "music": "nope", "energy": "nuclear"})
    assert len(errs) >= 5


def test_beat_outside_the_renderable_range_is_refused():
    errs = validate_spec(_spec(beats=[{"role": "b_roll", "seconds": 99}]))
    assert any(str(BEAT_MAX_SECONDS) in e for e in errs)


def test_too_many_beats_is_refused():
    beats = [{"role": "b_roll", "seconds": 3}] * (MAX_BEATS + 1)
    assert any("beats" in e for e in validate_spec(_spec(beats=beats)))


def test_build_raises_on_an_invalid_spec():
    with pytest.raises(ValueError) as e:
        build_template(_spec(caption_preset="glitchcore"))
    assert "glitchcore" in str(e.value)


# ── spec → the inspector's shape ─────────────────────────────────────

def test_authored_template_maps_to_real_render_params():
    m = map_template_to_render(build_template(_spec()))
    assert m["mode"] == "engaging_avatar"
    assert m["caption_style"] == "magenta_blocks"
    assert m["music_mood"] == "upbeat"
    assert m["aspect"] == "9:16"


def test_split_layout_overrides_the_authored_mode():
    # The author picked a full-frame mode; the layout must win, so the stored
    # template never claims full-frame for a stacked composition.
    t = build_template(_spec(layout="split_horizontal", production_mode="engaging_avatar"))
    assert t["production_mode"] == "split_horizontal"
    assert map_template_to_render(t)["mode"] == "split_horizontal"


def test_split_layout_derives_its_regions():
    t = build_template(_spec(layout="split_vertical"))
    positions = [r["position"] for r in t["layout"]["regions"]]
    assert positions == ["left", "right"]


def test_beats_become_segments_with_a_running_clock():
    t = build_template(_spec(beats=[
        {"role": "talking_head", "seconds": 3, "visual": "to camera"},
        {"role": "b_roll", "seconds": 4, "visual": "drone"},
    ]))
    segs = t["segments"]
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 3.0), (3.0, 7.0)]
    assert segs[0]["speaker"]["present"] is True     # talking_head speaks
    assert segs[1]["speaker"]["present"] is False    # b_roll does not


def test_mixed_mode_turns_beats_into_render_scenes():
    m = map_template_to_render(build_template(_spec(
        production_mode="mixed",
        beats=[{"role": "talking_head", "seconds": 4},
               {"role": "b_roll", "seconds": 4}],
    )))
    assert m["structure"] is not None
    assert len(m["structure"]) == 2


def test_authored_flag_travels_in_the_json():
    assert build_template(_spec())["authored"] is True


def test_caption_preset_lands_in_the_field_the_renderer_reads():
    # preset_guess is the inspector's GUESS field but the renderer's actual
    # input — an authored template writes the real key straight into it.
    assert build_template(_spec())["captions"]["preset_guess"] == "magenta_blocks"


def test_no_music_is_not_silently_upgraded_to_a_bed():
    m = map_template_to_render(build_template(_spec(music="")))
    assert m["music_mood"] == ""
    # No SUBSTITUTION note: "no bed" must stay no bed, not become 'upbeat'.
    # (The generic cut-rhythm approximation mentions music and is expected.)
    assert not any("substitut" in a.lower() for a in m["approximations"])


# ── round-trip: an inspected template can be opened in the builder ───

def test_round_trip_is_stable():
    spec = _spec(
        layout="split_horizontal",
        beats=[{"role": "talking_head", "seconds": 3, "visual": "to camera"},
               {"role": "b_roll", "seconds": 5, "visual": "site"}],
        distinctive_features=["speaker top half"],
        hook="Open on the number.",
    )
    first = build_template(spec)
    second = build_template(template_to_spec(first))
    assert second["production_mode"] == first["production_mode"]
    assert second["layout"]["type"] == first["layout"]["type"]
    assert second["captions"]["preset_guess"] == first["captions"]["preset_guess"]
    assert second["audio"]["music"]["type"] == first["audio"]["music"]["type"]
    assert len(second["segments"]) == len(first["segments"])
    assert second["hook"] == first["hook"]


def test_inspector_enums_normalise_when_opened_in_the_builder():
    # The inspector says 'minimal'/'trending'; the renderer knows
    # 'subtle_minimal'/'upbeat'. Opening an inspected template in the builder
    # must not silently drop either.
    inspected = {
        "style_name": "From a reference reel",
        "layout": {"type": "full_frame"},
        "captions": {"preset_guess": "minimal"},
        "audio": {"music": {"type": "trending"}},
        "production_mode": "engaging_avatar",
        "aspect_ratio": "9:16",
    }
    spec = template_to_spec(inspected)
    assert spec["caption_preset"] == "subtle_minimal"
    assert spec["music"] == "upbeat"
    assert validate_spec(spec) == []


def test_unsupported_inspected_layout_falls_back_rather_than_failing_validation():
    # A pip/grid reference can still be opened in the builder — it lands on a
    # layout the renderer can build, instead of an unsavable form.
    spec = template_to_spec({"style_name": "Pip ref", "layout": {"type": "pip"}})
    assert spec["layout"] in LAYOUTS
    assert validate_spec(spec) == []


# ── preview ──────────────────────────────────────────────────────────

def test_preview_reports_what_will_actually_render():
    pv = preview_render(_spec(layout="split_horizontal"))
    assert pv["applied"]["mode"] == "split_horizontal"
    assert pv["applied"]["caption_style"] == "magenta_blocks"
    assert pv["approximations"]          # a split is never pixel-cloned
    assert pv["beats_drive_render"] is False


def test_preview_says_when_beats_drive_the_render():
    pv = preview_render(_spec(production_mode="mixed",
                              beats=[{"role": "b_roll", "seconds": 4}]))
    assert pv["beats_drive_render"] is True
    assert pv["applied"]["scenes"] == 1


# ── the house starter library ────────────────────────────────────────

def test_every_house_template_is_renderable():
    # Guards the starter set against a retired caption preset or dropped mode:
    # it fails here rather than at seed time in production.
    assert validate_house_library() == []


def test_house_templates_map_to_distinct_render_shapes():
    shapes = set()
    for spec in HOUSE_TEMPLATES:
        s = {k: v for k, v in spec.items() if k not in ("tags", "trending_score")}
        m = map_template_to_render(build_template(s))
        shapes.add((m["mode"], m["caption_style"]))
    # A starter library whose entries all render identically is a library of one.
    assert len(shapes) == len(HOUSE_TEMPLATES)


def test_house_templates_have_the_narrative_a_brand_needs():
    for spec in HOUSE_TEMPLATES:
        assert spec.get("summary"), f"{spec['name']} has no summary"
        assert spec.get("hook"), f"{spec['name']} has no hook"
        assert spec.get("replication_recipe"), f"{spec['name']} has no recipe"
