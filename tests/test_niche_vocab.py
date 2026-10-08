"""The closed niche vocabulary, and the cheap read that tags an untagged image.

Free-text niches on both sides were the root problem: 'commercial spaceport'
matched 'commercial real estate' on a filler word, and 313 of 324 approved
uploads (2026-10-08) carried no tag at all. These pin the mapping for every
brand niche live in production today, the read's contract (one detail-"low"
gpt-4o call, labels filtered to the vocabulary, at most 3, metered), and the
matcher built on top.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from james_os import house_layouts as hl
from james_os import niche_vocab as nv
from james_os import spend

pytestmark = pytest.mark.nodb


# Every brand niche read from tenant_niches() in production, 2026-10-08.
LIVE_BRAND_NICHES = {
    "golf resort": ["golf", "hospitality"],
    "public golf courses in Wisconsin": ["golf"],
    "commercial real estate": ["commercial real estate", "real estate"],
    "New York commercial real estate": ["commercial real estate", "real estate"],
    "real estate investing": ["real estate", "finance"],
    "tour packages, travel and holidays": ["travel"],
    "curated India travel packages": ["travel"],
    "tour operator": ["travel"],
    "commercial spaceport": ["aerospace"],
    "AI automation for operations teams": ["ai & automation", "technology"],
    "New Jersey Comedy": ["comedy", "entertainment"],
    "parenting humor": ["parenting", "comedy", "entertainment"],
    "political candidates": ["politics"],
    "Real Estate, Fashion": ["real estate", "fashion"],
}


@pytest.mark.parametrize("niche,labels", list(LIVE_BRAND_NICHES.items()))
def test_every_live_brand_niche_maps_to_sensible_labels(niche, labels):
    assert nv.canonical(niche) == labels


def test_the_spaceport_is_not_real_estate():
    """THE false match. 'commercial' is a qualifier, not an industry."""
    got = nv.canonical("commercial spaceport")
    assert "real estate" not in got and "commercial real estate" not in got
    assert got == ["aerospace"]


@pytest.mark.parametrize("junk", [None, "", "   ", [], [None, ""], "knitting", "xyz lorem"])
def test_unknown_text_maps_to_nothing(junk):
    assert nv.canonical(junk) == []


def test_mapping_is_deterministic_and_reads_a_list_item_by_item():
    a = nv.canonical(["golf resort", "real estate investing"])
    assert a == ["golf", "hospitality", "real estate", "finance"]
    assert all(nv.canonical(["golf resort", "real estate investing"]) == a for _ in range(5))


def test_a_phrase_consumes_its_words():
    """Longest phrase first: 'office space' is commercial real estate and is not
    read again as anything about space; 'golf courses' is golf, not education."""
    assert nv.canonical("office space") == ["commercial real estate", "real estate"]
    assert "education" not in nv.canonical("public golf courses")


def test_every_label_maps_back_to_itself():
    """Stored labels are re-read through canonical() at ranking time, so a label
    that did not map to itself would silently stop matching."""
    for label in nv.LABELS:
        assert label in nv.canonical(label), label
    assert 35 <= len(nv.LABELS) <= 45


def test_parents_follow_their_children():
    assert nv.canonical("comedian") == ["comedy", "entertainment"]
    assert nv.canonical("chatbot") == ["ai & automation", "technology"]


# ── niche_rank on canonical labels ──────────────────────────────────────────


def test_labels_decide_when_both_sides_map():
    assert hl.niche_rank("commercial spaceport", ["commercial real estate"]) == 0
    assert hl.niche_rank("commercial spaceport", ["real estate"]) == 0
    # different words, same industry: the word matcher scored these 0
    assert hl.niche_tokens("tour operator") & hl.niche_tokens(["holidays"]) == set()
    assert hl.niche_rank("tour operator", ["holidays"]) >= 2
    assert hl.niche_rank("public golf courses in Wisconsin", ["golf resort"]) >= 2
    assert hl.niche_rank("parenting humor", ["New Jersey Comedy"]) >= 2


def test_untagged_still_beats_off_niche_and_contract_holds():
    assert hl.niche_rank("commercial spaceport", []) == 1
    assert hl.niche_rank("golf resort", ["politics"]) == 0


def test_word_overlap_is_the_fallback_when_either_side_maps_to_nothing():
    assert hl.niche_rank("knitting circles", ["knitting"]) == 3
    assert hl.niche_rank("knitting circles", ["golf"]) == 0
    assert hl.niche_rank("knitting", ["golf"]) == 0


def test_brand_tag_keys_carry_the_brands_labels_for_the_sql_presort():
    tags = hl.brand_tag_keys(["tour operator", "golf resort"])
    assert {"travel", "golf", "hospitality"} <= set(tags)
    assert tags.index("travel") > tags.index("golf resort"), "phrases first, labels last"


# ── the read ────────────────────────────────────────────────────────────────


class _Completions:
    def __init__(self, reply=None, boom=None):
        self.reply, self.boom, self.calls = reply, boom, []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return SimpleNamespace(
            model="gpt-4o-2024-08-06",
            usage=SimpleNamespace(prompt_tokens=410, completion_tokens=12),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.reply)))])


def _stub(monkeypatch, reply=None, boom=None):
    comp = _Completions(reply, boom)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    monkeypatch.setattr(nv, "_client", lambda: client)
    metered = []

    async def _rec(provider, model, usage, job, *a, **kw):
        metered.append((provider, model, job, usage))
        return True

    monkeypatch.setattr(spend, "record_tokens", _rec)
    return comp, metered


def test_the_read_is_one_low_detail_gpt4o_call_metered_as_niche_vocab_infer(monkeypatch):
    comp, metered = _stub(monkeypatch, {"labels": ["Golf", "hospitality"]})
    got = asyncio.run(nv.infer_from_image("https://cdn.example/p.png"))
    assert got == ["golf", "hospitality"]
    assert len(comp.calls) == 1
    call = comp.calls[0]
    assert call["model"] == "gpt-4o"
    image = call["messages"][1]["content"][1]["image_url"]
    assert image == {"url": "https://cdn.example/p.png", "detail": "low"}
    # the whole vocabulary is in the prompt, so the model chooses from it
    assert all(label in call["messages"][0]["content"] for label in nv.LABELS)
    assert [(p, j) for p, _m, j, _u in metered] == [("openai", "niche_vocab.infer")]


def test_labels_outside_the_vocabulary_are_dropped_and_capped_at_three(monkeypatch):
    _stub(monkeypatch, {"labels": ["golf", "luxury lifestyle", "travel", "golf",
                                   "hospitality", "events", 7]})
    assert asyncio.run(nv.infer_from_image(b"\x89PNG\r\n\x1a\nxx")) == [
        "golf", "travel", "hospitality"]


def test_a_quote_card_gets_nothing(monkeypatch):
    _stub(monkeypatch, {"labels": []})
    assert asyncio.run(nv.infer_from_image("https://cdn.example/quote.png")) == []


def test_a_failed_read_is_empty_never_raises_and_says_failed(monkeypatch):
    _stub(monkeypatch, boom=RuntimeError("429"))
    assert asyncio.run(nv.infer_from_image("https://cdn.example/p.png")) == []
    assert asyncio.run(nv.infer_detail("https://cdn.example/p.png"))["status"] == "failed"


def test_no_key_makes_no_call():
    # conftest's _no_niche_vision leaves no client
    assert asyncio.run(nv.infer_detail("https://cdn.example/p.png")) == {
        "status": "no_key", "labels": []}


def test_bytes_go_inline_and_a_path_outside_the_media_root_is_refused(monkeypatch):
    comp, _ = _stub(monkeypatch, {"labels": ["golf"]})
    asyncio.run(nv.infer_from_image(b"\x89PNG\r\n\x1a\nxx"))
    assert comp.calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,")
    got = asyncio.run(nv.infer_detail("/media-files/../../etc/passwd"))
    assert got["status"] == "failed" and len(comp.calls) == 1, "nothing was sent"


def test_the_estimate_is_about_a_tenth_of_a_cent():
    assert 0.0005 < nv.est_usd_per_image() < 0.003
