"""The spend ledger against the real table: rows land on the right tenant,
record() never raises, the cap fires on the boundary, PAUSE_SPEND stops the
scheduler and autopilot, and /v1/spend has the shape the 2.0 side reads."""

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from james_os import autopilot, scheduler, spend
from james_os.config import settings
from james_os.db import acquire, init_pool

TENANT = settings.default_tenant_id
OTHER = uuid.UUID("0000000a-0000-0000-0000-00000000c057")
BRAND_KEY = "test-spend-key"


@pytest.fixture(autouse=True)
async def _clean_ledger():
    """provider_spend is not in conftest's TRUNCATE list (it is append-only by
    design — the app role cannot DELETE), so each test sweeps what it wrote
    and resets the knobs it turned."""
    prev_pause = settings.pause_spend
    prev_cap = settings.spend_daily_cap_usd
    async with acquire(TENANT) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'ZZTEST spend') "
            "ON CONFLICT (id) DO NOTHING", OTHER)
    # Swept BEFORE as well as after: a crashed earlier run left rows behind and
    # the exact-count assertions below failed on leftovers, not on the code.
    await _sweep()
    yield
    settings.pause_spend = prev_pause
    settings.spend_daily_cap_usd = prev_cap
    await init_pool()
    await _sweep()


async def _sweep():
    for t in (TENANT, OTHER):
        async with acquire(t) as conn:
            # explicit tenant: the docker role is a superuser, so RLS is off here
            await conn.execute("DELETE FROM provider_spend WHERE tenant_id = $1", t)
            await conn.execute(
                "UPDATE tenants SET config = coalesce(config,'{}'::jsonb) - 'spend' "
                "WHERE id = $1", t)
    async with acquire(OTHER) as conn:
        await conn.execute("DELETE FROM autopilot_runs WHERE tenant_id = $1", OTHER)
        await conn.execute(
            "DELETE FROM scheduled_jobs WHERE tenant_id = $1 AND kind = 'zz_spend_probe'", OTHER)


async def _rows(tenant):
    async with acquire(tenant) as conn:
        return [dict(r) for r in await conn.fetch(
            "SELECT provider, model, units, unit_kind, est_usd, agent_or_job, meta "
            "FROM provider_spend WHERE tenant_id = $1 ORDER BY created_at", tenant)]


# ── recording ────────────────────────────────────────────────────────

async def test_record_tokens_writes_a_priced_row_on_the_named_tenant():
    class Usage:
        prompt_tokens = 1_000_000
        completion_tokens = 0

    ok = await spend.record_tokens("openai", "gpt-4o-mini", Usage(), "llm.complete_json",
                                   tenant_id=OTHER)
    assert ok is True
    rows = await _rows(OTHER)
    assert len(rows) == 1
    r = rows[0]
    assert r["provider"] == "openai" and r["model"] == "gpt-4o-mini"
    assert float(r["units"]) == 1_000_000 and r["unit_kind"] == "tokens"
    assert float(r["est_usd"]) == pytest.approx(0.15)
    meta = json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]
    assert meta["estimate"] is True
    assert meta["tokens_in"] == 1_000_000 and meta["tokens_out"] == 0
    assert "tenant_fallback" not in meta
    # GUC default landed it on OTHER, not on the default tenant.
    assert await _rows(TENANT) == []


async def test_record_images_is_one_row_per_image():
    ok = await spend.record_images("gpt-image-1", 3, "imagegen.generate_post_image",
                                   size="1024x1536", tenant_id=OTHER)
    assert ok is True
    rows = await _rows(OTHER)
    assert len(rows) == 3
    assert all(r["unit_kind"] == "image" and float(r["units"]) == 1 for r in rows)
    assert all(float(r["est_usd"]) == pytest.approx(0.25) for r in rows)


async def test_unknown_model_is_recorded_unpriced():
    await spend.record_tokens("openai", "gpt-99-ultra", {"prompt_tokens": 10, "completion_tokens": 5},
                              "x", tenant_id=OTHER)
    (r,) = await _rows(OTHER)
    meta = json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]
    assert float(r["est_usd"]) == 0 and meta["priced"] is False


