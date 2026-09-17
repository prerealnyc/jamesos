"""Every status the cutter writes must be one the table allows.

The YouTube import had never worked. Its first act was to set the source to
'downloading' — a good word, and not one of the five values the column's CHECK
constraint permits — so asyncpg raised on that line, the line sat OUTSIDE the
try below it, and the background task died with the row left saying 'uploading'
and no error written anywhere. Three of skelon's imports sat like that on
2026-09-18 while the panel showed them as still working.

The bug is not really "downloading": it is a vocabulary kept in two places that
nothing compared. This compares them.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.nodb

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/019_long_form_cutter.sql"
MODULE = ROOT / "src/james_os/long_form.py"


def allowed_statuses() -> set[str]:
    """The CHECK constraint on long_sources.status, read from the migration."""
    sql = MIGRATION.read_text()
    m = re.search(r"status\s+text[^)]*?CHECK\s*\(\s*status\s+IN\s*\((.*?)\)", sql, re.S)
    assert m, "could not find the status CHECK constraint"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def written_statuses() -> set[str]:
    """Every literal this module hands to _set(), which is the one writer of
    long_sources.status. Other tables in here have their own vocabulary
    ('suggested', 'dismissed' on the candidates) and their own constraint."""
    src = MODULE.read_text()
    return set(re.findall(r"""_set\(\s*conn,\s*source_id,[^)]*?status\s*=\s*["']([a-z_]+)["']""", src, re.S))


def test_the_cutter_only_writes_statuses_the_column_accepts():
    allowed = allowed_statuses()
    written = written_statuses()
    assert written, "no status writes found — the regex needs updating, not the code"
    unknown = written - allowed
    assert not unknown, (
        f"long_form.py writes {sorted(unknown)}, which the CHECK constraint rejects "
        f"(allowed: {sorted(allowed)}). A write like this raises inside a background "
        f"task and leaves the source stuck on its previous status forever."
    )


def test_the_first_write_of_the_youtube_worker_cannot_kill_the_task():
    """It runs before any other error handling, so if it throws the source is
    stranded with nothing on it to say why."""
    src = MODULE.read_text()
    start = src.index("async def fetch_from_youtube_then_ingest")
    body = src[start : start + 2000]
    first_set = body.index('_set(conn, source_id, status=')
    guarded = body.rindex("try:", 0, first_set)
    # the guard must be inside this function, not one from an earlier one
    assert guarded > body.index('"""', body.index('"""') + 3), "the first status write is unguarded"
    assert "_fail(" in body[first_set : first_set + 600]
