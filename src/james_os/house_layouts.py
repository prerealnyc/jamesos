"""The house catalogue: layouts any brand may draw from, curated by hand.

Three verbs, and the order matters.

  PROMOTE  a brand's learned layout becomes a CANDIDATE in the catalogue.
           Copied, never moved: the brand keeps its own row and its own counters.
  CURATE   a human approves, rejects or deletes. Nothing reaches another brand
           until someone has looked at it — "add a lot of good-looking images so
           the output is really good" is a claim about judgement, not volume.
  ADOPT    a brand below its minimum library forks approved layouts into its own
           tenant. From then on it owns that copy outright.

WHY FORK RATHER THAN READ THROUGH. Everything the picker sorts on is a mutable
per-row counter — last_used_at, approvals - rejections, qa_fails. Those are
per-BRAND facts: one brand retiring a layout after three bad renders must not
retire it for everyone, and one brand's rotation must not be another's. A shared
row cannot hold them. Worse, `design_templates` write-scopes by RLS, so a brand
updating a row it does not own updates ZERO rows and says nothing — the counters
would silently stop moving. Forking makes the copy genuinely the brand's.

WHY THIS TABLE HAS NO tenant_id AND NO RLS. It belongs to the platform, not to
any brand. Access is gated above the database: reads via the engine's service
key, writes only through admin-gated routes.

WHAT MAY BE SHARED. Only competitor- and monitoring-derived layouts, plus what a
curator uploads. A layout read from a brand's OWN posts is that brand's and is
refused here — enforced in `promote`, and by the table's CHECK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from . import niche_vocab
from .db import acquire

logger = logging.getLogger(__name__)

# What a brand's layout must be derived from to be shareable. A competitor's post
# is public material and the spec keeps nothing of it but the arrangement; a
# layout learned from the brand's own photographs is not ours to hand around.
SHAREABLE = ("competitor", "niche", "reference")

# The catalogue and a brand's library have DIFFERENT closed vocabularies for
# source_kind, and adopt() copies a row from one to the other. 'curated' is legal
# here and illegal there, so every adoption of an uploaded layout failed on
# design_templates_source_kind_check -- found by running it against production,
# not by reading it.
#
# Mapped rather than fixed by widening the other constraint: in a BRAND's library
# an adopted house layout genuinely is a reference (a layout read off an image
# someone pointed at), 'reference' already means exactly that and is already in
# SHAREABLE. Widening design_templates' vocabulary to admit a word only the
# catalogue uses would make that table's constraint describe less.
ADOPT_SOURCE_KIND = {"curated": "reference"}

# How many approved layouts a brand takes when it is short. Enough to clear
# MIN_LIBRARY and leave the rotation something to rotate through, not so many
# that a new brand's feed is entirely inherited.
ADOPT_BATCH = 8

# How many approved rows to consider before ranking. Ranking happens in Python
# (the tags are free text, so `&&` on the arrays is only a coarse pre-sort), so
# the candidate set has to be pulled first — wide enough that a brand's match is
# not cut off by the pre-sort, small enough to stay one cheap query.
#
# 400, and the brand's exclusions applied IN SQL before the LIMIT, since the
# nightly harvest (2026-10-07). With the exclusion done in Python after a LIMIT
# of 200, a brand that had already taken the oldest 200 approved rows was offered
# nothing at all, and anything approved after the 200th was invisible to every
# brand — exactly the rows the harvest adds.
CANDIDATE_POOL = 400

# How many of the brand's most recently used layouts define "a family it has
# just drawn". The picker prefers a different family, so a pool full of near
# twins (3 of 9 measured harvest survivors shared one family) does not hand the
# same arrangement out twice running.
RECENT_FAMILIES = 6

# The most niche tags one catalogue row carries. Shared by every writer.
MAX_NICHES = 12

# A catalogue row this young outranks an older one of the same niche fit. The
# nightly harvest adds rows every night and, ranked only on fit and type, they
# sat behind the rows every brand had already been offered for weeks — the
# "latest templates" never reached a feed. Two weeks is long enough for every
# brand's rotation to reach a new row, short enough that "new" means new.
HOUSE_FRESH_DAYS = 14

# ON CONFLICT ... DO UPDATE fragment: the row's niches become the UNION of what
# it had and what this write brought — lowercased, trimmed, distinct, first-seen
# order, at most MAX_NICHES. Replacing them (the old behaviour) meant a second
# niche finding the same shape silently took it away from the first.
_NICHE_UNION_SQL = f"""ARRAY(
                       SELECT d.t FROM (
                           SELECT lower(btrim(u.t)) AS t, min(u.ord) AS ord
                             FROM unnest(house_layouts.niches || EXCLUDED.niches)
                                  WITH ORDINALITY AS u(t, ord)
                            WHERE btrim(coalesce(u.t, '')) <> ''
                         GROUP BY 1
                       ) d
                       ORDER BY d.ord
                       LIMIT {MAX_NICHES})"""


def clean_niches(values, *, limit: int = MAX_NICHES) -> list[str]:
    """Niche tags as the catalogue stores them: trimmed, lowercased, at most 60
    characters each, distinct in first-seen order, at most `limit` of them."""
    out: list[str] = []
    for v in values or []:
        if v is None:
            continue
        t = str(v).strip().lower()[:60]
        if t and t not in out:
            out.append(t)
    return out[:limit]


# ── vocabulary labels a row carries that nobody typed ──
#
# Ingest, the harvest and the backfill add niche_vocab labels to a row's niches
# so ranking and the `niches && $tags` pre-sort find it ('golf resort' is also
# 'golf' and 'hospitality'; an untagged upload read as 'real estate'). But the
# harvest's balance caps and its per-niche coverage (house_harvest._COUNTS_SQL,
# harvest_stats) count rows by EXACT tag membership, and harvest tags are often
# labels themselves ('golf', 'real estate', 'commercial real estate'). Counted
# naively, every labelled 'golf resort' row held a 'golf' harvest at caps it had
# not reached, and ~313 curated uploads labelled by the backfill would have made
# 'real estate' look covered to BM2's seed/steady decision so it stopped
# harvesting it. So the labels ADDED are listed in harvest_meta.niche_labels
# (a jsonb column every row has since 071 — no migration, no deploy ordering)
# and the counts skip a tag that is only there as a label.
LABELS_KEY = "niche_labels"


def with_labels(typed) -> tuple[list[str], list[str]]:
    """(typed tags followed by their vocabulary labels, the labels nobody typed)."""
    tags = clean_niches(typed)
    stored = clean_niches(tags + niche_vocab.canonical(tags))
    return stored, [t for t in stored if t not in tags]


def counted_tag_sql(tag: str, alias: str = "") -> str:
    """SQL: `tag` is one of the row's niches AND was typed/harvested, not added
    as a vocabulary label. What the harvest's caps and stats count by."""
    a = f"{alias}." if alias else ""
    return (f"({tag} = ANY({a}niches) AND NOT (COALESCE({a}harvest_meta->'{LABELS_KEY}', "
            f"'[]'::jsonb) ? {tag}))")


# ON CONFLICT ... DO UPDATE fragment for harvest_meta, beside _NICHE_UNION_SQL:
# the union of both writes' added labels, minus any tag EITHER write carried as a
# real tag (a curator typing 'golf' on a row that had it only as a label makes
# it count from then on).
_LABELS_MERGE_SQL = f"""jsonb_set(house_layouts.harvest_meta, '{{{LABELS_KEY}}}', to_jsonb(ARRAY(
        SELECT DISTINCT e.x
          FROM jsonb_array_elements_text(
                 COALESCE(house_layouts.harvest_meta->'{LABELS_KEY}', '[]'::jsonb)
                 || COALESCE(EXCLUDED.harvest_meta->'{LABELS_KEY}', '[]'::jsonb)) AS e(x)
         WHERE NOT (e.x = ANY(house_layouts.niches) AND NOT
                    COALESCE(house_layouts.harvest_meta->'{LABELS_KEY}', '[]'::jsonb) ? e.x)
           AND NOT (e.x = ANY(EXCLUDED.niches) AND NOT
                    COALESCE(EXCLUDED.harvest_meta->'{LABELS_KEY}', '[]'::jsonb) ? e.x)
      ORDER BY e.x)))"""


MAX_BRAND_TAGS = 64


