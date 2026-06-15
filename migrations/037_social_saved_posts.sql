-- Saved trending posts from Social Listening (Xpoz). A curation shelf:
-- save interesting posts from search, then pick which ones to turn into
-- content. Idempotent per (tenant, source_platform, post_id).
CREATE TABLE IF NOT EXISTS social_saved_posts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  source_platform text NOT NULL DEFAULT '',          -- x | instagram | tiktok | reddit
  post_id         text NOT NULL DEFAULT '',          -- the platform post id
  author          text NOT NULL DEFAULT '',
  text            text NOT NULL DEFAULT '',
  likes           bigint NOT NULL DEFAULT 0,
  comments        bigint NOT NULL DEFAULT 0,
  url             text NOT NULL DEFAULT '',
  niche           text NOT NULL DEFAULT '',          -- the search term it came from
  status          text NOT NULL DEFAULT 'saved'
                    CHECK (status IN ('saved', 'drafted')),
  action_id       uuid,                              -- the queue draft made from it
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS social_saved_posts_uniq
  ON social_saved_posts (tenant_id, source_platform, post_id);
CREATE INDEX IF NOT EXISTS social_saved_posts_tenant_created_idx
  ON social_saved_posts (tenant_id, created_at DESC);

ALTER TABLE social_saved_posts ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS social_saved_posts_tenant ON social_saved_posts';
  EXECUTE 'CREATE POLICY social_saved_posts_tenant ON social_saved_posts USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
