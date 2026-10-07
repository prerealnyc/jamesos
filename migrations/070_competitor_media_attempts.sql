-- Two cost controls for the competitor pipeline (finding: "competitor refresh is
-- seeded DAILY").
--
-- 1. The daily competitor_refresh job. brands.py seeded ('competitor_refresh', 24)
--    for every tenant that finished intake, so every brand paid for a full
--    Xpoz/Apify pull of every tracked competitor every day, on content that
--    barely changes day to day. The seed is now 168 (brands.INTAKE_JOBS); this
--    moves the rows that were seeded before the change.
--
--    ONCE. migrate.py re-applies every file on every run (and the deploy runs
--    migrate.py), so a bare UPDATE would also revert an operator who later set a
--    tenant's competitor_refresh back to 24 on purpose, on every deploy. The
--    move is tied to the column below: it runs only while that column does not
--    exist yet, i.e. the first time this file is applied, and never again.
--    Rows on any other cadence are never touched.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = 'competitor_posts'::regclass
       AND attname = 'media_fetch_attempts'
       AND NOT attisdropped
  ) THEN
    UPDATE scheduled_jobs
       SET cadence_hours = 168
     WHERE kind = 'competitor_refresh'
       AND cadence_hours = 24;
  END IF;
END $$;

-- 2. Media that never stores. competitor_media re-scrapes a competitor through a
--    paid Apify actor for ANY post whose stored_media_url is still '', and when
--    the download then fails (stale CDN link, 403, over the size cap) or the
--    actor simply never returns that post again, the row stays '' and buys
--    another actor run on the next refresh, forever. Same shape as
--    template_read_attempts (060): count the failures, and after
--    competitor_media.MAX_MEDIA_FETCH_ATTEMPTS stop asking for it.
--    (Must stay AFTER the DO block above: its absence is what makes 1 run once.)
ALTER TABLE competitor_posts
  ADD COLUMN IF NOT EXISTS media_fetch_attempts int NOT NULL DEFAULT 0;

-- 3. The competitor whose profile never answers. A run that saw no post at all
--    (login wall, private, deleted) is an outage and does not count against the
--    posts — so a permanently private profile bought one actor run per refresh,
--    forever. Consecutive empty runs are counted here; after
--    competitor_media.MAX_MEDIA_OUTAGE_RUNS the media run for that competitor is
--    skipped until a run sees a post, a sync brings new posts, or force.
ALTER TABLE competitors
  ADD COLUMN IF NOT EXISTS media_outage_runs int NOT NULL DEFAULT 0;
