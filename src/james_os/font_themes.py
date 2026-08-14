"""Per-brand typography themes for static posts.

A brand owner picks ONE theme; the render engine swaps its display (headline) and
body (caption/label) faces accordingly. `bold` is the house default (Anton + Archivo
Black) and resolves to None so a default render is byte-identical to before the feature.

All faces are bundled OFL fonts in assets/fonts. `resolve(key)` returns the paths the
image compositor's `brand_fonts()` context manager expects: {"display": ..., "body": ...},
or None for the default look / an unknown key.
"""

from __future__ import annotations

import os

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")


def _f(name: str) -> str:
    return os.path.join(_FONT_DIR, name)


# key -> {label, blurb, display face, body face}. Order = display order in the picker.
# `bold` has no faces (None) → the untouched house default.
FONT_THEMES: dict[str, dict] = {
    "bold": {
        "label": "Bold",
        "blurb": "Anton + Archivo Black — the punchy house look.",
        "display": None,
        "body": None,
    },
    "clean": {
        "label": "Clean",
        "blurb": "Montserrat — modern, geometric, friendly-professional.",
        "display": _f("Montserrat-ExtraBold.ttf"),
        "body": _f("Montserrat-SemiBold.ttf"),
    },
    "editorial": {
        "label": "Editorial",
        "blurb": "Playfair Display + Montserrat — magazine, authoritative.",
        "display": _f("PlayfairDisplay-Black.ttf"),
        "body": _f("Montserrat-SemiBold.ttf"),
    },
    "elegant": {
        "label": "Elegant",
        "blurb": "Cormorant + Montserrat — high-contrast, luxury, refined.",
        "display": _f("Cormorant-Bold.ttf"),
        "body": _f("Montserrat-SemiBold.ttf"),
    },
    "friendly": {
        "label": "Friendly",
        "blurb": "Poppins — rounded, approachable, contemporary.",
        "display": _f("Poppins-Bold.ttf"),
        "body": _f("Poppins-Medium.ttf"),
    },
    "condensed": {
        "label": "Condensed",
        "blurb": "Oswald — tall, condensed, sporty and bold.",
        "display": _f("Oswald-Bold.ttf"),
        "body": _f("Oswald-Medium.ttf"),
    },
}

DEFAULT_KEY = "bold"
THEME_KEYS = list(FONT_THEMES.keys())


def catalog() -> list[dict]:
    """The picker catalog: [{key, label, blurb}, ...] (no filesystem paths)."""
    return [
        {"key": k, "label": v["label"], "blurb": v["blurb"]}
        for k, v in FONT_THEMES.items()
    ]


def normalize(key: str | None) -> str:
    """Coerce to a known theme key; unknown/empty → the default."""
    k = (key or "").strip().lower()
    return k if k in FONT_THEMES else DEFAULT_KEY


def resolve(key: str | None) -> dict | None:
    """Theme key → {"display": path, "body": path} for the compositor, or None for
    the default house look (no face remap)."""
    theme = FONT_THEMES.get(normalize(key)) or {}
    display, body = theme.get("display"), theme.get("body")
    if not display and not body:
        return None
    return {"display": display, "body": body}
