-- 039_login_attempts_rls_policy.sql
--
-- 034_rls_enforcement enabled ROW LEVEL SECURITY on login_attempts but never
-- created a policy for it. With RLS on and no policy, the application's
-- restricted role (james_app) is denied ALL access — so every login threw
-- `InsufficientPrivilegeError: new row violates row-level security policy for
-- table "login_attempts"` on the audit insert in auth._record_login_attempt,
-- surfacing to the user as HTTP 500 (even with the correct password).
--
-- login_attempts is a GLOBAL throttle/audit table (ip, email, succeeded,
-- created_at) — it has no tenant_id, so it is NOT tenant-scoped. Grant the app
-- role full access via a policy scoped to james_app ONLY; anon / authenticated
-- get no policy and therefore remain denied, so login emails/IPs are never
-- exposed through PostgREST. (auth.py additionally treats the audit write and
-- throttle read as best-effort, so a future policy gap can never 500 a login.)
DROP POLICY IF EXISTS login_attempts_app ON public.login_attempts;
CREATE POLICY login_attempts_app ON public.login_attempts
  FOR ALL TO james_app
  USING (true) WITH CHECK (true);
