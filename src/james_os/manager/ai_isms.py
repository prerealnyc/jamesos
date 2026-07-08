"""AI-ism lint lexicon + deterministic checks (Reviewer Layer 1, D7).
Ported verbatim from bm2.0 backend/app/data/ai_isms.py.

Pure functions: no LLM, no DB, no I/O. Phrase matching is case-insensitive
on word boundaries, one violation per phrase regardless of repeats. Curly
quotes are normalized to straight before matching; em-dash counting runs on
the original text.
"""

import re

EM_DASH_MAX_PER_1000_WORDS = 1.0
RULE_OF_THREE_MIN_STACK = 2  # one triad is style; two or more is stacking

BANNED_PHRASES: tuple[str, ...] = (
    # vocabulary tells
    "delve",
    "delves",
    "delving",
    "tapestry",
    "testament to",
    "symphony of",
    "kaleidoscope",
    "treasure trove",
    "beacon of",
    "myriad of",
    "plethora",
    "multifaceted",
    "intricacies",
    "ever-evolving",
    "seamless",
    "seamlessly",
    "robust framework",
    "holistic approach",
    "synergy",
    "paradigm shift",
    "quantum leap",
    "transformative",
    "revolutionize",
    "groundbreaking",
    # hype
    "game-changer",
    "game changer",
    "game-changing",
    "cutting-edge",
    "state-of-the-art",
    "best-in-class",
    "world-class",
    "top-notch",
    "second to none",
    "unparalleled",
    "gold standard",
    "secret sauce",
    "move the needle",
    "low-hanging fruit",
    "skyrocket",
    "supercharge",
    "unleash",
    "unlock the power",
    "unlock the potential",
    "harness the power",
    "leverage the power",
    "elevate your",
    "empower you to",
    "thought leader",
    "thought leadership",
    "actionable insights",
    "key takeaways",
    # discourse scaffolding
    "in today's fast-paced world",
    "in today's digital age",
    "in today's digital landscape",
    "digital landscape",
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "it is worth noting",
    "in conclusion",
    "at the end of the day",
    "at its core",
    "without further ado",
    "let's dive in",
    "deep dive into",
    "in the realm of",
    "embark on a journey",
    "navigating the complexities",
    "stay ahead of the curve",
    "furthermore",
    "moreover",
    # social slop
    "let that sink in",
    "read that again",
    "mind-blowing",
    "jaw-dropping",
    "thought-provoking",
    "hidden gem",
    "nugget of wisdom",
    "endless possibilities",
    "sky's the limit",
    "recipe for success",
    "the ultimate guide",
    "look no further",
    "picture this:",
    "in a world where",
    "let's face it",
    "let's be honest",
    "here's the kicker",
    "pro tip:",
    "boasts",
    "nestled in the heart of",
    # assistant self-tells
    "as an ai language model",
    "as a language model",
    "as an ai,",
)

BANNED_OPENERS: tuple[str, ...] = (
    "great question",
    "that's a great question",
    "what a great question",
    "certainly!",
    "certainly,",
    "absolutely!",
    "of course!",
    "sure thing",
    "i hope this email finds you well",
    "i hope this finds you well",
    "as an ai",
)

_PHRASE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (p, re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE)) for p in BANNED_PHRASES
)
# single-word triads: "fast, simple, and effective"
_RULE_OF_THREE_RE = re.compile(r"\b[\w'-]+, [\w'-]+, and [\w'-]+\b")
# "it's not just X, it's Y" contrast scaffold
_NOT_JUST_RE = re.compile(r"\b(?:it'?s|this is|that'?s|we'?re) not (?:just|only|merely)\b", re.IGNORECASE)

_QUOTE_MAP = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def lint(text: str) -> list[str]:
    """All deterministic AI-ism checks; one message per violated rule."""
    normalized = text.translate(_QUOTE_MAP)
    violations: list[str] = []

    for phrase, pattern in _PHRASE_PATTERNS:
        if pattern.search(normalized):
            violations.append(f"banned phrase: '{phrase}'")

    opener_src = normalized.lstrip(" \t\n\"'").lower()
    for opener in BANNED_OPENERS:
        if opener_src.startswith(opener):
            violations.append(f"AI-ism opener: '{opener}'")
            break

    triads = _RULE_OF_THREE_RE.findall(normalized)
    if len(triads) >= RULE_OF_THREE_MIN_STACK:
        violations.append(f"rule-of-three stacking: {len(triads)} triadic lists")

    if _NOT_JUST_RE.search(normalized):
        violations.append("contrast scaffold: \"it's not just X\" construction")

    words = max(len(text.split()), 1)
    em_dashes = text.count("—") + text.count("--")
    density = em_dashes / words * 1000
    if em_dashes and density > EM_DASH_MAX_PER_1000_WORDS:
        violations.append(
            f"em-dash density {density:.1f}/1000 words exceeds {EM_DASH_MAX_PER_1000_WORDS:g}"
        )

    return violations
