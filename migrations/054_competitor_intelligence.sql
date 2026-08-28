-- 054: competitor intelligence — the spine that carries a niche all the way
-- to a plan.
--
--   niche → competitors → competitor_posts → competitor_post_analysis
--                                          → competitor_profiles → strategy
--
-- Design notes:
--   * EVERYTHING we pull is persisted. The old Xpoz path threw its results
--     away unless a human clicked "save"; social_saved_posts had 0 rows.
--   * competitor_posts.metrics_history APPENDS a snapshot per sync instead of
--     overwriting, so "this is climbing" is distinguishable from "this peaked
--     a month ago" — the trend events table froze metrics at first scrape and
--     could never tell them apart.
--   * competitors.follower_history does the same for audience size, which is
--     what makes engagement RATE (not raw likes) computable.
--   * stored_media_url is OUR durable copy. Instagram/TikTok media URLs expire;
--     without a copy the visual-eyes pass can only ever run once.

CREATE TABLE IF NOT EXISTS competitors (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  platform        text NOT NULL,
  handle          text NOT NULL,
  name            text NOT NULL DEFAULT '',
  niche           text NOT NULL DEFAULT '',      -- the query that surfaced them
  bio             text NOT NULL DEFAULT '',
  profile_url     text NOT NULL DEFAULT '',
  avatar_url      text NOT NULL DEFAULT '',
  followers       bigint NOT NULL DEFAULT 0,
  following       bigint NOT NULL DEFAULT 0,
  posts_total     bigint NOT NULL DEFAULT 0,
  verified        boolean NOT NULL DEFAULT false,
  status          text NOT NULL DEFAULT 'candidate',  -- candidate|tracked|rejected
  discovered_via  text NOT NULL DEFAULT 'manual',
                  -- manual|niche_search|watchlist_import|research
  rank_score      double precision NOT NULL DEFAULT 0,
  why             text NOT NULL DEFAULT '',      -- why they made the shortlist
  follower_history jsonb NOT NULL DEFAULT '[]',  -- [{at, followers}]
  last_synced_at  timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS competitors_uniq
  ON competitors (tenant_id, platform, lower(handle));
CREATE INDEX IF NOT EXISTS competitors_tenant_idx
  ON competitors (tenant_id, status, rank_score DESC);
ALTER TABLE competitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitors FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS competitors_tenant ON competitors;
CREATE POLICY competitors_tenant ON competitors USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);


CREATE TABLE IF NOT EXISTS competitor_posts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id)
                   DEFAULT current_setting('app.current_tenant', true)::uuid,
  competitor_id    uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  platform         text NOT NULL,
  post_id          text NOT NULL DEFAULT '',
  url              text NOT NULL DEFAULT '',
  caption          text NOT NULL DEFAULT '',
  media_type       text NOT NULL DEFAULT '',   -- image|video|carousel|text
  media_url        text NOT NULL DEFAULT '',   -- source (often expiring)
  stored_media_url text NOT NULL DEFAULT '',   -- our durable copy
  thumbnail_url    text NOT NULL DEFAULT '',
  duration         int NOT NULL DEFAULT 0,
  likes            bigint NOT NULL DEFAULT 0,
  comments         bigint NOT NULL DEFAULT 0,
  shares           bigint NOT NULL DEFAULT 0,
  views            bigint NOT NULL DEFAULT 0,
  engagement_rate  double precision NOT NULL DEFAULT 0,  -- (likes+comments)/followers
  posted_at        timestamptz,
  metrics_history  jsonb NOT NULL DEFAULT '[]', -- [{at, likes, comments, views}]
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_synced_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS competitor_posts_uniq
  ON competitor_posts (tenant_id, platform, post_id) WHERE post_id <> '';
