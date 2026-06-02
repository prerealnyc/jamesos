-- SaaS auth layer — adds password hashing fields to users, creates
-- a revocable sessions table, and a per-IP login_attempts ledger for
-- rate limiting + per-account lockout.
--
-- Tenants gain a slug + owner_user_id so each new signup creates a
-- standalone workspace named after the user's email.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS password_hash text,
  ADD COLUMN IF NOT EXISTS display_name text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS disabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS last_login_at timestamptz,
  ADD COLUMN IF NOT EXISTS failed_login_count int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS lockout_until timestamptz;

CREATE TABLE IF NOT EXISTS sessions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  token_hash    text NOT NULL UNIQUE,
  user_agent    text NOT NULL DEFAULT '',
  ip            text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sessions_active_idx ON sessions (token_hash)
  WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS login_attempts (
  id          bigserial PRIMARY KEY,
  ip          text NOT NULL,
  email       text NOT NULL DEFAULT '',
  succeeded   boolean NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS login_attempts_ip_time_idx
  ON login_attempts (ip, created_at DESC);

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS slug text,
  ADD COLUMN IF NOT EXISTS owner_user_id uuid REFERENCES users(id);

CREATE UNIQUE INDEX IF NOT EXISTS tenants_slug_uniq
  ON tenants (slug) WHERE slug IS NOT NULL;
