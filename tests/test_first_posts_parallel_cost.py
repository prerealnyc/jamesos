"""First posts in parallel must cost EXACTLY what the sequential loop cost.

generate_template_samples clones competitor templates until n of them pass the
QA reviewer. Run sequentially that attempts a prefix of the candidate pool and
stops at the n-th pass. Run concurrently, a naive fan-out would overshoot: more
clones, more reviews, more drafts than the owner was ever charged for before.

The harness below counts every paid step (clone_post, render_reviewer.review_post,
generate_content, imagegen, the pre-drawn heroes) against a 12-post pool with a
fixed, deterministic reject set, and pins the counts to the numbers the ORIGINAL
sequential implementation produced on the same harness:

    all pass           clone 6   review 6   content 6   save 6
    some rejected      clone 10  review 9   content 6   save 6
    pool runs out      clone 12  review 12  content 4   save 4
    no photos          draws 2 (the pre-draw, unchanged), clone 6, review 6

Every AI / image / network / DB step is mocked; nothing here touches a provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import types
from uuid import uuid4

import pytest

from james_os import template_clone as tc

pytestmark = pytest.mark.nodb

TENANT = uuid4()
POOL = 12
N = 6


class Harness:
    """Counts and concurrency for every mocked paid step."""

    def __init__(self, *, reject: set[str] = frozenset(), clone_fail: set[str] = frozenset(),
                 photos: bool = True):
        self.reject, self.clone_fail, self.photos = set(reject), set(clone_fail), photos
        self.calls = {"clone": 0, "review": 0, "content": 0, "save": 0,
                      "draw": 0, "imagegen": 0}
        self.clone_order: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.progress: list[dict] = []
        self.payloads: list[dict] = []

    def posts(self) -> list[dict]:
        return [{"id": f"p{i:02d}", "handle": f"h{i}", "topic": f"topic {i}",
                 "stored_media_url": f"https://x/{i}.png"} for i in range(1, POOL + 1)]

    def install(self, monkeypatch) -> None:
        h = self
        from james_os import brand_identity, content, hero_context, imagegen, media, render_reviewer

        async def _top_posts(tenant_id, limit, *, templates_only=False):
            return h.posts() if templates_only else []

        async def _exemplars(tenant_id, k=2):
            return []

        async def _stock_pool(topics, tenant_id=None):
            return []

        async def _formats(tenant_id=None):
            return None

        async def _hero_files(tenant_id=None, limit=None, **kw):
            return [("lib-photo", b"photo")] if h.photos else []

        async def _draw(tenant_id, topic, spec=None):
            h.calls["draw"] += 1
            return b"drawn-" + str(h.calls["draw"]).encode(), True, ""

        async def _imagegen(*a, **kw):
            h.calls["imagegen"] += 1
            return b"png", {}, ""

        async def _nothing(*a, **kw):
            return None

        async def _no_images(*a, **kw):
            return []

        async def clone_post(post, tenant_id, *, hero_bytes=None, hero_generated=False,
                             hero_key="", stock_pool=None, allowed_formats=None):
            h.calls["clone"] += 1
            h.clone_order.append(post["id"])
            h.in_flight += 1
            h.max_in_flight = max(h.max_in_flight, h.in_flight)
            try:
                # Vary the wall time so completions interleave out of launch order.
                await asyncio.sleep(0.002 * ((int(post["id"][1:]) * 7) % 5 + 1))
            finally:
                h.in_flight -= 1
            if post["id"] in h.clone_fail:
                return None
            return {"png": b"png-" + post["id"].encode(), "kind": "graphic_card",
                    "content": {"headline": post["topic"]}, "topic": post["topic"],
                    "generated_hero": hero_generated, "from": post["handle"],
                    "hero_photo_key": hero_key, "spec": {"status": "ok"},
                    "source_url": post["stored_media_url"], "image_source": "library"}

        async def review_post(png, *, exemplars=None, mime="image/png"):
            h.calls["review"] += 1
            pid = png.decode().split("-", 1)[1]
            return {"status": "ok", "passed": pid not in h.reject,
                    "polish_score": 80.0 + int(pid[1:])}

        async def generate_content(brief, tenant_id=None):
            h.calls["content"] += 1
            return types.SimpleNamespace(action_id=str(uuid4()), draft="caption " + brief.topic)

        class _Storage:
            def save(self, tenant, png, name):
                h.calls["save"] += 1
                return f"https://cdn/{png.decode()}.png", "fp"

        class _Conn:
            async def execute(self, sql, action_id, payload):
                h.payloads.append({"action_id": action_id, "payload": payload})

        @contextlib.asynccontextmanager
        async def acquire(tenant_id=None):
            yield _Conn()

        monkeypatch.setattr(tc, "_top_posts", _top_posts)
        monkeypatch.setattr(tc, "_exemplars", _exemplars)
        monkeypatch.setattr(tc, "_stock_pool", _stock_pool)
        monkeypatch.setattr(tc, "_hero_or_placeholder", _draw)
        monkeypatch.setattr(tc, "clone_post", clone_post)
        monkeypatch.setattr(tc, "acquire", acquire)
        monkeypatch.setattr(tc, "_brand_logo", _nothing)
        # Item-3 helpers (found images before drawing) — absent on the
        # sequential baseline, patched out when present so no draw is saved.
        monkeypatch.setattr(tc, "_house_catalogue_images", _no_images, raising=False)
        monkeypatch.setattr(tc, "_found_heroes", _no_images, raising=False)
        monkeypatch.setattr(brand_identity, "get_enabled_formats", _formats)
        monkeypatch.setattr(hero_context, "get_hero_photo_files", _hero_files)
        monkeypatch.setattr(imagegen, "generate_post_image", _imagegen)
        monkeypatch.setattr(render_reviewer, "review_post", review_post)
        monkeypatch.setattr(content, "generate_content", generate_content)
        monkeypatch.setattr(media, "storage", lambda: _Storage())
        monkeypatch.setattr(media, "list_media", _no_images, raising=False)

    async def run(self):
        return await tc.generate_template_samples(TENANT, n=N, progress=self.progress.append)

    def done_steps(self) -> list[int]:
        return [p["done"] for p in self.progress if "done" in p]


def _check_progress(h: Harness, final: int) -> None:
    steps = h.done_steps()
    assert steps[0] == 0
    for a, b in zip(steps, steps[1:], strict=False):
        assert b - a in (0, 1), f"done jumped {a} -> {b}"
    assert steps[-1] == final


def _check_concurrency(h: Harness) -> None:
    cap = getattr(tc, "SAMPLE_CONCURRENCY", None)
    print(f"max clone_post in flight: {h.max_in_flight} (cap {cap})")
    if cap is None:          # the sequential baseline
        assert h.max_in_flight == 1
        return
    assert cap == 3
    assert h.max_in_flight == cap


def _report(name: str, h: Harness) -> None:
    print(f"[{name}] calls={h.calls} order={h.clone_order}")


@pytest.mark.asyncio
async def test_all_pass_attempts_exactly_n(monkeypatch):
    h = Harness()
    h.install(monkeypatch)
    res = await h.run()
    _report("all pass", h)
    assert res["count"] == 6 and res["rejected"] == 0 and res["failed"] == 0
    assert res["reviewed"] == 6
    assert h.calls == {"clone": 6, "review": 6, "content": 6, "save": 6,
                       "draw": 0, "imagegen": 0}
    assert sorted(h.clone_order) == [f"p{i:02d}" for i in range(1, 7)]
    assert h.clone_order == sorted(h.clone_order)   # launched in pool order
    assert len(h.payloads) == 6
    assert [s["score"] for s in res["samples"]] == sorted(
        (s["score"] for s in res["samples"]), reverse=True)
    _check_progress(h, 6)
    _check_concurrency(h)


@pytest.mark.asyncio
async def test_rejections_and_a_failed_clone_cost_the_sequential_prefix(monkeypatch):
    """p02, p04, p07 are rejected by the reviewer and p05 cannot be cloned:
    sequentially the 6th pass is p10, so exactly p01..p10 are attempted — ten
    clones, nine reviews (p05 never rendered), six captions."""
    h = Harness(reject={"p02", "p04", "p07"}, clone_fail={"p05"})
    h.install(monkeypatch)
    res = await h.run()
    _report("some rejected", h)
    assert res["count"] == 6
    assert res["rejected"] == 3 and res["failed"] == 1 and res["reviewed"] == 9
    assert h.calls == {"clone": 10, "review": 9, "content": 6, "save": 6,
                       "draw": 0, "imagegen": 0}
    assert h.clone_order == [f"p{i:02d}" for i in range(1, 11)]
    assert {s["from"] for s in res["samples"]} == {"h1", "h3", "h6", "h8", "h9", "h10"}
    _check_progress(h, 6)
    _check_concurrency(h)


@pytest.mark.asyncio
async def test_pool_runs_out_before_n(monkeypatch):
    """Eight of twelve rejected: every candidate is attempted once, never twice,
    and the four that passed are what comes back."""
    reject = {f"p{i:02d}" for i in (1, 2, 4, 6, 7, 9, 10, 12)}
    h = Harness(reject=reject)
    h.install(monkeypatch)
    res = await h.run()
    _report("pool runs out", h)
    assert res["count"] == 4 and res["rejected"] == 8 and res["reviewed"] == 12
    assert h.calls == {"clone": 12, "review": 12, "content": 4, "save": 4,
                       "draw": 0, "imagegen": 0}
    assert h.clone_order == [f"p{i:02d}" for i in range(1, 13)]
    assert {s["from"] for s in res["samples"]} == {"h3", "h5", "h8", "h11"}
    _check_progress(h, 4)
    _check_concurrency(h)


@pytest.mark.asyncio
async def test_no_photos_draws_the_same_two_backdrops(monkeypatch):
    """A brand with no library pre-draws two backdrops before the loop — that
    pre-draw is outside the loop and costs the same whatever the concurrency.
    The hero handed to each clone is assigned by launch order, so which
    backdrop a post gets never depends on completion timing."""
    h = Harness(photos=False)
    h.install(monkeypatch)
    res = await h.run()
    _report("no photos", h)
    assert res["count"] == 6
    assert h.calls == {"clone": 6, "review": 6, "content": 6, "save": 6,
                       "draw": 2, "imagegen": 0}
    _check_progress(h, 6)
    _check_concurrency(h)
