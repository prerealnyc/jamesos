-- B-roll pacing preset per production: '' (resolve at render time),
-- 'punchy' (1.5-2.5s flashes), 'illustrative' (4-5s holds — the
-- talking-head-overlay standard, the new default), 'reflective'
-- (6-8s slow holds). Chosen in the UI next to the B-roll engine.
ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS broll_pacing text NOT NULL DEFAULT '';
