"""The house reel library — the starter formats every brand inherits.

A brand-new tenant used to open the style library and find nothing: templates
are born from inspected reference videos, and a new brand has uploaded none. It
had to do the work before it could see the product work.

These are the formats JAMES OS renders WELL today, stated as builder specs and
seeded into the platform tenant (migration 057) so they are readable by every
brand from the moment it exists. They are FORMATS, not content — replicating one
fills in the replicating brand's own voice, hero, logo and colours, so the same
house template produces a James reel, a Turtleback reel and a Spaceport reel
that look nothing alike in substance and identical in shape.

They go in through `create_template`, which means through `build_template` —
the same validation as a hand-authored template. A starter template that the
renderer could not produce would fail to seed rather than ship broken.
"""

from uuid import UUID

from . import templates as T

# Ordered best-first: `trending_score` is what the library sorts on, and the
# autopilot's distinct-template picker walks that same order.
HOUSE_TEMPLATES: list[dict] = [
    {
        "name": "Talking head, magenta subs",
        "summary": "Straight to camera with B-roll cutaways and bold magenta "
                   "captions — the workhorse reel.",
        "layout": "full_frame",
        "production_mode": "engaging_avatar",
        "aspect": "9:16",
        "caption_preset": "magenta_blocks",
        "music": "upbeat",
        "format_type": "talking_head",
        "energy": "high",
        "hook": "Lead with the claim, not the greeting — the first line is the "
                "whole hook.",
        "distinctive_features": [
            "speaker full-frame, cutaways only where the line needs proof",
            "magenta caption blocks, one phrase at a time, below the face",
            "no intro card and no end card — the reel starts on the point",
        ],
        "replication_recipe": [
            "Open on the strongest sentence in the script; never on a name.",
            "Cut to B-roll only when a specific noun needs showing.",
            "Keep captions to one phrase per beat, below the face.",
            "End on the takeaway — no sign-off, no follow card.",
        ],
        "vibe": "direct, confident, unfussy",
        "trending_score": 100,
        "tags": ["house", "talking head"],
    },
    {
        "name": "Split 50/50 — speaker over proof",
        "summary": "Speaker pinned to the top half, B-roll running underneath "
                   "the whole time, captions on the seam.",
        "layout": "split_horizontal",
        "aspect": "9:16",
        "caption_preset": "magenta_white",
        "music": "upbeat",
        "format_type": "mixed",
        "energy": "high",
        "hook": "State the number in the first two seconds while the proof is "
                "already moving underneath.",
        "distinctive_features": [
            "held 50/50 composition — speaker top, evidence bottom, all the way through",
            "captions sit on the seam between the two halves",
            "the bottom half is doing the arguing while the top half talks",
        ],
        "replication_recipe": [
            "Pick a claim that needs visual proof — a place, a chart, a build.",
            "Keep the speaker framed head-and-shoulders so the top box holds the face.",
            "Run the proof footage continuously; don't cut it to the words.",
            "Put the number in the caption, not just the voiceover.",
        ],
        "vibe": "evidence-forward, energetic",
        "trending_score": 95,
        "tags": ["house", "split screen"],
    },
    {
        "name": "Cold open hook, then the answer",
        "summary": "A hard question or contrarian claim held on screen, then the "
                   "answer delivered to camera.",
        "layout": "full_frame",
        "production_mode": "engaging_avatar",
        "aspect": "9:16",
        "caption_preset": "viral_hook",
        "music": "upbeat",
        "format_type": "talking_head",
        "energy": "high",
        "hook": "A question the viewer can't answer, or a claim they want to argue with.",
        "distinctive_features": [
            "the hook is a held title card, not just a spoken line",
            "the answer starts before the title clears",
            "one idea only — no list, no second point",
        ],
        "replication_recipe": [
            "Write the hook as something a stranger would argue with.",
            "Answer it in the first eight seconds; don't tease.",
            "Land one idea and stop.",
        ],
        "vibe": "provocative, fast",
        "trending_score": 90,
        "tags": ["house", "hook"],
    },
    {
        "name": "Voiceover over stills",
        "summary": "No on-camera presence — narrated stills, calm bed, clean "
                   "captions. The format that ships when there's no footage.",
        "layout": "full_frame",
        "production_mode": "story_audio",
        "aspect": "9:16",
        "caption_preset": "clean_white",
        "music": "calm",
        "format_type": "b_roll_montage",
        "energy": "medium",
        "hook": "Open on the image that raises the question the voiceover answers.",
        "distinctive_features": [
            "no talking head at all — usable before a brand has hero footage",
            "one still per idea, held long enough to read",
            "clean white captions carry the whole script",
        ],
        "replication_recipe": [
            "Write it as narration, not as speech to camera.",
            "One image per sentence; hold each long enough to actually look.",
            "Keep the bed under the voice — calm, never driving.",
        ],
        "vibe": "considered, quiet, editorial",
        "trending_score": 80,
        "tags": ["house", "no-camera"],
    },
    {
        "name": "Three-beat proof montage",
        "summary": "A claim, three pieces of evidence, a close — cut scene by "
                   "scene from your beats.",
        "layout": "full_frame",
        "production_mode": "mixed",
        "aspect": "9:16",
        "caption_preset": "bold_pop",
        "music": "dramatic",
        "format_type": "mixed",
        "energy": "high",
        "hook": "Say what you're about to prove, then prove it three times.",
        "distinctive_features": [
            "the beats ARE the edit — this is the one format where your scene "
            "list drives the cut",
            "three pieces of evidence, no more",
            "the close restates the claim in one line",
        ],
        "replication_recipe": [
            "Name the claim in the first beat.",
            "Give exactly three proofs — a number, a place, a result.",
            "Close on the claim again, shorter.",
        ],
        "vibe": "punchy, structured, confident",
        "beats": [
            {"role": "talking_head", "seconds": 4, "visual": "The claim, straight to camera"},
            {"role": "b_roll", "seconds": 4, "visual": "First proof — the number on screen"},
            {"role": "b_roll", "seconds": 4, "visual": "Second proof — the place"},
            {"role": "b_roll", "seconds": 4, "visual": "Third proof — the result"},
            {"role": "talking_head", "seconds": 4, "visual": "The claim again, shorter"},
        ],
        "trending_score": 85,
        "tags": ["house", "montage"],
    },
    {
        "name": "Your own footage, cut to a reel",
        "summary": "Pull the strongest 30 seconds out of a long-form clip you "
                   "already shot, full-frame with bold subs.",
        "layout": "full_frame",
        "production_mode": "long_form_reel",
        "aspect": "9:16",
        "caption_preset": "magenta_blocks",
        "music": "",
        "format_type": "talking_head",
        "energy": "medium",
        "hook": "Start at the sentence that stands alone — cut everything before it.",
        "distinctive_features": [
            "sourced from real footage, not a generated avatar",
            "no music bed — the room tone carries it",
            "cut starts mid-thought, on the strongest line",
        ],
        "replication_recipe": [
            "Find the one sentence that works with no setup.",
            "Cut in on it — lose the wind-up entirely.",
            "Let it end where the thought ends, even if that's abrupt.",
        ],
        "vibe": "real, unpolished, credible",
        "trending_score": 75,
        "tags": ["house", "from your footage"],
    },
]


