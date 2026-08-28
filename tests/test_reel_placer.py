"""The placer's cascade — what fills a moment, and why.

The two guarantees under test:

  * the cascade always lands somewhere honest — the user's footage when it
    genuinely fits, a hero photo when a card needs a face, the speaker's own
    words when there's nothing else, and the speaker himself when there's not
    even that. Nothing is invented to fill a hole.
  * the confidence floor holds. A cutaway that has nothing to do with the
    sentence under it is the exact failure that made BROLL_ENABLED get switched
    off in production, so a weak match must be refused, not shipped.
"""

import pytest

from james_os.reel_cards import Card
from james_os.reel_placer import (
    IMAGE_STYLES,
    MATCH_FLOOR,
    TYPOGRAPHIC_STYLES,
    Asset,
    coverage,
    match_assets,
    placements_to_elements,
    plan_placements,
    plan_summary,
)


@pytest.fixture(autouse=True)
def fresh_pool():
    """Pure logic — shadow conftest's autouse Postgres fixture."""
    yield


class FakeEmbedder:
    """Embeddings we control: each text is a unit vector along the axis of the
    first keyword it contains, so 'matching' is exact and legible in a test."""

    def __init__(self, axes: dict[str, int], dim: int = 8, noise: float = 0.0):
        self.axes, self.dim, self.noise = axes, dim, noise

    async def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            low = t.lower()
            for word, axis in self.axes.items():
                if word in low:
                    v[axis] = 1.0
                    break
            else:
                v[self.dim - 1] = 1.0        # an axis nothing else uses
            if self.noise:
                v = [x + self.noise for x in v]
            out.append(v)
        return out


def _card(start, end, style="quote", lines=None) -> Card:
    return Card(style=style, start=start, end=end,
                lines=lines if lines is not None else ["a spoken line"])


def _video(id_="a1", desc="drone shot of the waterfront lot", seconds=0.0) -> Asset:
    return Asset(id=id_, url=f"https://x/{id_}.mp4", kind="video",
                 description=desc, seconds=seconds)


def _image(id_="i1", desc="headshot of the founder") -> Asset:
    return Asset(id=id_, url=f"https://x/{id_}.jpg", kind="image", description=desc)


# ── rung 1: the user's own footage ───────────────────────────────────

@pytest.mark.asyncio
async def test_matching_user_footage_becomes_a_full_frame_cutaway():
    emb = FakeEmbedder({"waterfront": 0})
    cards = [_card(10.0, 14.0, lines=["the waterfront lot"])]
    out = await plan_placements(cards, assets=[_video(desc="waterfront drone")], embedder=emb)
    assert len(out) == 1
    assert out[0].kind == "broll"
    assert out[0].source == "user_broll"
    assert out[0].url.endswith("a1.mp4")


@pytest.mark.asyncio
async def test_a_matching_still_fills_a_card_rather_than_cutting_away():
    emb = FakeEmbedder({"founder": 0})
    cards = [_card(10.0, 14.0, style="collage", lines=["the founder"])]
    out = await plan_placements(cards, assets=[_image(desc="founder headshot")], embedder=emb)
    assert out[0].kind == "card"
    assert out[0].source == "user_image"
    assert out[0].card.image_url.endswith("i1.jpg")


@pytest.mark.asyncio
async def test_a_cutaway_never_outlasts_the_clip_behind_it():
    emb = FakeEmbedder({"waterfront": 0})
    cards = [_card(10.0, 15.0, lines=["the waterfront lot"])]
    out = await plan_placements(
        cards, assets=[_video(desc="waterfront drone", seconds=2.0)], embedder=emb)
    assert out[0].kind == "broll"
    assert out[0].duration == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a_clip_too_short_to_cut_to_is_used_as_a_still_instead():
    emb = FakeEmbedder({"waterfront": 0})
    cards = [_card(10.0, 14.0, lines=["the waterfront lot"])]
    out = await plan_placements(
        cards, assets=[_video(desc="waterfront drone", seconds=0.4)], embedder=emb)
    assert out[0].kind == "card"
    assert out[0].source == "user_image"


@pytest.mark.asyncio
async def test_one_asset_is_never_used_twice():
    emb = FakeEmbedder({"waterfront": 0})
    cards = [_card(10.0, 14.0, lines=["the waterfront lot"]),
             _card(20.0, 24.0, lines=["the waterfront lot again"])]
    out = await plan_placements(cards, assets=[_video(desc="waterfront drone")], embedder=emb)
    assert sum(1 for p in out if p.source == "user_broll") == 1
    assert out[1].source == "words"          # the second falls through


@pytest.mark.asyncio
async def test_the_strongest_pairing_wins_the_asset_not_the_earliest():
    # Both moments mention it, but only the second is really about the lot.
    emb = FakeEmbedder({"waterfront lot": 0, "waterfront": 1})
    cards = [_card(10.0, 14.0, lines=["waterfront generally"]),
             _card(20.0, 24.0, lines=["waterfront lot"])]
    out = await plan_placements(
        cards, assets=[_video(desc="waterfront lot drone")], embedder=emb)
    broll = [p for p in out if p.kind == "broll"]
    assert len(broll) == 1
    assert broll[0].start == 20.0