def brand_tag_keys(niches) -> list[str]:
    """Every spelling under which a catalogue writer may have stored one of the
    brand's niches, for the picker's exact-overlap pre-sort (`niches && $tags`).

    The writers disagree on commas. Curated/promoted rows keep a niche whole
    (`clean_niches`); the harvest splits it on commas and stores the pieces
    (BM2 `split_tags` -> BM1 `parse_niches`), each `canon_niche`-d. So the brand
    niche 'tour packages, travel and holidays' is stored by the harvest as
    'tour packages' and 'travel and holidays' — and an overlap that tested only
    the whole string never matched the brand's own harvested rows, which then
    fell out of the LIMIT by recency. Per niche: the whole string as
    clean_niches keeps it, its canonical form (commas read as spaces), and each
    comma-separated piece in canonical form. Distinct, first-seen order.

    Then the brand's VOCABULARY labels (niche_vocab.canonical), last. Ingest and
    the backfill now tag catalogue rows with labels ('golf', 'travel'), and a
    pre-sort that offered only the brand's own phrases ('golf resort', 'tour
    operator') would overlap none of them — those rows would fall out of the
    LIMIT by recency exactly as the comma-split ones did.
    """
    from .house_harvest import canon_niche

    if isinstance(niches, str):
        niches = [niches]
    out: list[str] = []

    def _add(t: str) -> None:
        t = t[:60].strip()
        if t and t not in out:
            out.append(t)

    for v in niches or []:
        if v is None:
            continue
        raw = str(v)
        canon = canon_niche(raw)
        if not canon:          # only commas and blanks: no niche at all
            continue
        for whole in clean_niches([raw]):
            _add(whole)
        _add(canon)
        for piece in raw.split(","):
            _add(canon_niche(piece))
    for label in niche_vocab.canonical(niches):
        _add(label)
    return out[:MAX_BRAND_TAGS]


# Niche tags are FREE TEXT on both sides and nobody agreed a vocabulary: brands
# carry "golf resort", "commercial real estate", "tour packages, travel and
# holidays"; the pool carries "golf", "real estate". Matching those as whole
# strings — or with Postgres `&&` on the arrays — finds almost nothing, which is
# how niche targeting would look implemented and still behave randomly. So match
# on TOKENS, and rank rather than filter.
_NICHE_STOP = frozenset({
    "and", "the", "for", "of", "a", "an", "in", "on", "to", "with", "by",
    # words that appear in so many niches they carry no signal
    "packages", "package", "services", "service", "company", "brand", "business",
    "agency", "group", "new", "york",
    # A qualifier, not a niche: "commercial spaceport" and "commercial real
    # estate" share it and nothing else, and matching on it handed a spaceport
    # the real-estate rows ahead of untagged ones.
    "commercial",
})


def niche_tokens(values) -> set[str]:
    """The meaningful words in one or more niche phrases, lowercased."""
    if isinstance(values, str):
        values = [values]
    out: set[str] = set()
    for v in values or []:
        if v is None:
            continue          # a stray null must not become the token "none"
        for word in re.split(r"[^a-z0-9]+", str(v).lower()):
            if len(word) > 2 and word not in _NICHE_STOP:
                out.add(word)
    return out


def niche_rank(brand_niches, layout_niches) -> int:
    """How well a catalogue layout suits a brand. Higher is better.

      2+  a match (2 + N)
      1   carries NO tags at all — generic, suits anybody
      0   tagged, but for a different niche

    WHEN BOTH SIDES MAP TO THE VOCABULARY (niche_vocab.canonical), the labels
    decide: no shared label is 0 whatever words the phrases share — that is what
    ends 'commercial spaceport' matching 'commercial real estate' on a filler
    word, for every such pair rather than the one a stopword patched. N is the
    shared labels plus the shared meaningful words, so within a match the closer
    phrase still ranks higher ('luxury golf resort' suits 'golf resort' better
    than 'golf', both of which share the label golf). Only when either side maps
    to nothing does the word overlap alone decide, as it did before.

    Untagged beats off-niche on purpose. 12 of the 17 live pool rows carry no
    tags, so filtering strictly on a match would hand most brands nothing at all
    and the feature would read as broken. Ranking degrades instead of starving.
    """
    # `if str(t).strip()` is NOT enough: str(None) is "None", so a null in the
    # array would read as a real tag and rank the layout BELOW an untagged one.
    tags = [str(t).strip() for t in (layout_niches or [])
            if t is not None and str(t).strip()]
    if not tags:
        return 1
    words = niche_tokens(brand_niches) & niche_tokens(tags)
    mine, theirs = set(niche_vocab.canonical(brand_niches)), set(niche_vocab.canonical(tags))
    if mine and theirs:
        labels = mine & theirs
        return 2 + len(labels) + len(words) if labels else 0
    return 2 + len(words) if words else 0


async def tenant_niches(conn, tenant_id) -> list[str]:
    """What this brand is in, as it already knows it.

    Read from its OWN competitors rows — the niche a human confirmed during
    onboarding is recorded against the competitors discovered for it, so no new
    field and nothing to backfill. Live values today: 'golf resort',
    'commercial real estate', 'tour packages, travel and holidays',
    'political candidates', 'commercial spaceport'.
    """
    if not tenant_id:
        return []
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT niche FROM competitors WHERE coalesce(niche,'') <> ''")
        return [str(r["niche"]) for r in rows]
    except Exception:  # noqa: BLE001 — a brand with no competitors yet is normal
        logger.debug("no niche readable for %s", tenant_id, exc_info=True)
        return []


def family_key(spec: dict) -> str:
    """A COARSE signature grouping near-identical layouts.

    Deliberately not a finer hash. Measured on the real corpus 2026-09-29: 439
    layouts collapse to 436 exact fingerprints, so exact hashing has nothing left
    to catch — while the same corpus is full of layouts that differ by a few
    pixels and are plainly one idea. This buckets by what a person would use to
    say "those are the same": the kind, the background treatment, how many text
    blocks there are, and which QUADRANT each sits in.

    Not unique, and not a dedup key. It exists so a curator sees "these nine are
    one layout" rather than nine rows.
    """
    if not isinstance(spec, dict):
        return ""
    bg = spec.get("background") or {}
    quads = []
    for e in spec.get("elements") or []:
        if not isinstance(e, dict):
            continue
        b = e.get("box") or {}
        try:
            cx = float(b.get("x", 0)) + float(b.get("w", 0)) / 2
            cy = float(b.get("y", 0)) + float(b.get("h", 0)) / 2
        except (TypeError, ValueError):
            continue
        quads.append(f"{e.get('role','?')}{'R' if cx >= 0.5 else 'L'}{'B' if cy >= 0.5 else 'T'}")
    return "|".join([str(spec.get("kind") or "?"),
                     str(bg.get("treatment") or "?"),
                     str(len(quads))] + sorted(quads))


async def promote(
    tenant_id: UUID | str | None, template_id: str, *, note: str = "",
) -> dict:
    """Copy one of a brand's learned layouts into the catalogue as a candidate.

    Refuses anything not derived from public material. Idempotent on the
    fingerprint: a second brand promoting the same competitor post updates the
    existing row rather than adding a twin.
    """
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, kind, spec, fingerprint, source_kind, source_url, "
            "       source_image_uri, approvals, rejections, qa_passes, qa_fails "
            "  FROM design_templates WHERE id = $1::uuid", template_id)
    if not row:
        return {"promoted": False, "reason": "no such layout for this brand"}
    if str(row["source_kind"]) not in SHAREABLE:
        # The privacy line, enforced rather than documented.
        return {"promoted": False,
                "reason": f"source_kind '{row['source_kind']}' is the brand's own — not shareable"}

    spec = row["spec"]
    if isinstance(spec, str):
        spec = json.loads(spec)
    if not spec:
        return {"promoted": False, "reason": "empty spec"}

    # The catalogue has no tenant, so it is read on a connection with no tenant
    # scoping. acquire(None) is that connection.
    from .layout_types import classify
    named = classify(spec)

    async with acquire(None) as conn:
        hid = await conn.fetchval(
            """INSERT INTO house_layouts
                   (kind, spec, fingerprint, family_key, source_kind, source_url,
                    source_image_uri, promoted_from, review_note, layout_type, label)
               VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8::uuid, $9, $10, $11)
               ON CONFLICT (fingerprint) WHERE fingerprint <> ''
               DO UPDATE SET
                   -- a second brand vouching for the same shape is evidence, so
                   -- fold its record in rather than discarding it
                   approvals  = house_layouts.approvals  + EXCLUDED.approvals,
                   rejections = house_layouts.rejections + EXCLUDED.rejections,
                   layout_type = EXCLUDED.layout_type,
                   label       = EXCLUDED.label,
                   updated_at = now()
               RETURNING id""",
            str(row["kind"] or ""), json.dumps(spec), str(row["fingerprint"] or ""),
            family_key(spec), ("niche" if row["source_kind"] == "niche" else "competitor"),
            str(row["source_url"] or ""), str(row["source_image_uri"] or ""),
            str(tenant_id) if tenant_id else None, note[:500],
            named["type"], named["label"])
    return {"promoted": True, "house_layout_id": str(hid),
            "layout_type": named["type"], "label": named["label"]}


