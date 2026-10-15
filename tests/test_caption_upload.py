"""Captions on an owner's recording: the short path, pinned without a database."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest

from james_os import caption_upload

pytestmark = pytest.mark.nodb


def test_a_phone_sized_recording_keeps_its_frame():
    assert caption_upload.target_size(1080, 1920) is None
    assert caption_upload.target_size(720, 1280) is None


def test_a_4k_recording_is_brought_down_with_its_aspect_and_even_sides():
    assert caption_upload.target_size(2160, 3840) == (1080, 1920)
    w, h = caption_upload.target_size(3841, 2161)
    assert max(w, h) == 1920 and w % 2 == 0 and h % 2 == 0


def test_aspect_labels():
    assert caption_upload.aspect_of(1080, 1920) == "9:16"
    assert caption_upload.aspect_of(1920, 1080) == "16:9"
    assert caption_upload.aspect_of(1080, 1080) == "1:1"


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    async def fetchrow(self, sql, *args):
        self.sink.append((sql, args))
        return {"id": "00000000-0000-0000-0000-0000000000aa"}


def _fakes(monkeypatch, *, words, render_ok=True, size=(1080, 1920, 42.0)):
    from james_os import audio_trim, caption_burn, db, media, story_video, transcription

    inserted: list = []
    saved: list = []

    class _Store:
        def save(self, tenant, data, name):
            saved.append(name)
            return f"https://store.example/{name}", f"/tmp/{name}"

        def save_from_path(self, tenant, path, name):
            saved.append(name)
            return f"https://store.example/{name}", path

    async def probe(path):
        return size

    async def mp3(src, dst, bitrate="96k"):
        with open(dst, "wb") as fh:
            fh.write(b"mp3")
        return True

    async def words_of(name, data):
        return SimpleNamespace(text=" ".join(w.word for w in words), words=words, duration=size[2])

    async def burn(src, cues, **kw):
        if not render_ok:
            return {"ok": False, "reason": "ffmpeg is not available"}
        return {"ok": True, "path": src, "duration": size[2], "width": 1080, "height": 1920}

    @contextlib.asynccontextmanager
    async def acquire(tenant, require_tenant=False):
        yield _Conn(inserted)

    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(caption_burn, "probe_size", probe)
    monkeypatch.setattr(caption_burn, "render", burn)
    monkeypatch.setattr(audio_trim, "extract_audio_mp3", mp3)
    monkeypatch.setattr(transcription, "transcribe_words", words_of)
    monkeypatch.setattr(db, "acquire", acquire)
    monkeypatch.setattr(db, "set_request_tenant", lambda t: None)
    return inserted, saved, story_video


def _w(word, start, end):
    from james_os.transcription import TranscribedWord
    return TranscribedWord(word=word, start=start, end=end)


async def test_a_recording_comes_back_captioned_as_an_approved_production(monkeypatch):
    words = [_w("Folks", 0.0, 0.4), _w("capital", 0.5, 0.9), _w("matters", 1.0, 1.5),
             _w("here", 1.6, 2.0)]
    inserted, saved, _ = _fakes(monkeypatch, words=words)
    job: dict = {"status": "running", "error": ""}

    await caption_upload.run(job, tenant="t1", data=b"video", filename="take1.mov",
                             title="Capital on the line", platform="instagram",
                             caption_style="")

    assert job["status"] == "succeeded", job
    assert job["production_id"] and job["final_url"].endswith("captioned.mp4")
    assert job["cue_count"] == 2 and job["caption_style"] == caption_upload.DEFAULT_STYLE
    sql, args = inserted[0]
    assert "'succeeded'" in sql and "'approved'" in sql and "'long_form_reel'" in sql
    # The recording itself is the captionless cut the caption editor redraws on.
    assert args[6] == "https://store.example/take1.mov"
    assert len(json.loads(args[7])) == 2
    assert json.loads(args[8]) == {"captioned_upload": True}


async def test_silence_is_refused_with_the_reason(monkeypatch):
    _fakes(monkeypatch, words=[])
    job: dict = {"status": "running", "error": ""}
    await caption_upload.run(job, tenant="t1", data=b"video", filename="x.mp4", title="",
                             platform="instagram", caption_style="")
    assert job["status"] == "failed" and "no speech" in job["error"]


async def test_a_failed_burn_is_reported_and_nothing_is_saved_as_a_production(monkeypatch):
    inserted, _, _ = _fakes(monkeypatch, words=[_w("Hi", 0.0, 0.3)], render_ok=False)
    job: dict = {"status": "running", "error": ""}
    await caption_upload.run(job, tenant="t1", data=b"video", filename="x.mp4", title="",
                             platform="instagram", caption_style="")
    assert job["status"] == "failed" and "ffmpeg" in job["error"]
    assert inserted == []


async def test_an_overlong_recording_is_refused(monkeypatch):
    _fakes(monkeypatch, words=[_w("Hi", 0.0, 0.3)], size=(1080, 1920, 3600.0))
    job: dict = {"status": "running", "error": ""}
    await caption_upload.run(job, tenant="t1", data=b"video", filename="x.mp4", title="",
                             platform="instagram", caption_style="")
    assert job["status"] == "failed" and "minutes" in job["error"]


def test_progress_reads_finished_jobs_as_done():
    assert caption_upload.progress({"status": "succeeded"})["pct"] == 100
    assert caption_upload.progress({"status": "running", "stage": "captioning"})["label"]
