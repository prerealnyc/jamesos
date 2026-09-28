"""The engine must not learn its own prose as the brand's voice.

Two surfaces conspired to make it do exactly that, and neither was covered:

  * learning.record_approval promoted EVERY approved draft into voice_corpus at
    confidence 1.0 — including the untouched caption the engine had just
    written, which the owner had merely agreed to ship.
  * content._voice_exemplars placed those approved samples at the head of the
    voice bucket, ahead of the brand's real harvested words — whose filename
    ("bm2-voice-corpus.txt") the profile-anchor heuristic did not even match, so
    they sat in the random cadence lottery behind a 200-character floor that
    most real social captions fall under.

Together: each approval fed the next draft the model's own last output, ranked
above anything the owner actually said, with both corpora claiming the owner's
blessing. These tests pin the two halves of the fix.
"""

import asyncio
import contextlib
import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from james_os import content, learning


def _corpus_filename() -> str:
    """The filename BM2 uploads the harvested corpus under.

    BM2 is a separate service, so this is hard-coded on purpose — the point of
    the test is that the two sides agree, and importing it from here is not
    possible. Keep in step with backend/app/services/voice_sync.py CORPUS_FILE.
    """
    return "bm2-voice-corpus.txt"


# ---------------------------------------------------------------- record_approval

def _fake_row(payload: dict, action_type: str = "content"):
    return {"action_type": action_type, "payload": json.dumps(payload)}


def _run_approval(monkeypatch, payload: dict, action_type: str = "content"):
    """record_approval against a faked actions row; returns what it ingested."""
    ingested: list = []

    class _Conn:
        async def fetchrow(self, sql, action_id):
            return _fake_row(payload, action_type)

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    class _Stored:
        id = uuid4()

    async def _ingest_many(events, tenant_id=None):
        ingested.extend(events)
        return [_Stored()]

    monkeypatch.setattr(learning, "acquire", _acquire)
    monkeypatch.setattr(learning, "ingest_many", _ingest_many)
    result = asyncio.run(learning.record_approval(uuid4(), None))
    return result, ingested


_CAPTION = (
    "Three years ago I would have taken this deal. Here is exactly what changed "
    "my mind, and the one number I check before anything else now."
)


def test_unedited_approval_teaches_nothing(monkeypatch):
    """Approving says 'ship it', not 'this is how I sound'."""
    result, ingested = _run_approval(
        monkeypatch, {"caption": _CAPTION, "platform": "instagram", "format": "post"}
    )
    assert result is None, "an unedited draft is the engine's own prose"
    assert ingested == [], "nothing may enter voice_corpus from it"


def test_owner_edited_approval_is_learned(monkeypatch):
    """A caption the owner rewrote by hand IS their voice, and is imitated."""
    result, ingested = _run_approval(
        monkeypatch,
        {"caption": _CAPTION, "platform": "instagram", "format": "post",
         "edited_by_owner": True},
    )
    assert result is not None and len(ingested) == 1
    ev = ingested[0]
    assert ev.payload["category"] == "voice_corpus"
    assert ev.payload["source"] == learning.APPROVED_EXEMPLAR_SOURCE
    assert ev.raw_content == _CAPTION


def test_qa_failure_still_refused_even_when_edited(monkeypatch):
    """The older guard must survive the new one: an override-approved draft that
    FAILED voice-QA is shipped, never imitated."""
    for bad in ({"flagged": True}, {"qa_passed": False}):
        result, ingested = _run_approval(
            monkeypatch,
            {"caption": _CAPTION, "edited_by_owner": True, **bad},
        )
        assert result is None and ingested == [], bad


def test_non_content_actions_ignored(monkeypatch):
    result, ingested = _run_approval(
        monkeypatch, {"caption": _CAPTION, "edited_by_owner": True}, action_type="video"
    )
    assert result is None and ingested == []


# -------------------------------------------------------------- _voice_exemplars

def _ilike_to_regex(pattern: str) -> re.Pattern:
    """SQL ILIKE, faithfully: % is any run, _ is any single character.

    The single-character wildcard matters here — '%voice_corpus%' also matches
    "voice-corpus", which is the sort of accident this test exists to stop
    anyone relying on.
    """
    out = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.compile("^" + out + "$", re.I)


def _harvest_exemplars(monkeypatch):
    """Run _voice_exemplars with each of its three queries returning one
    identifiable row, so the ORDER it hands back can be asserted. Also captures
    the SQL, because the anchor heuristic is a filename pattern."""
    seen_sql: list[str] = []

    def _row(tag: str):
        return {"id": uuid4(), "event_type": "note", "raw_content": f"{tag} text",
                "payload": {"category": "voice_corpus", "tag": tag},
                "effective_at": datetime.now(UTC)}

    class _Conn:
        async def fetch(self, sql, *args):
            seen_sql.append(sql)
            if "approved_exemplar'" in sql and "<>" not in sql:
                return [_row("approved")]
            if "ILIKE" in sql and "NOT (" not in sql:
                return [_row("anchor")]
            return [_row("cadence")]

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(content, "acquire", _acquire)
    out = asyncio.run(content._voice_exemplars(None, 4))
    return [e.payload["tag"] for e in out], seen_sql


def test_brand_own_words_outrank_engine_approved_drafts(monkeypatch):
    """buckets["voice"] is truncated to content_voice_k, so this order decides
    what the model actually sees. The brand's corpus leads."""
    tags, _ = _harvest_exemplars(monkeypatch)
    assert tags.index("anchor") < tags.index("approved"), tags
    assert tags.index("approved") < tags.index("cadence"), tags


def test_harvested_corpus_filename_anchors(monkeypatch):
    """The filename BM2 uploads under must match the anchor heuristic.

    It did not: the patterns spelled it 'voice_profile'/'voice_spec' while BM2
    writes 'bm2-voice-corpus.txt', so the only verbatim record of the brand's
    voice was demoted to the random cadence bucket.
    """
    _, seen_sql = _harvest_exemplars(monkeypatch)
    anchor_sql = next(s for s in seen_sql if "ILIKE" in s and "NOT (" not in s)
    patterns = re.findall(r"ILIKE '([^']+)'", anchor_sql)
    name = _corpus_filename()
    assert any(_ilike_to_regex(p).match(name) for p in patterns), \
        f"{name} matches none of {patterns}"


def test_cadence_excludes_what_the_anchors_take(monkeypatch):
    """A row must not be able to arrive twice — the cadence query has to exclude
    both the anchors and the approved exemplars."""
    _, seen_sql = _harvest_exemplars(monkeypatch)
    cadence_sql = next(s for s in seen_sql if "NOT (" in s)
    assert "<> 'approved_exemplar'" in cadence_sql
