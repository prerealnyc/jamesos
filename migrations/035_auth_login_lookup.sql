-- Login needs to find a user by email BEFORE any tenant context exists,
-- but users carries a tenant RLS policy. Now that the app connects as a
-- non-BYPASSRLS role (james_app), a plain SELECT under the default-tenant
-- context would silently hide every non-default-tenant user — locking
-- them out. This SECURITY DEFINER function runs with its owner's rights
-- (the migration role, which owns the tables), so it sees across tenants
-- for exactly this one lookup and nothing else.
CREATE OR REPLACE FUNCTION auth_lookup_user_by_email(p_email text)
RETURNS TABLE (
  id uuid,
  tenant_id uuid,
  email text,
  display_name text,
  role text,
  password_hash text,
  disabled boolean,
  failed_login_count int,
  lockout_until timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT u.id, u.tenant_id, u.email, u.display_name, u.role,
         u.password_hash, u.disabled, u.failed_login_count, u.lockout_until
    FROM users u
   WHERE lower(u.email) = lower(p_email)
   LIMIT 1;
$$;

-- Not callable by arbitrary roles — only the app role(s).
REVOKE ALL ON FUNCTION auth_lookup_user_by_email(text) FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT EXECUTE ON FUNCTION auth_lookup_user_by_email(text) TO james_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_os') THEN
    GRANT EXECUTE ON FUNCTION auth_lookup_user_by_email(text) TO james_os;
  END IF;
END $$;
