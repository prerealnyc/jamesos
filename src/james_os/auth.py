"""Authentication — bcrypt + JWT-in-cookie + revocable sessions.

Design notes (the choices, defended):

  * **bcrypt cost 12.** ~250ms on a M-series Mac for one hash —
    expensive enough to throttle brute force, fast enough not to
    bother a legit signup. 14 is paranoid; 10 is too cheap.

  * **JWT in an HTTP-only cookie**, NOT in localStorage. Cookie is
    httpOnly + Secure (in prod) + SameSite=Lax — protects against
    XSS exfil and gives us free CSRF resistance for same-site
    requests. We still add an explicit CSRF token to be safe with
    cross-origin Next.js dev (3000 → 8001).

  * **Token is opaque to the client.** The JWT carries (session_id,
    user_id) signed with a tenant-local secret derived from env.
    The DB session row's `token_hash` is what we look up — that
    way we can revoke a session and the cookie stops working
    immediately, without waiting for JWT exp.

  * **Per-IP rate limit on /auth/login** — 5 attempts / 5 min.
    Per-account lockout — 5 consecutive failures triggers a 15-min
    cooldown. Both write to `login_attempts`. Successful logins
    reset the per-account counter.

  * **The first signup claims the default tenant.** Existing data
    (productions, watchlist, brand accounts, output library) becomes
    that user's workspace. We don't wipe — JP's 28 reels stay
    accessible. New users get fresh tenants.

Honest gaps flagged in code:
  * No email verification — no SMTP wired (Phase 2).
  * No forgot-password (same SMTP reason).
  * No 2FA. Phase 3.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response

from .config import settings
from .db import acquire


COOKIE_NAME = "jos_session"
CSRF_COOKIE_NAME = "jos_csrf"
SESSION_DAYS = 30
BCRYPT_ROUNDS = 12
LOGIN_RATE_WINDOW_MIN = 5
LOGIN_RATE_MAX_FAILS = 5
LOCKOUT_FAILS = 5
LOCKOUT_MINUTES = 15


def _jwt_secret() -> str:
    """JWT signing secret. Required in prod; derived in dev so the
    app still runs locally without any extra setup, with a clear
    warning printed once on cold start."""
    s = (os.environ.get("JOS_JWT_SECRET") or "").strip()
    if s:
        return s
    # Dev fallback: derive from a stable machine bit + a hardcoded
    # salt. This will VARY across machines, which is the right
    # behaviour (a session from another machine should not validate).
    derived = hashlib.sha256(
        f"jos-dev-fallback|{settings.database_url[:120]}".encode()
    ).hexdigest()
    if not getattr(_jwt_secret, "_warned", False):
        print(
            "[auth] WARN: JOS_JWT_SECRET not set; using dev-derived "
            "secret. Set this env var in production — anyone with "
            "your database URL could forge sessions otherwise."
        )
        _jwt_secret._warned = True  # type: ignore[attr-defined]
    return derived


def hash_password(plain: str) -> str:
    """bcrypt cost 12. Returns the full $2b$… string ready for DB
    storage. Truncates to 72 bytes (bcrypt's limit) — anything beyond
    is ignored by the algorithm anyway."""
    return bcrypt.hashpw(
        plain.encode("utf-8")[:72],
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(s)))


def validate_password(p: str) -> Optional[str]:
    """Returns an error string when the password fails policy, or
    None when it's acceptable. Policy: ≥ 10 chars, mixed character
    classes (≥3 of: lowercase / uppercase / digit / symbol)."""
    if not p or len(p) < 10:
        return "Password must be at least 10 characters."
    classes = 0
    if re.search(r"[a-z]", p): classes += 1
    if re.search(r"[A-Z]", p): classes += 1
    if re.search(r"\d", p): classes += 1
    if re.search(r"[^\w\s]", p): classes += 1
    if classes < 3:
        return "Password needs at least 3 of: lowercase, uppercase, digit, symbol."
    if len(p) > 256:
        return "Password is too long."
    return None


# ── session token ────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """SHA-256 of the raw JWT — the column we look up against. Storing
    the raw token in the DB would let a DB read forge sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mint_token(session_id: UUID, user_id: UUID) -> str:
    payload = {
        "sid": str(session_id),
        "uid": str(user_id),
        "iat": int(time.time()),
        # No exp in the JWT — the DB session row carries the real
        # expiry. JWT exp would just give us two places to update.
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except (jwt.PyJWTError, ValueError):
        return None


# ── domain objects ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    role: str
    session_id: UUID


# ── login throttling ────────────────────────────────────────────────


async def _record_login_attempt(ip: str, email: str, succeeded: bool) -> None:
    async with acquire(_DEFAULT_TENANT_UUID()) as conn:
        await conn.execute(
            "INSERT INTO login_attempts (ip, email, succeeded) "
            "VALUES ($1, $2, $3)",
            ip, email, succeeded,
        )


async def _ip_too_many_fails(ip: str) -> bool:
    """LOGIN_RATE_MAX_FAILS failures in LOGIN_RATE_WINDOW_MIN minutes
    blocks further attempts. Cheap rate limit without a Redis dep."""
    since = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_RATE_WINDOW_MIN)
    async with acquire(_DEFAULT_TENANT_UUID()) as conn:
        n = await conn.fetchval(
            "SELECT count(*) FROM login_attempts "
            "WHERE ip = $1 AND succeeded = false AND created_at >= $2",
            ip, since,
        )
    return int(n or 0) >= LOGIN_RATE_MAX_FAILS


def _DEFAULT_TENANT_UUID() -> UUID:
    # Helper — acquire() needs a tenant_id even for auth ops; we use
    # the legacy default tenant for the auth tables since they're
    # shared across the whole install.
    return settings.default_tenant_id


# ── session helpers ─────────────────────────────────────────────────


async def create_session(
    user_id: UUID, tenant_id: UUID, *, user_agent: str = "", ip: str = "",
) -> tuple[str, UUID]:
    """Create a new session row and return (raw_token, session_id).
    The raw token goes into the cookie; only the SHA-256 hash is
    persisted."""
    session_id = UUID(bytes=secrets.token_bytes(16), version=4)
    token = _mint_token(session_id, user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """INSERT INTO sessions
                 (id, user_id, tenant_id, token_hash,
                  user_agent, ip, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            session_id, user_id, tenant_id, _hash_token(token),
            user_agent[:300], ip[:64], expires_at,
        )
    return token, session_id


async def revoke_session(session_id: UUID, tenant_id: UUID) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE id = $1",
            session_id,
        )


async def get_session(token: str) -> Optional[dict]:
    """Validate a raw session token. Returns the joined user/tenant
    row when the session is alive (not revoked, not expired); None
    otherwise. Also bumps last_seen_at to give the next admin pane a
    'last active' signal."""
    if not token:
        return None
    decoded = _decode_token(token)
    if not decoded:
        return None
    th = _hash_token(token)
    async with acquire(_DEFAULT_TENANT_UUID()) as conn:
        # Two-step on purpose: sessions carries no RLS (it must be
        # readable before a tenant context exists), so look the session
        # up first, then re-bind app.current_tenant to ITS tenant before
        # touching users — users has an RLS tenant policy, and a join
        # under the default-tenant context would silently drop every
        # non-default-tenant user once RLS is enforced.
        sess = await conn.fetchrow(
            """SELECT id AS session_id, user_id, tenant_id,
                      expires_at, revoked_at
                 FROM sessions WHERE token_hash = $1""",
            th,
        )
        if sess is None:
            return None
        if sess["revoked_at"] is not None:
            return None
        if sess["expires_at"] < datetime.now(timezone.utc):
            return None
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, true)",
            str(sess["tenant_id"]),
        )
        u = await conn.fetchrow(
            "SELECT email, display_name, role, disabled FROM users WHERE id = $1",
            sess["user_id"],
        )
        if u is None or u["disabled"]:
            return None
        await conn.execute(
            "UPDATE sessions SET last_seen_at = now() WHERE id = $1",
            sess["session_id"],
        )
    return {**dict(sess), **dict(u)}


# ── FastAPI dependency ──────────────────────────────────────────────


_PUBLIC_PREFIXES = (
    "/auth/", "/health", "/healthz", "/openapi", "/docs", "/redoc",
)


def is_public_path(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in _PUBLIC_PREFIXES)


async def require_user(
    request: Request,
    jos_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> CurrentUser:
    """FastAPI dependency. Throws 401 on missing/invalid session.
    On success returns CurrentUser (and importantly sets
    request.state.tenant_id so the per-connection set_config in
    db.acquire() picks the right tenant)."""
    if not jos_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    row = await get_session(jos_session)
    if row is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    cu = CurrentUser(
        id=row["user_id"],
        tenant_id=row["tenant_id"],
        email=row["email"] or "",
        display_name=row["display_name"] or "",
        role=row["role"] or "operator",
        session_id=row["session_id"],
    )
    request.state.current_user = cu
    request.state.tenant_id = cu.tenant_id
    return cu


async def maybe_user(
    jos_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> Optional[CurrentUser]:
    """Like require_user but returns None instead of 401. Used by
    endpoints that are public but want to know the user if there."""
    if not jos_session:
        return None
    row = await get_session(jos_session)
    if row is None:
        return None
    return CurrentUser(
        id=row["user_id"], tenant_id=row["tenant_id"],
        email=row["email"] or "", display_name=row["display_name"] or "",
        role=row["role"] or "operator",
        session_id=row["session_id"],
    )


# ── signup + login ──────────────────────────────────────────────────


async def signup_user(
    *, email: str, password: str, display_name: str = "",
    user_agent: str = "", ip: str = "", invite_code: str = "",
) -> tuple[CurrentUser, str]:
    """Create a new user + their own tenant (or claim the default tenant
    if it's still unowned) + a fresh session. Returns (user, token).
    The first signup ever claims the legacy default tenant so existing
    data stays accessible.

    Signups are default-CLOSED past the bootstrap user: a public
    /auth/signup that mints a fresh tenant per stranger was the front
    door to the whole database. After the default tenant is claimed,
    new signups require SIGNUP_INVITE_CODE to be configured and the
    request to carry the matching invite_code."""
    email = normalize_email(email)
    if not valid_email(email):
        raise HTTPException(status_code=400, detail="invalid email")
    err = validate_password(password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    display_name = (display_name or "").strip()[:120]

    async with acquire(_DEFAULT_TENANT_UUID()) as conn:
        # Email uniqueness check (case-insensitive lookup; column is
        # canonicalised on write).
        existing = await conn.fetchval(
            "SELECT 1 FROM users WHERE lower(email) = $1", email,
        )
        if existing:
            raise HTTPException(status_code=400, detail="email already registered")

        # First signup ever? Claim the legacy default tenant.
        default_tid = _DEFAULT_TENANT_UUID()
        owner_for_default = await conn.fetchval(
            "SELECT owner_user_id FROM tenants WHERE id = $1", default_tid,
        )
        if owner_for_default is None:
            tenant_id = default_tid
            tenant_was_claimed = True
        else:
            # Past the bootstrap user, signup is invite-only (and closed
            # entirely when no invite code is configured). Never leak
            # whether a code is configured vs merely wrong.
            configured = (settings.signup_invite_code or "").strip()
            supplied = (invite_code or "").strip()
            if not configured or not secrets.compare_digest(
                supplied.encode(), configured.encode()
            ):
                raise HTTPException(
                    status_code=403,
                    detail="signups are closed (invite required)",
                )
            # Create a brand new tenant for this signup.
            slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0]).strip("-") or "user"
            slug = slug_base
            i = 1
            while await conn.fetchval(
                "SELECT 1 FROM tenants WHERE slug = $1", slug,
            ):
                i += 1
                slug = f"{slug_base}-{i}"
            # Generate the id app-side and re-bind app.current_tenant to
            # it BEFORE the inserts: with FORCE ROW LEVEL SECURITY the
            # tenants/users policies act as WITH CHECK on writes, so a
            # cross-tenant insert under the default-tenant context would
            # be rejected. Binding the new id makes signup work whether
            # or not the connecting role is subject to RLS.
            tenant_id = uuid4()
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                "INSERT INTO tenants (id, name, slug, config) "
                "VALUES ($1, $2, $3, '{}'::jsonb)",
                tenant_id, display_name or email.split("@")[0], slug,
            )
            tenant_was_claimed = False

        ph = hash_password(password)
        try:
            user_id = await conn.fetchval(
                """INSERT INTO users
                     (tenant_id, email, password_hash, display_name, role)
                   VALUES ($1, $2, $3, $4, 'owner') RETURNING id""",
                tenant_id, email, ph, display_name,
            )
        except asyncpg.UniqueViolationError:
            # The SELECT-based pre-check above only sees rows the current
            # tenant context exposes once RLS is enforced — the unique
            # index on users.email is the real cross-tenant guard.
            raise HTTPException(
                status_code=400, detail="email already registered"
            ) from None
        # Mark the tenant as owned by the new user.
        await conn.execute(
            "UPDATE tenants SET owner_user_id = $2 WHERE id = $1",
            tenant_id, user_id,
        )
        if tenant_was_claimed:
            # Give the legacy tenant a slug so future lookups work.
            slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0]).strip("-") or "owner"
            await conn.execute(
                "UPDATE tenants SET slug = coalesce(slug, $2) WHERE id = $1",
                tenant_id, slug_base,
            )

    token, session_id = await create_session(
        user_id, tenant_id, user_agent=user_agent, ip=ip,
    )
    cu = CurrentUser(
        id=user_id, tenant_id=tenant_id, email=email,
        display_name=display_name, role="owner", session_id=session_id,
    )
    return cu, token


async def login_user(
    *, email: str, password: str, user_agent: str = "", ip: str = "",
) -> tuple[CurrentUser, str]:
    """Verify credentials and mint a fresh session. Throttles on per-IP
    and per-account failure counts."""
    email = normalize_email(email)
    if await _ip_too_many_fails(ip):
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts from this IP — wait "
                   f"{LOGIN_RATE_WINDOW_MIN} minutes.",
        )

    async with acquire(_DEFAULT_TENANT_UUID()) as conn:
        row = await conn.fetchrow(
            """SELECT id, tenant_id, email, display_name, role,
                      password_hash, disabled, failed_login_count,
                      lockout_until
                 FROM users WHERE lower(email) = $1""",
            email,
        )

    # Constant-time-ish: hash a dummy when no row exists so the response
    # timing doesn't leak existence.
    if row is None:
        verify_password(password, "$2b$12$" + "x" * 53)
        await _record_login_attempt(ip, email, False)
        raise HTTPException(status_code=401, detail="invalid email or password")

    if row["disabled"]:
        raise HTTPException(status_code=403, detail="account disabled")
    if row["lockout_until"] and row["lockout_until"] > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=429,
            detail="account temporarily locked due to failed attempts",
        )

    ok = verify_password(password, row["password_hash"] or "")
    if not ok:
        # Bump fail count; lock if threshold exceeded. Bind the user's
        # own tenant so the UPDATE survives RLS enforcement.
        async with acquire(row["tenant_id"]) as conn:
            new_count = row["failed_login_count"] + 1
            lockout = (
                datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
                if new_count >= LOCKOUT_FAILS else None
            )
            await conn.execute(
                "UPDATE users SET failed_login_count = $2, "
                "lockout_until = $3 WHERE id = $1",
                row["id"], new_count, lockout,
            )
        await _record_login_attempt(ip, email, False)
        raise HTTPException(status_code=401, detail="invalid email or password")

    # Success — reset counters, mint session. (User's own tenant, so
    # the UPDATE survives RLS enforcement.)
    async with acquire(row["tenant_id"]) as conn:
        await conn.execute(
            "UPDATE users SET failed_login_count = 0, lockout_until = NULL, "
            "last_login_at = now() WHERE id = $1",
            row["id"],
        )
    await _record_login_attempt(ip, email, True)
    token, session_id = await create_session(
        row["id"], row["tenant_id"], user_agent=user_agent, ip=ip,
    )
    cu = CurrentUser(
        id=row["id"], tenant_id=row["tenant_id"], email=row["email"] or "",
        display_name=row["display_name"] or "", role=row["role"] or "operator",
        session_id=session_id,
    )
    return cu, token


# ── cookie helpers ──────────────────────────────────────────────────


def _cookie_secure() -> bool:
    """Secure flag only in prod (HTTPS). Set JOS_SECURE_COOKIE=1 to
    force it on (e.g. behind a TLS-terminating proxy)."""
    return (os.environ.get("JOS_SECURE_COOKIE", "") or "").strip() in ("1", "true", "yes")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME, httponly=True,
        secure=_cookie_secure(), samesite="lax", path="/",
    )


def set_csrf_cookie(response: Response) -> str:
    """A double-submit CSRF token — non-httponly so the SPA can read
    it and echo it back in a header. Compared server-side against the
    cookie value on state-changing requests."""
    token = secrets.token_urlsafe(24)
    response.set_cookie(
        key=CSRF_COOKIE_NAME, value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=False,  # the SPA must read this
        secure=_cookie_secure(), samesite="lax", path="/",
    )
    return token


__all__ = [
    "CurrentUser", "COOKIE_NAME", "CSRF_COOKIE_NAME", "SESSION_DAYS",
    "hash_password", "verify_password", "validate_password",
    "normalize_email", "valid_email",
    "signup_user", "login_user", "create_session", "revoke_session",
    "get_session", "require_user", "maybe_user",
    "set_session_cookie", "clear_session_cookie", "set_csrf_cookie",
    "is_public_path",
]
