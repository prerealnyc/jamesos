"""Transcript → card plan. The decision layer of the reel editor.

Given word-level timestamps (`transcription.transcribe_words`, which the
long-form path already runs on uploaded footage), this decides WHICH spoken
moments earn a designed cutaway, WHAT kind, and WHEN it starts and ends.

The whole design rests on one rule:

    A card may only ever show words that were actually spoken in the window
    it covers.

Not "should" — enforced. The LLM picks phrase indices out of a numbered list;
`_span_text()` then rebuilds the card's copy from the TRANSCRIPT, never from
what the model wrote back. A model that invents a punchier line gets its line
thrown away, because a card that contradicts the voiceover under it is worse
than no card. Timing comes from the same place: a card starts on its first
word's start and ends on its last word's end, so it can never drift off the
speech it belongs to.

Everything the model does NOT get to decide is decided in code: how many cards
a reel can hold, how close two cards may sit, how long one runs. Density is a
pacing decision, and pacing is the brand's, not the model's.
"""

import json
import re

from .reel_cards import CARD_STYLES, Card
from .transcription import TranscribedWord

# ── pacing rules (code, never the model) ─────────────────────────────
MIN_CARD_S = 1.8            # shorter than this and the build-in is a flicker
MAX_CARD_S = 5.0            # longer and the reel stalls on a static frame
MIN_GAP_S = 2.5             # speaker time between cards, so it stays a talking-head reel
SECONDS_PER_CARD = 9.0      # target density: ~1 card per 9s of runtime
MAX_CARDS = 8
LEAD_IN_S = 3.0             # never cut away inside the opening hook
_MAX_WORDS_PER_LINE = 5
_MAX_LINES = 2

_PAUSE_SPLIT_S = 0.34       # a gap this long ends a phrase
_MAX_PHRASE_WORDS = 9


# ── phrases: the units the model chooses between ─────────────────────

def split_phrases(words: list[TranscribedWord]) -> list[dict]:
    """Group timed words into speech phrases, split on real pauses and on
    length. These are the ONLY things a card can be pinned to — offering the
    model raw words invites it to slice mid-thought."""
    phrases: list[dict] = []
    cur: list[TranscribedWord] = []

    def flush():
        if not cur:
            return
        phrases.append({
            "index": len(phrases),
            "start": round(cur[0].start, 3),
            "end": round(cur[-1].end, 3),
            "text": _clean(" ".join(w.word for w in cur)),
            "words": list(cur),
        })

    prev_end = None
    for w in words or []:
        if cur and (
            (prev_end is not None and w.start - prev_end >= _PAUSE_SPLIT_S)
            or len(cur) >= _MAX_PHRASE_WORDS
        ):
            flush()
            cur = []
        cur.append(w)
        prev_end = w.end
    flush()
    return [p for p in phrases if p["text"]]


def words_from_captions(captions) -> list[TranscribedWord]:
    """Caption flashes → per-word timings.

    The long-form path has already word-transcribed the footage and grouped the
    result into 2-3 word flashes, so the timings here are real, not guessed —
    interpolating inside a flash that short is accurate to well under a tenth
    of a second, and it saves paying for a second transcription of audio the
    pipeline has already read once.
    """
    out: list[TranscribedWord] = []
    for c in captions or []:
        text = (c.get("raw_text") or c.get("text") or "") if isinstance(c, dict) \
            else getattr(c, "text", "")
        start = float((c.get("start") if isinstance(c, dict) else getattr(c, "start", 0)) or 0)
        end = float((c.get("end") if isinstance(c, dict) else getattr(c, "end", 0)) or 0)
        words = _clean(text).split()
        if not words or end <= start:
            continue
        step = (end - start) / len(words)
        for i, w in enumerate(words):
            out.append(TranscribedWord(
                word=w,
                start=round(start + i * step, 3),
                end=round(start + (i + 1) * step, 3),
            ))
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _span_text(phrases: list[dict], first: int, last: int) -> tuple[str, float, float]:
    """The VERBATIM transcript across a phrase span, plus its true timing.

    This is the grounding chokepoint: card copy is rebuilt here from the
    transcript, so whatever the model wrote in its `text` field is irrelevant.
    """
    span = [p for p in phrases if first <= p["index"] <= last]
    if not span:
        return "", 0.0, 0.0
    return (
        _clean(" ".join(p["text"] for p in span)),
        float(span[0]["start"]),
        float(span[-1]["end"]),
    )


def _to_lines(text: str) -> list[str]:
    """Break a spoken span into at most two readable card lines, keeping word
    order (it has to still read as the sentence being said)."""
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= _MAX_WORDS_PER_LINE:
        return [" ".join(words)]
    half = (len(words) + 1) // 2
    lines = [" ".join(words[:half]), " ".join(words[half:])]
    return [ln for ln in lines if ln][:_MAX_LINES]


# ── the model's part ─────────────────────────────────────────────────

