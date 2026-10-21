"""A brand with no photos still gets a fitting look — from its words.

POST /v1/brand/theme/propose used to read only hero photos. With none it had
nothing to say. Now the brand's stored name, niche and voice drive a
deterministic palette + font suggestion (no model, no network), and with photos
the same words go into the single existing vision call and fill what it leaves
out (the font key, a missing palette).
"""

from __future__ import annotations

import re

import pytest

from james_os import brand_identity as bi
from james_os import font_themes

pytestmark = pytest.mark.nodb

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
ROLES = {"background", "ink", "accent", "surface"}


def _well_formed(theme: dict) -> None:
    pal = theme["palette"]
    assert {p["role"] for p in pal} == ROLES
    assert all(HEX.match(p["hex"]) and p["label"] for p in pal)
    assert theme["font_theme"] in font_themes.FONT_THEMES
    assert font_themes.normalize(theme["font_theme"]) == theme["font_theme"]


def test_a_golf_resort_reads_as_quiet_luxury():
    t = bi.suggest_theme_from_context("Turtleback", "golf resort", "")
    _well_formed(t)
    assert t["font_theme"] == "elegant" and t["matched"] == "niche"
    assert any(p["role"] == "accent" and p["label"] == "Gold" for p in t["palette"])


def test_a_gym_reads_as_high_energy_in_a_condensed_face():
    t = bi.suggest_theme_from_context("", "crossfit gym", "")
    _well_formed(t)
    assert t["font_theme"] == "condensed"


def test_every_rule_yields_an_applyable_palette():
    for look in bi._WORD_LOOKS:
        t = bi.suggest_theme_from_context("", look["words"][0], "")
        _well_formed(t)
        assert t["font_theme"] == look["font_theme"]


def test_the_niche_wins_over_the_voice_which_wins_over_the_name():
    assert bi.suggest_theme_from_context("Bold Gym", "law firm", "warm and playful")["font_theme"] == "clean"
    assert bi.suggest_theme_from_context("Bold Gym", "", "warm and playful")["font_theme"] == "friendly"
    assert bi.suggest_theme_from_context("Bold Gym", "", "")["font_theme"] == "condensed"


def test_nothing_known_falls_back_to_the_neutral_house_look():
    t = bi.suggest_theme_from_context("", "", "")
    _well_formed(t)
    assert t["font_theme"] == "bold" and t["matched"] == ""
    assert t["palette"] == bi._NEUTRAL_PALETTE
    assert bi.suggest_theme_from_context("Acme", "widgets", "ok") == t | {"matched": ""}


def test_it_is_deterministic():
    a = bi.suggest_theme_from_context("A", "travel agency", "warm")
    assert a == bi.suggest_theme_from_context("A", "travel agency", "warm")


# ── filling a propose result ──────────────────────────────────────────────

CTX = {"name": "Turtleback", "niche": "golf resort", "voice": ""}


def test_no_photos_returns_the_word_derived_theme_marked_niche():
    res = bi.fill_theme_from_context({"status": "ok", "observed_palettes": []}, CTX, has_photos=False)
    assert res["source"] == "niche" and res["context_matched"] == "niche"
    assert res["proposed_theme"]["font_theme"] == "elegant" == res["font_theme"]
    assert res["verdict"] == "rebuild" and res["has_branding"] is False
    _well_formed(res["proposed_theme"])


def test_no_photos_never_reports_no_key_for_a_deterministic_answer():
    res = bi.fill_theme_from_context({"status": "no_key", "observed_palettes": []}, CTX, has_photos=False)
    assert res["status"] == "ok" and res["source"] == "niche"


def test_with_photos_the_vision_palette_stays_primary_and_only_the_font_is_filled():
    vision = {"status": "ok", "verdict": "tune", "proposed_theme": {
        "name": "Seen", "palette": [{"role": "background", "hex": "#000000", "label": "x"}],
        "type_direction": "whatever", "principles": ["p"]}}
    res = bi.fill_theme_from_context(vision, CTX, has_photos=True)
    assert res["source"] == "photos" and res["verdict"] == "tune"
    assert res["proposed_theme"]["palette"] == vision["proposed_theme"]["palette"]
    assert res["proposed_theme"]["name"] == "Seen"
    assert res["proposed_theme"]["font_theme"] == "elegant"


def test_with_photos_but_no_palette_from_vision_the_words_fill_it():
    vision = {"status": "failed", "observed_palettes": [[]], "error": "boom"}
    res = bi.fill_theme_from_context(vision, CTX, has_photos=True)
    assert res["source"] == "photos+niche" and res["status"] == "failed"
    _well_formed(res["proposed_theme"])
    assert res["error"] == "boom"


# ── reading the brand's words ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brand_context_reads_kit_profile_and_voice(monkeypatch):
    from james_os import autopilot, brand_kit, brands

    async def kit(tenant_id=None):
        return {"display_name": "Turtleback Golf", "tagline": "Refined days out"}

    async def profile(tenant_id=None):
        return {"identity": {"niche": "golf resort", "niche_terms": ["country club"],
                             "positioning": "the quiet course"}}

    async def voice(tenant_id=None):
        return "We speak softly and carry a 9-iron."

    monkeypatch.setattr(brand_kit, "get_brand_kit", kit)
    monkeypatch.setattr(brands, "get_brand_profile", profile)
    monkeypatch.setattr(autopilot, "_voice_for_ideation", voice)
    ctx = await bi.brand_context("t")
    assert ctx["name"] == "Turtleback Golf"
    assert ctx["niche"] == "golf resort country club"
    assert ctx["positioning"] == "the quiet course"
    assert "Refined days out" in ctx["voice"] and "9-iron" in ctx["voice"]


@pytest.mark.asyncio
async def test_brand_context_survives_every_read_failing(monkeypatch):
    from james_os import autopilot, brand_kit, brands

    async def boom(tenant_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(brand_kit, "get_brand_kit", boom)
    monkeypatch.setattr(brands, "get_brand_profile", boom)
    monkeypatch.setattr(autopilot, "_voice_for_ideation", boom)
    assert await bi.brand_context("t") == {"name": "", "niche": "", "voice": "", "positioning": ""}
