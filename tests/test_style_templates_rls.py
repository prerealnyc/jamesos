"""The house library's security model, verified against real RLS policies.

Migration 057 splits style_templates into two policies: a brand READS its own
rows plus the shared house library, and WRITES only its own. That asymmetry is
the entire reason a house template can be shared with 794 tenants safely, so it
needs a guard — the app-level `can_curate_platform()` check is defence in depth,
not the boundary.

It cannot be tested through the normal pool: the docker bootstrap role is the
cluster superuser and superusers bypass RLS unconditionally (main.py prints
"RLS IS NOT ENFORCED" on boot for exactly this reason). So these tests create a
throwaway NON-superuser role and connect as it. If the connected role can't
create one, the test skips rather than passing vacuously.
"""

import uuid
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from james_os.config import settings
from james_os.db import acquire
from james_os.templates import PLATFORM_TENANT_ID

PROBE_ROLE = "rls_probe_test"
PROBE_PASSWORD = "rls_probe_test"          # local throwaway role, local DB only
TENANT_A = uuid.UUID("0000000a-0000-0000-0000-0000000000a1")
TENANT_B = uuid.UUID("0000000a-0000-0000-0000-0000000000b2")


def _probe_dsn() -> str:
    """The pinned test DSN with the probe role's credentials swapped in, so this
    can never reach a database the suite isn't already pointed at."""
    p = urlparse(settings.database_url)
    netloc = f"{PROBE_ROLE}:{PROBE_PASSWORD}@{p.hostname}"
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, "", "", ""))


@pytest.fixture
async def probe():
    """A connection bound by RLS, plus the rows to probe against."""
    async with acquire() as conn:
        try:
            await conn.execute(
                f"DO $$ BEGIN "
                f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{PROBE_ROLE}') THEN "
                f"    CREATE ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}'; "
                f"  END IF; END $$;"
            )
            await conn.execute(f"GRANT USAGE ON SCHEMA public TO {PROBE_ROLE}")
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON style_templates TO {PROBE_ROLE}")
            await conn.execute(f"GRANT SELECT ON tenants TO {PROBE_ROLE}")
        except asyncpg.exceptions.InsufficientPrivilegeError:
            pytest.skip("connected role cannot create a role — RLS not observable here")

        for t, name in ((TENANT_A, "RLS brand A"), (TENANT_B, "RLS brand B")):
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1,$2) ON CONFLICT (id) DO NOTHING",
                t, name)
        await conn.execute("DELETE FROM style_templates WHERE name LIKE 'ZZRLS%'")
        ids = {}
        for tenant, scope, name in (
            (PLATFORM_TENANT_ID, "platform", "ZZRLS house"),
            (TENANT_A, "brand", "ZZRLS brand A"),
            (TENANT_B, "brand", "ZZRLS brand B"),
        ):
            ids[name] = await conn.fetchval(
                "INSERT INTO style_templates (tenant_id, name, slug, scope, origin, template) "
                "VALUES ($1,$2,$3,$4,'authored','{}'::jsonb) RETURNING id",
                tenant, name, name.lower().replace(" ", "-"), scope)

    try:
        conn = await asyncpg.connect(_probe_dsn(), ssl=None)
    except (asyncpg.exceptions.InvalidPasswordError,
            asyncpg.exceptions.ClientCannotConnectError) as e:
        pytest.skip(f"cannot connect as the probe role: {e}")

    # Act as brand B for the whole probe.
    tx = conn.transaction()
    await tx.start()
    await conn.execute("SELECT set_config('app.current_tenant',$1,true)", str(TENANT_B))
    try:
        yield conn, ids
    finally:
        await tx.rollback()
        await conn.close()
        async with acquire() as c:
            await c.execute("DELETE FROM style_templates WHERE name LIKE 'ZZRLS%'")


@pytest.mark.asyncio
async def test_a_brand_reads_the_house_library(probe):
    conn, _ = probe
    names = {r["name"] for r in
             await conn.fetch("SELECT name FROM style_templates WHERE name LIKE 'ZZRLS%'")}
    assert "ZZRLS house" in names          # the shared library is visible…
    assert "ZZRLS brand B" in names        # …and so is its own
    assert "ZZRLS brand A" not in names    # …but never another brand's


@pytest.mark.asyncio
async def test_a_brand_cannot_write_a_house_template(probe):
    conn, ids = probe
    hid = ids["ZZRLS house"]
    # RLS makes the row invisible to the WRITE policy: 0 rows, not an error.
    assert (await conn.execute(
        "UPDATE style_templates SET name='hijacked' WHERE id=$1", hid)).endswith(" 0")
    assert (await conn.execute(
        "DELETE FROM style_templates WHERE id=$1", hid)).endswith(" 0")


@pytest.mark.asyncio
async def test_a_brand_cannot_write_another_brands_template(probe):
    conn, ids = probe
    assert (await conn.execute(
        "UPDATE style_templates SET name='hijacked' WHERE id=$1",
        ids["ZZRLS brand A"])).endswith(" 0")


@pytest.mark.asyncio
async def test_a_brand_can_write_its_own_template(probe):
    conn, ids = probe
    assert (await conn.execute(
        "UPDATE style_templates SET summary='mine' WHERE id=$1",
        ids["ZZRLS brand B"])).endswith(" 1")


@pytest.mark.asyncio
async def test_a_brand_cannot_publish_into_the_house_library(probe):
    conn, _ = probe
    with pytest.raises((asyncpg.exceptions.CheckViolationError,
                        asyncpg.exceptions.InsufficientPrivilegeError)):
        async with conn.transaction():     # savepoint: don't poison the probe txn
            await conn.execute(
                "INSERT INTO style_templates (name, slug, scope, template) "
                "VALUES ('ZZRLS sneak','zzrls-sneak','platform','{}'::jsonb)")


@pytest.mark.asyncio
async def test_a_brands_insert_is_stamped_with_its_own_tenant(probe):
    conn, _ = probe
    # tenant_id is left to the column DEFAULT everywhere in templates.py; this
    # is what makes that safe — a brand cannot write a row it doesn't own.
    tid = await conn.fetchval(
        "INSERT INTO style_templates (name, slug, template) "
        "VALUES ('ZZRLS own','zzrls-own','{}'::jsonb) RETURNING tenant_id")
    assert tid == TENANT_B


@pytest.mark.asyncio
async def test_scope_and_owner_cannot_disagree(probe):
    conn, _ = probe
    # The schema-level invariant: scope='platform' iff owned by the platform
    # tenant. Without it, a brand row could masquerade as a house template and
    # become readable by every tenant.
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO style_templates (tenant_id, name, slug, scope, template) "
                "VALUES ($1,'ZZRLS bad','zzrls-bad','platform','{}'::jsonb)", TENANT_B)
