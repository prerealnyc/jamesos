"""The nightly niche harvest's way into the house catalogue.

Roy, 2026-10-07: "I do not want the user to pick templates ... we should have
enough templates in the database" — and then "yes i want volume". BM2 reads the
niche's top social images every night, screens out what is not a designed post,
and sends each survivor here, ONE image per request. This module decides what
becomes of it, behind machine gates, with no human in the steady state.

WHY NOT THE CURATOR'S /upload. That route defaults approve to TRUE, gates only on
usable(), and stores the image before it has read it. An engine that did not know
about a harvest flag would ignore it and approve everything — the pool would fail
OPEN. This path fails CLOSED: approve defaults false, nothing undrawable is ever
approved, and nothing is written or stored until every gate has passed.

THE ORDER IS THE COST CONTROL. The vision read is the one paid step (gpt-4o, one
call per image), so everything that can refuse an image for free runs first:
the inputs, then whether the catalogue already holds this exact image
(source_key). An image already known is never paid for twice.

SOURCES. The harvest reads more than one place (SPEC2, 2026-10-07): each image
arrives keyed by the source that found it — `onc` Onclusive social listening,
`igh` Instagram hashtag posts (Apify), `ggl` Google images (Serper), `bnb` the
owner's own Bannerbear project templates. The prefix is stored as
harvest_meta.source. Two per-request options exist for those sources:
  * store_image=false — keep the structural spec and the source link but NOT a
    copy of the picture (BM2 sends it for a third party's web page image);
  * spec_hint — layer geometry the source already knows (Bannerbear's
    config.objects). Stored for a human reading the row and NEVER trusted for a
    gate: the vision read stays the source of truth. Sized like BM2 sizes it
    (compact UTF-8 JSON, at most 16 KB); one over that is DROPPED
    (harvest_meta.spec_hint_dropped='oversize'), never a reason to refuse the
    image.
The written design rubric BM2 screens against lives in BM2; what it sends here
(e.g. meta.rubric_version) is provenance only and is stored with the rest of
meta, never read by a gate.

TENANT-FREE, like the rest of the catalogue. Every connection is acquire(None);
nothing here reads a brand's library, takes a tenant id, or writes a brand's
name. Only niche tag TEXT crosses in.

Verdicts, first failure wins:
  duplicate  source_key   — already in the catalogue; no vision call
  retry      no_key | vision_failed | timeout — nothing recorded; BM2 tries again
  rejected   unusable | not_drawable | plain_photo
  held       untyped | family_cap | family_cap_global | type_share | approve_off
             — written as a 'candidate' a human (or a later rebalance) may approve
  duplicate  fingerprint  — the SHAPE is already there; its niches are unioned
  approved   — in rotation for every brand from the next pick
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

from .db import acquire

logger = logging.getLogger(__name__)

# ── inputs ──────────────────────────────────────────────────────────────────

# One 3-letter prefix per SOURCE (not per vendor: fetch hosts, budgets and retry
# routing differ by source), then ':' + the sha1 hex of the image URL.
#   onc  Onclusive social listening        igh  Apify Instagram hashtag posts
#   ggl  Google images via Serper          bnb  the owner's Bannerbear templates
# Reserved for sources not built yet, and REFUSED until they are added here:
#   pin  Pinterest   fba  Facebook Ad Library   cva  Canva (owner's own designs)
SOURCE_PREFIXES: tuple[str, ...] = ("onc", "igh", "ggl", "bnb")
SOURCE_KEY_RE = re.compile(r"^(" + "|".join(SOURCE_PREFIXES) + r"):[0-9a-f]{40}$")
MAX_BYTES = 15 * 1024 * 1024
MAX_NICHES = 4
MAX_NICHE_LEN = 60
MAX_RUN_ID = 64
MAX_BY = 200
MAX_META_BYTES = 4096
MAX_SPEC_HINT_BYTES = 16 * 1024
# WEB: an image found on an ordinary web page (ggl). TEMPLATE: a designed
# template from the owner's own template tool (bnb). Anything else is OTHER.
MEDIA = ("FACEBOOK", "INSTAGRAM", "LINKEDIN", "TIKTOK", "WEB", "TEMPLATE", "OTHER")
BY_PREFIX = "harvest:"
AUTO_REVIEWER = "harvest:auto"
HELD_PREFIX = "auto-capped:"

# ── the paid step ───────────────────────────────────────────────────────────

# At most three vision reads in flight from the harvest, whatever BM2 sends.
_EXTRACT_SEM = asyncio.Semaphore(3)
EXTRACT_TIMEOUT_S = 60.0

# ── catalogue balance ───────────────────────────────────────────────────────
# Defaults, and the range a caller's `policy` may move each one within. A
# policy can tune the harvest; it cannot switch the balance off.
DEFAULT_POLICY: dict[str, Any] = {
    "family_per_niche": 2,   # approved rows of one family within the niche
    "family_global": 6,      # approved rows of one family across the catalogue
    "type_share": 0.35,      # the most one layout_type may hold of a niche...
    "type_min_pool": 10,     # ...once the niche has at least this many approved
}
_POLICY_RANGE: dict[str, tuple[float, float, type]] = {
    "family_per_niche": (1, 10, int),
    "family_global": (1, 20, int),
    "type_share": (0.2, 0.6, float),
    "type_min_pool": (0, 100, int),
}


class HarvestInputError(ValueError):
    """The request itself is wrong. Final for that image: sending it again will
    not change the answer. `status` is the HTTP code the route answers with."""

    status = 400


class HarvestTooLarge(HarvestInputError):
    status = 413


class HarvestUnsupportedType(HarvestInputError):
    status = 415


def sniff(data: bytes) -> str:
    """The image type the BYTES say they are; '' for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def canon_niche(tag) -> str:
    """The ONE spelling a niche tag is stored, capped and counted under.

    Lowercased, whitespace collapsed, and commas read as spaces: a comma is the
    separator of the legacy `niches` form field, so a tag that kept one would be
    keyed one way by /harvest (split into fragments) and another by
    /harvest/stats (taken whole). With commas gone both routes, and a caller
    that strips them itself, land on the same key for the same niche.
    """
    return " ".join(str(tag if tag is not None else "").replace(",", " ").lower().split())