async def test_job_scope_names_the_job_on_the_row():
    with spend.job_scope("competitor_sync"):
        await spend.record("openai", "gpt-4o", 10, "tokens", 0.001, "llm.complete_json",
                           tenant_id=OTHER)
    (r,) = await _rows(OTHER)
    assert r["agent_or_job"] == "competitor_sync"
    meta = json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]
    assert meta["site"] == "llm.complete_json"


async def test_fallback_crossing_is_a_counted_row():
    await spend.record_fallback("claude-opus-4-7", "gpt-4o-mini", "credit balance is too low",
                                tenant_id=OTHER)
    (r,) = await _rows(OTHER)
    assert r["provider"] == "fallback" and r["unit_kind"] == "crossing"
    assert r["model"] == "claude-opus-4-7->gpt-4o-mini" and float(r["est_usd"]) == 0


async def test_record_never_raises(monkeypatch):
    # The pool is gone / the DB is down: the call that spent the money already
    # succeeded, so the receipt failing must be a log line, not an exception.
    def _boom(*a, **k):
        raise RuntimeError("DB pool not initialized")
    monkeypatch.setattr(spend, "acquire", _boom)
    assert await spend.record("openai", "gpt-4o", 1, "tokens", 0.01, "x") is False
    assert await spend.record_tokens("openai", "gpt-4o", None, "x") is False
    assert await spend.record_images("gpt-image-1", 2, "x") is False
    assert await spend.record_fallback("a", "b", "why") is False
    # Garbage in is also not an exception.
    monkeypatch.undo()
    assert await spend.record("openai", "gpt-4o", "not-a-number", "tokens", 0.01, "x",
                              tenant_id=OTHER) is False


async def test_fallback_llm_logs_a_warning_and_a_crossing_row(caplog, monkeypatch):
    import logging

    from james_os.llm import LLM, FallbackLLM

    class Broke(LLM):
        model_name = "claude-opus-4-7"

        async def complete_json(self, *a, **k):
            raise RuntimeError("Your credit balance is too low to access the API")

    class Works(LLM):
        model_name = "gpt-4o-mini"

        async def complete_json(self, *a, **k):
            return {"ok": True}

    from james_os import db as db_module
    db_module.set_request_tenant(OTHER)
    try:
        with caplog.at_level(logging.WARNING, logger="llm"):
            out = await FallbackLLM(Broke(), Works()).complete_json("s", [])
    finally:
        db_module.set_request_tenant(None)
    assert out == {"ok": True}
    assert any("crossing to gpt-4o-mini" in m for m in caplog.messages)
    (r,) = await _rows(OTHER)
    assert r["provider"] == "fallback"


# ── the cap ──────────────────────────────────────────────────────────

async def test_over_cap_boundaries():
    settings.spend_daily_cap_usd = 5.0
    settings.pause_spend = False
    assert await spend.over_cap(OTHER) is False
    await spend.record("openai", "gpt-image-1", 1, "image", 4.99, "x", tenant_id=OTHER)
    assert await spend.daily_total_usd(OTHER) == pytest.approx(4.99)
    assert await spend.over_cap(OTHER) is False          # just under
    await spend.record("openai", "gpt-image-1", 1, "image", 0.01, "x", tenant_id=OTHER)
    assert await spend.over_cap(OTHER) is True           # exactly at the cap counts
    # The other tenant's spend is its own.
    assert await spend.over_cap(TENANT) is False
    st = await spend.cap_status(OTHER)
    assert st["blocked"] and st["over_cap"] and "cap reached" in st["reason"]
    assert st["cap_usd"] == 5.0 and st["today_usd"] == pytest.approx(5.0)