async def ingest(
    image: bytes, *, title: str = "", source_url: str = "", image_uri: str = "",
    niches: list[str] | None = None, by: str = "", note: str = "",
    approve: bool = False,
) -> dict:
    """Read ONE uploaded image into a catalogue candidate.

    The curator's own path in, and the reason it exists: learning only from
    competitors caps the catalogue at what competitors happen to post. A brand
    that wants a kind of post nobody in its niche makes has nowhere to get it.
    Uploading the shape directly removes that ceiling.

    No tenant anywhere in this function. An uploaded layout belongs to the
    platform from the moment it arrives -- it is not a brand's row promoted
    later, so it never passes through a tenant's library and cannot leak one
    brand's material into another's.

    Idempotent on the fingerprint: re-uploading the same image re-tags the
    existing row instead of adding a twin. `approve=True` skips the queue, for
    when the person uploading IS the curator -- which is the common case when
    they are adding images by hand.
    """
    from . import design_templates as dt
    from .design_cloner import extract_template_spec
    from .layout_types import classify

    spec = await extract_template_spec(image)
    if not spec or not dt.usable(spec):
        # Not an error: plenty of real images are photographs with no layout in
        # them. The caller shows this back to the curator per file.
        return {"ok": False, "reason": "no usable layout could be read from that image"}

    named = classify(spec)
    fp = dt.fingerprint(spec)
    # Approve only what the picker could actually DRAW. usable() says a layout
    # was read; drawable() says it survives prepare() — collisions resolved,
    # empty frames dropped — with a card left worth posting. An upload that is
    # not would sit in the approved pool, be skipped by every pick, and look to
    # the curator like a layout brands were using. Kept as a candidate with the
    # reason, so the curator sees it and nothing read is thrown away.
    can_draw = dt.drawable(spec)
    status = "approved" if (approve and can_draw) else "candidate"
    if not can_draw:
        why = undrawable_reason(spec)
        note = (f"not drawable: {why}" + (f" | {note}" if note else ""))
    tags, inferred, added = await _ingest_niches(niches, image, image_uri)

    async with acquire(None) as conn:
        row = await conn.fetchrow(
            """INSERT INTO house_layouts
                   (kind, spec, fingerprint, family_key, source_kind, source_url,
                    source_image_uri, layout_type, label, title, niches,
                    uploaded_by, status, review_note, reviewed_by, reviewed_at,
                    harvest_meta)
               VALUES ($1, $2::jsonb, $3, $4, 'curated', $5, $6, $7, $8, $9, $10,
                       $11, $12, $13, $15, CASE WHEN $12 = 'approved' THEN now() END,
                       $14::jsonb)
               ON CONFLICT (fingerprint) WHERE fingerprint <> ''
               DO UPDATE SET
                   title       = COALESCE(NULLIF(EXCLUDED.title, ''), house_layouts.title),
                   niches      = """ + _NICHE_UNION_SQL + """,
                   harvest_meta = """ + _LABELS_MERGE_SQL + """,
                   layout_type = EXCLUDED.layout_type,
                   label       = EXCLUDED.label,
                   updated_at  = now()
               RETURNING id::text, (created_at = updated_at) AS fresh, status""",
            str(spec.get("kind") or ""), json.dumps(spec), fp, family_key(spec),
            source_url[:500], image_uri[:500], named["type"], named["label"],
            title[:200], tags, by[:200], status, note[:500],
            json.dumps({LABELS_KEY: added}),
            by[:200] if status == "approved" else "")
    # The STORED status, read back. A re-upload of an approved shape with
    # approve=False leaves it approved (status is never touched on conflict), and
    # reporting the requested 'candidate' would tell the curator something false.
    stored = row.get("status") if hasattr(row, "get") else None
    return {
        "ok": True, "house_layout_id": row["id"], "duplicate": not row["fresh"],
        "layout_type": named["type"], "label": named["label"],
        "status": str(stored or status), "regions": named["regions"],
        "drawable": can_draw, "niches": tags, "niches_inferred": inferred,
    }


async def _ingest_niches(niches, image: bytes,
                         image_uri: str) -> tuple[list[str], bool, list[str]]:
    """(the tags an upload is stored with, whether they were read off the image,
    which of them are labels nobody typed — see LABELS_KEY).

    Typed tags are kept as typed PLUS their vocabulary labels, so 'golf resort'
    is also findable as 'golf' and 'hospitality'. With NO tags the image is read
    for them: 313 of 324 approved uploads (2026-10-08) arrived untagged and so
    ranked as generic for every brand — a golf course offered to a law firm on
    equal terms with a quote card. The read prefers the stored https copy (no
    upload of up to 15 MB inline) and falls back to the bytes in hand. A failed
    read stores [] and the upload goes on: a missing tag costs ranking, a
    failed upload costs the layout.
    """
    if clean_niches(niches):
        stored, added = with_labels(niches)
        return stored, False, added
    src = image_uri if str(image_uri or "").startswith(("https://", "http://")) else image
    try:
        # Bounded here too, above the client's own timeout: the upload must go
        # on untagged rather than wait on the model.
        labels = await asyncio.wait_for(niche_vocab.infer_from_image(src), INGEST_READ_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("niche inference timed out on upload; stored untagged")
        labels = []
    except Exception:  # noqa: BLE001 — infer_from_image never raises; belt and braces
        logger.warning("niche inference raised on upload", exc_info=True)
        labels = []
    stored = clean_niches(niche_vocab.canonical(labels))
    return stored, bool(labels), list(stored)


def undrawable_reason(spec: dict | None) -> str:
    """Why design_templates.drawable() said no, in words a curator can act on.
    Mirrors drawable()'s checks in order; "" when the layout is drawable."""
    from . import design_templates as dt

    if not dt.usable(spec):
        return "no usable layout was read"
    els = dt.prepare(spec).get("elements") or []
    if not els:
        return "no text boxes survive overlap clean-up"
    if not dt.drawable(spec):
        return (f"a solid card whose text spans under {dt.MIN_TEXT_SPAN_NO_PHOTO:.0%} "
                "of the height (a photo the read missed)")
    return ""


async def retag(layout_id: str, *, title: str | None = None,
                niches: list[str] | None = None) -> dict:
    """Rename a catalogue row or change which niches it suits."""
    sets, args = [], [layout_id]
    if title is not None:
        args.append(title[:200]); sets.append(f"title = ${len(args)}")
    if niches is not None:
        args.append([t.strip()[:60] for t in niches if t and t.strip()][:12])
        sets.append(f"niches = ${len(args)}")
        # Exactly what the curator typed, so none of it is an added label any more.
        sets.append(f"harvest_meta = harvest_meta - '{LABELS_KEY}'")
    if not sets:
        return {"ok": False, "reason": "nothing to change"}
    async with acquire(None) as conn:
        got = await conn.fetchval(
            f"UPDATE house_layouts SET {', '.join(sets)}, updated_at = now() "
            "WHERE id = $1::uuid RETURNING id", *args)
    return {"ok": bool(got)}


async def retype(*, limit: int = 1000) -> dict:
    """Name the rows that have no layout_type yet -- rows promoted before the
    taxonomy existed, and any whose spec has since been re-read."""
    from .layout_types import classify

    async with acquire(None) as conn:
        rows = await conn.fetch(
            "SELECT id::text, spec FROM house_layouts WHERE layout_type = '' "
            "LIMIT $1", max(1, min(int(limit), 5000)))
        named = 0
        for r in rows:
            spec = r["spec"]
            if isinstance(spec, str):
                spec = json.loads(spec)
            got = classify(spec)
            await conn.execute(
                "UPDATE house_layouts SET layout_type = $2, label = $3, "
                "updated_at = now() WHERE id = $1::uuid",
                r["id"], got["type"], got["label"])
            named += 1
    return {"named": named}


# What the curation screen reads per row. The harvest provenance columns come
# from migration 071; _CATALOGUE_COLS_PRE_071 is what a database without it can
# still answer, so the screen keeps working in the window between deploying this
# code and applying the migration.
_CATALOGUE_COLS_PRE_071 = """id::text, kind, spec, fingerprint, family_key, source_kind, source_url,
                       source_image_uri, layout_type, label, title, niches, uploaded_by,
                       status, review_note, reviewed_by, reviewed_at,
                       adopted_count, approvals, rejections, qa_passes, qa_fails, created_at"""
# harvest_meta minus spec_hint: a source's layer geometry (up to 16 KB a row) is
# stored for the record, but a screen listing hundreds of rows does not need it.
_CATALOGUE_COLS = (_CATALOGUE_COLS_PRE_071
                   + ", harvest_run_id, source_key, (harvest_meta - 'spec_hint') AS harvest_meta")

# A row came from the nightly harvest. Two signals, either is enough: the run id
# is the durable one, the uploaded_by prefix covers a row whose run id was lost.
_HARVESTED_SQL = "(harvest_run_id <> '' OR uploaded_by LIKE 'harvest:%')"


async def catalogue(
    *, status: str = "", layout_type: str = "", niche: str = "",
    limit: int = 200, offset: int = 0,
    harvested: bool | None = None, run_id: str = "",
) -> dict:
    """The catalogue, for the curation screen. No tenant: this is the platform's.

    Filters are AND-ed and each is optional. `by_status` and `by_type` always
    count the WHOLE pool, not the filtered page -- a curator narrowing to
    'candidate' still needs to see how much is approved. `matched` counts the
    filtered set, so a screen showing one page of it can say "N of matched".

    `harvested` True keeps only rows the nightly harvest wrote, False only the
    rest; `run_id` keeps one harvest run's rows.
    """
    where, args = [], []
    if status:
        args.append(status); where.append(f"status = ${len(args)}")
    if layout_type:
        args.append(layout_type); where.append(f"layout_type = ${len(args)}")
    if niche:
        # && is "overlaps": the row is tagged with this niche.
        args.append([niche]); where.append(f"niches && ${len(args)}::text[]")
    if harvested is True:
        where.append(_HARVESTED_SQL)
    elif harvested is False:
        where.append(f"NOT {_HARVESTED_SQL}")
    if run_id:
        args.append(run_id)
        where.append(f"harvest_run_id = ${len(args)}")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    page = (f"LIMIT {max(1, min(int(limit), 500))} OFFSET {max(0, int(offset))}")

    async def _read(cols: str):
        async with acquire(None) as conn:
            rows = await conn.fetch(
                f"""SELECT {cols}
                      FROM house_layouts {clause}
                  ORDER BY status, created_at DESC
                     {page}""", *args)
            matched = await conn.fetchval(
                f"SELECT count(*) FROM house_layouts {clause}", *args)
            counts = await conn.fetch("SELECT status, count(*) n FROM house_layouts GROUP BY 1")
            by_type = await conn.fetch(
                "SELECT layout_type, count(*) n FROM house_layouts GROUP BY 1 ORDER BY n DESC")
            niches = await conn.fetchval(
                "SELECT coalesce(array_agg(DISTINCT n ORDER BY n), '{}') "
                "  FROM house_layouts, unnest(niches) n")
        return rows, matched, counts, by_type, niches

    try:
        rows, matched, counts, by_type, niches = await _read(_CATALOGUE_COLS)
    except Exception as exc:  # noqa: BLE001 — narrowed just below
        # Only the one failure 071 explains, and only when nothing asked for the
        # columns it adds: a harvest filter on a database without them is an error.
        if (type(exc).__name__ != "UndefinedColumnError"
                or harvested is not None or run_id):
            raise
        logger.warning("house_layouts has no harvest columns yet (migration 071)")
        rows, matched, counts, by_type, niches = await _read(_CATALOGUE_COLS_PRE_071)

    out = []
    for r in rows:
        d = dict(r)
        # jsonb arrives as text unless a codec is registered; the screen needs an
        # object to draw a preview from, so parse it here rather than in every caller.
        for key in ("spec", "harvest_meta"):
            if isinstance(d.get(key), str):
                try:
                    d[key] = json.loads(d[key])
                except ValueError:
                    d[key] = {}
        out.append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()})

    return {
        "total": sum(int(c["n"]) for c in counts),
        "matched": int(matched or 0),
        "by_status": {c["status"]: int(c["n"]) for c in counts},
        "by_type": {(t["layout_type"] or "(unnamed)"): int(t["n"]) for t in by_type},
        "niches": list(niches or []),
        "layouts": out,
    }


