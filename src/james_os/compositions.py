"""Composition capability registry + build queue.

The Design Inspector captures a reference's LAYOUT (full_frame |
split_horizontal | split_vertical | pip | grid | other). The renderer can
only reproduce some of them faithfully today. Rather than fake an unsupported
composition, the library SURFACES it as a build request on the dashboard — the
library of trending styles drives the render roadmap. As each composition's
Creatomate build lands, add its layout key to SUPPORTED_LAYOUTS and it goes
live automatically for every template that needs it.

Mislabel guard: the whole "can we render this?" decision trusts the inspector's
layout LABEL. The safe failure is a layout it can't name → a non-matching
string → correctly queued. The DANGEROUS failure is a confident mis-label of a
multi-region composition as 'full_frame' → it would render flat while claiming
faithful. `layout_label_is_suspect` cross-checks the label against the
reference's own captured distinctive_features / description / regions: if a
flat label contradicts spatial cues, the template is flagged 'unverified'
(re-inspect) instead of silently trusted.
"""

import json
from uuid import UUID

from .db import acquire

# Layouts the renderer reproduces faithfully TODAY. 'full_frame' covers the
# standard single-frame modes (avatar / mixed / story). As new compositions
# are built (e.g. a real split-screen render), add their layout key here and
# the dashboard flips them from "queued" → "live" with no other change.
SUPPORTED_LAYOUTS: set[str] = {"", "full_frame", "none", "split_horizontal"}

# Layout labels that mean "nothing special / single frame". A multi-region
# composition mistakenly given one of these is the dangerous silent case.
_FLAT_LAYOUTS: set[str] = {"", "full_frame", "none", "single", "fullscreen"}

# Spatial-composition cues that CONTRADICT a flat label. If the inspector
# labels a layout full_frame but its own distinctive_features / description
# mention any of these, the label is suspect — the reference is probably a
# multi-region composition we'd silently flatten. Kept high-precision (phrases,
# not bare words) so an ordinary lower-third or logo doesn't trip it.
_SPLIT_CUES: tuple[str, ...] = (
    "split screen", "split-screen", "splitscreen", "split layout",
    "picture-in-picture", "picture in picture", "pip",
    "side-by-side", "side by side", "two-column", "two column",
    "top half", "bottom half", "left half", "right half",
    "upper half", "lower half", "stacked layout", "stacked region",
    "grid layout", "grid of", "sidebar", "split into",
    "speaker on top", "speaker on the left", "speaker on the right",
)


def is_supported(layout_type: str | None) -> bool:
    return (layout_type or "").strip().lower() in SUPPORTED_LAYOUTS


def _feature_blob(template: dict) -> str:
    """Lower-cased haystack of the reference's self-described features + the
    layout description — the text we scan for spatial cues."""
    parts: list[str] = []
    feats = template.get("distinctive_features")
    if isinstance(feats, list):
        parts.extend(str(f) for f in feats)
    elif isinstance(feats, str):
        parts.append(feats)
    layout = template.get("layout") or {}
    parts.append(str(layout.get("description") or ""))
    return " ".join(parts).lower()


def layout_label_is_suspect(template: dict) -> str | None:
    """If a template is labeled as a flat/full-frame layout but its OWN captured
    signals describe a multi-region composition, return a short reason — the
    label is probably a miss we'd silently flatten. Returns None when the label
    looks trustworthy (a real composition label, or no contradicting evidence).

    Two contradictions, either is enough:
      * structural — layout.regions places content in 2+ distinct positions
        (a full frame has at most one region);
      * textual — distinctive_features / description mention a split cue.
    """
    template = template or {}
    layout = template.get("layout") or {}
    ltype = str(layout.get("type") or "").strip().lower()
    if ltype not in _FLAT_LAYOUTS:
        return None  # a real composition label (e.g. split_horizontal) — trust it

    # Structural contradiction: multiple placed regions under a flat label.
    regions = layout.get("regions")
    if isinstance(regions, list):
        positions = {
            str(r.get("position") or "").strip().lower()
            for r in regions if isinstance(r, dict)
        }
        positions.discard("")
        if len(positions) >= 2:
            return ("its captured regions sit in multiple positions "
                    f"({', '.join(sorted(positions))})")

    # Textual contradiction: the reference's own features mention a split.
    blob = _feature_blob(template)
    hit = next((c for c in _SPLIT_CUES if c in blob), None)
    if hit:
        return f"its distinctive features mention '{hit}'"
    return None


async def composition_queue(tenant_id: UUID | None = None) -> list[dict]:
    """Distinct layouts seen across ready templates, each tagged:
      * live       — the renderer reproduces it today,
      * queued     — a composition still to build,
      * unverified — the layout was never captured (pre-upgrade), OR it's a flat
                     label contradicted by the reference's own features (the
                     mislabel guard) — re-inspect before trusting it.

    Honesty: a template is NEVER silently treated as faithful full-frame when
    either its layout is missing or its captured signals say it's a split. Both
    surface as 'unverified' with a re-inspect prompt instead."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT name, template FROM style_templates "
            "WHERE status = 'ready' ORDER BY created_at DESC"
        )

    # Bucket per template by its EFFECTIVE composition state.
    agg: dict[str, dict] = {}

    def _bump(key: str, *, status: str, supported: bool, description: str,
              example: str) -> None:
        b = agg.get(key)
        if b is None:
            agg[key] = {
                "layout_type": key, "count": 1, "example": example,
                "description": description, "supported": supported,
                "status": status,
            }
        else:
            b["count"] += 1
            if not b["example"]:
                b["example"] = example

    for r in rows:
        tpl = r["template"]
        if isinstance(tpl, str):
            try:
                tpl = json.loads(tpl)
            except Exception:  # noqa: BLE001
                tpl = {}
        tpl = tpl or {}
        layout = tpl.get("layout") or {}
        ltype = str(layout.get("type") or "").strip().lower()
        name = r["name"] or ""

        if not ltype:
            _bump("unknown", status="unverified", supported=False,
                  description=("Captured before layout analysis — re-inspect to "
                               "verify its composition before replicating."),
                  example=name)
            continue

        suspect = layout_label_is_suspect(tpl)
        if suspect:
            _bump("full_frame (suspect)", status="unverified", supported=False,
                  description=(f"Labeled full-frame but {suspect} — re-inspect to "
                               "confirm it isn't a split/stacked layout we'd flatten."),
                  example=name)
            continue

        supported = is_supported(ltype)
        _bump(ltype, status=("live" if supported else "queued"),
              supported=supported,
              description=str(layout.get("description") or ""),
              example=name)

    return sorted(agg.values(), key=lambda x: x["count"], reverse=True)


__all__ = [
    "SUPPORTED_LAYOUTS", "is_supported", "composition_queue",
    "layout_label_is_suspect",
]
