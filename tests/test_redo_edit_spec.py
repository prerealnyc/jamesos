"""Editing a card instead of re-authoring it.

Every redo used to re-run the art director at temperature 0.7, so "keep
everything the same, just change X" came back with every line of on-image copy
rewritten. Same layout, same photo, and still not the owner's card. These pin
the contract of the edit path: one thing changes, everything else is returned
byte-identical, and a failed edit returns the card unchanged rather than handing
the job back to a fresh composition.
"""

import pytest

from james_os import imagegen, template_clone

pytestmark = pytest.mark.nodb

BASE = {
    "format": "bold_statement",
    "statement": "Multifamily isn't sexy. But nothing beats steady, monthly cash flow.",
    "emphasis": "steady, monthly cash flow",
    "quote": "", "top_text": "", "bottom_text": "", "headline": "", "kicker": "",
    "stat": "14.49%", "stat_label": "ANNUAL YIELD", "stat_sub": "", "caption": "",
    "bg_prompt": "flat brand ground", "bg_kind": "none",
}


class _StubLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def complete_json(self, system=None, messages=None, **kw):
        self.calls.append({"system": system, "messages": messages, **kw})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _stub(monkeypatch, reply):
    llm = _StubLLM(reply)
    monkeypatch.setattr("james_os.llm.get_llm", lambda *a, **k: llm)
    return llm


async def test_an_untouched_line_comes_back_byte_identical(monkeypatch):
    """The editor returning only what it changed must not blank the rest."""
    _stub(monkeypatch, {"statement": "Multifamily is boring. That is the point."})
    out = await imagegen.edit_designed_spec(BASE, "make the first line blunter")
    assert out["statement"] == "Multifamily is boring. That is the point."
    for k in ("emphasis", "stat", "stat_label", "bg_prompt", "bg_kind"):
        assert out[k] == BASE[k], f"{k} was not preserved"


async def test_the_layout_is_never_editable(monkeypatch):
    """A redo that keeps the card must keep the card — an editor that decides to
    change format has escaped the one thing this path exists to hold still."""
    _stub(monkeypatch, {"format": "full_bleed", "statement": "New words"})
    out = await imagegen.edit_designed_spec(BASE, "change it")
    assert out["format"] == "bold_statement"


async def test_an_invented_key_is_dropped(monkeypatch):
    _stub(monkeypatch, {"statement": "New words", "handle": "@someone", "colour": "red"})
    out = await imagegen.edit_designed_spec(BASE, "add my handle")
    assert "handle" not in out and "colour" not in out
    assert set(out) == set(BASE)


async def test_a_failed_edit_returns_the_card_unchanged(monkeypatch):
    """Degrading to a fresh composition is precisely the failure this replaces."""
    _stub(monkeypatch, RuntimeError("model down"))
    assert await imagegen.edit_designed_spec(BASE, "make it blunter") == BASE
    _stub(monkeypatch, {})
    assert await imagegen.edit_designed_spec(BASE, "make it blunter") == BASE


async def test_no_feedback_is_not_an_edit(monkeypatch):
    llm = _stub(monkeypatch, {"statement": "should never be asked for"})
    assert await imagegen.edit_designed_spec(BASE, "   ") == BASE
    assert llm.calls == []


async def test_the_edit_prompt_carries_the_card_and_the_words(monkeypatch):
    llm = _stub(monkeypatch, {"statement": "x"})
    await imagegen.edit_designed_spec(BASE, "make the first line blunter")
    sent = llm.calls[0]["messages"][0]["content"]
    assert "14.49%" in sent                       # the card as it is
    assert "make the first line blunter" in sent  # and what to change
    assert llm.calls[0]["temperature"] == 0.0     # an edit is not a creative act


async def test_a_cloned_card_edits_its_copy_the_same_way(monkeypatch):
    """The cloned path has its own roles, but the same contract."""
    _stub(monkeypatch, {"headline": "A blunter headline"})
    out = await template_clone._edit_clone_copy(
        {"headline": "Strong Numbers, Real Results", "kicker": "2024 OUTLOOK"},
        "make the headline blunter", None)
    assert out["headline"] == "A blunter headline"
    assert out["kicker"] == "2024 OUTLOOK"        # untouched role survives


# ---------------------------------------------------- the cloned rebuild path


async def test_the_handle_goes_in_the_byline_not_through_the_model(monkeypatch):
    """"Add my @" is a brand fact, not a copy edit. The nine designed layouts
    draw it from the brand kit; a cloned template renders through a machine with
    no handle concept, so the same request quietly did nothing here."""
    monkeypatch.setattr("james_os.brand_kit.get_brand_kit",
                        lambda *a, **k: _async({"handle": "@j_prendamano"}))
    out = await template_clone._apply_handle_request(
        {"headline": "It's Not a Blowout.", "byline": "PreReal"},
        "just add @j_prendamano as a text to show his username somewhere",
        ["headline", "byline"], None)
    assert "@j_prendamano" in out["byline"]
    assert "PreReal" in out["byline"]              # what was there is kept
    assert out["headline"] == "It's Not a Blowout."  # nothing else moves


async def test_the_handle_is_not_added_twice(monkeypatch):
    monkeypatch.setattr("james_os.brand_kit.get_brand_kit",
                        lambda *a, **k: _async({"handle": "@j_prendamano"}))
    out = await template_clone._apply_handle_request(
        {"byline": "PreReal · @j_prendamano"}, "add my handle", ["byline"], None)
    assert out["byline"].count("@j_prendamano") == 1


async def test_no_byline_role_means_no_silent_change(monkeypatch):
    """A template with nowhere to put it must not have text forced somewhere else."""
    monkeypatch.setattr("james_os.brand_kit.get_brand_kit",
                        lambda *a, **k: _async({"handle": "@j_prendamano"}))
    before = {"headline": "It's Not a Blowout."}
    assert await template_clone._apply_handle_request(
        before, "add my handle", ["headline"], None) == before


async def test_unrelated_feedback_leaves_the_byline_alone(monkeypatch):
    monkeypatch.setattr("james_os.brand_kit.get_brand_kit",
                        lambda *a, **k: _async({"handle": "@j_prendamano"}))
    before = {"byline": "PreReal"}
    assert await template_clone._apply_handle_request(
        before, "make the headline blunter", ["byline"], None) == before


def _async(value):
    async def _f():
        return value
    return _f()
