"""The placer — what actually fills each moment the director picked.

`reel_director` decides WHERE a reel should cut away from the speaker.
This decides WHAT goes there, and it has to work in two very different
situations: the user handed us B-roll, or the user handed us nothing but a
talking head. So it is a CASCADE, not a matcher — each moment walks down a
chain of options until one can be honoured:

    1. the user's own B-roll, when it genuinely matches what is being said
    2. a hero photo from the brand library, for a card that wants a face
    3. a typographic card built from the speaker's own words
    4. stay on the speaker

Rung 3 is the one that makes this product work with no assets at all. Three of
the five card styles (`quote`, `stat`, `label`) are type on a colour field —
no photography, no generation, no licensing question. Roughly half the cards in
the reference reel are exactly that.

Rung 1 has a CONFIDENCE FLOOR, and that floor is the point. `BROLL_ENABLED` was
switched off in production because automatic cutaways cut badly — a clip with
nothing to do with the sentence under it is worse than no clip. Matching on
meaning and refusing below a threshold is the fix; generating something to fill
the hole is not. Nothing here generates imagery: an unmatched moment falls to
words, and a moment with no words falls back to the speaker.

Every placement records WHY it was chosen (`source`), so the review screen can
show a human what happened and nothing is quietly faked.
"""

import math
from dataclasses import dataclass, field

from .reel_cards import Card

# Styles that need no imagery whatsoever — the reason a bare talking head can
# still produce a reel with designed cutaways.
TYPOGRAPHIC_STYLES = ("quote", "stat", "label")
# Styles built around a picture. Without one they'd render as an empty frame,
# so they are DOWNGRADED rather than shown hollow.
IMAGE_STYLES = ("collage", "profile")

# An absolute sanity floor. On its own this is NOT enough, because embedding
# similarity for same-domain English sits in a narrow band: measured against
# voyage-3-large on a real reel, an obviously irrelevant clip ("a cat sleeping
# on a sofa") scored 0.526 while the best genuine match scored 0.653. A fixed
# threshold anywhere in that band is arbitrary.
MATCH_FLOOR = 0.55
# So the real test is RELATIVE: an asset must beat the average similarity across
# the whole moment/asset matrix by this margin. That measures "this asset is
# distinctly about this moment" rather than "these are both English sentences
# about property", which is what an absolute score actually captures.
MATCH_MARGIN = 0.04
# A margin needs a matrix to average over. Below this many pairs there is no
# baseline worth computing (one asset and one moment tells you nothing about
# what a NORMAL score looks like), so the absolute floor stands alone.
_MIN_PAIRS_FOR_MARGIN = 4
# A cutaway can't outlast the clip behind it.
_MIN_BROLL_S = 1.2

PLACEMENT_KINDS = ("broll", "card")
SOURCES = ("user_broll", "user_image", "hero_photo", "words")


@dataclass
class Asset:
    """One thing the user gave us, or one hero photo from the brand library.

    `description` is what the asset SHOWS, in words — written by the vision
    pass. An asset with no description cannot be matched at all: filenames like
    IMG_4821.mov carry no meaning, and guessing from one is exactly how a
    cutaway ends up contradicting the sentence under it.
    """
    id: str
    url: str
    kind: str = "video"            # "video" | "image"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    seconds: float = 0.0           # 0 = unknown

    @property
    def matchable(self) -> str:
        return " ".join([self.description, " ".join(self.tags)]).strip()


@dataclass
class Placement:
    """One filled moment, and the reason it was filled that way."""
    kind: str                      # "broll" | "card"
    start: float
    end: float
    source: str                    # which rung of the cascade answered
    asset_id: str = ""
    url: str = ""
    card: Card | None = None

    @property
    def duration(self) -> float:
        return max(0.0, round(self.end - self.start, 3))


# ── matching ─────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


