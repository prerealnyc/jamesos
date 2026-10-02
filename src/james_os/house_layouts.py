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
# (the tags are free text, so `&&` on the arrays is useless), so the candidate
# set has to be pulled first — wide enough that a brand's match is not cut off by
# the pre-sort, small enough to stay one cheap query.
CANDIDATE_POOL = 200


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
    status = "approved" if approve else "candidate"
    tags = [t.strip()[:60] for t in (niches or []) if t and t.strip()][:12]

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
                   niches      = CASE WHEN cardinality(EXCLUDED.niches) > 0
                                      THEN EXCLUDED.niches ELSE house_layouts.niches END,
                   layout_type = EXCLUDED.layout_type,
                   label       = EXCLUDED.label,
                   updated_at  = now()
               RETURNING id::text, (created_at = updated_at) AS fresh""",
            str(spec.get("kind") or ""), json.dumps(spec), fp, family_key(spec),
            source_url[:500], image_uri[:500], named["type"], named["label"],
            title[:200], tags, by[:200], status, note[:500],
            by[:200] if approve else "")
    return {
        "ok": True, "house_layout_id": row["id"], "duplicate": not row["fresh"],
        "layout_type": named["type"], "label": named["label"],
        "status": status, "regions": named["regions"],
    }


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


async def catalogue(
    *, status: str = "", layout_type: str = "", niche: str = "",
    limit: int = 200, offset: int = 0,
) -> dict:
    """The catalogue, for the curation screen. No tenant: this is the platform's.

    Filters are AND-ed and each is optional. `by_status` and `by_type` always
    count the WHOLE pool, not the filtered page -- a curator narrowing to
    'candidate' still needs to see how much is approved.
    """
    where, args = [], []
    if status:
        args.append(status); where.append(f"status = ${len(args)}")
    if layout_type:
        args.append(layout_type); where.append(f"layout_type = ${len(args)}")
    if niche:
        # && is "overlaps": the row is tagged with this niche.
        args.append([niche]); where.append(f"niches && ${len(args)}::text[]")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    async with acquire(None) as conn:
        rows = await conn.fetch(
            f"""SELECT id::text, kind, spec, fingerprint, family_key, source_kind, source_url,
                       source_image_uri, layout_type, label, title, niches, uploaded_by,
                       status, review_note, reviewed_by, reviewed_at,
                       adopted_count, approvals, rejections, qa_passes, qa_fails, created_at
                  FROM house_layouts {clause}
              ORDER BY status, created_at DESC
                 LIMIT {max(1, min(int(limit), 500))} OFFSET {max(0, int(offset))}""", *args)
        counts = await conn.fetch("SELECT status, count(*) n FROM house_layouts GROUP BY 1")
        by_type = await conn.fetch(
            "SELECT layout_type, count(*) n FROM house_layouts GROUP BY 1 ORDER BY n DESC")
        niches = await conn.fetchval(
            "SELECT coalesce(array_agg(DISTINCT n ORDER BY n), '{}') "
            "  FROM house_layouts, unnest(niches) n")

    out = []
    for r in rows:
        d = dict(r)
        # jsonb arrives as text unless a codec is registered; the screen needs an
        # object to draw a preview from, so parse it here rather than in every caller.
        if isinstance(d.get("spec"), str):
            try:
                d["spec"] = json.loads(d["spec"])
            except ValueError:
                d["spec"] = {}
        out.append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()})

    return {
        "total": sum(int(c["n"]) for c in counts),
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
        got = await conn.fetchval(
            "UPDATE house_layouts SET status = $2, reviewed_by = $3, review_note = $4, "
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
           "niche_tokens", "niche_rank", "tenant_niches"]
