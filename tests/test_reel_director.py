"""The card director — grounding and pacing.

The load-bearing guarantee: a card can only ever show words that were actually
spoken in the window it covers. The model picks WHERE; the transcript decides
WHAT and WHEN. These tests hand the director a model that lies — inventing copy,
proposing overlaps, picking bad timings — and prove none of it reaches a card.
"""

import pytest

from james_os.reel_cards import Card
from james_os.reel_director import (
    LEAD_IN_S,
    MAX_CARD_S,
    MAX_CARDS,
    MIN_CARD_S,
    MIN_GAP_S,
    enforce_pacing,
    plan_cards,
    plan_summary,
    split_phrases,
)
from james_os.transcription import TranscribedWord


@pytest.fixture(autouse=True)
def fresh_pool():
    """Pure logic — shadow conftest's autouse Postgres fixture."""
    yield


def words(spec: list[tuple[str, float, float]]) -> list[TranscribedWord]:
    return [TranscribedWord(word=w, start=s, end=e) for w, s, e in spec]


def even_speech(texts: list[str], start: float = 0.0, per: float = 0.4):
    """One word every `per` seconds — a steady stream with no pauses."""
    out, t = [], start
    for w in texts:
        out.append(TranscribedWord(word=w, start=round(t, 3), end=round(t + per, 3)))
        t += per
    return out


# ── phrase splitting ─────────────────────────────────────────────────

def test_a_real_pause_ends_a_phrase():
    ws = words([("after", 0.0, 0.3), ("half", 0.3, 0.6),
                ("a", 1.4, 1.6), ("billion", 1.6, 2.0)])   # 0.8s gap
    ph = split_phrases(ws)
    assert len(ph) == 2
    assert ph[0]["text"] == "after half"
    assert ph[1]["text"] == "a billion"


def test_a_long_run_without_pauses_still_splits():
    ph = split_phrases(even_speech([f"w{i}" for i in range(30)], per=0.2))
    assert len(ph) > 1
    assert all(len(p["words"]) <= 9 for p in ph)


def test_phrase_timings_come_from_the_words():
    ph = split_phrases(words([("a", 1.0, 1.2), ("b", 1.2, 1.5)]))
    assert ph[0]["start"] == 1.0 and ph[0]["end"] == 1.5


def test_no_words_no_phrases():
    assert split_phrases([]) == []


# ── grounding: the model does not get to write copy ──────────────────

async def _plan_with(monkeypatch, proposals, ws, **kw):
    async def fake(_phrases, _target, _brand):
        return proposals
    monkeypatch.setattr("james_os.reel_director._ask_model", fake)
    return await plan_cards(ws, **kw)


@pytest.mark.asyncio
async def test_card_copy_is_the_transcript_not_the_models_words(monkeypatch):
    ws = even_speech(["most", "of", "the", "time", "it", "does", "not"], start=6.0, per=0.5)
    cards = await _plan_with(monkeypatch, [{
        "first_phrase": 0, "last_phrase": 0, "style": "quote",
        "text": "BUY NOW BEFORE PRICES EXPLODE",     # the model inventing copy
    }], ws)
    assert cards
    said = " ".join(cards[0].lines).lower()
    assert "buy now" not in said
    for w in said.split():
        assert w in {"most", "of", "the", "time", "it", "does", "not"}


@pytest.mark.asyncio
async def test_card_timing_snaps_to_the_spoken_window(monkeypatch):
    # Long enough to survive the minimum-duration rule, so what's under test is
    # the timing source rather than the pacing filter.
    ws = even_speech(["one", "two", "three", "four", "five"], start=8.0, per=0.6)
    cards = await _plan_with(monkeypatch, [{
        "first_phrase": 0, "last_phrase": 0, "style": "quote",
        "start": 0.0, "end": 99.0,                    # the model inventing timing
    }], ws)
    assert cards[0].start == pytest.approx(8.0)
    assert cards[0].end == pytest.approx(11.0)


@pytest.mark.asyncio
async def test_a_phrase_index_out_of_range_is_dropped(monkeypatch):
    ws = even_speech(["a", "b", "c"], start=6.0, per=0.6)
    assert await _plan_with(monkeypatch, [
        {"first_phrase": 40, "last_phrase": 41, "style": "quote"}], ws) == []


@pytest.mark.asyncio
async def test_overlapping_spans_are_refused(monkeypatch):
    ws = even_speech([f"w{i}" for i in range(40)], start=5.0, per=0.5)
    cards = await _plan_with(monkeypatch, [
        {"first_phrase": 0, "last_phrase": 1, "style": "quote"},
        {"first_phrase": 1, "last_phrase": 2, "style": "stat"},   # overlaps the first
    ], ws)
    assert len(cards) == 1