def parse_niches(niches) -> list[str]:
    """1–4 niche tags, canonical (see canon_niche), distinct, first = primary.

    A STRING is the legacy comma-separated encoding and is split on commas; a
    LIST is already one tag per item and is never split, so a tag that itself
    contains a comma stays ONE tag."""
    if isinstance(niches, str):
        niches = niches.split(",")
    out: list[str] = []
    for n in niches or []:
        if n is None:
            continue
        t = canon_niche(n)
        if not t:
            continue
        if len(t) > MAX_NICHE_LEN:
            raise HarvestInputError(f"a niche tag is longer than {MAX_NICHE_LEN} characters")
        if t not in out:
            out.append(t)
    if not out:
        raise HarvestInputError("at least one niche tag is required")
    if len(out) > MAX_NICHES:
        raise HarvestInputError(f"at most {MAX_NICHES} niche tags")
    return out


def resolve_policy(policy: dict | None) -> dict:
    """The caller's policy over the defaults, each value clamped to its range.
    Unknown keys are ignored; a value that is not a number is an input error."""
    out = dict(DEFAULT_POLICY)
    if policy is None:
        return out
    if not isinstance(policy, dict):
        raise HarvestInputError("policy must be a JSON object")
    for key, (lo, hi, cast) in _POLICY_RANGE.items():
        if key not in policy or policy[key] is None:
            continue
        val = policy[key]
        if isinstance(val, bool):
            raise HarvestInputError(f"policy.{key} must be a number")
        try:
            num = cast(val)
        except (TypeError, ValueError):
            raise HarvestInputError(f"policy.{key} must be a number") from None
        out[key] = cast(min(max(num, lo), hi))
    return out


def source_of(source_key: str) -> str:
    """The source prefix of a VALID source_key ('onc', 'igh', 'ggl', 'bnb')."""
    return source_key.split(":", 1)[0]


