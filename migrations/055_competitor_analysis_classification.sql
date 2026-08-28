-- 055: the third analysis source.
--
-- competitor_post_analysis already carries design_dna (design_eye, stills) and
-- fingerprint (perception, video). `classification` is the text read — format,
-- hook pattern, topic, CTA, and who the post is talking to — which applies to
-- EVERY post regardless of media and is far cheaper than a vision call. Three
-- sources, three columns, so it stays obvious where a claim came from.
ALTER TABLE competitor_post_analysis
  ADD COLUMN IF NOT EXISTS classification jsonb NOT NULL DEFAULT '{}';

-- Ranking competitors needs their measured numbers, not just discovery-time
-- signals. These are recomputed from real synced posts.
ALTER TABLE competitors
  ADD COLUMN IF NOT EXISTS median_engagement_rate double precision NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS avg_engagement_rate    double precision NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS posts_per_week         double precision NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS measured_posts         int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS ranked_at              timestamptz;
