"""Mask API keys and tokens in log records before they are written.

The Apify key used to ride in a `?token=` query string, and httpx logs every
request URL at INFO and puts it in every HTTPStatusError — so the key sat in
plain text in the Railway logs. The calls now send it in an Authorization
header; this filter is the second wall, so no URL, message, argument or
traceback that carries a secret reaches a log handler unmasked.

`install()` runs once at startup (main.lifespan, after logging.basicConfig).
It puts ONE filter on the root logger and on every handler of the root,
uvicorn and httpx loggers. Handler filters are the ones that matter: a
logger's own filters only see records logged on that exact logger, while a
handler's filters see everything that propagates to it.
"""

from __future__ import annotations

import logging
import re

_MASK = "[redacted]"

# Secrets that announce themselves by shape.
_SHAPES = [
    re.compile(r"apify_api_[A-Za-z0-9]+"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
]
# `Bearer <x>` (covers "Authorization: Bearer <x>").
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s'\",;]+")
# Secret-named query/form params: keep the name, mask the value.
_PARAM = re.compile(
    r"(?i)\b((?:access_|api_|auth_|refresh_)?token|api_?key|key|secret|"
    r"client_secret|password|passwd)=([^&\s'\"<>]+)"
)


def _literal_keys() -> list[str]:
    """Configured secrets to mask by value, read lazily so a key loaded from
    the DB after startup is still covered. Only the Apify key today."""
    try:
        from .config import settings
    except Exception:  # noqa: BLE001 — redaction must never break logging
        return []
    key = (getattr(settings, "apify_api_key", "") or "").strip()
    return [key] if len(key) >= 8 else []


def redact(text: str) -> str:
    """Return `text` with anything that looks like a token or API key masked."""
    out = str(text)
    for key in _literal_keys():
        out = out.replace(key, _MASK)
    out = _PARAM.sub(lambda m: f"{m.group(1)}={_MASK}", out)
    out = _BEARER.sub(lambda m: f"{m.group(1)}{_MASK}", out)
    for rx in _SHAPES:
        out = rx.sub(_MASK, out)
    return out


def _redact_arg(arg):
    # Numbers stay numbers so `%d` / `%.2f` still format. Anything else is
    # replaced by its masked string only when it actually carried a secret,
    # so formatting and shape (uvicorn's access formatter unpacks args) hold.
    if arg is None or isinstance(arg, (bool, int, float)):
        return arg
    try:
        s = str(arg)
    except Exception:  # noqa: BLE001
        return arg
    r = redact(s)
    return arg if r == s else r


class SecretRedactingFilter(logging.Filter):
    """Masks secrets in msg, args, exception text and stack info. Never drops
    a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Args first, one by one, so the record keeps its shape.
            if isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            if not record.args and not isinstance(record.msg, str):
                record.msg = _redact_arg(record.msg)
            # Then the rendered line: a secret can straddle the template and an
            # argument ("Bearer %s"). If anything is still exposed, collapse
            # the record to its masked, already-formatted text.
            full = record.getMessage()
            masked = redact(full)
            if masked != full:
                record.msg, record.args = masked, ()
            if record.exc_info and not record.exc_text:
                # Format the traceback now so the handler's formatter uses the
                # masked copy (Formatter.format reuses exc_text when set).
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 — never lose a log line to redaction
            pass
        return True


_FILTER = SecretRedactingFilter()
_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore")


def install() -> SecretRedactingFilter:
    """Attach the shared filter to the root logger and to every handler of the
    root, uvicorn and httpx loggers. Safe to call more than once."""
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)
        for h in lg.handlers:
            if _FILTER not in h.filters:
                h.addFilter(_FILTER)
    return _FILTER


__all__ = ["SecretRedactingFilter", "install", "redact"]
