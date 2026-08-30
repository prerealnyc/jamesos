"""The one-click front door: upload a video, get a reel.

What's under test is the CHAIN, not the steps — each step (store, transcribe,
render) already has its own coverage. The chain's own contract is:

  * one progress line that spans the whole thing, so a user watching a
    transcription doesn't stare at a bar stuck on zero;
  * B-roll is described on the way in, because an undescribed asset can never
    be matched and would silently never be used;
  * a failure anywhere is REPORTED on the job, not raised into a background
    task where nobody sees it.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from james_os.config import settings
from james_os.db import acquire
from james_os.main import app
from james_os.reel_api import _REEL_JOBS, _reel_progress, _stage

BRAND_KEY = "test-front-door-key"


@pytest.fixture(autouse=True)
def _service_key():
    prev_key, prev_tid = settings.service_api_key, settings.service_api_tenant_id
    settings.service_api_key = BRAND_KEY
    settings.service_api_tenant_id = settings.default_tenant_id
    yield
    settings.service_api_key, settings.service_api_tenant_id = prev_key, prev_tid


def _headers() -> dict:
    return {"Authorization": f"Bearer {BRAND_KEY}"}


async def _client(fn):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            return await fn(client)


# ── the progress contract ────────────────────────────────────────────

def test_progress_spans_the_whole_chain_not_just_the_render():
    # The reason this exists: the production's own progress starts at the
    # render, so a user waiting on a 45s transcription would see 0%.
    job = {"status": "running"}
    seen = []
    for stage in ("uploading", "transcribing", "describing", "planning", "rendering"):
        _stage(job, stage)
        p = _reel_progress(job)
        seen.append(p["pct"])
        assert p["label"] and p["stage"] == stage
    assert seen == sorted(seen), f"progress went backwards: {seen}"
    assert seen[0] > 0, "the bar must not sit at zero while work is happening"


def test_progress_is_complete_only_when_the_job_is():
    job = {"status": "running"}
    _stage(job, "rendering")
    assert _reel_progress(job)["pct"] < 100
    job["status"] = "succeeded"
    assert _reel_progress(job)["pct"] == 100
    job["status"] = "failed"
    assert _reel_progress(job) == {"stage": "failed", "label": "Failed", "pct": 100}


def test_every_stage_has_something_a_human_can_read():
    job = {"status": "running"}
    for stage in ("uploading", "transcribing", "describing", "planning", "rendering"):
        _stage(job, stage)
        label = _reel_progress(job)["label"]
        assert label and label[0].isupper() and stage not in label.lower()


# ── the endpoint ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_non_video_upload_is_refused_before_any_work():
    r = await _client(lambda c: c.post(
        "/video/reel",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=_headers()))
    assert r.status_code == 400
    assert "video" in r.json()["detail"]


@pytest.mark.asyncio
async def test_an_empty_upload_is_refused():
    r = await _client(lambda c: c.post(
        "/video/reel",
        files={"file": ("clip.mp4", b"", "video/mp4")},
        headers=_headers()))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_the_call_hands_back_a_job_and_a_bar_and_defers_the_work(monkeypatch):
    """202 with a job and a moving bar, and the chain gets the upload.

    (Whether the chain literally runs off-thread isn't observable here —
    Starlette drains BackgroundTasks inside the same ASGI cycle — so this
    asserts the contract that IS observable: the response shape, and that the
    work was handed to the chain rather than done before responding.)
    """
    called = {}

    async def record(job, **kw):
        called.update(kw)
        called["job"] = job

    monkeypatch.setattr("james_os.reel_api._run_reel_job", record)

    r = await _client(lambda c: c.post(
        "/video/reel",
        files={"file": ("clip.mp4", b"\x00\x00fake", "video/mp4")},
        data={"title": "ZZ front door", "cards": "true"},
        headers=_headers()))
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running"
    assert body["progress"]["pct"] > 0
    assert body["progress"]["label"]

    assert called["head_bytes"] == b"\x00\x00fake"
    assert called["title"] == "ZZ front door"
    assert called["cards"] is True
    # The tenant is resolved BEFORE the chain starts — a background task can't
    # rely on the request contextvar still being set.
    assert called["tenant"] is not None
    _REEL_JOBS.pop(body["job_id"], None)


@pytest.mark.asyncio
async def test_broll_uploaded_alongside_is_counted(monkeypatch):
    async def never_runs(*_a, **_k):
        return None
    monkeypatch.setattr("james_os.reel_api._run_reel_job", never_runs)

    r = await _client(lambda c: c.post(
        "/video/reel",
        files=[
            ("file", ("clip.mp4", b"\x00head", "video/mp4")),
            ("broll", ("a.mp4", b"\x00a", "video/mp4")),
            ("broll", ("b.mp4", b"\x00b", "video/mp4")),
        ],
        headers=_headers()))
    assert r.status_code == 202
    assert r.json()["broll_received"] == 2
    _REEL_JOBS.pop(r.json()["job_id"], None)


@pytest.mark.asyncio
async def test_an_unknown_job_is_a_404():
    r = await _client(lambda c: c.get(f"/video/reel/{uuid.uuid4()}", headers=_headers()))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_status_never_leaks_internal_bookkeeping(monkeypatch):
    async def never_runs(*_a, **_k):
        return None
    monkeypatch.setattr("james_os.reel_api._run_reel_job", never_runs)
    started = await _client(lambda c: c.post(
        "/video/reel",
        files={"file": ("clip.mp4", b"\x00head", "video/mp4")},
        headers=_headers()))
    job_id = started.json()["job_id"]
    r = await _client(lambda c: c.get(f"/video/reel/{job_id}", headers=_headers()))
    assert r.status_code == 200
    # `_stage_at` is a monotonic clock reading — internal, and not JSON-meaningful.
    assert not any(k.startswith("_") for k in r.json())
    _REEL_JOBS.pop(job_id, None)


# ── failure is reported, never swallowed ─────────────────────────────

@pytest.mark.asyncio
async def test_a_failure_anywhere_lands_on_the_job(monkeypatch):
    from james_os.reel_api import _run_reel_job

    def boom(*_a, **_k):
        raise RuntimeError("storage exploded")
    monkeypatch.setattr("james_os.media.storage", boom)

    job = {"status": "running"}
    await _run_reel_job(
        job, tenant=settings.default_tenant_id, head_bytes=b"x",
        head_name="c.mp4", broll=[], title="", platform="instagram",
        aspect="9:16", caption_style="", music_mood="calm", cards=True)
    assert job["status"] == "failed"
    assert "storage exploded" in job["error"]


@pytest.mark.asyncio
async def test_the_real_ingest_failure_is_surfaced_not_replaced(monkeypatch):
    """Ingest records WHY it failed on the source row. Reporting a generic
    guess instead sends the user after the wrong problem — this actually
    happened: storage handed back a path instead of a URL, and the job said
    'no usable audio'."""
    monkeypatch.setattr("james_os.media.storage",
                        lambda: type("S", (), {"save": staticmethod(
                            lambda *_a: ("/media-files/x.mp4", "/tmp/x.mp4"))})())

    async def fake_source(**_k):
        return {"id": str(uuid.uuid4())}
    async def noop(*_a, **_k):
        return None
    async def failed_source(*_a, **_k):
        return {"status": "failed", "error": "source_url is not a real URL"}
    monkeypatch.setattr("james_os.long_form.create_source", fake_source)
    monkeypatch.setattr("james_os.long_form.ingest_source", noop)
    monkeypatch.setattr("james_os.long_form.get_source_with_candidates", failed_source)

    from james_os.reel_api import _run_reel_job
    job = {"status": "running"}
    await _run_reel_job(
        job, tenant=settings.default_tenant_id, head_bytes=b"x",
        head_name="c.mp4", broll=[], title="", platform="instagram",
        aspect="9:16", caption_style="", music_mood="calm", cards=True)
    assert job["status"] == "failed"
    assert job["error"] == "source_url is not a real URL"


@pytest.mark.asyncio
async def test_a_video_with_no_usable_audio_fails_with_a_readable_reason(monkeypatch):
    """The most likely real failure: a clip that stores fine and transcribes to
    nothing. It must say so, not die on an AttributeError deeper in."""
    monkeypatch.setattr("james_os.media.storage",
                        lambda: type("S", (), {"save": staticmethod(
                            lambda *_a: ("https://x/c.mp4", "/tmp/c.mp4"))})())

    async def fake_source(**_k):
        return {"id": str(uuid.uuid4())}
    async def noop(*_a, **_k):
        return None
    monkeypatch.setattr("james_os.long_form.create_source", fake_source)
    monkeypatch.setattr("james_os.long_form.ingest_source", noop)
    monkeypatch.setattr("james_os.long_form.create_whole_source_candidate", noop)

    from james_os.reel_api import _run_reel_job
    job = {"status": "running"}
    await _run_reel_job(
        job, tenant=settings.default_tenant_id, head_bytes=b"x",
        head_name="c.mp4", broll=[], title="", platform="instagram",
        aspect="9:16", caption_style="", music_mood="calm", cards=True)
    assert job["status"] == "failed"
    assert "audio" in job["error"].lower()
