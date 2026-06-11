"""Story-video mode — caption-pinned B-roll from a HeyGen voiceover.

Pipeline (the `story_audio` production mode):

    script
      ↓ HeyGen renders avatar video (we only want the audio track from it)
    avatar.mp4
      ↓ ffmpeg strips the audio track
    voice.mp3
      ↓ Whisper with word-level timestamps
    [TranscribedWord, …]
      ↓ heuristic sentence-aware chunking, ~4s per beat
    [Beat(start, end, text), …]
      ↓ one LLM call for visual prompts of every beat
    [Beat with .image_prompt, …]
      ↓ gpt-image-1 in parallel, one still per beat
    [Beat with .image_url (Supabase Storage), …]
      ↓ Creatomate source: audio on track 1, stills on track 2 with
        subtle Ken-Burns zoom, word-pinned captions on track 3, optional
        music underneath
    final.mp4

The audio is the timing source of truth — every image pins to exact
word timestamps from Whisper. No drift. No "the planner said 5s but the
speech took 7s" gap.

Honest scope:
  * We pay for one HeyGen video render even though we only use the
    audio. Cheaper alternative is HeyGen's TTS-only endpoint or
    ElevenLabs — flagged future swap, not silently faked.
  * gpt-image-1 can still go uncanny on close-up faces. The visual
    prompts steer toward environments and objects; for a real-estate
    brand that's the right bias anyway.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .audio_trim import extract_audio_mp3
from .config import settings
from .imagegen import POST_STYLES, generate_post_image
from .llm import get_llm
from .media import storage as media_storage
from .transcription import TranscribedWord, transcribe_words

# Targets for beat segmentation. Tuned for TikTok/Reels — 3-6 seconds
# per still keeps the visual pace fast enough to feel like a story, not
# a slideshow, while leaving room for a sentence to land.
_TARGET_BEAT_SECONDS = 4.0
_MIN_BEAT_SECONDS = 2.0
_MAX_BEAT_SECONDS = 7.0
# Cap total beats so a long script doesn't blow up image-gen cost.
_MAX_BEATS = 18

_HTTP_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


@dataclass
class Beat:
    index: int
    start: float                       # seconds into the audio
    end: float                         # seconds into the audio
    text: str                          # spoken words in this beat
    image_prompt: str = ""             # LLM-generated visual idea
    image_url: str | None = None       # Supabase Storage URL once rendered
    image_error: str = ""              # honest marker if a beat failed
    uses_hero: bool = False            # LLM-tagged "the hero is the subject"
                                       # of this beat; routes image gen
                                       # through the reference-photo path
                                       # in imagegen.generate_post_image_with_refs.
    # ── avatar_story_mix mode only ──
    role: str = "broll"                # "avatar" (James on camera) or "broll"
    role_reason: str = ""              # LLM's one-line why
    video_url: str | None = None       # silent HeyGen slice URL when role=avatar
    video_error: str = ""


@dataclass
class StoryAudioResult:
    audio_url: str                     # durable mp3 URL
    audio_duration: float              # total seconds
    beats: list[Beat]
    captions: list[dict] = field(default_factory=list)  # [{start,end,text}]
    error: str = ""


# ── audio sourcing ────────────────────────────────────────────────────


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


async def _strip_to_mp3(video_bytes: bytes) -> bytes | None:
    """Run ffmpeg in a temp dir. Returns the MP3 bytes or None if ffmpeg
    failed (caller decides how to surface the error to the production)."""
    with tempfile.TemporaryDirectory() as td:
        in_path = f"{td}/in.mp4"
        out_path = f"{td}/out.mp3"
        Path(in_path).write_bytes(video_bytes)
        ok = await extract_audio_mp3(in_path, out_path)
        if not ok:
            return None
        try:
            return Path(out_path).read_bytes()
        except OSError:
            return None


async def persist_audio(
    audio_bytes: bytes, label: str, tenant_id: str | None = None
) -> str | None:
    """Push MP3 bytes to Supabase Storage and return the public URL.
    Creatomate needs a fetchable URL — local /media-files paths don't
    reach their CDN."""
    try:
        url, _ = await asyncio.to_thread(
            media_storage().save,
            tenant_id or str(settings.default_tenant_id),
            audio_bytes,
            f"{label}.mp3",
        )
        return url
    except Exception:  # noqa: BLE001
        return None


# ── beat segmentation ─────────────────────────────────────────────────


_SENT_END = re.compile(r"[.!?…]$")


def _is_sentence_end(token: str) -> bool:
    # Whisper word tokens include trailing punctuation in some langs.
    return bool(_SENT_END.search(token.strip()))


def segment_beats(words: list[TranscribedWord]) -> list[Beat]:
    """Chunk a word stream into 8-18 visual beats.

    Two pressures balance each other:
      * prefer to cut at sentence boundaries (so a caption never breaks
        mid-thought)
      * keep each beat between MIN and MAX seconds (so the slideshow
        feels like a story, not a strobe or a still)

    Algorithm:
      1. Walk the words. Open a beat; accumulate words until either
         we hit a sentence end AND have ≥ TARGET_BEAT_SECONDS, OR we
         pass MAX_BEAT_SECONDS regardless of punctuation.
      2. If after step 1 we ended up with > _MAX_BEATS beats, greedily
         merge the shortest adjacent pair until we're under the cap.

    Returns beats with index/start/end/text filled; image_prompt and
    image_url stay empty until the next pipeline stage.
    """
    if not words:
        return []
    beats: list[Beat] = []
    bs = words[0].start
    bw: list[str] = []
    for i, w in enumerate(words):
        bw.append(w.word)
        elapsed = w.end - bs
        last = i == len(words) - 1
        cut = (
            last
            or (_is_sentence_end(w.word) and elapsed >= _TARGET_BEAT_SECONDS)
            or elapsed >= _MAX_BEAT_SECONDS
        )
        if cut:
            beats.append(Beat(
                index=len(beats),
                start=round(bs, 3),
                end=round(w.end, 3),
                text=_clean_text(" ".join(bw)),
            ))
            if not last:
                bs = words[i + 1].start
                bw = []

    # Coalesce tiny tail beats with their predecessor (e.g. a trailing
    # "Right?" of < MIN_BEAT_SECONDS would otherwise flash for 0.4s).
    coalesced: list[Beat] = []
    for b in beats:
        if (
            coalesced
            and (b.end - b.start) < _MIN_BEAT_SECONDS
        ):
            prev = coalesced[-1]
            prev.end = b.end
            prev.text = _clean_text(f"{prev.text} {b.text}")
            continue
        coalesced.append(b)

    # Enforce the beat-count cap (image-gen cost guard). Merge the
    # shortest adjacent pair repeatedly until we're under the cap.
    while len(coalesced) > _MAX_BEATS:
        # Find shortest adjacent pair by combined duration
        shortest = 0
        shortest_dur = float("inf")
        for i in range(len(coalesced) - 1):
            d = coalesced[i + 1].end - coalesced[i].start
            if d < shortest_dur:
                shortest_dur = d
                shortest = i
        a = coalesced[shortest]
        b = coalesced[shortest + 1]
        a.end = b.end
        a.text = _clean_text(f"{a.text} {b.text}")
        del coalesced[shortest + 1]

    # Re-index after the merges so the caller sees 0..N-1.
    for i, b in enumerate(coalesced):
        b.index = i
    return coalesced


_WS = re.compile(r"\s+")


def _clean_text(s: str) -> str:
    return _WS.sub(" ", s).strip()


# ── caption lines pinned to word groups ───────────────────────────────


# Common English stopwords + filler — words that should NEVER be the
# emphasized one in a flash. The emphasis word is usually a noun or a
# strong verb; never "the" or "and". Conservative list — we lean on
# longest-word ties to break a phrase like "I felt obligated" toward
# "obligated".
_CAPTION_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "you", "your", "my", "we", "i", "is", "was", "are",
    "were", "be", "been", "will", "would", "can", "could", "do", "did",
    "does", "this", "that", "it", "what", "when", "where", "who", "how",
    "as", "by", "from", "if", "so", "not", "no", "yes", "than", "then",
    "into", "out", "up", "down", "more", "less", "they", "them", "their",
    "he", "she", "him", "her", "his", "hers", "us", "our", "ours",
    "have", "has", "had", "just", "very", "too", "only", "also", "still",
    "only", "any", "all", "some", "every",
})


def _emphasize_in_phrase(text: str) -> str:
    """Return `text` with the strongest content word uppercased.

    Heuristic: longest non-stopword token wins. Ties → first occurrence.
    Single-word phrases get the whole word uppercased (still reads as
    emphasis vs. surrounding lowercase flashes). Stripped of common
    edge punctuation when comparing length so "calendar." and
    "calendar" tie length-wise.

    Examples:
      'decades come off the calendar' → 'decades come off the CALENDAR'
      'so loud that'                   → 'so LOUD that'
      'I felt obligated'               → 'i felt OBLIGATED'
    """
    tokens = text.split()
    if not tokens:
        return text
    if len(tokens) == 1:
        return tokens[0].upper()
    best_i = -1
    best_len = 0
    for i, tok in enumerate(tokens):
        bare = "".join(c for c in tok.lower() if c.isalpha() or c == "'")
        if bare in _CAPTION_STOPWORDS:
            continue
        if len(bare) > best_len:
            best_len = len(bare)
            best_i = i
    if best_i < 0:
        # Everything is a stopword (e.g. "but it is") — uppercase the
        # longest token anyway so something pops.
        best_i = max(range(len(tokens)), key=lambda i: len(tokens[i]))
    tokens[best_i] = tokens[best_i].upper()
    return " ".join(tokens)


def caption_lines(
    words: list[TranscribedWord], group: int = 3
) -> list[dict]:
    """Group words into 2-3-word caption flashes (TikTok-style) pinned
    to the actual spoken-word timestamps, with the strongest content
    word in each flash rendered in ALL CAPS for visible emphasis.

    Each entry is {start, end, text, emphasis_word}.

    Reference videos use 2-3 words per flash; 4 felt cluttered. The
    emphasis-word treatment matches the pattern in the references
    ("decades come off the CALENDAR", "so LOUD that") without requiring
    Creatomate's multi-style inline text.
    """
    out: list[dict] = []
    if not words:
        return out
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        raw_text = _clean_text(" ".join(w.word for w in chunk))
        out.append({
            "start": round(chunk[0].start, 3),
            "end": round(chunk[-1].end, 3),
            "text": _emphasize_in_phrase(raw_text),
            # Preserve the unstyled text in case any downstream wants
            # to render its own emphasis pattern.
            "raw_text": raw_text,
        })
    return out


# ── visual prompts for each beat (one LLM call) ───────────────────────


_PROMPT_SYSTEM = """You are the art director for a personal brand's
short-form video. The brand voice is being read aloud by an AI clone of
the brand owner; the video plays this audio over a slideshow of
AI-generated still images, one per "beat" of the script.

