-- 040_sessions_rls_policy.sql
--
-- public.sessions had ROW LEVEL SECURITY enabled but no policy, so the
-- application's restricted role (james_app) was denied — auth.create_session's
-- INSERT (and every authenticated request's session lookup) failed with
-- `InsufficientPrivilegeError: new row violates row-level security policy for
-- table "sessions"`, surfacing as HTTP 500 on login.
--
-- Sessions are looked up by their random token_hash BEFORE the tenant is known,
-- so a tenant-scoped policy (as used on users) can't be applied here. Scope to
-- james_app ONLY (anon / authenticated get no policy → session tokens are never
-- exposed via PostgREST). The session's security is its cryptographically random
-- token_hash, enforced in application code — not RLS.
DROP POLICY IF EXISTS sessions_app ON public.sessions;
CREATE POLICY sessions_app ON public.sessions
  FOR ALL TO james_app
  USING (true) WITH CHECK (true);
