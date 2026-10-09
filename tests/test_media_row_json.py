"""A library row must always come out of media._row as plain JSON.

photo_subject writes a pgvector `subject_embedding` onto hero photos. Passed
through by `SELECT *` it reached the API as a pgvector value, which is not JSON,
so GET /media?role=hero_photo (and any hero photo edit) answered 500 for every
tenant whose photos had been read.
"""
import json
from datetime import datetime, timezone
from uuid import uuid4

from pgvector import Vector

from james_os import media


def _hero_row(**over):
    now = datetime.now(timezone.utc)
    row = {
        "id": uuid4(), "tenant_id": uuid4(), "role": "hero_photo", "source_type": "upload",
        "uri": "https://cdn.example/p.jpg", "file_path": "/data/p.jpg", "title": "", "platform": "",
        "mime": "image/jpeg", "tags": [], "notes": "", "analysis": None, "quality": "{}",
        "taken_at": None, "created_at": now, "updated_at": now,
        "subject_caption": "a person at a desk",
        "subject_embedding": Vector([0.01] * 1024),
        "subject_read_at": now,
    }
    row.update(over)
    return row


def test_a_read_hero_photo_serializes_without_its_embedding():
    out = media._row(_hero_row())
    assert "subject_embedding" not in out
    assert "file_path" not in out and "tenant_id" not in out
    assert isinstance(out["subject_read_at"], str)
    assert out["subject_caption"] == "a person at a desk"
    json.dumps(out)  # raises if anything non-JSON is left


def test_an_unread_photo_is_unchanged():
    out = media._row(_hero_row(subject_embedding=None, subject_read_at=None))
    assert "subject_embedding" not in out and out["subject_read_at"] is None
    json.dumps(out)