async def review(layout_id: str, verdict: str, *, by: str = "", note: str = "") -> dict:
    """A curator's verdict. 'rejected' keeps the row and its reason; 'discard'
    removes it outright — the one place in this system that truly deletes, and it
    is deliberate: a curated pool that cannot forget is not curated."""
    if verdict not in ("approved", "rejected", "discard"):
        return {"ok": False, "reason": "verdict must be approved, rejected or discard"}
    async with acquire(None) as conn:
        if verdict == "discard":
            gone = await conn.fetchval(
                "DELETE FROM house_layouts WHERE id = $1::uuid RETURNING id", layout_id)
            return {"ok": bool(gone), "discarded": bool(gone)}
        # Approving with no note of their own keeps an upload gate's "not
        # drawable: ..." note. The approval is the curator's call, but wiping the
        # reason left an approved row every pick skips with no record of why.
        got = await conn.fetchval(
            "UPDATE house_layouts SET status = $2, reviewed_by = $3, "
            "review_note = CASE WHEN $2 = 'approved' AND $4 = '' "
            "                    AND coalesce(review_note, '') LIKE 'not drawable:%' "
            "                   THEN review_note ELSE $4 END, "
            "reviewed_at = now(), updated_at = now() WHERE id = $1::uuid RETURNING id",
            layout_id, verdict, by[:200], note[:500])
    return {"ok": bool(got), "status": verdict if got else None}


def _adopt_kind(source_kind) -> str:
    """The catalogue's source_kind, translated into one a brand's library accepts.

    Stripped, and blank falls back: `str(x or "competitor")` catches None and ""
    but hands a whitespace-only value straight through to a CHECK that rejects it.
    """
    kind = str(source_kind or "").strip() or "competitor"
    return ADOPT_SOURCE_KIND.get(kind, kind)


async def adopt(tenant_id: UUID | str | None, *, limit: int = ADOPT_BATCH,
                niches: list[str] | None = None) -> dict:
    """Fork approved catalogue layouts into this brand's own library.

    NICHE-RANKED, not niche-filtered. A layout tagged for this brand's niche goes
    first, an untagged one next (generic, suits anybody), an off-niche one last.
    Filtering strictly would starve almost everyone — 12 of the 17 live pool rows
    carry no tags at all — so a brand always gets its batch, just the best-fitting
    rows in it.

    Idempotent per layout: `design_templates_house_uniq` makes a second adoption
    of the same house layout a no-op, which is what lets this run on every pick
    that finds the library short rather than once at signup. Seeding once at
    signup was the obvious design and it does not work — a brand can fall back
    below the minimum later, and nothing would top it up.
    """
    if not tenant_id:
        return {"adopted": 0, "reason": "no tenant"}
    want = max(1, min(int(limit), 50))

    # What this brand is in. The caller may name it; otherwise the brand tells us
    # itself, from the niche recorded against its own competitors.
    if niches is None:
        async with acquire(tenant_id) as conn:
            niches = await tenant_niches(conn, tenant_id)

    async with acquire(None) as conn:
        rows = await conn.fetch(
            """SELECT id::text, kind, spec, fingerprint, source_kind, source_url,
                      source_image_uri, niches
                 FROM house_layouts
                WHERE status = 'approved'
             ORDER BY (approvals - rejections) DESC, adopted_count DESC, created_at
                LIMIT $1""", CANDIDATE_POOL)
    if not rows:
        return {"adopted": 0, "reason": "the catalogue has no approved layouts yet"}

    # Rank by fit, then take the batch. sorted() is stable, so layouts of equal
    # fit keep the order the query gave them — best-performing first.
    ranked = sorted(rows, key=lambda r: -niche_rank(niches, r["niches"]))[:want]

    taken = []
    for r in ranked:
        spec = r["spec"]
        if isinstance(spec, str):
            spec = json.loads(spec)
        async with acquire(tenant_id) as conn:
            # ON CONFLICT DO NOTHING on the (tenant, house_layout_id) index makes
            # re-adoption free; RETURNING tells us whether anything landed.
            new_id = await conn.fetchval(
                """INSERT INTO design_templates
                       (kind, spec, fingerprint, source_kind, source_url,
                        source_image_uri, status, house_layout_id)
                   VALUES ($1, $2::jsonb, $3, $4, $5, $6, 'active', $7::uuid)
                   ON CONFLICT DO NOTHING
                   RETURNING id""",
                str(r["kind"] or ""), json.dumps(spec), str(r["fingerprint"] or ""),
                _adopt_kind(r["source_kind"]), str(r["source_url"] or ""),
                str(r["source_image_uri"] or ""), r["id"])
        if new_id:
            taken.append(r["id"])
    if taken:
        async with acquire(None) as conn:
            await conn.execute(
                "UPDATE house_layouts SET adopted_count = adopted_count + 1, "
                "updated_at = now() WHERE id = any($1::uuid[])", taken)
    return {"adopted": len(taken), "offered": len(ranked),
            "considered": len(rows), "matched_on": list(niches or [])}