def spec_hint_size(spec_hint) -> int:
    """The hint's size in bytes, measured EXACTLY as BM2 sizes it before sending:
    compact separators, UTF-8 (non-ASCII kept, not \\u-escaped). json.dumps'
    defaults add a space after every ',' and ':' and escape non-ASCII, which
    inflates the same hint by roughly 9-15% — so a hint BM2 had trimmed to fit
    would read as oversize here."""
    return len(json.dumps(spec_hint, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode())


def _validate_spec_hint(spec_hint) -> tuple[Any, str | None]:
    """(hint, dropped): the hint to store, and why it was dropped, if it was.

    A hint of the wrong TYPE is a malformed request (400). An OVERSIZE hint is
    not: the hint is never trusted for anything, so losing it costs nothing,
    while refusing the image would cost the image for good (BM2 records a 400
    as the final verdict rejected_input). It is dropped with a warning and the
    row records harvest_meta.spec_hint_dropped = 'oversize'."""
    if spec_hint is None:
        return None, None
    if not isinstance(spec_hint, (dict, list)):
        raise HarvestInputError("spec_hint must be a JSON object or array")
    try:
        size = spec_hint_size(spec_hint)
    except (TypeError, ValueError):
        raise HarvestInputError("spec_hint is not JSON-serialisable") from None
    if size > MAX_SPEC_HINT_BYTES:
        logger.warning("harvest: spec_hint dropped, %d bytes > %d (the image is still read)",
                       size, MAX_SPEC_HINT_BYTES)
        return None, "oversize"
    return spec_hint, None


def _validate(image, *, source_key, niches, run_id, by, source_url, title,
              source_media, meta, spec_hint=None) -> dict:
    if not isinstance(source_key, str) or not SOURCE_KEY_RE.match(source_key):
        raise HarvestInputError(
            "source_key must be one of " + ", ".join(f"'{p}:'" for p in SOURCE_PREFIXES)
            + " + 40 lowercase hex")
    tags = parse_niches(niches)
    run_id = str(run_id or "").strip()
    if not 1 <= len(run_id) <= MAX_RUN_ID:
        raise HarvestInputError(f"run_id must be 1-{MAX_RUN_ID} characters")
    by = str(by or "").strip()
    if not by.startswith(BY_PREFIX) or len(by) > MAX_BY:
        raise HarvestInputError(f"by must start with '{BY_PREFIX}' (at most {MAX_BY})")
    media = str(source_media or "OTHER").strip().upper()
    if media not in MEDIA:
        media = "OTHER"
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise HarvestInputError("meta must be a JSON object")
    try:
        if len(json.dumps(meta, default=str).encode()) > MAX_META_BYTES:
            raise HarvestInputError(f"meta is larger than {MAX_META_BYTES} bytes")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, HarvestInputError):
            raise
        raise HarvestInputError("meta is not JSON-serialisable") from None
    spec_hint, hint_dropped = _validate_spec_hint(spec_hint)
    if not isinstance(image, (bytes, bytearray)) or not image:
        raise HarvestInputError("no image")
    if len(image) > MAX_BYTES:
        raise HarvestTooLarge("larger than 15 MB")
    mime = sniff(bytes(image[:16]))
    if not mime:
        raise HarvestUnsupportedType("not a PNG, JPEG or WebP image")
    return {"tags": tags, "run_id": run_id, "by": by, "media": media,
            "meta": meta, "mime": mime, "source": source_of(source_key),
            "spec_hint": spec_hint, "spec_hint_dropped": hint_dropped,
            "source_url": str(source_url or "")[:500], "title": str(title or "")[:200]}


# ── the verdict ─────────────────────────────────────────────────────────────

def _zero_counts() -> dict:
    return {"family_in_niche": 0, "family_global": 0, "type_in_niche": 0,
            "niche_approved": 0}


def _result(verdict: str, reason: str = "", *, started: float, dry_run: bool,
            vision_called: bool = False, **over) -> dict:
    out = {
        "verdict": verdict,
        "reason": reason,
        "retryable": verdict == "retry",
        "vision_called": vision_called,
        "house_layout_id": None,
        "stored_status": None,
        "layout_type": "",
        "label": "",
        "kind": "",
        "family_key": "",
        "fingerprint": "",
        "n_elements": 0,
        "niches": [],
        "counts": _zero_counts(),
        "stored_image_uri": "",
        # Always null: the cost is estimated by BM2 from vision_called. The
        # extractor does not hand its token usage back, and a guessed figure
        # here would read as a measured one.
        "usage": None,
        "dry_run": bool(dry_run),
    }
    out.update(over)
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return out


