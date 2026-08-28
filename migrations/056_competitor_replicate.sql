-- 056: the brand's own verdict on a competitor post.
--
-- The point of holding a competitor's posts is that the brand can look at
-- them and say "make me one of those". This is where that answer lives:
-- the brand picks, and the picks become replication targets.
--
-- Kept ON the post rather than in a join table because it is a single
-- per-post verdict, not a history, and every read that shows a post wants
-- to show whether it was picked.
ALTER TABLE competitor_posts
  ADD COLUMN IF NOT EXISTS replicate_status text NOT NULL DEFAULT '',
      -- '' = untouched | 'saved' = brand wants this | 'skipped' = seen, no
      -- | 'queued' = a draft has been generated from it
  ADD COLUMN IF NOT EXISTS replicate_note   text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS replicate_at     timestamptz;

-- The gallery's default view is "what has the brand saved", so index it.
CREATE INDEX IF NOT EXISTS competitor_posts_replicate_idx
  ON competitor_posts (tenant_id, replicate_status, replicate_at DESC)
  WHERE replicate_status <> '';