async def match_assets(
    moments: list[str],
    assets: list[Asset],
    *,
    embedder=None,
    floor: float = MATCH_FLOOR,
) -> dict[int, tuple[Asset, float]]:
    """moment index → (asset, score), for moments an asset genuinely fits.

    Greedy best-first over every (moment, asset) pair, so the strongest pairing
    wins the asset rather than whichever moment happened to come first. Each
    asset is used at most ONCE — the same clip appearing twice in a 40-second
    reel reads as a mistake.

    Assets with no description are skipped, not guessed at.
    """
    usable = [a for a in (assets or []) if a.matchable and a.url]
    if not moments or not usable:
        return {}

    if embedder is None:
        from .embedder import get_embedder
        embedder = get_embedder()

    try:
        vecs = await embedder.embed([*moments, *[a.matchable for a in usable]])
    except Exception:  # noqa: BLE001 — matching is additive; fall through the cascade
        return {}
    if len(vecs) != len(moments) + len(usable):
        return {}
    m_vecs = vecs[: len(moments)]
    a_vecs = vecs[len(moments):]

    # Strongest pairing first; ties go to the EARLIER moment, so an equally
    # good match lands predictably and establishes the format sooner rather
    # than arbitrarily favouring whichever index sorted higher.
    pairs = [
        (_cosine(m_vecs[i], a_vecs[j]), i, j)
        for i in range(len(moments))
        for j in range(len(usable))
    ]
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    # The bar: the absolute floor, raised to the matrix average plus a margin
    # once there is enough of a matrix to have an average.
    bar = floor
    if len(pairs) >= _MIN_PAIRS_FOR_MARGIN:
        baseline = sum(p[0] for p in pairs) / len(pairs)
        bar = max(bar, baseline + MATCH_MARGIN)

    out: dict[int, tuple[Asset, float]] = {}
    taken_assets: set[int] = set()
    for score, i, j in pairs:
        if score < bar:
            break                                  # sorted — nothing below fits
        if i in out or j in taken_assets:
            continue
        out[i] = (usable[j], round(score, 4))
        taken_assets.add(j)
    return out


# ── the cascade ──────────────────────────────────────────────────────

def _as_card(card: Card, *, style: str = "", image_url: str = "") -> Card:
    return Card(
        style=style or card.style,
        start=card.start, end=card.end,
        lines=list(card.lines), accent=card.accent,
        image_url=image_url or card.image_url,
        handle=card.handle,
    )


def _broll_placement(card: Card, asset: Asset, source: str) -> Placement | None:
    """A full-frame cutaway to the user's own footage. Clamped to the clip's
    own length so we never hold on a frame past the end of the video."""
    end = card.end
    if asset.seconds and asset.seconds > 0:
        end = min(end, round(card.start + asset.seconds, 3))
    if end - card.start < _MIN_BROLL_S:
        return None                                # too short to be worth a cut
    return Placement(kind="broll", start=card.start, end=end,
                     source=source, asset_id=asset.id, url=asset.url)


async def plan_placements(
    cards: list[Card],
    *,
    assets: list[Asset] | None = None,
    hero_photos: list[Asset] | None = None,
    embedder=None,
    floor: float = MATCH_FLOOR,
) -> list[Placement]:
    """The director's moments → what fills each one.

    Never invents imagery and never leaves a moment half-dressed: a card that
    wanted a picture and couldn't get one is DOWNGRADED to a typographic style
    rather than rendered as an empty frame.
    """
    cards = [c for c in (cards or []) if c.lines or c.image_url]
    if not cards:
        return []

    texts = [" ".join(c.lines) for c in cards]
    matched = await match_assets(texts, assets or [], embedder=embedder, floor=floor)

    heroes = list(hero_photos or [])
    hero_i = 0
    out: list[Placement] = []

    for i, card in enumerate(cards):
        hit = matched.get(i)

        # ── rung 1: the user's own footage, when it actually fits ──
        if hit is not None:
            asset, _score = hit
            if asset.kind == "video":
                p = _broll_placement(card, asset, "user_broll")
                if p is not None:
                    out.append(p)
                    continue
                # too short to cut to — fall through and use it as a still
            style = card.style if card.style in IMAGE_STYLES else "collage"
            out.append(Placement(
                kind="card", start=card.start, end=card.end, source="user_image",
                asset_id=asset.id, url=asset.url,
                card=_as_card(card, style=style, image_url=asset.url)))
            continue

        # ── rung 2: a hero photo, but only for a style built around a picture ──
        if card.style in IMAGE_STYLES and hero_i < len(heroes):
            hero = heroes[hero_i]
            hero_i += 1
            out.append(Placement(
                kind="card", start=card.start, end=card.end, source="hero_photo",
                asset_id=hero.id, url=hero.url,
                card=_as_card(card, image_url=hero.url)))
            continue

        # ── rung 3: the speaker's own words, on a typographic card ──
        if not card.lines:
            continue                               # rung 4: nothing to show
        style = card.style
        if style not in TYPOGRAPHIC_STYLES:
            # Wanted a picture, hasn't got one. Downgrading reads as a design
            # choice; an image card with no image reads as a bug.
            style = "quote"
        out.append(Placement(
            kind="card", start=card.start, end=card.end, source="words",
            card=_as_card(card, style=style)))

    return out


