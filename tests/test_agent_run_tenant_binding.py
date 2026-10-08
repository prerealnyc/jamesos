"""/agent/run acts as the tenant the request is bound to.

The tenant is the auth middleware's decision; these tests pin that the handler
takes it from request.state and nothing else.
"""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from james_os.main import _bound_tenant

pytestmark = pytest.mark.nodb


def _request(state_tenant, header_tenant=None):
    headers = {"x-tenant-id": str(header_tenant)} if header_tenant else {}
    return SimpleNamespace(state=SimpleNamespace(tenant_id=state_tenant), headers=headers)


def test_the_bound_tenant_is_used():
    bound = uuid4()
    assert _bound_tenant(_request(str(bound))) == bound


def test_a_foreign_header_is_ignored():
    bound, foreign = uuid4(), uuid4()
    assert _bound_tenant(_request(str(bound), header_tenant=foreign)) == bound


def test_no_binding_means_default_tenant_not_the_header():
    foreign = uuid4()
    assert _bound_tenant(_request(None, header_tenant=foreign)) is None


def test_main_no_longer_reads_the_header_in_the_handler():
    import inspect

    from james_os import main

    src = inspect.getsource(main.agent_run)
    assert 'headers.get("x-tenant-id")' not in src, "the handler must not consult the header"