__all__ = ["SHAREABLE", "ADOPT_BATCH", "family_key", "promote", "ingest", "retag",
           "retype", "catalogue", "review", "adopt", "ADOPT_SOURCE_KIND",
           "niche_tokens", "niche_rank", "tenant_niches",
           "type_profile", "fit_rank", "candidates", "adopt_one",
           "interleave_families", "clean_niches", "CANDIDATE_POOL", "MAX_NICHES",
           "RECENT_FAMILIES", "HOUSE_FRESH_DAYS", "is_fresh", "undrawable_reason",
           "for_brand", "with_labels", "counted_tag_sql", "LABELS_KEY", "record_verdict", "record_outcome", "outcomes_for",
           "outcome_score", "outcome_evidence", "outcome_term",
           "OUTCOME_MIN_EVIDENCE", "OUTCOME_STEP", "outcome_keys", "lift_of", "template_outcome",
           "OUTCOME_NEUTRAL", "LIFT_MAX", "infer_niches_backfill"]


# ───────────────────────────────── the pool as a source, not a top-up ──
#
# Roy, 2026-10-07: "I do not want the user to pick templates. We are building an
# intelligent system, so it should adopt and pick it by itself. And whenever a
# new brand onboards, it should learn what kind of templates it should make."
#
# So the catalogue stops being a shelf that gets restocked when it runs low and
# becomes a SOURCE the picker reads every time, ranked by how well each layout
# suits this brand. The threshold was never a judgement about fit — it only ever
# asked "is the shelf low", which is why a brand with 242 layouts could never see
# a new template however well it matched.
#
# A row is copied into the brand at the MOMENT IT IS PICKED, not in a speculative
# batch of eight. The copy is still necessary (see WHY FORK above: the counters
# are per-brand and RLS makes a shared row silently unwritable), but copying on
# use means a brand only ever owns what it actually drew.


def type_profile(specs) -> dict:
    """What KINDS of post this brand's own evidence says it makes.

    Classifies the layouts a brand has learned from its own niche and counts the
    types. This is the "learn what it should make" half: a golf resort whose
    competitors post offers and stats gets offer and stat layouts from the
    catalogue, not testimonials, without anyone saying so.

    Derived rather than stored — it moves as the brand learns, and there is
    nothing to migrate or keep in step.
    """
    from .layout_types import classify

    out: dict[str, int] = {}
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        t = classify(spec).get("type") or ""
        if t and t != "other":
            out[t] = out.get(t, 0) + 1
    return out


def is_fresh(created_at, *, now: datetime | None = None) -> int:
    """1 if a catalogue row was created within HOUSE_FRESH_DAYS, else 0.

    Tolerant: a datetime (asyncpg), an ISO string, or nothing at all — the
    picker's tests build rows by hand, and a missing date must read as "not
    new", never as an exception in the path that produces every designed post.
    """
    if not created_at:
        return 0
    when = created_at
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if not isinstance(when, datetime):
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return 1 if now - when <= timedelta(days=HOUSE_FRESH_DAYS) else 0


def fit_rank(brand_niches, profile: dict, layout_niches, layout_type: str,
             created_at=None, *, now: datetime | None = None,
             outcome: float | None = None) -> tuple:
    """How well one catalogue layout suits this brand. Higher sorts first.

    (niche, new, outcome, type, popularity) as a tuple so the comparison is
    explicit and testable rather than a weighted sum nobody can reason about:

      niche  — niche_rank: matching > untagged > tagged for someone else
      new    — created within HOUSE_FRESH_DAYS. Right AFTER niche on purpose:
               the latest layouts reach a brand before older ones of the SAME
               fit, but a fresh off-niche row never jumps a matching one.
      outcome — what owners in this brand's niche said and what its posts
               measured (outcome_term: neutral until there are a few results,
               then the smoothed score in coarse steps). After `new` so a result
               never buries the latest rows; a caller that passes none gets
               OUTCOME_NEUTRAL and today's order.
      type   — does this brand's own library already contain this KIND of post?
               A type the brand demonstrably uses beats one it never makes.
      pop    — how common that type is in its library, as the tiebreak.
    """
    t = str(layout_type or "")
    seen = int(profile.get(t, 0)) if profile else 0
    # A brand with no profile yet (day one) scores every type 0, so niche alone
    # decides — which is the right answer when there is no evidence to use.
    score = OUTCOME_NEUTRAL if outcome is None else float(outcome)
    return (niche_rank(brand_niches, layout_niches), is_fresh(created_at, now=now),
            score, 1 if seen else 0, seen)


def interleave_families(rows: list[dict]) -> list[dict]:
    """The same order, except that one family_key never appears twice in a row
    while an alternative is left.

    Greedy and stable: at each step the best remaining row is taken unless it is
    of the family just placed, in which case the best remaining row of ANY other
    family goes first. Rows with no family_key never conflict with anything.
    """
    out: list[dict] = []
    rest = list(rows)
    while rest:
        last = str((out[-1].get("family_key") if out else "") or "")
        idx = 0
        if last:
            for i, r in enumerate(rest):
                if str(r.get("family_key") or "") != last:
                    idx = i
                    break
        out.append(rest.pop(idx))
    return out


async def candidates(
    conn, tenant_id, *, niches=None, profile: dict | None = None, limit: int = 40,
    recent_families=None, recent_types=None,
) -> list[dict]:
    """Approved catalogue layouts this brand has NOT already taken, best fit first.

    `conn` must be the TENANT's connection: the "already taken" check reads
    design_templates, which is RLS-scoped.

    `recent_families` are the family_keys of the brand's most recently used
    layouts. Among rows of equal niche fit, a family the brand has NOT just drawn
    sorts first, and the final order never repeats a family back to back while an
    alternative exists.
    """
    if not tenant_id:
        return []
    # "Already taken" is BOTH: a row forked from this catalogue entry, and a
    # layout of the same SHAPE the brand learned for itself. Checking only
    # house_layout_id offers back shapes it already has — measured on
    # one travel brand, 2026-10-07, 12 of the pool's fingerprints were already in
    # its own library — and `design_templates_fingerprint_uniq` then refuses the
    # insert, so the pick could never be recorded against anything.
    mine = await conn.fetch(
        "SELECT house_layout_id::text h, fingerprint FROM design_templates")
    taken = {r["h"] for r in mine if r["h"]}
    held = {r["fingerprint"] for r in mine if r["fingerprint"]}
    # Exact tag overlap is only a COARSE pre-sort (tags are free text); it decides
    # which rows survive the LIMIT, and fit_rank's token match below decides the
    # real order. The writers spell a niche differently (whole vs comma-split),
    # so every stored spelling of each brand niche is offered — see brand_tag_keys.
    brand_tags = brand_tag_keys(niches)

    # The exclusion is IN SQL, before the LIMIT. Done in Python after it, a brand
    # that had taken the first CANDIDATE_POOL rows was offered nothing at all.
    async with acquire(None) as pool_conn:
        rows = await pool_conn.fetch(
            """SELECT id::text, kind, spec, fingerprint, family_key, source_kind,
                      source_url, source_image_uri, niches, layout_type,
                      (approvals - rejections) AS score, adopted_count, created_at
                 FROM house_layouts
                WHERE status = 'approved'
                  AND NOT (id::text = ANY($1::text[]))
                  AND NOT (fingerprint <> '' AND fingerprint = ANY($2::text[]))
             ORDER BY (niches && $3::text[]) DESC,
                      (approvals - rejections) DESC,
                      adopted_count DESC,
                      created_at DESC
                LIMIT $4""",
            sorted(taken), sorted(held), brand_tags, CANDIDATE_POOL)
        # Every candidate's results in ONE query, not one per row.
        results = await outcomes_for([r["id"] for r in rows or []], conn=pool_conn)

    # Belt and braces: the same exclusion again, so a row the SQL let through
    # (or a stand-in connection that ignores the arguments) is still never offered.
    out = [dict(r) for r in rows
           if r["id"] not in taken and str(r["fingerprint"] or "") not in held]
    recent = {str(f) for f in (recent_families or []) if f}
    # Layout TYPES the brand drew most recently (offer_card, testimonial, …). Every
    # adoption so far was one of two types while five others sat unused, so a
    # type the brand has NOT drawn lately wins a tie — inside the same niche,
    # recency and outcome tier, never across them.
    recent_t = {str(t) for t in (recent_types or []) if t}

    now = datetime.now(UTC)
    labels = niche_vocab.canonical(niches)

    def _rank(r: dict) -> tuple:
        niche, new, outcome, *rest = fit_rank(
            niches, profile or {}, r.get("niches"), r.get("layout_type"),
            r.get("created_at"), now=now,
            outcome=outcome_term(results.get(str(r["id"]), []), labels))
        fam = str(r.get("family_key") or "")
        fresh = 0 if (fam and fam in recent) else 1
        lt = str(r.get("layout_type") or "")
        type_fresh = 0 if (lt and lt in recent_t) else 1
        return (niche, new, outcome, type_fresh, fresh, *rest)

    out.sort(key=_rank, reverse=True)
    return interleave_families(out)[: max(1, int(limit))]


