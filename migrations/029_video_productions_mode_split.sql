-- Sync the video_productions.mode CHECK constraint with the Python-level
-- validation in start_production (video_pipeline.py). The constraint had
-- drifted behind the code: it never gained 'hero_clone', and then
-- 'split_horizontal' / 'split_screen'. Inserting a production with any of
-- those modes raised CheckViolationError → HTTP 500 on "produce video"
-- (hit first by replicating a split_horizontal style template).
ALTER TABLE video_productions DROP CONSTRAINT IF EXISTS video_productions_mode_check;
-- NOT VALID below: historical list — 030 re-adds the final, validated
-- one. Re-running must not fail on rows using newer modes.
ALTER TABLE video_productions ADD CONSTRAINT video_productions_mode_check
  CHECK (mode = ANY (ARRAY[
    'mixed', 'avatar_only', 'timeline', 'story_audio', 'avatar_story_mix',
    'engaging_avatar', 'long_form_reel', 'hero_clone',
    'split_horizontal', 'split_screen'
  ])) NOT VALID;