For each beat I give you, write ONE concrete visual prompt — what
the image SHOWS, not what it SYMBOLIZES. Specific subjects, real-world
settings, an interesting angle when natural. Continuity matters: the
prompts should feel like the same world / same documentary, not 12
random stock photos.

Hard rules:
  * Single clear focal point. Uncluttered. Plenty of negative space.
  * No text, no captions, no logos in the image (we burn captions over
    the top later).
  * No close-up faces unless the beat absolutely demands one.
  * Don't repeat the beat's spoken text — describe what the viewer SEES.
  * Each prompt is one sentence, 12-30 words.

Return STRICT JSON:
{"beats": [{"index": int, "prompt": str}, ...]}
The list must have exactly one entry per input beat, in the same order,
with the same `index`.
"""


_CINEMATIC_PROMPT_SYSTEM = """You are the cinematographer + editor for
a short documentary-style reel. The voiceover is being read by an AI
clone of the brand's hero; the reel plays that audio over a sequence
of AI-generated cinematic stills, one per beat of the script. Your job
is to choose the SHOT for every beat so the reel feels like ONE film
the audience cannot look away from.

— STORY ARC (apply across the beats) —

Treat the beats as a three-act structure:
  Act 1 (first 1-2 beats)   STAKES.  Establish the cost / problem.
  Act 2 (middle beats)      TENSION. Visualize the struggle, the choice,
                                     the comparison, the antagonist.
  Act 3 (last 1-2 beats)    LANDING. The lesson, the symbol of mastery,
                                     the quiet moment of conviction.

Every beat should ADVANCE the arc. A beat that just repeats the
previous shot's emotional register is a missed beat — re-imagine it.

— SHOT VOCABULARY (reach for, in order of impact) —

  HERO INSERTS (when present)
    The brand's hero may appear in beats that are about HIM — what he
    saw, what he did, where he was standing. Use sparingly: 1-2 hero
    beats per reel maximum, ideally Act 1 (setting his stakes) or Act
    3 (the moment of conviction). When you place a hero shot, you MUST
    reference the hero description in the prompt verbatim so the
    character stays consistent.

  OBJECT SYMBOLS
    A ledger with red ink. A brass key on a contract. A signed deed.
    An empty wooden chair. A clock at 4 AM. Calendar pages mid-air.
    These are the most reliable beats — pick a SINGLE object that
    embodies the line.

  PLACE SYMBOLS
    An empty boardroom at night. A foggy crossroads. A wood-paneled
    office with one lamp on. A skyline through rain on a window.
    Sense of where the story lives.

  ATMOSPHERE SHOTS
    Light beams through dust. Paper in motion. A coffee mug going cold.
    Use to underscore a quiet beat or transition.

— STRONG WORKED EXAMPLES (study them) —

  beat: "Most investors miss this market"
    → close-up of a leather-bound ledger on a dim mahogany desk, one
      lamp casting hard side light, red ink crawling down a column,
      a brass pen abandoned across the figures
  beat: "I watched my mentor walk away from a hundred deals"
    → mid-shot of the HERO from behind, standing in a dim wood-paneled
      brokerage office, hand reaching for a stack of contracts he is
      sliding away, hard rim light from a window
  beat: "the deal that broke him"
    → close-up of a brass key resting on a torn contract, harsh side
      light, shallow depth, single bead of condensation beside it
  beat: "no one is coming to save you"
    → a single empty wooden chair under a hanging Edison bulb in a
      vast dark hall, hard top-down light, dust suspended in the beam
  beat: "the quiet decision you make alone at 4 AM"
    → wall clock in deep shadow, hands at 4:00, hard top-down light
      catching a single bead of condensation on the brass case
  beat: "the next forty years are decided right now"
    → calendar pages tearing through a dim concrete room, mid-air,
      swirling dust caught in a hard overhead spotlight