async def adopt_one(conn, layout: dict) -> str | None:
    """Copy ONE catalogue row into the brand, at the moment it is picked.

    Returns the id of the BRAND'S row — the one the caller will mark as used —
    never the catalogue's. Those are different tables, and `mark_used` on a
    catalogue id updates ZERO rows and says nothing, so the layout's counters
    would never move and the picker would offer it forever.

    `conn` must be the tenant's. Idempotent two ways: on
    design_templates_house_uniq for a second adoption of the same catalogue row,
    and on design_templates_fingerprint_uniq when the brand already learned that
    shape itself. Either conflict means the brand HAS the layout, so the existing
    row is found and returned rather than treated as a failure.
    """
    spec = layout["spec"]
    if isinstance(spec, str):
        spec = json.loads(spec)
    new_id = await conn.fetchval(
        """INSERT INTO design_templates
               (kind, spec, fingerprint, source_kind, source_url,
                source_image_uri, status, house_layout_id)
           VALUES ($1, $2::jsonb, $3, $4, $5, $6, 'active', $7::uuid)
           ON CONFLICT DO NOTHING
           RETURNING id::text""",
        str(layout["kind"] or ""), json.dumps(spec), str(layout["fingerprint"] or ""),
        _adopt_kind(layout["source_kind"]), str(layout["source_url"] or ""),
        str(layout["source_image_uri"] or ""), layout["id"])
    if new_id:
        # A real adoption, so count it like adopt() does — the curation screen
        # reads adopted_count to see what brands actually take, and the pick-time
        # path (every adoption since 2026-10-07) never moved it. Best effort and
        # on the catalogue's own connection: the tenant's cannot be trusted to
        # write a platform row, and a counter must never cost the pick.
        try:
            async with acquire(None) as pool_conn:
                await pool_conn.execute(
                    "UPDATE house_layouts SET adopted_count = adopted_count + 1, "
                    "updated_at = now() WHERE id = $1::uuid", layout["id"])
        except Exception:  # noqa: BLE001
            logger.warning("could not count the adoption of %s", layout.get("id"),
                           exc_info=True)
        return new_id
    # Conflicted: this brand already holds it, by provenance or by shape.
    return await conn.fetchval(
        "SELECT id::text FROM design_templates "
        "WHERE house_layout_id = $1::uuid "
        "   OR ($2 <> '' AND fingerprint = $2) LIMIT 1",
        layout["id"], str(layout["fingerprint"] or ""))




# ─────────────────────────────── the catalogue as one brand would see it ──
#
# The picker draws from the catalogue one row at a time, ranked and rotated, and
# nobody can see which rows a brand would be offered or ask for one by name. The
# showcase does exactly that: BM2 lists what suits a brand, then renders a chosen
# row through /v1/generate with house_layout_id (design_templates.pick_house).

FOR_BRAND_MAX = 20
_TIER_ORDER = {"niche": 0, "general": 1, "off_niche": 2}


def _tier(rank: int) -> str:
    """niche_rank as the showcase names it: matching / untagged / someone else's."""
    return "niche" if rank >= 2 else ("general" if rank == 1 else "off_niche")


async def for_brand(tenant_id, *, limit: int = 8) -> dict:
    """Approved, drawable catalogue layouts this brand could be shown next.

    Ordered by tier — on-niche, then untagged (generic), then tagged for someone
    else — and within a tier LATEST FIRST, so the nightly harvest's new rows are
    what a brand sees. A layout the brand already holds and has DRAWN
    (times_used > 0) is left out: showing it again is not showing anything new.
    One it holds but never drew is kept, flagged already_forked, and a pin
    reuses that copy. A held copy that is paused or retired is left out too —
    pick_house refuses it, so offering it would only produce a fallback.

    "Holds" means by provenance (house_layout_id) OR by shape (fingerprint): a
    brand that learned the same layout itself owns it already, and adopt_one
    hands back that row.

    Tenant-scoped: the brand's niche and its holdings are read on ITS connection
    (RLS); the catalogue, which has no tenant, on the unscoped one.

    This is the one TENANT route that reads the catalogue's own metadata, and a
    row's tags and reference image can come from OTHER brands: the harvest tags
    a row with the niche of the brand it ran for, and a promoted row keeps the
    promoting brand's stored image (/media-files/<their tenant>/...). So a brand
    is shown only the tags that share a word with its own niche, and the image
    only when the catalogue owns it (uploaded or harvested, promoted_from NULL)
    or this brand promoted it. The layout itself is shared by design; who else
    uses it is not.
    """
    if not tenant_id:
        return {"niche": [], "layouts": []}
    from . import design_templates as dt
    from .layout_types import classify, display_name

    want = max(1, min(int(limit), FOR_BRAND_MAX))
    async with acquire(tenant_id) as conn:
        niches = await tenant_niches(conn, tenant_id)
        mine = await conn.fetch(
            "SELECT house_layout_id::text h, fingerprint, times_used, status "
            "  FROM design_templates")

    # held key -> may it still be shown? Keyed by provenance AND by shape; a
    # key held twice is showable only if every copy is unused and active.
    held: dict[str, bool] = {}
    for r in mine or []:
        ok = int(r["times_used"] or 0) == 0 and str(r["status"] or "") == "active"
        for key in (f"h:{r['h']}" if r["h"] else "", f"f:{r['fingerprint']}"
                    if r["fingerprint"] else ""):
            if key:
                held[key] = held.get(key, True) and ok

    async with acquire(None) as pool_conn:
        rows = await pool_conn.fetch(
            """SELECT id::text, kind, spec, fingerprint, source_kind,
                      source_image_uri, niches, layout_type, label, title, created_at,
                      promoted_from::text AS promoted_from
                 FROM house_layouts
                WHERE status = 'approved'
             ORDER BY (niches && $1::text[]) DESC, created_at DESC
                LIMIT $2""",
            brand_tag_keys(niches), CANDIDATE_POOL)
        results = await outcomes_for([r["id"] for r in rows or []], conn=pool_conn)

    me = str(tenant_id).lower()
    mine_tokens = niche_tokens(niches)
    # A vocabulary label ('golf', 'travel') is generic, not another brand's
    # phrase, so it is shown when it is one of THIS brand's labels.
    mine_labels = set(niche_vocab.canonical(niches))
    out: list[dict] = []
    for r in rows or []:
        hid, fp = str(r["id"]), str(r["fingerprint"] or "")
        keys = [k for k in (f"h:{hid}", f"f:{fp}" if fp else "") if k in held]
        forked = bool(keys)
        if not all(held[k] for k in keys):
            continue
        spec = r["spec"]
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                continue
        if not isinstance(spec, dict) or not dt.drawable(spec):
            continue
        ltype = str(r["layout_type"] or "") or classify(spec)["type"]
        created = r["created_at"]
        tags = [str(t) for t in (r["niches"] or []) if t is not None and str(t).strip()]
        promoter = str(r.get("promoted_from") or "").lower()
        out.append({
            "id": hid,
            "created_at": created.isoformat() if hasattr(created, "isoformat")
            else str(created or ""),
            # Ranked on every tag; SHOWN only the ones that are this brand's own.
            "niches": [t for t in tags
                       if niche_tokens(t) & mine_tokens or t.lower() in mine_labels],
            "type": ltype,
            "name": (str(r["title"] or "").strip() or str(r["label"] or "").strip()
                     or display_name(ltype)),
            "source_kind": str(r["source_kind"] or ""),
            "image_url": (str(r["source_image_uri"] or "")
                          if not promoter or promoter == me else ""),
            "tier": _tier(niche_rank(niches, tags)),
            "already_forked": forked,
            "_ts": created,
            "_new": is_fresh(created),
        })

    def _ts(d: dict) -> float:
        t = d["_ts"]
        if isinstance(t, str):
            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                return 0.0
        if isinstance(t, datetime):
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            return t.timestamp()
        return 0.0

    # Latest first, then a STABLE sort by tier keeps that order inside each tier.
    # Inside a tier, fresh rows first and then what owners and posts in this
    # brand's niche rated better (fit_rank's order). With no results every score
    # is neutral and fresh rows are the newest anyway, so the order is exactly
    # latest-first, as before.
    for d in out:
        d["_score"] = outcome_term(results.get(d["id"], []), mine_labels)
    out.sort(key=_ts, reverse=True)
    out.sort(key=lambda d: (_TIER_ORDER[d["tier"]], -d["_new"], -d["_score"]))
    for d in out:
        for k in ("_ts", "_new", "_score"):
            d.pop(k, None)
    return {"niche": list(niches), "layouts": out[:want]}


