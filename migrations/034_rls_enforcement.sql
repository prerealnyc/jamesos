-- RLS enforcement — close the production tenant-isolation hole.
--
-- Three problems fixed here (audit ranks 3 + 21):
--
--   1. agent_runs and voice_ingest_jobs were created WITHOUT any RLS at
--      all — the only two tenant tables that were skipped. They hold the
--      agent's raw prompts/tool calls and voice-ingest contents, some of
--      the most sensitive data in the system. Add the same tenant policy
--      every sibling table already has.
--
--   2. ENABLE ROW LEVEL SECURITY does NOT apply to the table owner.
--      In production the app connects as the role that owns the tables
--      (Supabase 'postgres'), so every policy was decorative: any tenant's
--      session could read every other tenant's rows. FORCE ROW LEVEL
--      SECURITY makes the policies bind the owner too. The app already
--      SET LOCALs app.current_tenant on every connection (db.acquire),
--      so enforcement is transparent for correct code paths.
--
--   3. The local dev role was explicitly granted BYPASSRLS in 001 —
--      revoke it so local behaviour matches production. (Note: the
--      docker-compose role is the cluster superuser, which always
--      bypasses RLS; real enforcement is only observable with a
--      non-superuser role, e.g. production 'postgres'.)
--
-- Deliberately NOT forced: tenants, users, sessions, login_attempts.
-- Those are the auth tables — signup/login/session lookup must run
-- BEFORE a tenant context exists (find the user by email, find the
-- session by token hash, create a new tenant). They are only touched
-- by auth.py, which scopes them explicitly, and rows there carry no
-- business data. Forcing them would brick login for every non-default
-- tenant.

-- (1) agent_runs + voice_ingest_jobs — same policy pattern as siblings.
ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS agent_runs_tenant ON agent_runs';
  EXECUTE 'CREATE POLICY agent_runs_tenant ON agent_runs USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;

ALTER TABLE voice_ingest_jobs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS voice_ingest_jobs_tenant ON voice_ingest_jobs';
  EXECUTE 'CREATE POLICY voice_ingest_jobs_tenant ON voice_ingest_jobs USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;

-- (2) FORCE RLS on every tenant-data table so the owning role is bound
-- by the policies too. Idempotent — FORCE on an already-forced table is
-- a no-op.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'events', 'plug_ins', 'adapters', 'queries', 'actions', 'outbox',
    'connections', 'video_jobs', 'media_assets', 'video_productions',
    'autopilot_runs', 'long_sources', 'reel_candidates',
    'style_templates', 'feedback_changes', 'agent_runs',
    'voice_ingest_jobs'
  ] LOOP
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- (3) Revoke the dev role's explicit RLS bypass (001 used to grant it).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_os') THEN
    ALTER ROLE james_os WITH NOBYPASSRLS;
  END IF;
END $$;
