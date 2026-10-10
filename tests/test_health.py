import pytest
from httpx import ASGITransport, AsyncClient

from james_os.config import settings
from james_os.main import app

# /events is a WRITE endpoint and is no longer public — the auth middleware only
# exempts /health, /healthz and /auth/* (auth.py:308,320). This test predates
# that and was posting unauthenticated, so it asserted 201 and got 401. The
# endpoint being protected is correct; the test needed a key. Same pattern as
# tests/test_reel_front_door.py.
SERVICE_KEY = "test-health-events-key"


@pytest.fixture
def _service_key():
    prev_key, prev_tid = settings.service_api_key, settings.service_api_tenant_id
    settings.service_api_key = SERVICE_KEY
    settings.service_api_tenant_id = settings.default_tenant_id
    yield
    settings.service_api_key, settings.service_api_tenant_id = prev_key, prev_tid


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_list_event(_service_key):
    headers = {"Authorization": f"Bearer {SERVICE_KEY}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            create = await client.post(
                "/events",
                headers=headers,
                json={
                    "event_type": "note",
                    "payload": {"text": "via api"},
                    "raw_content": "via api",
                    "source": {"adapter": "manual", "dedupe_key": "api-1"},
                },
            )
            assert create.status_code == 201, create.text
            listed = await client.get("/events", headers=headers)
    assert listed.status_code == 200
    assert any(e["raw_content"] == "via api" for e in listed.json())
