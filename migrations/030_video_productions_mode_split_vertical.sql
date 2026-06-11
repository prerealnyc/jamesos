-- Add 'split_vertical' to the video_productions.mode whitelist (speaker LEFT /
-- B-roll+text RIGHT). Mirrors split_horizontal. Keeps the constraint in sync
-- with start_production's Python-level guard.
ALTER TABLE video_productions DROP CONSTRAINT IF EXISTS video_productions_mode_check;
ALTER TABLE video_productions ADD CONSTRAINT video_productions_mode_check
  CHECK (mode = ANY (ARRAY[
    'mixed', 'avatar_only', 'timeline', 'story_audio', 'avatar_story_mix',
    'engaging_avatar', 'long_form_reel', 'hero_clone',
    'split_horizontal', 'split_screen', 'split_vertical'
  ]));