# ───────────────────────────────────────── learning from results, per niche ──
#
# What a layout EARNED, kept per catalogue row and per canonical niche: owners'
# approvals and rejections of posts drawn on it (design_templates.mark_verdict),
# and measured engagement against the brand's baseline (/outcome). Per niche
# because "approved by golf brands" says nothing about a law firm.
#
# Measured 2026-10-08: no owner has ever sent a verdict on a learned post, and
# 20 of 1845 BM2 artifacts carry metrics, none learned. So this fills as data
# arrives, and the score is built for that: priors make NO data exactly neutral
# and keep ONE result from swinging the order.

OUTCOME_NEUTRAL = 0.5          # (0+1)/(0+0+2) x (0+2)/(0+2): no evidence at all
LIFT_MAX = 5.0                 # one viral post must not read as a 50x layout

# Conditional on the catalogue row still existing. design_templates.house_layout_id
# has no foreign key (067 adds a bare uuid) and review 'discard' and harvest
# revoke hard-delete catalogue rows, so a brand's fork can point at nothing. A
# plain INSERT then raised ForeignKeyViolation and /outcome answered 500 on every
# retry — and BM2 marks only what succeeded, so it would retry that post forever.
_OUTCOME_UPSERT = """
    INSERT INTO house_layout_outcomes AS o
           (house_layout_id, niche, approvals, rejections, measured, lift_sum)
    SELECT $1::uuid, n, $3, $4, $5, $6::numeric FROM unnest($2::text[]) AS n
     WHERE EXISTS (SELECT 1 FROM house_layouts h WHERE h.id = $1::uuid)
    ON CONFLICT (house_layout_id, niche) DO UPDATE SET
        approvals  = o.approvals  + EXCLUDED.approvals,
        rejections = o.rejections + EXCLUDED.rejections,
        measured   = o.measured   + EXCLUDED.measured,
        lift_sum   = o.lift_sum   + EXCLUDED.lift_sum,
        updated_at = now()"""


def outcome_keys(niches) -> list[str]:
    """The niche keys a result is filed under: the brand's canonical labels, or
    '' for a brand whose niche maps to nothing (so its results still count for
    brands that likewise have none, instead of vanishing)."""
    return niche_vocab.canonical(niches) or [""]


def _rows_written(status) -> int:
    try:
        return int(str(status or "").rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def _upsert_outcome(house_layout_id: str, niches, *, a: int, r: int,
                          m: int, lift: float) -> list[str]:
    """The keys written, or [] when the catalogue row no longer exists."""
    import asyncpg

    keys = outcome_keys(niches)
    try:
        async with acquire(None) as conn:
            status = await conn.execute(_OUTCOME_UPSERT, str(house_layout_id), keys,
                                        a, r, m, float(lift))
    except asyncpg.ForeignKeyViolationError:
        return []       # discarded between the EXISTS and the write: same answer
    return keys if _rows_written(status) else []


async def record_verdict(house_layout_id: str, niches, approved: bool) -> list[str]:
    """One owner verdict on a post drawn from this catalogue row, filed under each
    of the brand's canonical niches. Returns the keys written ([] when the row
    has been discarded)."""
    return await _upsert_outcome(house_layout_id, niches, a=1 if approved else 0,
                                 r=0 if approved else 1, m=0, lift=0.0)


async def record_outcome(house_layout_id: str, niches, lift: float) -> list[str]:
    """One measured result (lift = engagement / the brand's baseline), filed under
    each of the brand's canonical niches. Returns the keys written ([] when the
    row has been discarded)."""
    return await _upsert_outcome(house_layout_id, niches, a=0, r=0, m=1,
                                 lift=max(0.0, min(LIFT_MAX, float(lift))))


def lift_of(engagement, baseline) -> float:
    """engagement / baseline, clamped to [0, LIFT_MAX]. No usable baseline is 1.0:
    'no information', but still counted as one measured result."""
    try:
        e, b = float(engagement), float(baseline or 0)
    except (TypeError, ValueError):
        return 1.0
    if not (b > 0) or e != e or b == float("inf"):
        return 1.0
    return round(max(0.0, min(LIFT_MAX, e / b)), 4)


_OUTCOMES_SQL = """SELECT house_layout_id::text AS h, niche, approvals, rejections,
                          measured, lift_sum
                     FROM house_layout_outcomes
                    WHERE house_layout_id = ANY($1::uuid[])"""


async def outcomes_for(ids, *, conn=None) -> dict[str, list[dict]]:
    """{house_layout_id: [{niche, approvals, rejections, measured, lift_sum}]} for
    every id, in ONE query. Fail-soft to {} — every score neutral, today's order —
    because the picker must not stop drawing when this table is missing (code
    deployed before migration 073) or unreadable.

    `conn` is the catalogue connection the caller already holds; the read runs
    in a SAVEPOINT on it, so a missing table cannot abort the caller's
    transaction, and the picker pays for no second connection."""
    want = sorted({str(i) for i in ids or [] if i})
    if not want:
        return {}
    try:
        if conn is not None:
            async with conn.transaction():
                rows = await conn.fetch(_OUTCOMES_SQL, want)
        else:
            async with acquire(None) as own:
                rows = await own.fetch(_OUTCOMES_SQL, want)
        out: dict[str, list[dict]] = {}
        for r in rows or []:
            out.setdefault(str(r["h"]), []).append({
                "niche": str(r["niche"] or ""), "approvals": int(r["approvals"] or 0),
                "rejections": int(r["rejections"] or 0), "measured": int(r["measured"] or 0),
                "lift_sum": float(r["lift_sum"] or 0)})
        return out
    except Exception:  # noqa: BLE001 — neutral ordering beats no ordering
        logger.warning("house_layout_outcomes unreadable; ranking without results",
                       exc_info=True)
        return {}


def outcome_evidence(rows, brand_labels) -> tuple[int, int, int, float]:
    """(approvals, rejections, measured, lift_sum) for this brand, each result
    counted ONCE.

    A result is filed under EVERY label of the brand that sent it ('golf resort'
    -> golf AND hospitality), so summing a reader's labels counted it once per
    label it shared: one rejection read 0.33 for a 'golf' brand, 0.25 for
    'commercial real estate' (2 labels) and 0.20 for 'parenting humor' (3), and
    one lift-5 post read 1.17 / 1.5 / 1.7. 10 of the 14 live brand niches map to
    2+ labels, so the priors' smoothing was halved for most brands. Instead the
    brand reads the ONE of its labels with the most evidence: a brand whose
    labels were all written together sees each result once, and a brand that
    shares only 'golf' with a golf-course brand still reads golf's record.
    """
    keys = set(brand_labels or []) or {""}
    best: tuple[int, int, int, float] = (0, 0, 0, 0.0)
    # Walked in label order and replaced only on MORE evidence, so equal evidence
    # goes to the alphabetically first label whatever order the rows came in.
    mine = sorted((o for o in rows or [] if str(o.get("niche") or "") in keys),
                  key=lambda o: str(o.get("niche") or ""))
    for o in mine:
        ev = (int(o.get("approvals") or 0), int(o.get("rejections") or 0),
              int(o.get("measured") or 0), float(o.get("lift_sum") or 0))
        if ev[0] + ev[1] + ev[2] > best[0] + best[1] + best[2]:
            best = ev
    return best


def outcome_score(rows, brand_labels) -> float:
    """Smoothed approval rate x smoothed lift, from this brand's best-evidenced
    niche ('' when it has none) — see outcome_evidence for why not a sum.

      verdict = (a + 1) / (a + r + 2)        no verdicts      -> 0.5
      perf    = (lift_sum + 2) / (measured + 2)   no measurements -> 1.0

    So no data is OUTCOME_NEUTRAL, five golf approvals read 0.86 for any golf
    brand and stay 0.5 for a real-estate one, and one rejection reads 0.33
    whether the brand has one label or three.
    """
    a, r, m, lift = outcome_evidence(rows, brand_labels)
    return round(((a + 1) / (a + r + 2)) * ((lift + 2) / (m + 2)), 2)


# Results needed before the score may move a row at all, and the step it moves in.
# The sort is a strict tuple: smoothing made one rejection read 0.33 instead of 0,
# but 0.33 < 0.5 still dropped that row below EVERY unrated row of equal fit and
# freshness, and in the picker above family rotation and type fit too — one
# result swung the order outright. Below the minimum the term is neutral; above
# it, scores within a step of each other tie and the later terms decide.
OUTCOME_MIN_EVIDENCE = 3
OUTCOME_STEP = 0.1


def outcome_term(rows, brand_labels) -> float:
    """The value fit_rank sorts on: OUTCOME_NEUTRAL until this brand's niche has
    OUTCOME_MIN_EVIDENCE results on the row, then outcome_score in OUTCOME_STEP
    steps."""
    a, r, m, _ = outcome_evidence(rows, brand_labels)
    if a + r + m < OUTCOME_MIN_EVIDENCE:
        return OUTCOME_NEUTRAL
    return round(round(outcome_score(rows, brand_labels) / OUTCOME_STEP) * OUTCOME_STEP, 2)


async def template_outcome(tenant_id, template_id: str, engagement, baseline) -> dict | None:
    """A measured result for a post drawn on one of THIS brand's layouts.

    None when the brand holds no such row (the route's 404 — RLS hides another
    brand's). A layout the brand learned itself has no catalogue row to credit,
    so nothing is recorded and that is said: house_layout_id None, niches [].
    """
    lift = lift_of(engagement, baseline)
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT house_layout_id::text AS h FROM design_templates WHERE id = $1::uuid",
            str(template_id))
        if not row:
            return None
        hid = row["h"]
        niches = await tenant_niches(conn, tenant_id) if hid else []
    if not hid:
        return {"ok": True, "house_layout_id": None, "niches": [], "lift": lift,
                "recorded": False}
    keys = await record_outcome(hid, niches, lift)
    if not keys:
        # The fork outlived its catalogue row (discarded or revoked). Nothing to
        # credit, and saying ok lets the caller mark the post instead of retrying
        # a write that can never succeed.
        return {"ok": True, "house_layout_id": None, "niches": [], "lift": lift,
                "recorded": False, "reason": "catalogue row discarded"}
    return {"ok": True, "house_layout_id": hid, "niches": keys, "lift": lift,
            "recorded": True}