# ── the confidence floor ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unrelated_clip_is_refused_not_shipped():
    # The failure that turned BROLL_ENABLED off: a cutaway with nothing to do
    # with the sentence under it.
    emb = FakeEmbedder({"waterfront": 0, "taxes": 1})
    cards = [_card(10.0, 14.0, lines=["about taxes"])]
    out = await plan_placements(cards, assets=[_video(desc="waterfront drone")], embedder=emb)
    assert out[0].kind == "card"
    assert out[0].source == "words"          # fell through, no bad cutaway


@pytest.mark.asyncio
async def test_an_asset_with_no_description_is_never_matched():
    # A filename carries no meaning; guessing from one is how bad cuts happen.
    emb = FakeEmbedder({"waterfront": 0})
    cards = [_card(10.0, 14.0, lines=["the waterfront lot"])]
    blind = Asset(id="x", url="https://x/IMG_4821.mov", kind="video", description="")
    out = await plan_placements(cards, assets=[blind], embedder=emb)
    assert out[0].source == "words"


@pytest.mark.asyncio
async def test_a_weak_match_loses_to_the_relative_margin():
    """The calibration finding, pinned.

    Measured against voyage-3-large on a real reel, EVERY same-domain pair
    scores 0.52-0.65 — an irrelevant clip lands only ~0.12 below the best real
    match. So an asset must beat the matrix AVERAGE, not just a fixed number,
    or a merely-plausible clip gets forced onto a moment it has nothing to do
    with. These are the real measured scores.
    """
    class Measured:
        """moment0 = 'positioned correctly before market',
           moment1 = 'reach out, happy to share'."""
        rows = {
            "pricing": [0.646, 0.639],     # genuinely about moment 0
            "drone": [0.572, 0.599],       # plausible, about neither
            "cat": [0.526, 0.529],         # irrelevant
        }
        async def embed(self, texts):
            # Two moments first, then the assets, as match_assets orders them.
            out = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
            for t in texts[2:]:
                key = next(k for k in self.rows if k in t)
                a, b = self.rows[key]
                out.append([a, b, (1 - a * a - b * b) ** 0.5 if a * a + b * b < 1 else 0.0])
            return out

    hits = await match_assets(
        ["positioned correctly before market", "reach out happy to share"],
        [_video("p", "pricing strategy desk"), _video("d", "drone waterfront"),
         _video("c", "cat on a sofa")],
        embedder=Measured(),
    )
    # The pricing clip earns its moment; the merely-plausible drone does not get
    # pushed onto the leftover moment, and the cat is nowhere.
    assert 0 in hits and hits[0][0].id == "p"
    assert 1 not in hits


@pytest.mark.asyncio
async def test_match_assets_respects_the_floor():
    emb = FakeEmbedder({"a": 0, "b": 1})
    hits = await match_assets(["a thing"], [_video(desc="b thing")], embedder=emb)
    assert hits == {}


@pytest.mark.asyncio
async def test_an_embedder_outage_degrades_to_words(monkeypatch):
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("provider down")
    out = await plan_placements([_card(10.0, 14.0)], assets=[_video()], embedder=Broken())
    assert out[0].source == "words"          # a reel still ships


# ── rung 2: hero photos ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_card_that_wants_a_face_takes_a_hero_photo():
    emb = FakeEmbedder({})
    cards = [_card(10.0, 14.0, style="profile")]
    out = await plan_placements(cards, hero_photos=[_image("h1", "james on site")], embedder=emb)
    assert out[0].source == "hero_photo"
    assert out[0].card.image_url.endswith("h1.jpg")


@pytest.mark.asyncio
async def test_a_typographic_card_does_not_burn_a_hero_photo():
    emb = FakeEmbedder({})
    cards = [_card(10.0, 14.0, style="quote")]
    out = await plan_placements(cards, hero_photos=[_image("h1")], embedder=emb)
    assert out[0].source == "words"
    assert not out[0].card.image_url


@pytest.mark.asyncio
async def test_hero_photos_are_not_repeated():
    emb = FakeEmbedder({})
    cards = [_card(10.0, 14.0, style="collage"), _card(20.0, 24.0, style="collage")]
    out = await plan_placements(cards, hero_photos=[_image("h1")], embedder=emb)
    assert out[0].source == "hero_photo"
    assert out[1].source == "words"


# ── rung 3: words alone — the bare-talking-head case ─────────────────

@pytest.mark.asyncio
async def test_with_no_assets_at_all_the_reel_still_gets_cards():
    # The whole point: a user who uploads only a talking head still gets
    # designed cutaways, built from what they said.
    emb = FakeEmbedder({})
    cards = [_card(10.0, 14.0, style="stat", lines=["half a billion"]),
             _card(20.0, 24.0, style="label", lines=["pricing strategy"])]
    out = await plan_placements(cards, embedder=emb)
    assert len(out) == 2
    assert all(p.kind == "card" and p.source == "words" for p in out)
    assert all(p.card.style in TYPOGRAPHIC_STYLES for p in out)