— CONTINUITY (non-negotiable) —

Every prompt MUST share the same film: same desaturated cool color
grading (deep blues, muted browns, occasional warm rim), same lighting
vocabulary (hard directional spot / side / top-down / window beam),
same scale (close-ups and mid-shots, never wide aerial / drone).

— HARD RULES —

  * One symbolic subject per beat, hero/close-up/mid framed.
  * ALWAYS name the lighting direction and quality.
  * ALWAYS name the mood (gravitas, weight, stillness, foreboding).
  * Atmospheric detail (dust, smoke, beams, condensation, paper in
    motion, raindrops) when it fits the beat.
  * NO text/logos/captions in the image — we burn captions over later.
  * NO close-up faces unless the beat is about a person's face.
  * When you place a hero shot, reference the hero description.
  * 22-40 words per prompt — these are dense, not generic.

Return STRICT JSON:
{"beats": [{"index": int, "prompt": str, "uses_hero": bool}, ...]}
Exactly one entry per input beat, same order, same `index`.
`uses_hero` true when the prompt features the hero, false otherwise.
"""


_CAPTION_PICK_SYSTEM = """You are the editor for a short-form social
video. Given the script and the target platform, pick the caption preset
that best matches the energy and the audience. Choices and when to use:

  * viral_hook      — the script OPENS with a strong claim, question, or
                      promise ("how to…", "the truth about…", a warning).
                      Renders that hook as a huge stacked title (key line
                      yellow) then drops to small clean captions. Best
                      default for hook-driven educational reels.
  * magenta_blocks  — bold declarations, hot takes, contrarian statements
                      ("X will not replace Y"). Street-interview /
                      conference energy; statements land as solid color
                      blocks. Great for opinion + market-call scripts.
  * editorial_serif — premium announcement or launch ("the first…",
                      "introducing…"), luxury-listing or magazine tone.
                      Yellow italic serif title card; elegant, not loud.
  * gradient_mint   — calm premium-ad voice, aspirational lifestyle copy,
                      short sentences that breathe ("Sales reps don't
                      need more tools"). Scattered mint phrases.
  * tiktok_yellow   — high-energy hook, fast-paced, listicle-style,
                      anything that punches in the first 2 seconds.
                      TikTok + Reels first-feed material.
  * highlight_box   — busy or bright B-roll-heavy reels where legibility
                      is at risk: text on a solid dark pill reads on ANY
                      footage. Modern, premium, safe.
  * karaoke_green   — same energy as tiktok_yellow but for outdoor /
                      real-estate-exterior footage where yellow washes
                      out against sunlight or brick.
  * bold_pop        — universal safe choice: Mr Beast / Hormozi look.
                      Use for any reel that doesn't clearly call for
                      another style. Default when you're unsure.
  * clean_white     — thoughtful, narrative, story arc. The "decades
                      come off the calendar" type beat. Best for IG
                      Reels with an emotional turn or Substack-cross.
  * subtle_minimal  — LinkedIn, institutional, B2B, calm authority.
                      Captions accessibility-grade — script does the
                      heavy lifting.
  * branded_red     — when the post is explicitly PreReal / James-brand
                      signature; use sparingly, only when the script
                      mentions PreReal or the visual call is for a
                      brand-stamp look.

Pick for THIS script's content and opening line, not by habit — e.g. a
script opening with "How to…" wants viral_hook; a blunt market take wants
magenta_blocks; a luxury listing tour wants editorial_serif.

If the payload includes an `avoid` block, it lists caption choices /
positions a human has already REJECTED on past renders. Treat it as a
hard constraint — do not pick a style or describe a placement the avoid
block warns against.

Return STRICT JSON: {"caption_style": str, "reason": str}
The reason is one short clause (≤ 14 words).
"""


async def pick_caption_style(
    script: str, platform: str, brand_context: str = "",
    avoid: str = "",
) -> tuple[str, str]:
    """One LLM call → (preset_name, reason). Honest fallback: returns
    the default preset on any failure so a production never crashes
    over caption styling.

    `avoid` — a video_feedback.video_avoid_block() string of past human
    rejections (caption + general tags). Passed as its own payload field
    so it survives brand_context truncation and steers the picker away
    from styles/positions a human already rejected. Empty = no steering.
    """
    from .caption_styles import CAPTION_PRESETS, DEFAULT_CAPTION_STYLE
    payload = {
        "platform": platform,
        "brand_context": brand_context[:400],
        "avoid": avoid[:800],
        "script": (script or "")[:1500],
        "available": list(CAPTION_PRESETS.keys()),
    }
    try:
        out = await get_llm().complete_json(
            system=_CAPTION_PICK_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=120, temperature=0.2,
        )
    except Exception:  # noqa: BLE001
        return DEFAULT_CAPTION_STYLE, "LLM picker failed; using default"
    name = str(out.get("caption_style") or "").strip().lower()
    if name not in CAPTION_PRESETS:
        return DEFAULT_CAPTION_STYLE, f"LLM returned unknown '{name}'; default"
    reason = str(out.get("reason") or "")[:140]
    return name, reason


_CLASSIFY_SYSTEM = """You are the director for a short-form social video.
The voiceover is a personal brand owner speaking — you choose, beat by
beat, whether the viewer should SEE that person speaking on camera, or
should see a B-roll still that visualizes what they're saying.

Rule of thumb:
  * AVATAR  — first-person declarations, emotional beats, the hook
    that sets the stakes, the close/CTA where eye-contact lands the
    promise. Anywhere the speaker IS the visual.
  * BROLL — facts, numbers, locations, comparisons, anything the
    viewer benefits from seeing literally (a street, a building, a
    chart concept, an object). The visual carries information the
    voice alone can't.

Practical tilt: a 30-60s reel usually opens with 1 avatar beat, has
2-4 broll beats in the middle, closes with 1 avatar beat. Don't paint
by numbers — follow the script — but expect that shape.

Return STRICT JSON:
{"beats": [{"index": int, "role": "avatar"|"broll", "reason": str (one short clause, &lt;=12 words)}, ...]}
Exactly one entry per input beat, same order, same `index`.
"""


async def classify_beats(beats: list[Beat], brand_context: str) -> None:
    """Mutates `beats` in place — fills each beat's `role` + `role_reason`.

    Honest fallback: if the LLM call fails or omits a beat, that beat
    keeps the default role of 'broll'. The pipeline still produces a
    usable video (it just falls back to story_audio behavior).
    """
    if not beats:
        return
    payload = {
        "brand_context": brand_context[:600],
        "beats": [{"index": b.index, "text": b.text} for b in beats],
    }
    try:
        out = await get_llm().complete_json(
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=900, temperature=0.3,
        )
    except Exception:  # noqa: BLE001
        return
    by_idx = {}
    for entry in (out.get("beats") or []):
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry["index"])
        except (KeyError, TypeError, ValueError):
            continue
        role = str(entry.get("role") or "").strip().lower()
        if role not in ("avatar", "broll"):
            continue
        by_idx[idx] = (role, str(entry.get("reason") or "").strip()[:120])
    for b in beats:
        if b.index in by_idx:
            b.role, b.role_reason = by_idx[b.index]


# ── engaging_avatar mode ──────────────────────────────────────────────


@dataclass
class Insert:
    """A B-roll cutaway window inside an engaging_avatar render.

    Each insert overlays the avatar video for [start, end] seconds.
    Two output paths:
      * image_url — static still from gpt-image-1 (cheap, no motion)
      * video_url — Runway-animated 5s clip seeded by the still
        (real motion, ~$0.50 each). When video_url is present, the
        Creatomate source uses it as a video element instead of the
        still — the avatar's audio keeps playing under either way.
    """
    index: int
    start: float
    end: float
    text: str                          # phrase that triggered this insert
    image_prompt: str = ""
    image_url: str | None = None
    image_error: str = ""
    uses_hero: bool = False
    video_url: str | None = None       # Runway image-to-video output
    video_error: str = ""


def inserts_to_dict(inserts: list[Insert]) -> list[dict[str, Any]]:
    return [
        {
            "index": i.index, "start": i.start, "end": i.end,
            "text": i.text, "image_prompt": i.image_prompt,
            "image_url": i.image_url, "image_error": i.image_error,
            "uses_hero": i.uses_hero,
            "video_url": i.video_url, "video_error": i.video_error,
        }
        for i in inserts
    ]


_INSERT_PICK_SYSTEM = """You are the editor for a short-form video.
The hero is on camera the whole time, talking. Your job is to drop
one cinematic B-roll cutaway in EVERY slot you're given — no skipping
slots, no extras. Each insert lasts 1.5-2.0 seconds and the image
must visualize the SPECIFIC PHRASE spoken in that 1.5-2.0s window.

You'll receive a list of slots. Each slot has:
  * slot_start, slot_end  — the 5s window you're editing inside
  * words[]               — the words spoken in this slot with their
                            individual {t: float, w: str} timestamps

For each slot, return ONE insert:
  * Pick start INSIDE the slot, between (slot_start + 0.4) and
    (slot_end - 1.8). Land it on a word that names a place / object /
    number / dramatic concept when one exists in the slot; otherwise
    pick the most CONCRETE word in the slot (a verb of action, a
    proper noun, anything visualisable). Avoid pure connector words
    ('and', 'so', 'but') as the anchor.
  * end = start + 1.7 typically. Always within the slot bounds.
  * prompt: ONE concrete cinematic image prompt, 18-30 words, that
    paints WHAT THE ANCHOR WORD/PHRASE describes — not the script as
    a whole. Single symbolic subject, dramatic directional light, deep
    shadows, atmospheric detail. NEVER text/captions in the image.
  * uses_hero: true ONLY when the anchor word/phrase is about the
    brand hero himself (e.g. "I watched my mentor" = uses_hero,
    because the hero IS the mentor figure; "the calendar" = no).
  * text: short label of the anchor word(s), max 4 words.

CRITICAL: Every slot gets exactly one insert. Even an "abstract" slot
("conviction is not loud") needs ONE — pick a metaphorical image like
"a single Edison bulb glowing alone in a dark room". Never return
fewer entries than slots.

If the payload includes an `avoid` block, it lists B-roll behaviours a
human has already REJECTED on past renders (e.g. inserts too short,
uncanny close-ups, wrong vibe). Treat every line as a hard rule — your
inserts must NOT repeat any of those mistakes.

Return STRICT JSON:
{"inserts": [{"slot": int, "start": float, "end": float,
              "text": str, "prompt": str, "uses_hero": bool}, ...]}
The slot field is the 0-indexed slot the insert is for. Same order
as the slots in the payload.
"""

# Cadence in seconds — one cutaway per slot of this size. 5s means a
# 30s reel gets ~5 inserts (one per slot). User-configurable later via
# a per-render field if they want to tune; the default matches the
# explicit ask ("every 5 seconds").
_INSERT_CADENCE_S = 5.0
_INSERT_MIN_DUR = 1.5
_INSERT_MAX_DUR = 2.0
# Lead-in pad: first slot starts at this offset so viewers see James
# begin before any cut. 3s = roughly long enough to register the
# speaker (~10 words at typical pace) without burning the whole first
# slot of cadence.
_INSERT_LEAD_IN_S = 3.0
# Tail safety zone — keep this much of the end on the avatar so the
# reel closes on him. 0.4s is the smallest we can go without cutting
# off the final spoken word.
_INSERT_TAIL_S = 0.4


async def pick_insert_points(
    *,
    audio_duration: float,
    words: list[TranscribedWord],
    brand_context: str,
    hero_description: str = "",
    avoid: str = "",
    cadence_s: float = _INSERT_CADENCE_S,
    min_dur: float = _INSERT_MIN_DUR,
    max_dur: float = _INSERT_MAX_DUR,
) -> list[Insert]:
    """Word-anchored dense cutaway picker.

    Slices the audio into `cadence_s` slots starting at _INSERT_LEAD_IN_S
    (so the viewer sees James start). For each slot, the LLM picks
    one 1.5-2.0s insert + prompt that visualizes the SPECIFIC phrase
    spoken in that window — not the script overall. This matches the
    'one cut every 5s, tied to the words right now' format the user
    asked for, replacing the earlier sparse 2-5-discretion picker.

    Empty list when:
      * audio is too short for at least one slot;
      * the LLM call fails;
      * every returned insert fails sanity validation.
    """
    # Live-tunable (feedback loop): shadow the module defaults with the
    # per-render values so the existing slot-fitting math below uses them.
    _INSERT_MIN_DUR = min_dur
    _INSERT_MAX_DUR = max_dur
    if not words or audio_duration <= _INSERT_LEAD_IN_S + _INSERT_MIN_DUR:
        return []

    # Build slot list. Slot is viable when it can fit MIN_DUR of insert
    # before audio_duration - TAIL. We allow the LAST slot to be
    # narrower than cadence_s (down to MIN_DUR) so the tail of the
    # script still gets one cutaway when there's room.
    slots: list[dict] = []
    t = _INSERT_LEAD_IN_S
    slot_idx = 0
    latest_end = audio_duration - _INSERT_TAIL_S
    while t + _INSERT_MIN_DUR <= latest_end:
        slot_end = min(t + cadence_s, latest_end)
        slot_words = [
            {"t": round(w.start, 2), "w": w.word}
            for w in words
            if w.start >= t and w.start < slot_end
        ]
        # An empty slot means the speaker is silent — skip so we don't
        # paint over a pause. Rare in normal scripts.
        if slot_words:
            slots.append({
                "slot": slot_idx,
                "slot_start": round(t, 2),
                "slot_end": round(slot_end, 2),
                "words": slot_words,
            })
            slot_idx += 1
        t += cadence_s

    if not slots:
        return []

    payload = {
        "brand_context": brand_context[:600],
        "hero_description": hero_description[:400] if hero_description else "",
        "avoid": avoid[:800],
        "audio_duration": round(audio_duration, 2),
        "slots": slots,
    }
    try:
        out = await get_llm().complete_json(
            system=_INSERT_PICK_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            # Bump the ceiling — denser cadence = more inserts =
            # more prompt text returned.
            max_tokens=2400, temperature=0.4,
        )
    except Exception:  # noqa: BLE001
        return []

    by_slot: dict[int, dict] = {}
    for entry in (out.get("inserts") or []):
        if not isinstance(entry, dict):
            continue
        try:
            s_idx = int(entry.get("slot", -1))
        except (TypeError, ValueError):
            continue
        if s_idx < 0:
            continue
        by_slot[s_idx] = entry

    inserts: list[Insert] = []
    last_end = -1e9
    for s in slots:
        entry = by_slot.get(s["slot"])
        if entry is None:
            continue
        try:
            start = float(entry.get("start", 0.0))
            end = float(entry.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        # Clamp start/end to the slot's window so a hallucinated
        # timestamp can't escape its lane.
        start = max(start, float(s["slot_start"]) + 0.3)
        end = min(end, float(s["slot_end"]),
                  audio_duration - _INSERT_TAIL_S)
        dur = end - start
        if dur < _INSERT_MIN_DUR:
            # Renormalize: pin to slot_start + lead, extend to MIN_DUR
            start = float(s["slot_start"]) + 0.3
            end = min(start + _INSERT_MIN_DUR,
                      audio_duration - _INSERT_TAIL_S)
            dur = end - start
        if dur > _INSERT_MAX_DUR:
            end = start + _INSERT_MAX_DUR
            dur = _INSERT_MAX_DUR
        if dur < _INSERT_MIN_DUR:
            continue
        prompt = str(entry.get("prompt") or "").strip()
        if not prompt:
            continue
        # Minimum spacing between inserts so cuts don't pile up at slot
        # boundaries when the LLM's start drifts late in one slot and
        # early in the next.
        if start < last_end + 0.4:
            start = last_end + 0.4
            end = min(start + dur, float(s["slot_end"]),
                      audio_duration - _INSERT_TAIL_S)
            if end - start < _INSERT_MIN_DUR:
                continue
        inserts.append(Insert(
            index=len(inserts),
            start=round(start, 3),
            end=round(end, 3),
            text=str(entry.get("text") or "")[:140],
            image_prompt=prompt,
            uses_hero=bool(entry.get("uses_hero")),
        ))
        last_end = end
    return inserts


# Runway gen4_turbo only supports a fixed ratio set; map our aspect.
_RUNWAY_INSERT_RATIO = {
    "9:16": "720:1280", "16:9": "1280:720", "1:1": "960:960",
}
# Runway's minimum image-to-video duration is 5s. Our insert windows
# are 1.5-2s, so we generate 5s and Creatomate trims via the element's
# `duration` field. No re-encode pass needed.
_RUNWAY_INSERT_DURATION = 5
# Polling parameters per Runway task — same defaults as the older
# mixed-mode broll renderer in video_pipeline.py.
_RUNWAY_POLL_EVERY = 5.0
_RUNWAY_MAX_POLLS = 36                  # 3 minutes per insert ceiling


async def animate_inserts(
    inserts: list[Insert],
    aspect: str,
    tenant_id: str,
    *,
    concurrency: int = 2,
    engine: str = "",
) -> None:
    """Chain each insert's still through image-to-video so the cutaway has
    real motion instead of just Creatomate's Ken Burns.

    `engine` picks the B-roll animator per render: '' = the global default
    provider, 'runway' = Runway gen4_turbo, 'higgsfield' = Higgsfield DOP.
    Every template can therefore choose Runway or Higgsfield for its B-roll.

    Mutates each insert in place — fills `video_url` on success, sets
    `video_error` on failure. Honest fallback: when Runway fails or is
    stubbed, the Creatomate source falls back to the static image.

    Concurrency capped at 2 because Runway's gen4_turbo rate-limits
    aggressively above that for the dev plan. Each call is ~30-60s
    end-to-end (submit + poll), so 5 inserts = ~2-3 min wall-clock.
    """
    from .video import get_video_provider, provider_for

    targets = [
        i for i in inserts
        if (i.image_url or "").startswith("http")
    ]
    if not targets:
        return

    # Per-render engine override (Runway / Higgsfield), falling back to the
    # global provider when blank. Honest fail if the chosen engine's keys are
    # missing — the still is kept rather than silently swapped to the other.
    try:
        provider = provider_for(engine) if engine else get_video_provider()
    except ValueError as e:  # keys for the requested engine missing
        for i in targets:
            i.video_error = str(e)
        return
    if provider.name == "stub":
        for i in targets:
            i.video_error = "video B-roll engine not configured (stub)"
        return

    # Each provider carries its own model id; Higgsfield ignores ratio (the
    # seed image fixes the aspect), Runway needs it.
    model = (
        settings.higgsfield_model if provider.name == "higgsfield"
        else settings.runway_model
    )
    ratio = _RUNWAY_INSERT_RATIO.get(aspect, _RUNWAY_INSERT_RATIO["9:16"])
    sem = asyncio.Semaphore(concurrency)

    async def _one(insert: Insert) -> None:
        async with sem:
            # Motion-focused, TOPIC-grounded prompt. The still already fixes
            # the content (it was generated from this insert's script-derived
            # scene), so the prompt's job is to (a) keep the animation ON the
            # same subject and (b) describe natural cinematic motion. Grounding
            # the prompt in the scene text is what makes the B-roll match what
            # is being said at this moment, on Higgsfield and Runway alike.
            scene = (insert.image_prompt or insert.text or "").strip()
            motion_prompt = (
                "Cinematic, subtle, photoreal motion that matches the subject: "
                "a slow camera push-in or gentle parallax, natural movement "
                "within the scene, no morphing, no text. "
                f"Scene: {scene}"
            )[:950]
            sub = await provider.submit(
                motion_prompt,
                insert.image_url,
                model=model,
                ratio=ratio,
                duration=_RUNWAY_INSERT_DURATION,
            )
            if sub.status == "failed":
                insert.video_error = sub.error or f"{provider.name} submit failed"
                return
            poll_url = None
            for _ in range(_RUNWAY_MAX_POLLS):
                await asyncio.sleep(_RUNWAY_POLL_EVERY)
                p = await provider.poll(sub.provider_job_id)
                if p.status == "succeeded":
                    poll_url = p.result_url
                    break
                if p.status == "failed":
                    insert.video_error = p.error or f"{provider.name} render failed"
                    return
            if not poll_url:
                insert.video_error = f"{provider.name} poll timed out"
                return

            # Download from Runway's CDN (jwt-signed, expires fast) and
            # rehost on Supabase so Creatomate has a durable URL for
            # assembly. Same pattern as _persist_clip_to_storage in
            # video_pipeline.py — kept local here so this module isn't
            # cross-coupled to the pipeline worker.
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                    r = await c.get(poll_url, follow_redirects=True)
                    r.raise_for_status()
                    data = r.content
            except Exception as e:  # noqa: BLE001
                insert.video_error = f"download from Runway failed: {e}"
                return
            try:
                url, _ = await asyncio.to_thread(
                    media_storage().save,
                    tenant_id, data,
                    f"insert-vid-{insert.index:02d}-{uuid.uuid4().hex[:8]}.mp4",
                )
                insert.video_url = url
            except Exception as e:  # noqa: BLE001
                insert.video_error = f"storage save failed: {e}"

    await asyncio.gather(*(_one(i) for i in targets))


async def gen_insert_images(
    inserts: list[Insert],
    aspect: str,
    platform: str,
    style: str,
    tenant_id: str,
    *,
    concurrency: int = 4,
    hero_refs: list[tuple[str, bytes]] | None = None,
) -> None:
    """Parallel fill of every insert's image_url. Mirrors gen_beat_images
    but operates on the Insert dataclass and uses the hero-edit path
    when insert.uses_hero is true and hero_refs are present."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(i: Insert) -> None:
        async with sem:
            if not i.image_prompt:
                i.image_error = "no prompt"
                return
            if i.uses_hero and hero_refs:
                from .imagegen import generate_post_image_with_refs
                png, _meta, err = await generate_post_image_with_refs(
                    topic=i.image_prompt,
                    references=hero_refs,
                    platform=platform,
                    brief="",
                    aspect=aspect,
                    style=style,
                )
            else:
                png, _meta, err = await generate_post_image(
                    topic=i.image_prompt,
                    platform=platform,
                    brief="",
                    aspect=aspect,
                    style=style,
                )
            if not png:
                i.image_error = err or "image gen failed"
                return
            try:
                url, _ = await asyncio.to_thread(
                    media_storage().save,
                    tenant_id, png,
                    f"insert-{i.index:02d}-{uuid.uuid4().hex[:8]}.png",
                )
                i.image_url = url
            except Exception as e:  # noqa: BLE001
                i.image_error = f"storage save failed: {e}"

    await asyncio.gather(*(_one(i) for i in inserts))


async def slice_avatar_beats(
    avatar_video_bytes: bytes,
    beats: list[Beat],
    tenant_id: str,
    *,
    concurrency: int = 3,
) -> None:
    """For every beat with role='avatar', cut a silent video slice from
    the cached HeyGen mp4 covering that beat's [start, end] window and
    upload it. Mutates `beats` in place — fills `video_url` or sets
    `video_error` on failure. Silent cuts ensure no audio collision
    with the master voice track on the final compose.
    """
    from .audio_trim import slice_video_silent
    targets = [b for b in beats if b.role == "avatar"]
    if not targets:
        return

    # Write the source mp4 once; every slice reads from the same file.
    with tempfile.TemporaryDirectory() as td:
        src = f"{td}/source.mp4"
        Path(src).write_bytes(avatar_video_bytes)

        sem = asyncio.Semaphore(concurrency)

        async def _one(b: Beat) -> None:
            async with sem:
                out_path = f"{td}/beat-{b.index:02d}.mp4"
                ok = await slice_video_silent(src, out_path, b.start, b.end)
                if not ok:
                    b.video_error = "ffmpeg slice failed"
                    return
                try:
                    data = Path(out_path).read_bytes()
                except OSError as e:
                    b.video_error = f"read sliced mp4 failed: {e}"
                    return
                try:
                    url, _ = await asyncio.to_thread(
                        media_storage().save,
                        tenant_id, data,
                        f"avatar-beat-{b.index:02d}-{uuid.uuid4().hex[:8]}.mp4",
                    )
                    b.video_url = url
                except Exception as e:  # noqa: BLE001
                    b.video_error = f"storage save failed: {e}"

        await asyncio.gather(*(_one(b) for b in targets))


async def write_image_prompts(
    beats: list[Beat],
    brand_context: str,
    style: str,
    *,
    hero_description: str = "",
) -> None:
    """Mutates `beats` in place — fills each beat's `image_prompt`.

    The system prompt branches on `style`:
      * 'cinematic' uses _CINEMATIC_PROMPT_SYSTEM which trains the LLM
        to reach for symbolic objects + dramatic lighting + atmospheric
        detail. Matches the reference videos with the gavel / ballot
        box / calendar-pages aesthetic.
      * Everything else (photoreal, editorial, bw_photo, minimal) uses
        _PROMPT_SYSTEM which describes what the image SHOWS literally.

    `hero_description` — when present, the cinematic system prompt may
    place 1-2 hero-shot beats where the script is about the hero himself
    (his stakes, his decision, his moment of conviction). The hero
    description is injected verbatim into the LLM payload so it can be
    referenced consistently across beats. Empty string = no hero
    context, prompts proceed without any "the hero" references.

    Style branching at the prompt-generation level is intentional —
    POST_STYLES[style] also kicks in at the image-render layer, and
    the two together (prompt + style prefix) is what produces a
    coherent look. Mixing photoreal prompts with cinematic prefix
    gives mediocre output.

    If the LLM call fails, every beat keeps an empty prompt — the caller
    refuses to render any beats and surfaces the error rather than
    paying for 12 random images.
    """
    if not beats:
        return
    system = (
        _CINEMATIC_PROMPT_SYSTEM if style.lower() == "cinematic"
        else _PROMPT_SYSTEM
    )
    payload = {
        "brand_context": brand_context[:600],
        "style_note": POST_STYLES.get(style, POST_STYLES["editorial"])[:240],
        "hero_description": hero_description[:600] if hero_description else "",
        "beats": [
            {"index": b.index, "text": b.text} for b in beats
        ],
    }
    try:
        out = await get_llm().complete_json(
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            max_tokens=1600, temperature=0.5,
        )
    except Exception:  # noqa: BLE001
        return
    by_idx: dict[int, dict] = {}
    for p in (out.get("beats") or []):
        if not isinstance(p, dict) or "index" not in p:
            continue
        try:
            idx = int(p["index"])
        except (TypeError, ValueError):
            continue
        by_idx[idx] = {
            "prompt": str(p.get("prompt") or "").strip(),
            "uses_hero": bool(p.get("uses_hero")),
        }
    for b in beats:
        entry = by_idx.get(b.index)
        if entry is None:
            continue
        b.image_prompt = entry["prompt"]
        b.uses_hero = entry["uses_hero"]


# ── image generation per beat (parallel) ──────────────────────────────


async def _gen_one_beat_image(
    beat: Beat, aspect: str, platform: str, style: str,
    tenant_id: str,
    *,
    hero_refs: list[tuple[str, bytes]] | None = None,
) -> None:
    """Generate, persist, attach. Mutates `beat` in place.

    When `beat.uses_hero` is true AND `hero_refs` is non-empty, the
    image is generated via gpt-image-1's edit endpoint with those
    reference photos as visual conditioning — the recurring person
    appears across all hero beats as the SAME character. Other beats
    use the cheaper text-only generate path.

    A single failure doesn't crash the whole run — the beat keeps an
    empty image_url and the caller can decide whether to fail or render
    with placeholders.
    """
    if not beat.image_prompt:
        beat.image_error = "no prompt"
        return
    if beat.uses_hero and hero_refs:
        from .imagegen import generate_post_image_with_refs
        png, _meta, err = await generate_post_image_with_refs(
            topic=beat.image_prompt,
            references=hero_refs,
            platform=platform,
            brief="",
            aspect=aspect,
            style=style,
        )
    else:
        png, _meta, err = await generate_post_image(
            topic=beat.image_prompt,
            platform=platform,
            brief="",
            aspect=aspect,
            style=style,
        )
    if not png:
        beat.image_error = err or "image gen failed"
        return
    try:
        url, _ = await asyncio.to_thread(
            media_storage().save,
            tenant_id,
            png,
            f"story-beat-{beat.index:02d}-{uuid.uuid4().hex[:8]}.png",
        )
        beat.image_url = url
    except Exception as e:  # noqa: BLE001
        beat.image_error = f"storage save failed: {e}"


async def gen_beat_images(
    beats: list[Beat],
    aspect: str,
    platform: str,
    style: str,
    tenant_id: str,
    *,
    concurrency: int = 4,
    hero_refs: list[tuple[str, bytes]] | None = None,
) -> None:
    """Parallel fill of every beat's image_url. The concurrency cap
    keeps us under OpenAI's images RPM ceiling and from blowing the
    Supabase Storage upload pool at the same time.

    `hero_refs` is fetched once per render (see hero_context.
    get_hero_photo_files) and shared across every beat — hero-tagged
    beats use them via gpt-image-1's edit endpoint."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(b: Beat) -> None:
        async with sem:
            await _gen_one_beat_image(
                b, aspect, platform, style, tenant_id,
                hero_refs=hero_refs,
            )

    await asyncio.gather(*(_one(b) for b in beats))


# ── public entrypoint used by the production pipeline ─────────────────


async def build_story_audio_assets(
    *,
    avatar_video_url: str,
    aspect: str,
    style: str,                        # the image style — photoreal / cinematic / etc
    brand_context: str,
    platform: str = "instagram",
    tenant_id: str | None = None,
) -> StoryAudioResult:
    """End-to-end: HeyGen URL → durable audio + beats + captions, ready
    for the Creatomate story_audio assembler.

    Steps run sequentially because each depends on the previous. The
    only parallelism is gen_beat_images inside the final stage.
    """
    tid = tenant_id or str(settings.default_tenant_id)
    if not avatar_video_url or not avatar_video_url.startswith("http"):
        return StoryAudioResult(
            audio_url="", audio_duration=0.0, beats=[],
            error="story_audio needs a real HeyGen video URL to strip audio from",
        )

    try:
        video_bytes = await _download(avatar_video_url)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult("", 0.0, [], error=f"could not fetch HeyGen video: {e}")

    audio_bytes = await _strip_to_mp3(video_bytes)
    if not audio_bytes:
        return StoryAudioResult("", 0.0, [], error="ffmpeg failed to extract audio")

    audio_url = await persist_audio(audio_bytes, f"story-audio-{uuid.uuid4().hex[:8]}", tid)
    if not audio_url:
        return StoryAudioResult("", 0.0, [], error="could not persist audio to storage")

    try:
        tr = await transcribe_words("voice.mp3", audio_bytes)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult(audio_url, 0.0, [], error=f"whisper failed: {e}")

    if not tr.words:
        return StoryAudioResult(
            audio_url, tr.duration, [],
            error="whisper returned no word timestamps — story_audio needs them",
        )

    beats = segment_beats(tr.words)
    if not beats:
        return StoryAudioResult(
            audio_url, tr.duration, [],
            error="could not segment words into beats",
        )

    # Hero context — when the user uploaded hero_photo assets we both
    # describe the hero (text → into the prompt LLM) AND fetch the
    # photo bytes (binary → into gpt-image-1's edit endpoint per
    # uses_hero-tagged beat). Two layers: the description steers the
    # prompt LLM toward placing hero shots; the bytes lock the face.
    from .hero_context import (
        get_hero_context as _hero_ctx,
        get_hero_photo_files as _hero_files,
    )
    hero_ctx = await _hero_ctx()
    hero_description = hero_ctx.description if hero_ctx else ""
    hero_refs = await _hero_files() if hero_ctx else []

    await write_image_prompts(
        beats, brand_context, style, hero_description=hero_description,
    )
    missing = [b for b in beats if not b.image_prompt]
    if missing:
        return StoryAudioResult(
            audio_url, tr.duration, beats,
            error=f"LLM did not return visual prompts for {len(missing)} beat(s)",
        )

    await gen_beat_images(
        beats, aspect, platform, style, tid, hero_refs=hero_refs,
    )
    failed = [b for b in beats if not b.image_url]
    if failed and len(failed) > len(beats) // 2:
        return StoryAudioResult(
            audio_url, tr.duration, beats,
            error=(
                f"{len(failed)}/{len(beats)} beat images failed — refusing to "
                f"assemble. First reason: {failed[0].image_error}"
            ),
        )

    captions = caption_lines(tr.words)
    return StoryAudioResult(
        audio_url=audio_url,
        audio_duration=tr.duration or (beats[-1].end if beats else 0.0),
        beats=beats,
        captions=captions,
    )


def beats_to_dict(beats: list[Beat]) -> list[dict[str, Any]]:
    """Serialize for storage in video_productions.scenes (jsonb).

    The mixed-mode fields (`role`, `video_url`, etc.) are always
    included — they just stay at their defaults for plain story_audio
    productions, which is harmless on read.
    """
    return [
        {
            "index": b.index, "start": b.start, "end": b.end,
            "text": b.text, "image_prompt": b.image_prompt,
            "image_url": b.image_url, "image_error": b.image_error,
            "uses_hero": b.uses_hero,
            "role": b.role, "role_reason": b.role_reason,
            "video_url": b.video_url, "video_error": b.video_error,
        }
        for b in beats
    ]


async def build_avatar_story_mix_assets(
    *,
    avatar_video_url: str,
    aspect: str,
    style: str,
    brand_context: str,
    platform: str = "instagram",
    tenant_id: str | None = None,
) -> StoryAudioResult:
    """Mixed-mode pipeline.

    Same first six steps as build_story_audio_assets — fetch HeyGen
    mp4, strip + persist audio, Whisper, segment into beats. Then:

      a) classify each beat as 'avatar' or 'broll'
      b) for broll beats: write a visual prompt, generate the still
      c) for avatar beats: slice the HeyGen mp4 to that beat's window
         (silent — the master audio carries the voice)

    The returned StoryAudioResult has beats with mixed `image_url` /
    `video_url` fields that the avatar_story_mix assembler reads.
    """
    tid = tenant_id or str(settings.default_tenant_id)
    if not avatar_video_url or not avatar_video_url.startswith("http"):
        return StoryAudioResult(
            audio_url="", audio_duration=0.0, beats=[],
            error="avatar_story_mix needs a real HeyGen video URL",
        )

    try:
        video_bytes = await _download(avatar_video_url)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult("", 0.0, [], error=f"could not fetch HeyGen video: {e}")

    audio_bytes = await _strip_to_mp3(video_bytes)
    if not audio_bytes:
        return StoryAudioResult("", 0.0, [], error="ffmpeg failed to extract audio")

    audio_url = await persist_audio(
        audio_bytes, f"story-audio-{uuid.uuid4().hex[:8]}", tid
    )
    if not audio_url:
        return StoryAudioResult("", 0.0, [], error="could not persist audio")

    try:
        tr = await transcribe_words("voice.mp3", audio_bytes)
    except Exception as e:  # noqa: BLE001
        return StoryAudioResult(audio_url, 0.0, [], error=f"whisper failed: {e}")
    if not tr.words:
        return StoryAudioResult(
            audio_url, tr.duration, [],
            error="whisper returned no word timestamps",
        )

    beats = segment_beats(tr.words)
    if not beats:
        return StoryAudioResult(audio_url, tr.duration, [], error="no beats")

    # (a) role classification
    await classify_beats(beats, brand_context)

    # (b) prompts + image gen — but only for broll beats. The LLM may
    # see avatar-beat text as context, just doesn't paint for them.
    broll_beats = [b for b in beats if b.role == "broll"]
    if broll_beats:
        from .hero_context import (
            get_hero_context as _hero_ctx,
            get_hero_photo_files as _hero_files,
        )
        hero_ctx = await _hero_ctx()
        hero_description = hero_ctx.description if hero_ctx else ""
        hero_refs = await _hero_files() if hero_ctx else []
        await write_image_prompts(
            broll_beats, brand_context, style,
            hero_description=hero_description,
        )
        missing_prompt = [b for b in broll_beats if not b.image_prompt]
        if missing_prompt:
            return StoryAudioResult(
                audio_url, tr.duration, beats,
                error=f"LLM did not return visual prompts for "
                      f"{len(missing_prompt)} of {len(broll_beats)} broll beats",
            )
        await gen_beat_images(
            broll_beats, aspect, platform, style, tid,
            hero_refs=hero_refs,
        )
        failed_img = [b for b in broll_beats if not b.image_url]
        if failed_img and len(failed_img) > len(broll_beats) // 2:
            return StoryAudioResult(
                audio_url, tr.duration, beats,
                error=(
                    f"{len(failed_img)}/{len(broll_beats)} broll images failed. "
                    f"First reason: {failed_img[0].image_error}"
                ),
            )

    # (c) silent avatar slices for avatar-tagged beats
    await slice_avatar_beats(video_bytes, beats, tid)
    failed_vid = [
        b for b in beats if b.role == "avatar" and not b.video_url
    ]
    avatar_count = sum(1 for b in beats if b.role == "avatar")
    if failed_vid and len(failed_vid) > max(1, avatar_count // 2):
        return StoryAudioResult(
            audio_url, tr.duration, beats,
            error=(
                f"{len(failed_vid)}/{avatar_count} avatar slices failed. "
                f"First reason: {failed_vid[0].video_error}"
            ),
        )

    captions = caption_lines(tr.words)
    return StoryAudioResult(
        audio_url=audio_url,
        audio_duration=tr.duration or (beats[-1].end if beats else 0.0),
        beats=beats,
        captions=captions,
    )


@dataclass
class EngagingAvatarResult:
    """Bundle returned by build_engaging_avatar_assets."""
    avatar_video_url: str              # HeyGen render (full duration)
    audio_url: str                     # extracted mp3 — for captions/Whisper
    audio_duration: float
    inserts: list[Insert]
    captions: list[dict]               # word-pinned flashes
    error: str = ""


async def build_engaging_avatar_assets(
    *,
    avatar_video_url: str,
    aspect: str,
    style: str,                        # cinematic / photoreal / etc
    brand_context: str,
    platform: str = "instagram",
    tenant_id: str | None = None,
    broll_avoid: str = "",
    engine: str = "",                  # B-roll animator: ''|runway|higgsfield
) -> EngagingAvatarResult:
    """Engaging-avatar pipeline.

    Same first steps as story_audio: strip audio, persist, Whisper.
    Then forks: instead of segmenting words into 8-18 beats, the LLM
    picks 2-5 short cutaway INSERTS — windows of 1.5-2.5s where a
    cinematic B-roll image overlays the avatar. The HeyGen video keeps
    playing underneath; the audio is continuous.

    Hero refs flow on insert.uses_hero just like in the story modes.

    `broll_avoid` — a video_feedback.video_avoid_block() string of past
    human B-roll rejections, forwarded into the insert-picker prompt so
    the LLM stops repeating cutaway mistakes. Empty = no steering.
    """
    tid = tenant_id or str(settings.default_tenant_id)
    if not avatar_video_url or not avatar_video_url.startswith("http"):
        return EngagingAvatarResult(
            avatar_video_url="", audio_url="", audio_duration=0.0,
            inserts=[], captions=[],
            error="engaging_avatar needs a real HeyGen video URL",
        )

    try:
        video_bytes = await _download(avatar_video_url)
    except Exception as e:  # noqa: BLE001
        return EngagingAvatarResult(
            "", "", 0.0, [], [],
            error=f"could not fetch HeyGen video: {e}",
        )

    audio_bytes = await _strip_to_mp3(video_bytes)
    if not audio_bytes:
        return EngagingAvatarResult(
            avatar_video_url, "", 0.0, [], [],
            error="ffmpeg failed to extract audio",
        )

    audio_url = await persist_audio(
        audio_bytes, f"engaging-audio-{uuid.uuid4().hex[:8]}", tid,
    )
    if not audio_url:
        return EngagingAvatarResult(
            avatar_video_url, "", 0.0, [], [],
            error="could not persist audio",
        )

    try:
        tr = await transcribe_words("voice.mp3", audio_bytes)
    except Exception as e:  # noqa: BLE001
        return EngagingAvatarResult(
            avatar_video_url, audio_url, 0.0, [], [],
            error=f"whisper failed: {e}",
        )

    # Hero context (description + refs) — same as story modes.
    from .hero_context import (
        get_hero_context as _hero_ctx,
        get_hero_photo_files as _hero_files,
    )
    hero_ctx = await _hero_ctx()
    hero_description = hero_ctx.description if hero_ctx else ""
    hero_refs = await _hero_files() if hero_ctx else []

    # Live-tunable B-roll insert duration (feedback loop → render_tuning).
    from .render_tuning import get_render_tuning
    _rt = await get_render_tuning(tenant_id)
    inserts = await pick_insert_points(
        audio_duration=tr.duration,
        words=tr.words,
        brand_context=brand_context,
        hero_description=hero_description,
        avoid=broll_avoid,
        min_dur=_rt["broll_insert_min_dur"],
        max_dur=_rt["broll_insert_max_dur"],
    )

    if inserts:
        await gen_insert_images(
            inserts, aspect, platform, style, tid,
            hero_refs=hero_refs,
        )
        # Chain each generated still through Runway image-to-video.
        # Honest fallback: every insert that fails to animate keeps its
        # static image_url and the Creatomate source falls back to a
        # still element. Tracked per-insert via video_error so the UI
        # can show what was actually rendered as motion.
        await animate_inserts(inserts, aspect, tid, engine=engine)

    captions = caption_lines(tr.words)
    return EngagingAvatarResult(
        avatar_video_url=avatar_video_url,
        audio_url=audio_url,
        audio_duration=tr.duration,
        inserts=inserts,
        captions=captions,
    )


__all__ = [
    "Beat", "StoryAudioResult",
    "Insert", "EngagingAvatarResult",
    "segment_beats", "caption_lines", "write_image_prompts",
    "gen_beat_images", "build_story_audio_assets",
    "classify_beats", "slice_avatar_beats", "build_avatar_story_mix_assets",
    "pick_caption_style", "pick_insert_points", "gen_insert_images",
    "animate_inserts",
    "build_engaging_avatar_assets", "inserts_to_dict",
    "beats_to_dict",
]
