-- Two cost controls for the competitor pipeline (finding: "competitor refresh is
-- seeded DAILY").
--
-- 1. The daily competitor_refresh job. brands.py seeded ('competitor_refresh', 24)
--    for every tenant that finished intake, so every brand paid for a full
--    Xpoz/Apify pull of every tracked competitor every day, on content that
--    barely changes day to day. The seed is now 168 (brands.INTAKE_JOBS); this
--    moves the rows that were seeded before the change. Only rows still on the
--    old default are touched — an operator who set a cadence by hand keeps it.
UPDATE scheduled_jobs
   SET cadence_hours = 168
 WHERE kind = 'competitor_refresh'
   AND cadence_hours = 24;

-- 2. Media that never stores. competitor_media re-scrapes a competitor through a
--    paid Apify actor for ANY post whose stored_media_url is still '', and when
--    the download then fails (stale CDN link, 403, over the size cap) or the
--    actor simply never returns that post again, the row stays '' and buys
--    another actor run on the next refresh, forever. Same shape as
--    template_read_attempts (060): count the failures, and after
--    competitor_media.MAX_MEDIA_FETCH_ATTEMPTS stop asking for it.
ALTER TABLE competitor_posts
  ADD COLUMN IF NOT EXISTS media_fetch_attempts int NOT NULL DEFAULT 0;