async def test_per_tenant_cap_comes_from_tenants_config():
    settings.spend_daily_cap_usd = 5.0
    async with acquire(OTHER) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config,'{}'::jsonb) || "
            "'{\"spend\": {\"daily_cap_usd\": 1.5}}'::jsonb WHERE id = $1", OTHER)
    assert await spend.daily_cap_usd(OTHER) == 1.5
    assert await spend.daily_cap_usd(TENANT) == 5.0       # untouched brand → default
    await spend.record("openai", "gpt-4o", 1, "tokens", 1.5, "x", tenant_id=OTHER)
    assert await spend.over_cap(OTHER) is True
    # a cap of 0 means no cap
    async with acquire(OTHER) as conn:
        await conn.execute(
            "UPDATE tenants SET config = config || '{\"spend\": {\"daily_cap_usd\": 0}}'::jsonb "
            "WHERE id = $1", OTHER)
    assert await spend.over_cap(OTHER) is False


async def test_pause_spend_blocks_every_tenant_regardless_of_spend():
    settings.pause_spend = True
    assert await spend.over_cap(OTHER) is True
    st = await spend.cap_status(TENANT)
    assert st["paused"] and st["blocked"] and "PAUSE_SPEND" in st["reason"]
    assert st["over_cap"] is False                      # paused ≠ over cap


# ── entry points ─────────────────────────────────────────────────────