def placements_to_elements(placements: list[Placement], brand_kit: dict | None = None) -> list[dict]:
    """Every placement → the Creatomate elements that draw it.

    Cards go through `reel_cards`; a B-roll cutaway is a full-bleed video on the
    card background track, so it covers the speaker exactly as a card does and
    obeys the same z-order.
    """
    from .reel_cards import TRACK_BG, card_elements

    out: list[dict] = []
    for p in sorted(placements or [], key=lambda x: x.start):
        if p.kind == "card" and p.card is not None:
            out.extend(card_elements(p.card, brand_kit))
        elif p.kind == "broll" and p.url:
            out.append({
                "type": "video", "source": p.url,
                "track": TRACK_BG, "time": p.start, "duration": p.duration,
                "fit": "cover",
                "volume": 0,          # the speaker's audio carries the whole reel
                "animations": [
                    {"time": 0, "duration": 0.18, "type": "fade"},
                    {"time": max(0.0, p.duration - 0.18), "duration": 0.18,
                     "type": "fade", "reversed": True},
                ],
            })
    return out


def plan_summary(placements: list[Placement]) -> list[dict]:
    """The plan as a human reads it on the review screen — what lands where,
    and which rung of the cascade answered for it."""
    return [
        {
            "kind": p.kind,
            "source": p.source,
            "start": round(p.start, 2),
            "end": round(p.end, 2),
            "seconds": p.duration,
            "style": p.card.style if p.card else "",
            "lines": list(p.card.lines) if p.card else [],
            "asset_id": p.asset_id,
            "url": p.url,
        }
        for p in placements
    ]


def coverage(placements: list[Placement], duration: float,
             assets: list[Asset] | None = None) -> dict:
    """How much of the reel is cutaway vs speaker, and where each cutaway came
    from. A reel that is mostly cutaway has stopped being a talking-head reel,
    so this is worth surfacing rather than discovering on playback.

    When the user supplied assets, this also reports how many were actually
    used and — if none were — WHY. "We didn't use your clips" with a reason
    beats a silent fallback: the honest answer is usually that nothing they
    uploaded was distinctly about what they said, and that's actionable.
    """
    total = sum(p.duration for p in placements or [])
    by_source: dict[str, int] = {}
    for p in placements or []:
        by_source[p.source] = by_source.get(p.source, 0) + 1

    out = {
        "placements": len(placements or []),
        "cutaway_seconds": round(total, 2),
        "speaker_seconds": round(max(0.0, duration - total), 2),
        "cutaway_pct": round(100.0 * total / duration, 1) if duration > 0 else 0.0,
        "by_source": by_source,
    }

    if assets is not None:
        supplied = list(assets)
        used = {p.asset_id for p in (placements or []) if p.asset_id}
        undescribed = sum(1 for a in supplied if not a.matchable)
        out["assets"] = {
            "supplied": len(supplied),
            "used": len([a for a in supplied if a.id in used]),
            "undescribed": undescribed,
        }
        if supplied and not used:
            out["assets"]["why_unused"] = (
                f"{undescribed} of {len(supplied)} have no description yet, so "
                "they can't be matched — describe them first"
                if undescribed else
                "none of them were distinctly about what's being said, so the "
                "reel stayed on the speaker's own words rather than cutting to "
                "something unrelated"
            )
    return out


__all__ = [
    "Asset", "Placement", "plan_placements", "match_assets",
    "placements_to_elements", "plan_summary", "coverage",
    "TYPOGRAPHIC_STYLES", "IMAGE_STYLES", "MATCH_FLOOR", "MATCH_MARGIN",
    "PLACEMENT_KINDS",
]
