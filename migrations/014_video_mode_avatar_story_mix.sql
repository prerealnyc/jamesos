-- Widen video_productions.mode CHECK to allow 'avatar_story_mix'.
--
-- avatar_story_mix = ONE HeyGen render, reused twice:
--   * its audio drives the whole timeline (transcribed by Whisper for
--     word-pinned captions and beat segmentation, same as story_audio)
--   * the avatar video is sliced silently per "James on camera" beat;
--     B-roll beats get an AI still
-- The LLM classifies each beat as avatar vs broll. Final visual track
-- alternates between James on camera and AI stills with one continuous
-- voice. No second HeyGen spend.

ALTER TABLE video_productions
  DROP CONSTRAINT IF EXISTS video_productions_mode_check;

ALTER TABLE video_productions
  ADD CONSTRAINT video_productions_mode_check
    CHECK (mode IN ('mixed','avatar_only','timeline','story_audio','avatar_story_mix'));