# ─────────────────────────────────────── backfill: tag what nobody tagged ──

INFER_CONCURRENCY = 4
INFER_MAX = 1000
INGEST_READ_TIMEOUT = 25.0
# Where the backfill notes a read that wrote no tags, so the next run moves on:
# {"at": iso, "status": "generic" | "failed"}. Without it the newest untagged
# rows — quote cards the model rightly calls generic, or images whose stored copy
# is gone — were picked first on EVERY run, so once `limit` of them piled up the
# backfill paid for the same reads forever and never reached older uploads.
READ_KEY = "niche_read"
# A failed read (unreachable image, model error) is worth one more try later.
FAILED_READ_RETRY = timedelta(days=7)


def _labels_of(row) -> set[str]:
    try:
        got = row["labels"]
    except (KeyError, IndexError):
        return set()
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            return set()
    return {str(t) for t in got or []} if isinstance(got, list) else set()


async def infer_niches_backfill(*, limit: int = 100, dry_run: bool = False) -> dict:
    """Give approved catalogue rows vocabulary labels.

    Untagged rows (313 of 324 approved uploads on 2026-10-08) are READ: one
    detail-"low" gpt-4o call on the stored image, at most `limit` of them, four
    at a time. Rows that carry text tags but not their labels get the labels
    from the words, with no call. dry_run reads nothing and writes nothing: it
    counts and prices.

    Refuses the PAID half, and says why, when there is no OpenAI key or
    PAUSE_SPEND is set; the free half still runs. A row the model says carries
    nothing industry-specific stays untagged (generic) and is noted, so it is
    never read again; a failed read is retried after FAILED_READ_RETRY.
    """
    from .config import settings

    limit = max(1, min(int(limit), INFER_MAX))
    async with acquire(None) as conn:
        rows = await conn.fetch(
            f"""SELECT id::text, niches, source_image_uri,
                       COALESCE(harvest_meta->'{LABELS_KEY}', '[]'::jsonb) AS labels,
                       harvest_meta->'{READ_KEY}' AS read_note
                  FROM house_layouts
                 WHERE status = 'approved' ORDER BY created_at DESC""")
    empty, relabel = [], []
    for r in rows or []:
        tags = clean_niches(r["niches"])
        if not tags:
            empty.append(r)
            continue
        want = clean_niches(tags + niche_vocab.canonical(tags))
        if want != list(r["niches"] or []):
            had = _labels_of(r)
            relabel.append((r, want, [t for t in want if t in had or t not in tags]))
    def _note(r) -> dict:
        try:
            n = r["read_note"]
        except (KeyError, IndexError):
            return {}
        if isinstance(n, str):
            try:
                n = json.loads(n)
            except ValueError:
                return {}
        return n if isinstance(n, dict) else {}

    now = datetime.now(UTC)
    never, retry, judged_generic = [], [], 0
    for r in empty:
        if not str(r["source_image_uri"] or "").strip():
            continue
        note = _note(r)
        if note.get("status") == "generic":
            judged_generic += 1           # read once, nothing industry-specific: done
            continue
        if note.get("status") == "failed":
            try:
                at = datetime.fromisoformat(str(note.get("at")))
            except ValueError:
                at = None
            if at and (now - (at if at.tzinfo else at.replace(tzinfo=UTC))) < FAILED_READ_RETRY:
                continue                  # cooling off
            retry.append(r)
            continue
        never.append(r)
    # Never-read rows first, then failures whose cool-off has passed.
    to_read = (never + retry)[:limit]
    per = niche_vocab.est_usd_per_image()
    report = {"scanned": len(rows or []), "inferred": 0, "labelled_from_text": 0,
              "still_untagged": len(empty), "failed": 0, "est_usd": 0.0,
              "judged_generic": judged_generic, "dry_run": bool(dry_run)}

    if dry_run:
        report.update(inferred=len(to_read), labelled_from_text=len(relabel),
                      still_untagged=len(empty) - len(to_read),
                      est_usd=round(len(to_read) * per, 4))
        return report

    labelled = 0
    for r, want, added in relabel:
        async with acquire(None) as conn:
            # Only if the tags are still what was read: a curator's retag since
            # wins over a backfill computed from the old ones.
            got = await conn.fetchval(
                "UPDATE house_layouts SET niches = $2::text[], "
                f"harvest_meta = jsonb_set(harvest_meta, '{{{LABELS_KEY}}}', $4::jsonb), "
                "updated_at = now() "
                "WHERE id = $1::uuid AND niches = $3::text[] RETURNING id",
                r["id"], want, list(r["niches"] or []), json.dumps(added))
        labelled += 1 if got else 0
    report["labelled_from_text"] = labelled

    if to_read and not niche_vocab.has_key():
        report["vision_skipped"] = "no OpenAI key: untagged rows were not read"
        return report
    if to_read and settings.pause_spend:
        report["vision_skipped"] = "PAUSE_SPEND is set: untagged rows were not read"
        return report

    sem = asyncio.Semaphore(INFER_CONCURRENCY)
    calls = inferred = failed = generic = 0

    async def _mark(row_id: str, status: str) -> None:
        try:
            async with acquire(None) as conn:
                await conn.execute(
                    "UPDATE house_layouts SET harvest_meta = jsonb_set("
                    f"COALESCE(harvest_meta, '{{}}'::jsonb), '{{{READ_KEY}}}', $2::jsonb) "
                    "WHERE id = $1::uuid AND cardinality(niches) = 0",
                    row_id, json.dumps({"at": datetime.now(UTC).isoformat(),
                                        "status": status}))
        except Exception:  # noqa: BLE001 — a lost note costs one re-read, nothing more
            logger.warning("could not note the niche read of %s", row_id, exc_info=True)

    async def _one(r) -> None:
        nonlocal calls, inferred, failed, generic
        async with sem:
            got = await niche_vocab.infer_detail(str(r["source_image_uri"]))
        if got.get("status") == "no_key":
            failed += 1
            return
        calls += 1
        if got.get("status") != "ok":
            failed += 1
            await _mark(r["id"], "failed")
            return
        labels = clean_niches(niche_vocab.canonical(got.get("labels") or []))
        if not labels:
            generic += 1
            await _mark(r["id"], "generic")   # read fine: nothing industry-specific
            return
        try:
            async with acquire(None) as conn:
                done = await conn.fetchval(
                    "UPDATE house_layouts SET niches = $2::text[], "
                    f"harvest_meta = jsonb_set(harvest_meta, '{{{LABELS_KEY}}}', $3::jsonb), "
                    "updated_at = now() "
                    "WHERE id = $1::uuid AND cardinality(niches) = 0 RETURNING id",
                    r["id"], labels, json.dumps(labels))
        except Exception:  # noqa: BLE001 — one row's write must not end the run
            logger.warning("could not write inferred niches for %s", r["id"], exc_info=True)
            failed += 1
            return
        inferred += 1 if done else 0

    await asyncio.gather(*(_one(r) for r in to_read))
    report.update(inferred=inferred, failed=failed,
                  judged_generic=judged_generic + generic,
                  still_untagged=len(empty) - inferred, est_usd=round(calls * per, 4))
    return report
