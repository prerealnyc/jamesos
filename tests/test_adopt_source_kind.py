"""The catalogue and a brand's library have DIFFERENT closed vocabularies for
source_kind, and adopt() copies a row across that boundary.

This is the bug this file exists for: 'curated' is legal in house_layouts and
illegal in design_templates, so EVERY adoption of an uploaded layout died on
design_templates_source_kind_check. Nothing caught it — the unit tests stubbed
the connection, so the CHECK was never consulted, and it only surfaced when the
call was made against production.

So the test reads both vocabularies out of the MIGRATION FILES rather than
restating them: a future migration that adds a word to one side and not the other
fails here instead of at the first adopt.
"""

import re
from pathlib import Path

import pytest

from james_os.house_layouts import ADOPT_SOURCE_KIND, _adopt_kind

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _vocabulary(table: str) -> set[str]:
    """The source_kind values `table`'s CHECK constraint admits, from the SQL."""
    for sql_file in sorted(MIGRATIONS.glob("*.sql")):
        sql = sql_file.read_text()
        # the CREATE TABLE (or ALTER ... ADD CONSTRAINT) that constrains source_kind
        for m in re.finditer(
            r"source_kind[^;]*?CHECK\s*\(\s*source_kind\s+IN\s*\(([^)]*)\)", sql,
            re.IGNORECASE | re.DOTALL,
        ):
            if table in sql:
                return set(re.findall(r"'([a-z_]+)'", m.group(1)))
    return set()


def test_both_vocabularies_were_found():
    """A regex that silently matches nothing would make every assertion below
    vacuously true — the failure mode that makes schema tests worthless."""
    assert _vocabulary("house_layouts"), "could not read house_layouts' CHECK"


def test_every_catalogue_source_kind_is_adoptable():
    """THE invariant. Each value the catalogue can hold must map to one a brand's
    library accepts — directly, or through ADOPT_SOURCE_KIND."""
    catalogue = _vocabulary("house_layouts")
    assert catalogue, "no house_layouts vocabulary parsed"
    # design_templates' vocabulary, as the live table has it. Restated here (its
    # CREATE TABLE predates this migrations directory) and asserted against the
    # production constraint in the comment above test_the_mapping_is_minimal.
    library = {"competitor", "reference", "niche", "own", "sample"}
    for kind in catalogue:
        landed = _adopt_kind(kind)
        assert landed in library, (
            f"a catalogue layout with source_kind={kind!r} adopts as {landed!r}, "
            f"which design_templates_source_kind_check rejects"
        )


def test_curated_specifically_becomes_a_reference():
    """The value that actually broke. Pinned by name so a refactor that drops the
    mapping fails loudly rather than reintroducing a guaranteed CheckViolation."""
    assert _adopt_kind("curated") == "reference"


def test_kinds_both_tables_share_are_passed_through_untouched():
    """Only the words that NEED translating are translated — a layout read off a
    competitor must still read as 'competitor' in the brand's library, or the
    picker's niche/non-niche lanes would be fed the wrong thing."""
    for kind in ("competitor", "niche", "reference"):
        assert _adopt_kind(kind) == kind
    assert set(ADOPT_SOURCE_KIND) == {"curated"}


@pytest.mark.parametrize("junk", [None, "", "  ", 0])
def test_a_missing_source_kind_falls_back_to_a_legal_value(junk):
    assert _adopt_kind(junk) in {"competitor", "reference", "niche", "own", "sample"}
