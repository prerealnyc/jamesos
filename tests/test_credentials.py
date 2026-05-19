"""Tenant-managed credentials: persist → live → revert, no raw leaks.

Uses fields that are empty in the .env baseline (perplexity / xpoz) so
assertions are deterministic regardless of the developer's .env.
"""

import pytest

from james_os import research as research_module
from james_os.config import settings
from james_os.credentials import load_into_settings, mask, save, status


@pytest.fixture(autouse=True)
async def _clean_credentials(fresh_pool):
    # Depend on fresh_pool so the DB pool exists before we touch it.
    # Tests share the tenants row (conftest doesn't truncate tenants);
    # start and end each test with an empty credential overlay.
    await save({f: "" for f in ("perplexity_api_key", "xpoz_api_key")})
    yield
    await save({f: "" for f in ("perplexity_api_key", "xpoz_api_key")})


def test_mask_never_reveals_a_secret():
    assert mask("") == ""
    assert mask("short") == "••••"
    assert mask("sk-ant-superlongsecretvalue") == "sk-a••••alue"
    # non-secret fields are shown plainly (e.g. a model name)
    assert mask("sonar-pro", secret=False) == "sonar-pro"


@pytest.mark.asyncio
async def test_save_makes_a_key_live_without_restart():
    await save({"xpoz_api_key": "xpoz-LIVE-123"})
    # The running process picked it up immediately.
    assert settings.xpoz_api_key == "xpoz-LIVE-123"
    st = await status()
    field = next(f for f in st["fields"] if f["name"] == "xpoz_api_key")
    assert field["configured"] is True
    assert field["source"] == "ui"
    # Status must never echo the raw value, and the mask must drop the
    # middle so it can't be reconstructed.
    assert "xpoz-LIVE-123" not in str(st)
    assert field["masked"] == "xpoz••••-123"
    assert "LIVE" not in field["masked"]


@pytest.mark.asyncio
async def test_clearing_reverts_to_env_baseline():
    await save({"xpoz_api_key": "temp"})
    assert settings.xpoz_api_key == "temp"
    await save({"xpoz_api_key": ""})
    # .env baseline for xpoz is empty → reverts to "".
    assert settings.xpoz_api_key == ""
    st = await status()
    field = next(f for f in st["fields"] if f["name"] == "xpoz_api_key")
    assert field["configured"] is False
    assert field["source"] == "none"


@pytest.mark.asyncio
async def test_perplexity_key_auto_activates_research():
    research_module._provider = None
    await save({"perplexity_api_key": "pplx-TEST"})
    # "connect automatically": presence of the key flips the provider.
    assert settings.research_provider == "perplexity"
    prov = research_module.get_research_provider()
    assert prov.name == "perplexity"

    research_module._provider = None
    await save({"perplexity_api_key": ""})
    assert settings.research_provider == "stub"
    assert research_module.get_research_provider().name == "stub"


@pytest.mark.asyncio
async def test_load_into_settings_applies_db_overlay():
    await save({"xpoz_api_key": "from-db"})
    settings.xpoz_api_key = "scribbled-over"
    # Startup hook must restore the persisted value.
    await load_into_settings()
    assert settings.xpoz_api_key == "from-db"
