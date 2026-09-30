"""Choosing a hero photo that is actually about the headline.

From a measured render, 2026-09-28: "Unforgettable Dubai Awaits" over Phang Nga
limestone karst. Nothing was misbehaving — layout fit scores edge energy and the
picker rotates by least-recently-used, and neither has any idea what a photo is
of.

The tests that matter here are the REFUSALS. A ranker that always has an opinion
is worse than none, because it is confidently wrong instead of visibly absent.
"""

import pytest

from james_os import photo_subject as ps



def _refs(n: int) -> list[tuple[str, bytes]]:
    return [(f"https://cdn.test/{i}.jpg", b"x") for i in range(n)]


# --------------------------------------------------------------- the refusals


@pytest.mark.asyncio
async def test_it_refuses_to_rank_on_stub_embeddings(monkeypatch):
    """The failure that would look exactly like success.

    StubEmbedder returns a seeded vector per string — same text, same vector —
    so every plumbing check passes while the rankings are pure noise. Ranking on
    that is worse than not ranking at all.
    """
    class Stub:
        model_name = "stub"

        async def embed(self, texts):
            raise AssertionError("must not embed with the stub")

    monkeypatch.setattr("james_os.embedder.get_embedder", lambda: Stub())
    refs = _refs(10)
    assert await ps.subject_ranked("t", "a Dubai long weekend", refs) == refs


@pytest.mark.asyncio
async def test_a_library_with_no_opinion_is_left_alone(monkeypatch):
    """Every photo scoring identically means the library has no preference."""
    flat = [(0.80, f"u{i}") for i in range(8)]
    assert ps._narrow(flat) is None


@pytest.mark.asyncio
async def test_a_pool_where_nothing_matches_is_left_alone():
    """Measured off-niche means sit at 0.52-0.64; on-niche at 0.60-0.78."""
    weak = [(0.58, "u1"), (0.55, "u2"), (0.53, "u3"), (0.51, "u4"), (0.50, "u5")]
    assert ps._narrow(weak) is None, "a mean below the floor: nothing here is about it"


@pytest.mark.asyncio
async def test_the_standout_test_that_measurement_rejected_is_not_used():
    """The gate this replaced was `best >= mean + k*sigma`, and it was backwards.

    An irrelevant query scores low but VERY tightly, so a tiny deviation reads as
    a huge standout: measured on Turtleback 2026-09-29, off-niche queries
    averaged z=2.07 against on-niche z=1.55, and a query about loft apartments
    scored the highest z of all twenty. This is that exact shape — one clear
    standout over a tight, low pack — and it must be refused on the MEAN.
    """
    tight_low = [(0.61, "standout")] + [(0.57, f"u{i}") for i in range(9)]
    assert ps._narrow(tight_low) is None


@pytest.mark.asyncio
async def test_a_clear_winner_narrows_the_pool():
    scored = [(0.85, "aerial"), (0.80, "a"), (0.78, "b"), (0.72, "c"),
              (0.66, "d"), (0.64, "e"), (0.62, "f"), (0.60, "g")]
    keep = ps._narrow(scored)
    assert keep is not None and "aerial" in keep
    assert len(keep) < len(scored), "it must actually narrow"


@pytest.mark.asyncio
async def test_narrowing_never_collapses_to_one_photo():
    """Collapsing to a single best photo would end the rotation that stops a
    brand posting the same picture every week — a regression suited_photos
    already recorded once."""
    scored = [(0.95, "one")] + [(0.62, f"u{i}") for i in range(9)]
    keep = ps._narrow(scored)
    assert keep is not None and len(keep) >= ps._KEEP_MIN


# ------------------------------------------------------------ what it embeds


def test_the_place_leads_the_searchable_text():
    """A caption about turquoise water matches a Dubai headline about as well as
    a Thailand one; the place name is what breaks the tie."""
    text = ps.searchable({"caption": "Turquoise water and a longtail boat.",
                          "place": "Phang Nga Bay, Thailand", "subject": "boat",
                          "setting": "outdoor, coastal", "tags": ["sea", "karst"]})
    assert text.startswith("Phang Nga Bay, Thailand")
    assert "karst" in text


def test_an_unrecognised_place_is_left_blank_not_invented():
    text = ps.searchable({"caption": "A city skyline at dusk.", "place": "",
                          "subject": "skyline", "setting": "outdoor", "tags": []})
    assert "city skyline" in text
    assert not text.startswith("·")


def test_a_junk_description_yields_nothing_to_embed():
    assert ps.searchable({}) == ""
    assert ps.searchable(None) == ""


# ------------------------------------------------------------- not disturbing


@pytest.mark.asyncio
async def test_a_small_library_is_never_narrowed(monkeypatch):
    """Below the rotation floor there is nothing to gain and a rotation to lose."""
    monkeypatch.setattr(ps, "_real_embedder", lambda: object())
    refs = _refs(ps._KEEP_MIN)
    assert await ps.subject_ranked("t", "anything", refs) == refs


@pytest.mark.asyncio
async def test_an_empty_topic_changes_nothing(monkeypatch):
    monkeypatch.setattr(ps, "_real_embedder", lambda: object())
    refs = _refs(10)
    assert await ps.subject_ranked("t", "   ", refs) == refs


@pytest.mark.asyncio
async def test_a_database_failure_leaves_the_library_as_it_was(monkeypatch):
    class Real:
        model_name = "voyage-3"

        async def embed(self, texts):
            return [[1.0] * 8]

    monkeypatch.setattr(ps, "_real_embedder", lambda: Real())

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ps, "acquire", boom)
    refs = _refs(10)
    assert await ps.subject_ranked("t", "a Dubai long weekend", refs) == refs