def decide(counts: dict, *, layout_type: str, approve: bool, policy: dict) -> tuple[str, str]:
    """(status, reason) for a typed, drawable layout given the niche's counts.
    Pure, so every cap is testable without a database."""
    if counts["family_in_niche"] >= policy["family_per_niche"]:
        return "held", "family_cap"
    if counts["family_global"] >= policy["family_global"]:
        return "held", "family_cap_global"
    n = counts["niche_approved"]
    if n >= policy["type_min_pool"] and \
            (counts["type_in_niche"] + 1) / (n + 1) > policy["type_share"]:
        return "held", "type_share"
    if not approve:
        return "held", "approve_off"
    return "approved", ""


def _is_plain_photo(spec: dict) -> bool:
    """A photograph with at most a caption on it: nothing a brand could reuse as
    a LAYOUT. design_cloner emits two kinds, photo_forward and graphic_card."""
    els = [e for e in (spec.get("elements") or []) if isinstance(e, dict)]
    decos = [d for d in (spec.get("decorations") or []) if isinstance(d, dict)]
    return spec.get("kind") == "photo_forward" and len(els) < 2 and not decos


_COUNTS_SQL = """
    SELECT count(*) FILTER (WHERE family_key = $2 AND $1 = ANY(niches)) AS family_in_niche,
           count(*) FILTER (WHERE family_key = $2)                      AS family_global,
           count(*) FILTER (WHERE layout_type = $3 AND $1 = ANY(niches)) AS type_in_niche,
           count(*) FILTER (WHERE $1 = ANY(niches))                     AS niche_approved
      FROM house_layouts
     WHERE status = 'approved'"""

_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('house_harvest:' || $1))"


def _insert_sql() -> str:
    from .house_layouts import _NICHE_UNION_SQL

    return f"""
    INSERT INTO house_layouts
        (kind, spec, fingerprint, family_key, source_kind, source_url,
         layout_type, label, title, niches, uploaded_by, status,
         review_note, reviewed_by, reviewed_at,
         source_key, harvest_run_id, harvest_meta)
    VALUES ($1, $2::jsonb, $3, $4, 'niche', $5,
            $6, $7, $8, $9::text[], $10, $11,
            $12, '{AUTO_REVIEWER}', CASE WHEN $11 = 'approved' THEN now() END,
            $13, $14, $15::jsonb)
    ON CONFLICT (fingerprint) WHERE fingerprint <> ''
    DO UPDATE SET
        -- the shape is already here: another niche finding it is evidence it
        -- suits that niche too, so its tags are UNIONED. Status is left alone.
        niches     = {_NICHE_UNION_SQL},
        updated_at = now()
    RETURNING id::text, (created_at = updated_at) AS fresh, status, niches"""


def _is_source_key_race(exc: BaseException) -> bool:
    """The source_key unique index refused the insert: a concurrent request for
    the same image got there first."""
    if type(exc).__name__ != "UniqueViolationError":
        return False
    where = " ".join(str(x) for x in (getattr(exc, "constraint_name", "") or "", exc))
    return "source_key" in where


async def _store_image(layout_id: str, image: bytes, *, source_key: str, mime: str) -> str:
    """Keep the picture a curator judges the row by — only for a row that was
    really written, so a refused or duplicate image costs no storage."""
    from .media import storage as media_storage

    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "bin")
    name = f"{source_key.replace(':', '_')}.{ext}"
    try:
        uri, _ = await asyncio.to_thread(media_storage().save, "house", bytes(image), name)
        async with acquire(None) as conn:
            await conn.execute(
                "UPDATE house_layouts SET source_image_uri = $2, updated_at = now() "
                "WHERE id = $1::uuid", layout_id, str(uri)[:500])
        return str(uri)
    except Exception:  # noqa: BLE001 — the layout is the product; the picture is for review
        logger.warning("harvest: could not store the image for %s", layout_id, exc_info=True)
        return ""