@pytest.mark.asyncio
async def test_an_image_style_with_no_image_is_downgraded_not_left_hollow():
    emb = FakeEmbedder({})
    out = await plan_placements([_card(10.0, 14.0, style="collage")], embedder=emb)
    assert out[0].card.style in TYPOGRAPHIC_STYLES
    assert out[0].card.style not in IMAGE_STYLES


# ── rung 4: stay on the speaker ──────────────────────────────────────

@pytest.mark.asyncio
async def test_a_moment_with_nothing_to_show_is_left_on_the_speaker():
    emb = FakeEmbedder({})
    out = await plan_placements([Card(style="quote", start=10.0, end=14.0, lines=[])],
                                embedder=emb)
    assert out == []


# ── rendering + reporting ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cutaway_is_muted_so_it_cannot_talk_over_the_speaker():
    emb = FakeEmbedder({"waterfront": 0})
    out = await plan_placements(
        [_card(10.0, 14.0, lines=["the waterfront lot"])],
        assets=[_video(desc="waterfront drone")], embedder=emb)
    els = placements_to_elements(out)
    vid = [e for e in els if e["type"] == "video"]
    assert len(vid) == 1
    assert vid[0]["volume"] == 0
    assert vid[0]["fit"] == "cover"


@pytest.mark.asyncio
async def test_elements_come_out_in_time_order():
    emb = FakeEmbedder({})
    out = await plan_placements([_card(20.0, 24.0), _card(10.0, 14.0)], embedder=emb)
    times = [e["time"] for e in placements_to_elements(out)]
    assert times == sorted(times)


@pytest.mark.asyncio
async def test_the_plan_says_which_rung_answered():
    emb = FakeEmbedder({"waterfront": 0})
    out = await plan_placements(
        [_card(10.0, 14.0, lines=["the waterfront lot"]), _card(20.0, 24.0)],
        assets=[_video(desc="waterfront drone")], embedder=emb)
    rows = plan_summary(out)
    assert [r["source"] for r in rows] == ["user_broll", "words"]
    assert rows[0]["kind"] == "broll"


@pytest.mark.asyncio
async def test_coverage_reports_how_much_is_cutaway():
    emb = FakeEmbedder({})
    out = await plan_placements([_card(10.0, 14.0), _card(20.0, 23.0)], embedder=emb)
    cov = coverage(out, duration=40.0)
    assert cov["placements"] == 2
    assert cov["cutaway_seconds"] == pytest.approx(7.0)
    assert cov["speaker_seconds"] == pytest.approx(33.0)
    assert cov["by_source"] == {"words": 2}


@pytest.mark.asyncio
async def test_coverage_says_why_supplied_clips_went_unused():
    # Silently ignoring what the user uploaded is a bad experience; the plan
    # has to say WHY nothing of theirs was used.
    emb = FakeEmbedder({"waterfront": 0, "taxes": 1})
    supplied = [_video(desc="waterfront drone")]
    out = await plan_placements([_card(10.0, 14.0, lines=["about taxes"])],
                                assets=supplied, embedder=emb)
    cov = coverage(out, 40.0, supplied)
    assert cov["assets"] == {"supplied": 1, "used": 0, "undescribed": 0,
                             "why_unused": cov["assets"]["why_unused"]}
    assert "distinctly about" in cov["assets"]["why_unused"]


@pytest.mark.asyncio
async def test_coverage_blames_the_missing_description_when_that_is_the_cause():
    emb = FakeEmbedder({})
    blind = [Asset(id="x", url="https://x/IMG_1.mov", kind="video", description="")]
    out = await plan_placements([_card(10.0, 14.0)], assets=blind, embedder=emb)
    cov = coverage(out, 40.0, blind)
    assert cov["assets"]["undescribed"] == 1
    assert "describe them first" in cov["assets"]["why_unused"]


@pytest.mark.asyncio
async def test_coverage_counts_a_used_asset():
    emb = FakeEmbedder({"waterfront": 0})
    supplied = [_video(desc="waterfront drone")]
    out = await plan_placements([_card(10.0, 14.0, lines=["the waterfront lot"])],
                                assets=supplied, embedder=emb)
    cov = coverage(out, 40.0, supplied)
    assert cov["assets"]["used"] == 1
    assert "why_unused" not in cov["assets"]


def test_the_typographic_styles_need_no_imagery():
    # The claim the bare-talking-head case rests on.
    from james_os.reel_cards import card_elements
    for style in TYPOGRAPHIC_STYLES:
        els = card_elements(Card(style=style, start=5.0, end=9.0, lines=["a line"]))
        assert els, style
        assert not any(e.get("type") == "image" for e in els), style
