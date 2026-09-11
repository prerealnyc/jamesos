-- 060 — the design template library: every still-image layout learned from a
-- reference post, kept forever.
--
-- design_cloner reads a reference post's pixels and emits a structured,
-- brand-agnostic layout spec (where each text block sits, its size, weight and
-- colour role, the background treatment, bars and badges). Until now that spec
-- was saved only on the single sample it produced (actions.payload.clone_spec)
-- and never reused: every good layout the system learned was used once and
-- effectively thrown away, and autopilot kept drawing from the same nine
-- hand-built formats.
--
-- The owner's rules: autopilot uses the reference engine; the competitor images
-- we already collect are read into templates; "don't throw away any layouts,
-- keep all data in-house"; and every template works at every platform's size.
--
-- So:
--   * nothing is ever deleted — `status` retires a layout, it never removes it;
--   * `source_image_uri` is OUR durable copy of the reference image (the same
--     media storage competitor_posts.stored_media_url points at), never only the
--     source URL, which for Instagram/TikTok expires within days;
--   * the spec itself is size-independent (every box is a fraction of the
--     canvas), so one row serves every platform shape.
--
-- Still layouts get their own table rather than style_templates, which is the
-- VIDEO style library (format_type, duration, transcript) with a different
-- `template` shape.

CREATE TABLE IF NOT EXISTS design_templates (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id)
                     DEFAULT current_setting('app.current_tenant', true)::uuid,
  spec               jsonb NOT NULL,                     -- design_cloner output
  kind               text NOT NULL DEFAULT 'photo_forward', -- photo_forward | graphic_card
  -- where the layout was learned from
  source_kind        text NOT NULL DEFAULT 'competitor'
                     CHECK (source_kind IN ('competitor', 'reference', 'own', 'sample')),
  source_post_id     uuid,                               -- competitor_posts.id
  source_url         text NOT NULL DEFAULT '',           -- the post's page
  source_image_uri   text NOT NULL DEFAULT '',           -- OUR durable copy
  source_handle      text NOT NULL DEFAULT '',
  source_platform    text NOT NULL DEFAULT '',
  source_engagement  double precision NOT NULL DEFAULT 0,
  rubric_version     text NOT NULL DEFAULT '',
  -- a hash of the STRUCTURE, so the same layout seen twice is one row
  fingerprint        text NOT NULL DEFAULT '',
  -- retired = hidden from autopilot, never deleted
  status             text NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'retired')),
  -- how it has done, so good layouts are drawn more and broken ones fade
  qa_passes          int NOT NULL DEFAULT 0,
  qa_fails           int NOT NULL DEFAULT 0,
  times_used         int NOT NULL DEFAULT 0,
  approvals          int NOT NULL DEFAULT 0,
  rejections         int NOT NULL DEFAULT 0,
  last_used_at       timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- one layout per source post, and one row per distinct structure
CREATE UNIQUE INDEX IF NOT EXISTS design_templates_source_uniq
  ON design_templates (tenant_id, source_post_id) WHERE source_post_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS design_templates_fingerprint_uniq
  ON design_templates (tenant_id, fingerprint) WHERE fingerprint <> '';
-- autopilot's pick: active layouts, least recently used first
CREATE INDEX IF NOT EXISTS design_templates_pick_idx
  ON design_templates (tenant_id, status, last_used_at NULLS FIRST);

ALTER TABLE design_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE design_templates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS design_templates_tenant ON design_templates;
CREATE POLICY design_templates_tenant ON design_templates USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- When a competitor post was read for a layout — set whatever the read produced:
-- a new template, a duplicate of one we already hold, or no layout in it. Without
-- it a post that yielded no NEW row was picked up again on every run, and each
-- read is a paid vision-model call.
ALTER TABLE competitor_posts
  ADD COLUMN IF NOT EXISTS template_read_at timestamptz;
-- A read that ERRORED is an outage, not a verdict: it is retried, and the post
-- is only marked read after design_templates.MAX_READ_ATTEMPTS failures.
ALTER TABLE competitor_posts
  ADD COLUMN IF NOT EXISTS template_read_attempts int NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS competitor_posts_template_read_idx
  ON competitor_posts (tenant_id, template_read_at) WHERE template_read_at IS NULL;

-- The app's runtime role (Supabase's limited `james_app`, not postgres) gets
-- read and write, RLS still applying. Guarded like 053, so this is a no-op on a
-- local install where the role does not exist.
--
-- Deliberately NO DELETE. "Don't throw away any layouts" is enforced by the
-- database, not by a convention a future change could quietly break: the app
-- can retire a layout (status='retired') but it cannot remove one.
--
-- Leaving DELETE out of the GRANT is not enough. Supabase's default privileges
-- on `public` give james_app arwd (incl. DELETE) on every new table — found by
-- checking after the first apply — so it is REVOKED explicitly.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT SELECT, INSERT, UPDATE ON design_templates TO james_app;
    REVOKE DELETE, TRUNCATE ON design_templates FROM james_app;
  END IF;
END $$;