async def harvest_ingest(
    image: bytes, *, source_key: str, niches: list[str], run_id: str, by: str,
    source_url: str = "", title: str = "", source_media: str = "OTHER",
    approve: bool = False, dry_run: bool = False, meta: dict | None = None,
    policy: dict | None = None, store_image: bool = True, spec_hint=None,
) -> dict:
    """Read ONE harvested image and decide what the catalogue does with it.

    Raises HarvestInputError (400), HarvestTooLarge (413) or
    HarvestUnsupportedType (415) for a request that is wrong in itself. Every
    other outcome is a verdict dict (see the module docstring); anything else
    that raises is an infrastructure fault the caller should treat as retryable.

    dry_run runs every step up to the decision — the vision read IS paid — and
    writes nothing: no row, no stored image.

    store_image=False writes the row (spec, source link, provenance) but keeps
    no copy of the picture: stored_image_uri is ''. spec_hint is stored in
    harvest_meta and read by nothing here — every gate runs on the vision read.
    """
    from . import design_cloner
    from . import design_templates as dt
    from .house_layouts import family_key
    from .layout_types import classify

    started = time.monotonic()
    # 1. the request itself
    v = _validate(image, source_key=source_key, niches=niches, run_id=run_id, by=by,
                  source_url=source_url, title=title, source_media=source_media,
                  meta=meta, spec_hint=spec_hint)
    pol = resolve_policy(policy)
    tags, primary = v["tags"], v["tags"][0]

    # 2. already in the catalogue? Free, so before the paid read.
    async with acquire(None) as conn:
        hit = await conn.fetchrow(
            "SELECT id::text, status, niches FROM house_layouts WHERE source_key = $1",
            source_key)
    if hit:
        return _result("duplicate", "source_key", started=started, dry_run=dry_run,
                       house_layout_id=hit["id"], stored_status=hit["status"],
                       niches=list(hit["niches"] or []))

    # 3. the paid read
    try:
        async with _EXTRACT_SEM:
            spec = await asyncio.wait_for(
                design_cloner.extract_template_spec(bytes(image), mime=v["mime"]),
                timeout=EXTRACT_TIMEOUT_S)
    except TimeoutError:          # asyncio.wait_for raises the builtin on 3.11+
        return _result("retry", "timeout", started=started, dry_run=dry_run,
                       vision_called=True)
    except Exception as exc:  # noqa: BLE001 — a failed read is retryable, never final
        logger.warning("harvest: vision read raised for %s: %s", source_key, exc)
        return _result("retry", "vision_failed", started=started, dry_run=dry_run,
                       vision_called=True)
    status = spec.get("status") if isinstance(spec, dict) else "failed"
    if status == "no_key":
        return _result("retry", "no_key", started=started, dry_run=dry_run,
                       vision_called=True)
    if status == "failed" or not isinstance(spec, dict):
        return _result("retry", "vision_failed", started=started, dry_run=dry_run,
                       vision_called=True)

    els = [e for e in (spec.get("elements") or []) if isinstance(e, dict)]
    shape = {"kind": str(spec.get("kind") or ""), "n_elements": len(els)}

    # 4. is it a layout at all?
    if not dt.usable(spec):
        return _result("rejected", "unusable", started=started, dry_run=dry_run,
                       vision_called=True, **shape)
    if not dt.drawable(spec):
        return _result("rejected", "not_drawable", started=started, dry_run=dry_run,
                       vision_called=True, **shape)
    if _is_plain_photo(spec):
        return _result("rejected", "plain_photo", started=started, dry_run=dry_run,
                       vision_called=True, **shape)

    # 5. what kind of post it is; 6. its identity and family
    named = classify(spec)
    ltype = str(named.get("type") or "")
    fp = dt.fingerprint(spec)
    fam = family_key(spec)
    shape.update(layout_type=ltype, label=str(named.get("label") or ""),
                 fingerprint=fp, family_key=fam)

    # Derived provenance wins over anything the caller put under the same key.
    stored_meta = {**v["meta"], "sha256": hashlib.sha256(bytes(image)).hexdigest(),
                   "source_media": v["media"], "source": v["source"],
                   "image_stored": bool(store_image)}
    stored_meta.pop("spec_hint", None)
    stored_meta.pop("spec_hint_dropped", None)
    if v["spec_hint"] is not None:
        stored_meta["spec_hint"] = v["spec_hint"]
    if v["spec_hint_dropped"]:
        stored_meta["spec_hint_dropped"] = v["spec_hint_dropped"]

    # 7. the decision and 8. the write, in ONE transaction under a lock on the
    # exact primary niche tag: two requests for one niche cannot both read
    # "family 1/2" and both approve.
    row = None
    counts = _zero_counts()
    try:
        async with acquire(None) as conn:
            await conn.execute(_LOCK_SQL, primary)
            c = await conn.fetchrow(_COUNTS_SQL, primary, fam, ltype)
            counts = {k: int((c[k] if c else 0) or 0) for k in _zero_counts()}
            if not ltype or ltype == "other":
                verdict, reason = "held", "untyped"
            else:
                verdict, reason = decide(counts, layout_type=ltype,
                                         approve=bool(approve), policy=pol)
            new_status = "approved" if verdict == "approved" else "candidate"
            if verdict == "approved":
                note = (f"auto: drawable; family {counts['family_in_niche'] + 1}/"
                        f"{pol['family_per_niche']}; {ltype} {counts['type_in_niche'] + 1}/"
                        f"{counts['niche_approved'] + 1}")
            else:
                note = f"{HELD_PREFIX}{reason}"

            if dry_run:
                # Read-only: would the shape collide with one already here?
                twin = await conn.fetchrow(
                    "SELECT id::text, status, niches FROM house_layouts "
                    "WHERE fingerprint = $1 AND fingerprint <> ''", fp)
                if twin:
                    return _result("duplicate", "fingerprint", started=started,
                                   dry_run=True, vision_called=True, counts=counts,
                                   house_layout_id=twin["id"],
                                   stored_status=twin["status"], **shape)
                return _result(verdict, reason, started=started, dry_run=True,
                               vision_called=True, counts=counts,
                               stored_status=new_status, **shape)

            row = await conn.fetchrow(
                _insert_sql(),
                shape["kind"], json.dumps(spec), fp, fam, v["source_url"],
                ltype, shape["label"], v["title"], tags, v["by"], new_status,
                note[:500], source_key, v["run_id"], json.dumps(stored_meta, default=str))
    except Exception as exc:
        if not _is_source_key_race(exc):
            raise
        async with acquire(None) as conn:
            hit = await conn.fetchrow(
                "SELECT id::text, status, niches FROM house_layouts WHERE source_key = $1",
                source_key)
        return _result("duplicate", "source_key", started=started, dry_run=False,
                       vision_called=True, counts=counts,
                       house_layout_id=hit["id"] if hit else None,
                       stored_status=hit["status"] if hit else None,
                       niches=list((hit["niches"] if hit else None) or []), **shape)

    stored_niches = list(row["niches"] or []) if row else []
    if not row or not row["fresh"]:
        # The SHAPE was already in the catalogue: its niches were unioned and its
        # status left as it was, which is what is reported.
        return _result("duplicate", "fingerprint", started=started, dry_run=False,
                       vision_called=True, counts=counts,
                       house_layout_id=row["id"] if row else None,
                       stored_status=row["status"] if row else None,
                       niches=stored_niches, **shape)

    # 9. a NEW row — only now is the picture kept, and only if the caller may
    # keep a copy of it (store_image=False: a third party's web image).
    uri = ""
    if store_image:
        uri = await _store_image(row["id"], image, source_key=source_key, mime=v["mime"])
    if verdict == "approved":
        logger.info("harvest: approved %s (%s) into %s", row["id"], ltype, primary)
    return _result(verdict, reason, started=started, dry_run=False, vision_called=True,
                   counts=counts, house_layout_id=row["id"],
                   stored_status=row["status"], niches=stored_niches,
                   stored_image_uri=uri, **shape)


