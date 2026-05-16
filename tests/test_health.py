import pytest
from httpx import ASGITransport, AsyncClient

from james_os.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_list_event():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            create = await client.post(
                "/events",
                json={
                    "event_type": "note",
                    "payload": {"text": "via api"},
                    "raw_content": "via api",
                    "source": {"adapter": "manual", "dedupe_key": "api-1"},
                },
            )
            assert create.status_code == 201
            listed = await client.get("/events")
    assert listed.status_code == 200
    assert any(e["raw_content"] == "via api" for e in listed.json())
