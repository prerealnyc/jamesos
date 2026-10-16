"""The shared log filter masks keys and tokens before a record is written —
in the message, in %-args, inside URL query strings, in Bearer headers and in
exception tracebacks (httpx puts the full request URL in its errors)."""

import io
import logging

import httpx
import pytest

from james_os import log_redact
from james_os.config import settings

pytestmark = pytest.mark.nodb

FAKE = "apify_api_FAKEKEY987654"


@pytest.fixture
def out(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "")
    root = logging.getLogger()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    old_level = root.level
    root.addHandler(h)
    root.setLevel(logging.INFO)
    log_redact.install()  # after the handler exists, like after basicConfig
    yield buf
    root.removeHandler(h)
    root.setLevel(old_level)
    for name in log_redact._LOGGERS:
        logging.getLogger(name).removeFilter(log_redact._FILTER)
        for hh in logging.getLogger(name).handlers:
            hh.removeFilter(log_redact._FILTER)


def test_plain_message(out):
    logging.getLogger("james_os.test").info("key is %s here", FAKE)
    text = out.getvalue()
    assert FAKE not in text and "key is [redacted] here" in text


def test_url_query_from_a_propagated_httpx_record(out):
    # httpx's own logger: proves the handler-level filter covers child loggers.
    logging.getLogger("httpx").info(
        'HTTP Request: %s %s "%s %d %s"', "POST",
        "https://api.apify.com/v2/acts/a~b/runs?token=FAKEtoken12345&timeout=60",
        "HTTP/1.1", 201, "Created")
    text = out.getvalue()
    assert "FAKEtoken12345" not in text
    assert "runs?token=[redacted]&timeout=60" in text
    assert '"HTTP/1.1 201 Created"' in text  # %d still formats


def test_bearer_header_and_other_key_shapes(out):
    lg = logging.getLogger("james_os.test")
    lg.warning("Authorization: Bearer %s", "abc.DEF-secret_123")
    lg.warning("openai sk-%s gh ghp_%s", "a" * 24, "b" * 30)
    text = out.getvalue()
    assert "abc.DEF-secret_123" not in text and "Authorization: Bearer [redacted]" in text
    assert "a" * 24 not in text and "b" * 30 not in text


def test_exception_message_with_url(out):
    req = httpx.Request("POST", f"https://api.apify.com/v2/acts/a~b/runs?token={FAKE}")
    resp = httpx.Response(403, request=req)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        logging.getLogger("james_os.test").exception("apify failed")
    text = out.getvalue()
    assert "HTTPStatusError" in text and "403" in text
    assert FAKE not in text and "token=[redacted]" in text


def test_exception_passed_as_arg(out):
    err = RuntimeError(f"Client error for url 'https://x/y?token={FAKE}&a=1'")
    logging.getLogger("james_os.test").error("sync failed: %s", err)
    text = out.getvalue()
    assert FAKE not in text and "token=[redacted]&a=1" in text


def test_configured_key_is_masked_by_value(out, monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "plainKEYwithoutPrefix42")
    logging.getLogger("james_os.test").info("raw %s", "plainKEYwithoutPrefix42")
    assert "plainKEYwithoutPrefix42" not in out.getvalue()


def test_clean_text_and_numbers_are_untouched(out):
    logging.getLogger("james_os.test").info("synced %d posts for %s in %.1fs", 12, "acme", 3.25)
    assert "synced 12 posts for acme in 3.2s" in out.getvalue() or \
        "synced 12 posts for acme in 3.3s" in out.getvalue()


def test_install_is_idempotent(out):
    log_redact.install()
    log_redact.install()
    root = logging.getLogger()
    assert root.filters.count(log_redact._FILTER) == 1
    for h in root.handlers:
        assert h.filters.count(log_redact._FILTER) == 1


def test_redact_function():
    s = "https://api.apify.com/v2/users/me?token=abc123XYZ and api_key=zzz9&x=2"
    r = log_redact.redact(s)
    assert "abc123XYZ" not in r and "zzz9" not in r and "&x=2" in r


def test_uvicorn_access_line_keeps_its_shape(out):
    """uvicorn's AccessFormatter unpacks record.args by position — the filter
    must mask the path without collapsing the args."""
    from uvicorn.logging import AccessFormatter

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                   use_colors=False))
    h.addFilter(log_redact._FILTER)
    lg = logging.getLogger("uvicorn.access.test")
    lg.propagate = False
    lg.addHandler(h)
    try:
        lg.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET",
                "/api/x?token=FAKEaccess999&y=1", "1.1", 200)
    finally:
        lg.removeHandler(h)
    text = buf.getvalue()
    assert "FAKEaccess999" not in text
    assert "GET /api/x?token=[redacted]&y=1 HTTP/1.1" in text and " 200" in text