CREATE INDEX IF NOT EXISTS competitor_posts_competitor_idx
  ON competitor_posts (tenant_id, competitor_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS competitor_posts_eng_idx
  ON competitor_posts (tenant_id, engagement_rate DESC);
ALTER TABLE competitor_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_posts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS competitor_posts_tenant ON competitor_posts;
CREATE POLICY competitor_posts_tenant ON competitor_posts USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);


-- One analysis row per post — the visual-eyes output. design_eye grades
-- stills; perception fingerprints reels. status is reported honestly
-- ('no_key' / 'failed'), never a faked score.
CREATE TABLE IF NOT EXISTS competitor_post_analysis (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id)
                       DEFAULT current_setting('app.current_tenant', true)::uuid,
  post_id              uuid NOT NULL REFERENCES competitor_posts(id) ON DELETE CASCADE,
  kind                 text NOT NULL DEFAULT '',   -- image|video
  status               text NOT NULL DEFAULT '',   -- ok|no_key|failed|skipped
  eye_score            double precision,
  axes                 jsonb NOT NULL DEFAULT '{}',
  design_dna           jsonb NOT NULL DEFAULT '{}',
  fingerprint          jsonb NOT NULL DEFAULT '{}', -- perception output (video)
  why_it_works         text NOT NULL DEFAULT '',
  transferable_pattern text NOT NULL DEFAULT '',
  format               text NOT NULL DEFAULT '',
  hook                 text NOT NULL DEFAULT '',
  hook_pattern         text NOT NULL DEFAULT '',
  topic                text NOT NULL DEFAULT '',
  cta                  text NOT NULL DEFAULT '',
  rubric_version       text NOT NULL DEFAULT '',
  model                text NOT NULL DEFAULT '',
  error                text NOT NULL DEFAULT '',
  analyzed_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS competitor_post_analysis_uniq
  ON competitor_post_analysis (post_id);
CREATE INDEX IF NOT EXISTS competitor_post_analysis_tenant_idx
  ON competitor_post_analysis (tenant_id, analyzed_at DESC);
ALTER TABLE competitor_post_analysis ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_post_analysis FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS competitor_post_analysis_tenant ON competitor_post_analysis;
CREATE POLICY competitor_post_analysis_tenant ON competitor_post_analysis USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);


-- The rollup: one live row per competitor, recomputed from posts + analyses.
CREATE TABLE IF NOT EXISTS competitor_profiles (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id)
                      DEFAULT current_setting('app.current_tenant', true)::uuid,
  competitor_id       uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  posts_analyzed      int NOT NULL DEFAULT 0,
  cadence_per_week    double precision NOT NULL DEFAULT 0,
  format_mix          jsonb NOT NULL DEFAULT '{}',  -- {reel: .6, carousel: .2}
  topic_clusters      jsonb NOT NULL DEFAULT '[]',  -- [{topic, share, avg_eng}]
  hook_patterns       jsonb NOT NULL DEFAULT '[]',  -- [{pattern, n, avg_eng}]
  posting_windows     jsonb NOT NULL DEFAULT '[]',  -- [{dow, hour, n}]
  avg_engagement_rate double precision NOT NULL DEFAULT 0,
  follower_growth     jsonb NOT NULL DEFAULT '{}',  -- {per_week, pct, window_days}
  design_signature    jsonb NOT NULL DEFAULT '{}',
  top_posts           jsonb NOT NULL DEFAULT '[]',  -- [{url, why, engagement_rate}]
  growth_strategy     text NOT NULL DEFAULT '',     -- the synthesised read
  evidence            jsonb NOT NULL DEFAULT '[]',
  computed_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS competitor_profiles_uniq
  ON competitor_profiles (competitor_id);
CREATE INDEX IF NOT EXISTS competitor_profiles_tenant_idx
  ON competitor_profiles (tenant_id, computed_at DESC);
ALTER TABLE competitor_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS competitor_profiles_tenant ON competitor_profiles;
CREATE POLICY competitor_profiles_tenant ON competitor_profiles USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