@pytest.mark.asyncio
async def test_an_unknown_style_becomes_a_real_one(monkeypatch):
    ws = even_speech(["a", "b", "c", "d"], start=6.0, per=0.6)
    cards = await _plan_with(monkeypatch, [
        {"first_phrase": 0, "last_phrase": 0, "style": "hologram"}], ws)
    assert cards and cards[0].style == "quote"


@pytest.mark.asyncio
async def test_styles_can_be_restricted_by_the_template(monkeypatch):
    ws = even_speech(["a", "b", "c", "d"], start=6.0, per=0.6)
    cards = await _plan_with(monkeypatch, [
        {"first_phrase": 0, "last_phrase": 0, "style": "stat"}], ws, styles=["quote"])
    assert cards and cards[0].style == "quote"


@pytest.mark.asyncio
async def test_a_provider_failure_yields_no_cards_not_a_crash(monkeypatch):
    # Patch the PROVIDER, not _ask_model — the guard under test lives inside
    # _ask_model, and a render must survive a director outage with plain
    # captions rather than dying.
    class Down:
        model_name = "down"
        async def complete_json(self, *_a, **_k):
            raise RuntimeError("provider down")
    monkeypatch.setattr("james_os.llm.get_llm", lambda: Down())
    assert await plan_cards(even_speech(["a", "b", "c", "d"], start=6.0, per=0.6)) == []


@pytest.mark.asyncio
async def test_garbage_proposals_are_skipped(monkeypatch):
    ws = even_speech(["a", "b", "c"], start=6.0, per=0.6)
    assert await _plan_with(monkeypatch, [
        {"first_phrase": "banana"}, {}, {"last_phrase": 0}], ws) == []


@pytest.mark.asyncio
async def test_no_transcript_means_no_cards(monkeypatch):
    assert await _plan_with(monkeypatch, [{"first_phrase": 0, "last_phrase": 0}], []) == []


# ── pacing: decided in code, not by the model ────────────────────────

def _c(start, end, style="quote"):
    return Card(style=style, start=start, end=end, lines=["a line"])


def test_the_opening_hook_is_never_carded():
    assert enforce_pacing([_c(0.5, 4.0)], 40.0) == []
    assert enforce_pacing([_c(LEAD_IN_S + 0.1, LEAD_IN_S + 3.0)], 40.0)


def test_a_card_shorter_than_the_build_in_is_dropped():
    assert enforce_pacing([_c(10.0, 10.0 + MIN_CARD_S - 0.2)], 40.0) == []


def test_an_over_long_card_is_clamped_not_dropped():
    kept = enforce_pacing([_c(10.0, 40.0)], 60.0)
    assert len(kept) == 1
    assert kept[0].duration == pytest.approx(MAX_CARD_S)


def test_the_speaker_must_come_back_between_cards():
    kept = enforce_pacing([_c(10.0, 13.0), _c(13.5, 16.5)], 40.0)   # only 0.5s apart
    assert len(kept) == 1
    spaced = enforce_pacing([_c(10.0, 13.0), _c(13.0 + MIN_GAP_S + 0.1, 19.0)], 40.0)
    assert len(spaced) == 2


def test_a_card_cannot_run_past_the_end_of_the_reel():
    kept = enforce_pacing([_c(30.0, 45.0)], 33.0)
    assert kept and kept[0].end <= 33.0


def test_card_count_is_capped():
    many = [_c(5.0 + i * 8.0, 8.0 + i * 8.0) for i in range(MAX_CARDS + 6)]
    assert len(enforce_pacing(many, 400.0)) == MAX_CARDS


def test_pacing_output_is_ordered_by_time():
    kept = enforce_pacing([_c(30.0, 33.0), _c(10.0, 13.0), _c(20.0, 23.0)], 60.0)
    assert [c.start for c in kept] == sorted(c.start for c in kept)


@pytest.mark.asyncio
async def test_density_stays_sane_on_a_long_transcript(monkeypatch):
    ws = even_speech([f"w{i}" for i in range(200)], start=0.0, per=0.4)   # 80s
    proposals = [{"first_phrase": i, "last_phrase": i, "style": "quote"}
                 for i in range(0, 20, 2)]
    cards = await _plan_with(monkeypatch, proposals, ws)
    assert len(cards) <= MAX_CARDS
    for a, b in zip(cards, cards[1:]):
        assert b.start - a.end >= MIN_GAP_S


def test_plan_summary_reports_what_lands_where():
    rows = plan_summary([_c(10.0, 13.0, "stat")])
    assert rows == [{"style": "stat", "start": 10.0, "end": 13.0,
                     "seconds": 3.0, "lines": ["a line"]}]
