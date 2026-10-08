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

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

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

      2+  shares N meaningful words with the brand's niche (2 + N)
      1   carries NO tags at all — generic, suits anybody
      0   tagged, but for a different niche

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
    shared = niche_tokens(brand_niches) & niche_tokens(tags)
    return 2 + len(shared) if shared else 0


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
    tags = clean_niches(niches)

    async with acquire(None) as conn:
        row = await conn.fetchrow(
            """INSERT INTO house_layouts
                   (kind, spec, fingerprint, family_key, source_kind, source_url,
                    source_image_uri, layout_type, label, title, niches,
                    uploaded_by, status, review_note, reviewed_by, reviewed_at)
               VALUES ($1, $2::jsonb, $3, $4, 'curated', $5, $6, $7, $8, $9, $10,
                       $11, $12, $13, $14, CASE WHEN $12 = 'approved' THEN now() END)
               ON CONFLICT (fingerprint) WHERE fingerprint <> ''
               DO UPDATE SET
                   title       = COALESCE(NULLIF(EXCLUDED.title, ''), house_layouts.title),
                   niches      = """ + _NICHE_UNION_SQL + """,
                   layout_type = EXCLUDED.layout_type,
                   label       = EXCLUDED.label,
                   updated_at  = now()
               RETURNING id::text, (created_at = updated_at) AS fresh, status""",
            str(spec.get("kind") or ""), json.dumps(spec), fp, family_key(spec),
            source_url[:500], image_uri[:500], named["type"], named["label"],
            title[:200], tags, by[:200], status, note[:500],
            by[:200] if status == "approved" else "")
    # The STORED status, read back. A re-upload of an approved shape with
    # approve=False leaves it approved (status is never touched on conflict), and
    # reporting the requested 'candidate' would tell the curator something false.
    stored = row.get("status") if hasattr(row, "get") else None
    return {
        "ok": True, "house_layout_id": row["id"], "duplicate": not row["fresh"],
        "layout_type": named["type"], "label": named["label"],
        "status": str(stored or status), "regions": named["regions"],
        "drawable": can_draw,
    }


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
           "for_brand"]


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
             created_at=None, *, now: datetime | None = None) -> tuple:
    """How well one catalogue layout suits this brand. Higher sorts first.

    (niche, new, type, popularity) as a tuple so the comparison is explicit and
    testable rather than a weighted sum nobody can reason about:

      niche  — niche_rank: matching > untagged > tagged for someone else
      new    — created within HOUSE_FRESH_DAYS. Right AFTER niche on purpose:
               the latest layouts reach a brand before older ones of the SAME
               fit, but a fresh off-niche row never jumps a matching one.
      type   — does this brand's own library already contain this KIND of post?
               A type the brand demonstrably uses beats one it never makes.
      pop    — how common that type is in its library, as the tiebreak.
    """
    t = str(layout_type or "")
    seen = int(profile.get(t, 0)) if profile else 0
    # A brand with no profile yet (day one) scores every type 0, so niche alone
    # decides — which is the right answer when there is no evidence to use.
    return (niche_rank(brand_niches, layout_niches), is_fresh(created_at, now=now),
            1 if seen else 0, seen)


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
    recent_families=None,
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
    # trouvaillertours 2026-10-07, 12 of the pool's fingerprints were already in
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

    # Belt and braces: the same exclusion again, so a row the SQL let through
    # (or a stand-in connection that ignores the arguments) is still never offered.
    out = [dict(r) for r in rows
           if r["id"] not in taken and str(r["fingerprint"] or "") not in held]
    recent = {str(f) for f in (recent_families or []) if f}

    now = datetime.now(UTC)

    def _rank(r: dict) -> tuple:
        niche, new, *rest = fit_rank(niches, profile or {}, r.get("niches"),
                                     r.get("layout_type"), r.get("created_at"), now=now)
        fam = str(r.get("family_key") or "")
        fresh = 0 if (fam and fam in recent) else 1
        return (niche, new, fresh, *rest)

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

    me = str(tenant_id).lower()
    mine_tokens = niche_tokens(niches)
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
            "niches": [t for t in tags if niche_tokens(t) & mine_tokens],
            "type": ltype,
            "name": (str(r["title"] or "").strip() or str(r["label"] or "").strip()
                     or display_name(ltype)),
            "source_kind": str(r["source_kind"] or ""),
            "image_url": (str(r["source_image_uri"] or "")
                          if not promoter or promoter == me else ""),
            "tier": _tier(niche_rank(niches, tags)),
            "already_forked": forked,
            "_ts": created,
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
    out.sort(key=_ts, reverse=True)
    out.sort(key=lambda d: _TIER_ORDER[d["tier"]])
    for d in out:
        d.pop("_ts", None)
    return {"niche": list(niches), "layouts": out[:want]}