_SYSTEM = (
    "You are the editor of a short-form vertical reel. You are given the "
    "speaker's transcript as NUMBERED PHRASES with timings.\n\n"
    "Choose the moments that deserve cutting away from the talking head to a "
    "designed graphic card. A card should land where the words alone are weak "
    "but the IDEA is strong: a number, a named thing, a list item, a hard "
    "claim, a turn in the argument. Do NOT card the greeting, filler, or a "
    "sentence still setting up.\n\n"
    "Card styles:\n"
    "  stat    — a number or quantity that deserves the whole frame\n"
    "  label   — one short named thing (a category, a step, a service)\n"
    "  quote   — a hard claim or turn, held as a statement\n"
    "  collage — a scene-setting aside; a supporting visual idea\n"
    "  profile — a person or account being named\n\n"
    "Return STRICT JSON: {\"cards\": [{\"first_phrase\": <int>, "
    "\"last_phrase\": <int>, \"style\": \"stat|label|quote|collage|profile\", "
    "\"why\": \"<8 words: why this moment>\"}]}\n\n"
    "Rules: spans are SHORT (1-3 phrases). Never overlap spans. Never pick "
    "consecutive spans — the speaker must return between cards. Prefer FEWER, "
    "better moments over hitting a quota. You do not write copy: the card "
    "shows the speaker's own words, so choose the span, not the wording."
)


async def _ask_model(phrases: list[dict], target: int, brand_note: str) -> list[dict]:
    from .llm import get_llm

    listing = "\n".join(
        f'{p["index"]}. [{p["start"]:.1f}-{p["end"]:.1f}s] {p["text"]}'
        for p in phrases
    )
    body = (
        (f"BRAND CONTEXT: {brand_note}\n\n" if brand_note else "")
        + f"Pick AT MOST {target} moments.\n\nPHRASES:\n{listing}"
    )
    try:
        out = await get_llm().complete_json(
            system=_SYSTEM,
            messages=[{"role": "user", "content": body}],
            max_tokens=900, temperature=0.2,
        )
    except Exception:  # noqa: BLE001 — a director failure must not kill a render
        return []
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except ValueError:
            return []
    cards = (out or {}).get("cards") if isinstance(out, dict) else None
    return [c for c in (cards or []) if isinstance(c, dict)]


# ── plan assembly + the rules the model doesn't get to bend ──────────

def _target_count(duration: float) -> int:
    if duration <= 0:
        return 0
    return max(1, min(MAX_CARDS, int(duration // SECONDS_PER_CARD)))


def enforce_pacing(cards: list[Card], duration: float) -> list[Card]:
    """Apply the pacing rules to whatever the model proposed: clamp durations,
    drop anything in the opening hook, enforce the gap between cards, and cap
    the count. Pure and deterministic, so it's testable on its own."""
    kept: list[Card] = []
    for c in sorted(cards, key=lambda x: x.start):
        if c.start < LEAD_IN_S:
            continue                                  # never cut away in the hook
        if duration and c.end > duration:
            c.end = round(duration, 3)
        if c.duration < MIN_CARD_S:
            continue
        if c.duration > MAX_CARD_S:
            c.end = round(c.start + MAX_CARD_S, 3)
        if kept and c.start - kept[-1].end < MIN_GAP_S:
            continue                                  # the speaker must come back
        kept.append(c)
        if len(kept) >= MAX_CARDS:
            break
    return kept


async def plan_cards(
    words: list[TranscribedWord],
    *,
    duration: float = 0.0,
    brand_note: str = "",
    styles: list[str] | None = None,
) -> list[Card]:
    """Timed transcript → the cards to cut in. Returns [] when there's nothing
    worth carding, which is a valid answer — a reel of nothing but cutaways
    isn't the format."""
    phrases = split_phrases(words)
    if not phrases:
        return []
    duration = duration or float(phrases[-1]["end"])
    allowed = {s for s in (styles or CARD_STYLES) if s in CARD_STYLES} or set(CARD_STYLES)

    proposals = await _ask_model(phrases, _target_count(duration), brand_note)
    last_index = phrases[-1]["index"]

    cards: list[Card] = []
    used: set[int] = set()
    for p in proposals:
        try:
            first = int(p.get("first_phrase"))
            last = int(p.get("last_phrase", first))
        except (TypeError, ValueError):
            continue
        if first > last:
            first, last = last, first
        if first < 0 or last > last_index:
            continue
        if any(i in used for i in range(first, last + 1)):
            continue                                   # no overlapping spans
        style = str(p.get("style") or "").strip().lower()
        if style not in allowed:
            style = "quote"                            # a real style, never a broken one

        # GROUNDING: copy and timing come from the transcript, not the model.
        text, start, end = _span_text(phrases, first, last)
        if not text:
            continue
        lines = _to_lines(text)
        if not lines:
            continue

        used.update(range(first, last + 1))
        cards.append(Card(style=style, start=start, end=end, lines=lines))

    return enforce_pacing(cards, duration)


def plan_summary(cards: list[Card]) -> list[dict]:
    """A plan rendered for a human (or an API response) — what lands where."""
    return [
        {
            "style": c.style,
            "start": round(c.start, 2),
            "end": round(c.end, 2),
            "seconds": c.duration,
            "lines": list(c.lines),
        }
        for c in cards
    ]


__all__ = [
    "plan_cards", "plan_summary", "split_phrases", "enforce_pacing",
    "words_from_captions",
    "MIN_CARD_S", "MAX_CARD_S", "MIN_GAP_S", "MAX_CARDS", "SECONDS_PER_CARD",
]