async def seed_house_library(*, dry_run: bool = False) -> dict:
    """Create any house template that isn't there yet. Idempotent — matches on
    slug, so re-running adds only what's missing and never duplicates or
    overwrites a curator's edits to an existing house template.

    Returns {created, skipped, errors} naming each template, so a partial seed
    reports exactly what landed rather than a bare count."""
    existing = {
        t.get("slug") for t in await T.list_templates(
            T.PLATFORM_TENANT_ID, scope="platform"
        )
    }
    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for spec in HOUSE_TEMPLATES:
        spec = dict(spec)
        tags = spec.pop("tags", [])
        score = spec.pop("trending_score", 0)
        slug = T._slugify(spec["name"])
        if slug in existing:
            skipped.append(spec["name"])
            continue
        if dry_run:
            created.append(spec["name"])
            continue
        try:
            await T.create_template(
                spec, tenant_id=T.PLATFORM_TENANT_ID, scope="platform",
                tags=tags, trending_score=score,
            )
            created.append(spec["name"])
        except Exception as e:  # noqa: BLE001 — report, never abort the batch
            errors.append(f"{spec['name']}: {e}")

    return {"created": created, "skipped": skipped, "errors": errors,
            "total": len(HOUSE_TEMPLATES), "dry_run": dry_run}


def validate_house_library() -> list[str]:
    """Every starter spec checked against the builder's rules — no database
    needed. Used by the test suite so a starter template that stopped being
    renderable (a retired caption preset, a dropped mode) fails CI instead of
    failing at seed time in production."""
    from .template_spec import validate_spec

    problems: list[str] = []
    for spec in HOUSE_TEMPLATES:
        spec = {k: v for k, v in spec.items() if k not in ("tags", "trending_score")}
        for err in validate_spec(spec):
            problems.append(f"{spec.get('name', '?')}: {err}")
    return problems


__all__ = ["HOUSE_TEMPLATES", "seed_house_library", "validate_house_library"]