async def test_scheduler_skips_a_job_when_paused(capsys):
    settings.pause_spend = True
    ran = []

    async def handler(tenant_id, config):
        ran.append(tenant_id)

    async with acquire(OTHER) as conn:
        job_id = await conn.fetchval(
            "INSERT INTO scheduled_jobs (tenant_id, kind) VALUES ($1, 'zz_spend_probe') "
            "ON CONFLICT (tenant_id, kind) DO UPDATE SET enabled = true RETURNING id", OTHER)
    try:
        await scheduler._run_one({"id": job_id, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        assert ran == []
        async with acquire(OTHER) as conn:
            row = await conn.fetchrow(
                "SELECT last_status, last_error, last_run_at FROM scheduled_jobs WHERE id=$1", job_id)
        assert row["last_status"] == "skipped" and "PAUSE_SPEND" in row["last_error"]
        assert row["last_run_at"] is not None     # stamped, so it does not hog the due queue
        assert "skipped" in capsys.readouterr().out
        # and with the switch off, the same job runs
        settings.pause_spend = False
        await scheduler._run_one({"id": job_id, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        assert ran == [OTHER]
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM scheduled_jobs WHERE id = $1", job_id)


async def test_autopilot_manual_skip_leaves_a_visible_refusal_row(caplog):
    # POST /autopilot/run says "Batch running — watch /autopilot/runs"; a skip
    # that only logged left that list silent. The refusal is a failed row
    # whose error names the gate.
    settings.spend_daily_cap_usd = 0.5
    await spend.record("openai", "gpt-image-1", 1, "image", 0.5, "x", tenant_id=OTHER)
    try:
        out = await autopilot.run_batch("manual", tenant_id=OTHER)
        assert out["status"] == "skipped" and "cap reached" in out["reason"]
        assert out["id"] is not None
        async with acquire(OTHER) as conn:
            row = await conn.fetchrow(
                "SELECT status, stage, error, trigger, completed_at FROM autopilot_runs "
                "WHERE id = $1 AND tenant_id = $2", out["id"], OTHER)
        assert row["status"] == "failed" and row["stage"] == "skipped"
        assert row["error"].startswith("skipped:") and "cap reached" in row["error"]
        assert row["trigger"] == "manual" and row["completed_at"] is not None
        assert any("batch skipped" in m for m in caplog.messages)
        # nothing was drafted: no 'running' row was ever opened for this brand
        async with acquire(OTHER) as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM autopilot_runs WHERE tenant_id = $1 "
                "AND status <> 'failed'", OTHER) == 0
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM autopilot_runs WHERE tenant_id = $1", OTHER)


async def test_autopilot_scheduled_skip_writes_at_most_one_row_a_day():
    # The scheduled trigger is retried every 30 minutes; 48 refusal rows a day
    # would bury the real runs.
    settings.pause_spend = True
    try:
        a = await autopilot.run_batch("scheduled", tenant_id=OTHER)
        b = await autopilot.run_batch("scheduled", tenant_id=OTHER)
        assert a["status"] == b["status"] == "skipped" and "PAUSE_SPEND" in a["reason"]
        assert a["id"] == b["id"]
        async with acquire(OTHER) as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM autopilot_runs WHERE tenant_id = $1 "
                "AND stage = 'skipped'", OTHER) == 1
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM autopilot_runs WHERE tenant_id = $1", OTHER)


async def _probe_job(cadence_hours: int = 24):
    async with acquire(OTHER) as conn:
        return await conn.fetchval(
            "INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours) "
            "VALUES ($1, 'zz_spend_probe', $2) "
            "ON CONFLICT (tenant_id, kind) DO UPDATE SET enabled = true, "
            "cadence_hours = EXCLUDED.cadence_hours, last_run_at = NULL RETURNING id",
            OTHER, cadence_hours)


async def _next_due_in_hours(job_id) -> float:
    async with acquire(OTHER) as conn:
        return float(await conn.fetchval(
            "SELECT extract(epoch FROM (last_run_at + make_interval(hours => cadence_hours)"
            " - now())) / 3600 FROM scheduled_jobs WHERE id = $1", job_id))


async def test_scheduled_job_spend_is_billed_to_the_jobs_brand():
    # Handlers call llm.complete_json / vision sites, which record with no
    # tenant_id. Before the job scope carried the tenant, every brand's
    # scheduled spend landed on the default tenant (tenant_fallback=true) and
    # the busy brand's own cap never saw it.
    settings.pause_spend = False
    settings.spend_daily_cap_usd = 5.0

    async def handler(tenant_id, config):
        await spend.record_tokens("openai", "gpt-4o", {"prompt_tokens": 1_000_000,
                                                       "completion_tokens": 0},
                                  "llm.complete_json")

    job_id = await _probe_job()
    try:
        await scheduler._run_one({"id": job_id, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        (r,) = await _rows(OTHER)
        meta = json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]
        assert "tenant_fallback" not in meta
        assert r["agent_or_job"] == "zz_spend_probe" and meta["site"] == "llm.complete_json"
        assert await _rows(TENANT) == []
        st = await spend.cap_status(OTHER)
        assert st["today_usd"] == pytest.approx(2.5)
        # the scope does not leak past the job
        assert spend.current_tenant() is None and spend.current_job() is None
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM scheduled_jobs WHERE id = $1", job_id)


async def test_paused_weekly_job_comes_back_within_hours_not_a_week():
    settings.pause_spend = True

    async def handler(tenant_id, config):
        raise AssertionError("must not run while paused")

    job_id = await _probe_job(cadence_hours=168)
    try:
        await scheduler._run_one({"id": job_id, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        due_in = await _next_due_in_hours(job_id)
        assert 0 < due_in <= 1.01            # an hour, not 168
        # ...but not due on THIS tick, so it does not hog the LIMIT 10
        due = {j["id"] for j in await scheduler._due_jobs()}
        assert job_id not in due
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM scheduled_jobs WHERE id = $1", job_id)


async def test_over_cap_job_comes_back_after_the_utc_reset():
    settings.pause_spend = False
    settings.spend_daily_cap_usd = 0.5
    await spend.record("openai", "gpt-image-1", 1, "image", 0.5, "x", tenant_id=OTHER)

    async def handler(tenant_id, config):
        raise AssertionError("must not run over the cap")

    weekly = await _probe_job(cadence_hours=168)
    try:
        await scheduler._run_one({"id": weekly, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        expected = scheduler._seconds_until_utc_midnight() / 3600
        assert await _next_due_in_hours(weekly) == pytest.approx(expected, abs=0.05)
        assert expected <= 24.1
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM scheduled_jobs WHERE id = $1", weekly)
    # a job whose cadence is shorter than the wait is never pushed past it
    hourly = await _probe_job(cadence_hours=1)
    try:
        await scheduler._run_one({"id": hourly, "tenant_id": OTHER, "kind": "zz_spend_probe",
                                  "config": {}}, {"zz_spend_probe": handler})
        assert await _next_due_in_hours(hourly) <= 1.01
    finally:
        async with acquire(OTHER) as conn:
            await conn.execute("DELETE FROM scheduled_jobs WHERE id = $1", hourly)


async def test_pause_spend_stops_the_weekly_roster_refresh(monkeypatch):
    # The autopilot loop's weekly refresh ends in a paid Apify scrape; it was
    # the one recurring door the kill switch left open.
    from james_os import research_roster
    calls = []

    async def fake_refresh(tenant_id=None):
        calls.append(tenant_id)
        return {"refreshed": True}

    monkeypatch.setattr(research_roster, "maybe_weekly_refresh", fake_refresh)
    settings.pause_spend = True
    out = await scheduler.weekly_roster_refresh()
    assert out["skipped"] is True and "PAUSE_SPEND" in out["reason"]
    assert calls == []
    settings.pause_spend = False
    assert await scheduler.weekly_roster_refresh() == {"refreshed": True}
    assert calls == [None]


# ── call sites write rows ─────────────────────────────────────────────

class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def test_openai_complete_json_writes_a_row():
    from james_os import db as db_module
    from james_os.llm import OpenAILLM

    class Completions:
        async def create(self, **kw):
            return _NS(
                usage=_NS(prompt_tokens=2000, completion_tokens=500),
                choices=[_NS(message=_NS(content='{"ok": true}'), finish_reason="stop")],
            )

    llm = OpenAILLM("sk-test", "gpt-4o")
    llm.client = _NS(chat=_NS(completions=Completions()))
    db_module.set_request_tenant(OTHER)
    try:
        assert await llm.complete_json("s", [{"role": "user", "content": "x"}]) == {"ok": True}
    finally:
        db_module.set_request_tenant(None)
    (r,) = await _rows(OTHER)
    assert (r["provider"], r["model"], r["unit_kind"]) == ("openai", "gpt-4o", "tokens")
    assert float(r["units"]) == 2500
    assert float(r["est_usd"]) == pytest.approx((2000 * 2.5 + 500 * 10) / 1e6)


async def test_anthropic_complete_json_records_before_a_truncated_parse_fails():
    from james_os.llm import AnthropicLLM, LLMParseError

    class Messages:
        calls = 0

        async def create(self, **kw):
            Messages.calls += 1
            return _NS(usage=_NS(input_tokens=1000, output_tokens=4096),
                       content=[_NS(text='{"half": "an answ')], stop_reason="max_tokens")

    llm = AnthropicLLM("sk-ant-test", "claude-sonnet-4-5")
    llm.client = _NS(messages=Messages())
    with spend.job_scope("zz_probe", tenant_id=OTHER):
        with pytest.raises(LLMParseError):
            await llm.complete_json("s", [{"role": "user", "content": "x"}])
    assert Messages.calls == 1                  # a parse failure is not retried
    (r,) = await _rows(OTHER)                   # ...but it WAS billed, and counted
    assert r["provider"] == "anthropic" and r["agent_or_job"] == "zz_probe"
    assert float(r["est_usd"]) == pytest.approx((1000 * 3 + 4096 * 15) / 1e6)


async def test_generate_post_image_writes_one_row_per_image(monkeypatch):
    import base64

    from james_os import imagegen

    png = base64.b64encode(b"\x89PNG fake").decode()

    class Images:
        async def generate(self, **kw):
            return _NS(data=[_NS(b64_json=png), _NS(b64_json=png)], usage=None)

    monkeypatch.setattr(imagegen, "_client", lambda: _NS(images=Images()))

    async def no_directive(_t):
        return ""

    monkeypatch.setattr(imagegen, "_brand_visual_directive", no_directive)
    data, meta, err = await imagegen.generate_post_image(
        "a topic", platform="instagram", aspect="1:1", tenant_id=OTHER)
    assert err == "" and data
    rows = await _rows(OTHER)
    assert len(rows) == 2
    assert all(r["unit_kind"] == "image" and r["model"] == settings.image_model for r in rows)
    assert all(float(r["est_usd"]) == pytest.approx(
        spend.estimate_image_usd(settings.image_model, meta["size"])) for r in rows)
    assert await _rows(TENANT) == []


# ── the route ────────────────────────────────────────────────────────

async def _client(fn):
    from james_os.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            return await fn(client)


async def test_v1_spend_shape_is_tenant_bound():
    settings.spend_daily_cap_usd = 5.0
    settings.pause_spend = False
    prev_key, prev_tid = settings.service_api_key, settings.service_api_tenant_id
    settings.service_api_key = BRAND_KEY
    settings.service_api_tenant_id = OTHER
    try:
        await spend.record_tokens("openai", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 1000},
                                  "llm.complete_json", tenant_id=OTHER)
        await spend.record_images("gpt-image-1", 2, "imagegen", size="1024x1024", tenant_id=OTHER)
        # noise on the OTHER brand that must not appear
        await spend.record("openai", "gpt-4o", 1, "tokens", 99.0, "x", tenant_id=TENANT)
        headers = {"Authorization": f"Bearer {BRAND_KEY}"}
        r = await _client(lambda c: c.get("/v1/spend?days=7", headers=headers))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["days"] == 7 and body["estimate"] is True
        assert body["cap_usd"] == 5.0 and body["paused"] is False and body["over_cap"] is False
        by_model = {(x["provider"], x["model"]): x for x in body["rows"]}
        assert by_model[("openai", "gpt-4o")]["calls"] == 1
        assert by_model[("openai", "gpt-4o")]["est_usd"] == pytest.approx(0.0125)
        assert by_model[("openai", "gpt-image-1")]["calls"] == 2
        assert by_model[("openai", "gpt-image-1")]["units"] == 2
        assert all(set(x) >= {"day", "provider", "model", "units", "calls", "est_usd"}
                   for x in body["rows"])
        assert body["today_usd"] == pytest.approx(0.0125 + 2 * 0.167)
        assert body["total_usd"] == pytest.approx(body["today_usd"])   # the $99 is not ours
        # no key → 401, same as every other /v1 route
        r = await _client(lambda c: c.get("/v1/spend"))
        assert r.status_code == 401
    finally:
        settings.service_api_key, settings.service_api_tenant_id = prev_key, prev_tid


async def test_a_brand_cap_is_set_and_cleared_through_v1():
    """The cap was read from tenants.config and nothing could write it."""
    settings.spend_daily_cap_usd = 0.0
    prev_key, prev_tid = settings.service_api_key, settings.service_api_tenant_id
    settings.service_api_key = BRAND_KEY
    settings.service_api_tenant_id = OTHER
    try:
        headers = {"Authorization": f"Bearer {BRAND_KEY}"}
        r = await _client(lambda c: c.put("/v1/spend/cap", json={"cap_usd": 7.5}, headers=headers))
        assert r.status_code == 200, r.text
        assert r.json()["cap_usd"] == 7.5
        await init_pool()   # the client's lifespan closed the pool on exit
        assert await spend.daily_cap_usd(OTHER) == 7.5
        assert await spend.daily_cap_usd(TENANT) == 0.0, "only the bound brand"
        r = await _client(lambda c: c.put("/v1/spend/cap", json={"cap_usd": None}, headers=headers))
        assert r.json()["default"] is True
        await init_pool()
        assert await spend.daily_cap_usd(OTHER) == 0.0
        r = await _client(lambda c: c.put("/v1/spend/cap", json={"cap_usd": -1}, headers=headers))
        assert r.status_code == 422
        r = await _client(lambda c: c.put("/v1/spend/cap", json={"cap_usd": 1}))
        assert r.status_code == 401
    finally:
        settings.service_api_key, settings.service_api_tenant_id = prev_key, prev_tid