# ── reading the harvest back ────────────────────────────────────────────────

async def harvest_stats(niches: list[str]) -> dict:
    """Coverage per niche tag, for BM2's seed/steady decision, its type-deficit
    upload order and its report. Membership is exact: tag = ANY(niches)."""
    tags: list[str] = []
    for n in niches or []:
        t = canon_niche(n)[:MAX_NICHE_LEN]
        if t and t not in tags:
            tags.append(t)
    tags = tags[:20]

    per: dict[str, dict] = {
        t: {"approved": 0, "held": 0, "held_by_reason": {}, "families": 0,
            "harvested_last_24h": 0, "by_type": {}}
        for t in tags
    }
    async with acquire(None) as conn:
        rows = []
        fams = []
        if tags:
            rows = await conn.fetch(
                f"""SELECT tag, h.status, h.layout_type,
                           CASE WHEN h.review_note LIKE '{HELD_PREFIX}%'
                                THEN substr(h.review_note, {len(HELD_PREFIX) + 1})
                                ELSE '' END AS held_reason,
                           count(*) AS n,
                           count(*) FILTER (WHERE h.harvest_run_id <> ''
                                              AND h.created_at > now() - interval '24 hours')
                                AS recent
                      FROM unnest($1::text[]) AS tag
                      JOIN house_layouts h ON tag = ANY(h.niches)
                     WHERE h.status IN ('approved', 'candidate')
                  GROUP BY 1, 2, 3, 4""", tags)
            fams = await conn.fetch(
                """SELECT tag, count(DISTINCT h.family_key) AS families
                     FROM unnest($1::text[]) AS tag
                     JOIN house_layouts h ON tag = ANY(h.niches)
                    WHERE h.status = 'approved' AND h.family_key <> ''
                 GROUP BY 1""", tags)
        tot = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE status = 'approved') AS total_approved,
                      count(*) FILTER (WHERE status = 'approved' AND harvest_run_id <> '')
                          AS harvested_approved
                 FROM house_layouts""")

    for r in rows:
        d = per.get(r["tag"])
        if d is None:
            continue
        n = int(r["n"] or 0)
        if r["status"] == "approved":
            d["approved"] += n
            d["harvested_last_24h"] += int(r["recent"] or 0)
            t = str(r["layout_type"] or "") or "(unnamed)"
            d["by_type"].setdefault(t, {"approved": 0, "share": 0.0})["approved"] += n
        else:
            d["held"] += n
            why = str(r["held_reason"] or "") or "awaiting_review"
            d["held_by_reason"][why] = d["held_by_reason"].get(why, 0) + n
    for r in fams:
        if r["tag"] in per:
            per[r["tag"]]["families"] = int(r["families"] or 0)
    for d in per.values():
        for t in d["by_type"].values():
            t["share"] = round(t["approved"] / d["approved"], 4) if d["approved"] else 0.0

    return {
        "niches": per,
        "total_approved": int((tot["total_approved"] if tot else 0) or 0),
        "harvested_approved": int((tot["harvested_approved"] if tot else 0) or 0),
    }


async def harvest_revoke(run_id: str, by: str) -> dict:
    """Take one run's machine decisions back out of rotation.

    Only rows the harvest itself decided (reviewed_by = 'harvest:auto') are
    rejected; a row a human has since reviewed is left alone and counted.
    Idempotent: a second call finds nothing left to reject. Rows brands already
    adopted stay in those brands' libraries — they own their copies.
    """
    run_id = str(run_id or "").strip()
    if not 1 <= len(run_id) <= MAX_RUN_ID:
        raise HarvestInputError(f"run_id must be 1-{MAX_RUN_ID} characters")
    by = str(by or "").strip()[:MAX_BY]
    async with acquire(None) as conn:
        rejected = await conn.fetchval(
            """WITH hit AS (
                   UPDATE house_layouts
                      SET status = 'rejected',
                          review_note = left('auto-revoked by ' || $2 || '; ' || review_note, 500),
                          updated_at = now()
                    WHERE harvest_run_id = $1
                      AND reviewed_by = $3
                      AND status <> 'rejected'
                RETURNING 1)
               SELECT count(*) FROM hit""", run_id, by or "unknown", AUTO_REVIEWER)
        skipped = await conn.fetchval(
            "SELECT count(*) FROM house_layouts WHERE harvest_run_id = $1 AND reviewed_by <> $2",
            run_id, AUTO_REVIEWER)
    return {"run_id": run_id, "rejected": int(rejected or 0),
            "skipped_human_reviewed": int(skipped or 0)}


__all__ = ["harvest_ingest", "harvest_stats", "harvest_revoke", "decide",
           "resolve_policy", "parse_niches", "canon_niche", "sniff", "DEFAULT_POLICY",
           "HarvestInputError", "HarvestTooLarge", "HarvestUnsupportedType",
           "SOURCE_KEY_RE", "SOURCE_PREFIXES", "MEDIA", "MAX_BYTES",
           "MAX_SPEC_HINT_BYTES", "source_of", "spec_hint_size"]
