"""HTTP tests for the template builder + the house library.

These drive the REAL routes through the app (including the auth middleware),
because the things most likely to break here are not pure functions:

  * route ordering — /templates/capabilities and /templates/house/seed must be
    matched before /templates/{template_id}, which parses its segment as a UUID
    and would reject them with a 422;
  * the curator gate — a brand must not be able to publish into, seed, or edit
    the shared house library;
  * the read/write asymmetry — a brand SEES house templates and can copy one,
    but cannot change it.

Auth uses the BRAND service key, which the middleware already allows for
/templates; rebinding the key's tenant between calls is how one test acts as two
different tenants. (The platform key would be the more natural fit, but its
middleware path calls a SECURITY DEFINER `tenant_exists()` that lives only in
production and was never captured as a migration — so it cannot run against a
fresh database.)
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from james_os.config import settings
from james_os.db import acquire, init_pool
from james_os.main import app
from james_os.templates import PLATFORM_TENANT_ID

BRAND_KEY = "test-brand-key"
CURATOR = settings.default_tenant_id            # the house-library curator
OTHER_BRAND = uuid.UUID("0000000a-0000-0000-0000-00000000cafe")


@pytest.fixture(autouse=True)
async def _service_key_and_second_tenant():
    """Turn the brand service key on for the duration of a test, and make sure
    the second tenant really exists (style_templates.tenant_id is a FK)."""
    prev_key = settings.service_api_key
    prev_tid = settings.service_api_tenant_id
    settings.service_api_key = BRAND_KEY
    async with acquire(CURATOR) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1,'Test Brand') "
            "ON CONFLICT (id) DO NOTHING", OTHER_BRAND,
        )
    yield
    settings.service_api_key = prev_key
    settings.service_api_tenant_id = prev_tid
    # Each _client() call enters and exits the app lifespan, whose shutdown
    # closes the pool — so re-open it before cleaning up.
    await init_pool()
    # style_templates is not in conftest's TRUNCATE list, so clear what a test
    # left behind: the seeded house library AND this brand's rows. Without the
    # house sweep, a later seed finds everything already present and a test
    # asserting "seeding created something" fails on leaked state.
    async with acquire(PLATFORM_TENANT_ID) as conn:
        await conn.execute("DELETE FROM style_templates WHERE scope = 'platform'")
    async with acquire(OTHER_BRAND) as conn:
        await conn.execute(
            "DELETE FROM style_templates WHERE tenant_id = $1 OR name LIKE 'ZZAPI%'",
            OTHER_BRAND)


def _headers(tenant) -> dict:
    """Act as `tenant`. A brand key is BOUND to one tenant, so rebinding it is
    how a single test speaks as the curator and then as another brand."""
    settings.service_api_tenant_id = tenant
    return {"Authorization": f"Bearer {BRAND_KEY}"}


async def _client(fn):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            return await fn(client)


def _spec(**over) -> dict:
    base = {
        "name": "ZZAPI authored", "summary": "built in a test",
        "layout": "full_frame", "production_mode": "engaging_avatar",
        "aspect": "9:16", "caption_preset": "bold_pop", "music": "upbeat",
    }
    base.update(over)
    return base


# ── route ordering ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capabilities_is_not_swallowed_by_the_uuid_route():
    r = await _client(lambda c: c.get("/templates/capabilities", headers=_headers(CURATOR)))
    assert r.status_code == 200, r.text          # 422 here = the {template_id} route won
    body = r.json()
    assert body["layouts"] and body["caption_presets"]
    assert body["can_curate_platform"] is True


@pytest.mark.asyncio
async def test_house_seed_path_is_not_swallowed_by_the_spec_route():
    r = await _client(lambda c: c.post(
        "/templates/house/seed?dry_run=true", headers=_headers(CURATOR)))
    assert r.status_code == 201, r.text
    assert r.json()["dry_run"] is True


# ── the curator gate ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_another_brand_is_not_a_curator():
    r = await _client(lambda c: c.get(
        "/templates/capabilities", headers=_headers(OTHER_BRAND)))
    assert r.status_code == 200
    assert r.json()["can_curate_platform"] is False


@pytest.mark.asyncio
async def test_a_brand_cannot_publish_into_the_house_library():
    r = await _client(lambda c: c.post(
        "/templates", json={"spec": _spec(), "scope": "platform"},
        headers=_headers(OTHER_BRAND)))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_brand_cannot_seed_the_house_library():
    r = await _client(lambda c: c.post(
        "/templates/house/seed", headers=_headers(OTHER_BRAND)))
    assert r.status_code == 403


# ── preview + validation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_reports_render_params_without_saving():
    async def go(c):
        r = await c.post("/templates/preview", json={"spec": _spec()},
                         headers=_headers(OTHER_BRAND))
        listed = await c.get("/templates?scope=brand", headers=_headers(OTHER_BRAND))
        return r, listed
    r, listed = await _client(go)
    assert r.status_code == 200
    assert r.json()["valid"] is True
    assert r.json()["applied"]["mode"] == "engaging_avatar"
    # Nothing was persisted by previewing.
    assert listed.json()["templates"] == []


@pytest.mark.asyncio
async def test_preview_returns_every_error_for_an_unrenderable_spec():
    r = await _client(lambda c: c.post(
        "/templates/preview",
        json={"spec": _spec(layout="pip", caption_preset="glitchcore")},
        headers=_headers(OTHER_BRAND)))
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert len(body["errors"]) >= 2


@pytest.mark.asyncio
async def test_creating_an_unrenderable_template_is_a_400_not_a_broken_row():
    r = await _client(lambda c: c.post(
        "/templates", json={"spec": _spec(music="dubstep")},
        headers=_headers(OTHER_BRAND)))
    assert r.status_code == 400
    assert "dubstep" in r.json()["detail"]


# ── authoring, forking, publishing ───────────────────────────────────

@pytest.mark.asyncio
async def test_author_a_template_then_open_it_in_the_builder():
    async def go(c):
        made = await c.post("/templates", json={"spec": _spec()},
                            headers=_headers(OTHER_BRAND))
        tid = made.json()["id"]
        spec = await c.get(f"/templates/{tid}/spec", headers=_headers(OTHER_BRAND))
        return made, spec
    made, spec = await _client(go)
    assert made.status_code == 201, made.text
    assert made.json()["origin"] == "authored"
    assert made.json()["scope"] == "brand"
    assert spec.status_code == 200
    assert spec.json()["editable"] is True
    # The round-trip returns the same format, ready to edit.
    assert spec.json()["spec"]["caption_preset"] == "bold_pop"


@pytest.mark.asyncio
async def test_a_new_brand_sees_the_house_library_and_can_copy_from_it():
    async def go(c):
        seeded = await c.post("/templates/house/seed", headers=_headers(CURATOR))
        listed = await c.get("/templates", headers=_headers(OTHER_BRAND))
        house = [t for t in listed.json()["templates"] if t["scope"] == "platform"]
        forked = await c.post(f"/templates/{house[0]['id']}/fork", json={},
                              headers=_headers(OTHER_BRAND))
        return seeded, house, forked
    seeded, house, forked = await _client(go)
    assert seeded.status_code == 201, seeded.text
    # A brand that has authored nothing still has renderable formats.
    assert len(house) >= 1
    assert forked.status_code == 201, forked.text
    assert forked.json()["scope"] == "brand"
    assert forked.json()["origin"] == "forked"
    assert forked.json()["reference_media_id"] is None


@pytest.mark.asyncio
async def test_a_brand_cannot_edit_or_delete_a_house_template():
    async def go(c):
        await c.post("/templates/house/seed", headers=_headers(CURATOR))
        listed = await c.get("/templates?scope=platform", headers=_headers(OTHER_BRAND))
        hid = listed.json()["templates"][0]["id"]
        edit = await c.put(f"/templates/{hid}/spec", json={"spec": _spec()},
                           headers=_headers(OTHER_BRAND))
        drop = await c.delete(f"/templates/{hid}", headers=_headers(OTHER_BRAND))
        return edit, drop
    edit, drop = await _client(go)
    assert edit.status_code == 403
    assert "fork it" in edit.json()["detail"]
    assert drop.status_code == 403


@pytest.mark.asyncio
async def test_the_curator_can_publish_a_brand_template_and_keeps_the_original():
    async def go(c):
        made = await c.post("/templates", json={"spec": _spec()},
                            headers=_headers(CURATOR))
        tid = made.json()["id"]
        pub = await c.post(f"/templates/{tid}/publish",
                           json={"name": "ZZAPI published"},
                           headers=_headers(CURATOR))
        original = await c.get(f"/templates/{tid}", headers=_headers(CURATOR))
        # and the other brand can now see it
        seen = await c.get("/templates?scope=platform", headers=_headers(OTHER_BRAND))
        await c.delete(f"/templates/{tid}", headers=_headers(CURATOR))
        return pub, original, seen
    pub, original, seen = await _client(go)
    assert pub.status_code == 201, pub.text
    assert pub.json()["scope"] == "platform"
    assert original.status_code == 200          # publishing COPIES, never moves
    assert any(t["name"] == "ZZAPI published" for t in seen.json()["templates"])


@pytest.mark.asyncio
async def test_seeding_twice_adds_nothing_the_second_time():
    async def go(c):
        first = await c.post("/templates/house/seed", headers=_headers(CURATOR))
        second = await c.post("/templates/house/seed", headers=_headers(CURATOR))
        return first, second
    first, second = await _client(go)
    assert first.json()["created"]
    assert second.json()["created"] == []
    assert len(second.json()["skipped"]) == second.json()["total"]
