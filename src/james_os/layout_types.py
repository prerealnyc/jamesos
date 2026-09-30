"""Naming the KINDS of layout we hold, so the catalogue is browsable.

The engine records exactly two kinds — photo_forward and graphic_card — and 454
learned layouts collapse into them. That is why the library reads as an
undifferentiated pile: "graphic_card" says nothing about whether a layout is an
offer with a call to action, a testimonial with an attribution, or a bare
statement over a photograph.

A commercial template library's real asset is not its artwork, it is this: a set
of NAMED types, each knowing what content it needs and which niches use it. That
part is recoverable from our own corpus, and it is better recovered that way —
these are the types the brands we actually serve demonstrably post, not a generic
catalogue's guess.

The signature is (roles present) x (background treatment) x (photo regions).
Measured across the corpus 2026-09-30, it separates cleanly:

    headline                    full_bleed_photo   140
    headline+subhead            full_bleed_photo    52
    headline+subhead            solid               20
    headline                    solid               16
    headline+subhead            photo_with_scrim    16
    headline+kicker             full_bleed_photo    13
    headline+kicker+subhead     photo_with_scrim    11
    cta+headline+subhead        full_bleed_photo     9

Worth reading the top row honestly: a THIRD of everything learned is a bare
headline over a photograph. Much of the corpus is thin, and knowing which rows
are thin is exactly what a curator needs before promoting any of it.
"""

from __future__ import annotations

# A type is named by what it NEEDS, because that is what the writer must supply.
# Order matters: the first rule whose requirements are met wins, so the more
# specific types are listed first.
_RULES: list[tuple[str, str, frozenset, frozenset]] = [
    # name                     description                        required roles          forbidden
    ("offer_card",      "A call to action over a photograph",     frozenset({"cta"}),                  frozenset()),
    ("stat_card",       "One figure carrying the whole post",     frozenset({"stat"}),                 frozenset({"cta"})),
    ("testimonial",     "A quote with its attribution",           frozenset({"headline", "byline"}),   frozenset({"stat", "cta"})),
    ("announcement",    "Label, statement and detail",            frozenset({"kicker", "headline", "subhead"}), frozenset({"cta", "stat"})),
    ("labelled",        "A short label above a statement",        frozenset({"kicker", "headline"}),   frozenset({"subhead", "cta", "stat"})),
    ("statement_pair",  "A statement and one supporting line",    frozenset({"headline", "subhead"}),  frozenset({"kicker", "cta", "stat", "byline"})),
    ("statement",       "A single line, nothing else",            frozenset({"headline"}),             frozenset({"subhead", "kicker", "cta", "stat", "byline"})),
]

# How the background changes what a type IS, not merely how it looks: the same
# roles over a solid panel is a typographic card, over a photograph it is a
# photo post, and the writer's job differs between them.
# The slug is for machines (filters, indexes); this is what a curator reads. Kept
# apart because the readable name does not always survive being derived from the
# slug -- "labelled_photo" on a solid background would print "photo labelled photo".
_DISPLAY = {
    "offer_card": "offer",
    "stat_card": "stat",
    "testimonial": "testimonial",
    "announcement": "announcement",
    "labelled": "labelled statement",
    "statement_pair": "statement pair",
    "statement": "statement",
    "other": "unclassified",
}

_GROUND = {
    "solid": "typographic",
    "photo_with_scrim": "scrimmed",
    "full_bleed_photo": "photo",
    "photo_top": "split",
    "photo_bottom": "split",
    "photo_side": "split",
}


def roles_of(spec: dict) -> frozenset:
    """The roles a layout uses, with repeat numbering stripped ("cta#2" -> "cta")."""
    out = set()
    for e in (spec or {}).get("elements") or []:
        if isinstance(e, dict) and e.get("role"):
            out.add(str(e["role"]).split("#", 1)[0])
    return frozenset(out)


def classify(spec: dict) -> dict:
    """Name this layout: {type, ground, regions, label}.

    Never raises and always answers — an unrecognised shape is 'other', which is
    a useful thing for a curator to filter on rather than an error.
    """
    if not isinstance(spec, dict):
        return {"type": "other", "ground": "unknown", "regions": 1, "label": "other"}
    roles = roles_of(spec)
    bg = (spec.get("background") or {})
    ground = _GROUND.get(str(bg.get("treatment") or ""), "photo")
    regions = len(bg.get("photo_boxes") or []) or 1

    name = "other"
    for rule_name, _desc, required, forbidden in _RULES:
        if required <= roles and not (forbidden & roles):
            name = rule_name
            break

    label = f"{ground} {_DISPLAY.get(name, name.replace('_', ' '))}"
    if regions > 1:
        # A collage is a different job for the writer — several moments, not one.
        label = f"{regions}-up {label}"
    return {"type": name, "ground": ground, "regions": regions, "label": label}


def display_name(type_name: str) -> str:
    """The readable name for a type slug."""
    return _DISPLAY.get(type_name, type_name.replace("_", " "))


def describe(type_name: str) -> str:
    for name, desc, _r, _f in _RULES:
        if name == type_name:
            return desc
    return "An arrangement we have not named yet"


__all__ = ["classify", "describe", "display_name", "roles_of"]
