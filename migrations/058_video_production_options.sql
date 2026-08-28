-- Per-production render options.
--
-- The one-click reel front door (POST /video/reel) needs to turn designed
-- cutaway cards on for a single production without inventing a style template
-- first. Until now the ONLY way to enable cards was through a template's
-- `cards` block, which is right for a saved, reusable format and wrong for
-- "upload this video and make me a reel".
--
-- A jsonb bag rather than a boolean column: this is the third or fourth
-- per-production toggle the reel path has grown (caption_style, music_mood,
-- broll_pacing, broll_style all became their own columns), and the next one
-- shouldn't need a migration. Template settings remain the default; anything
-- present here overrides them for this production only.
--
-- Known keys today:
--   cards        bool  — place designed cutaway cards from the transcript
--   card_styles  text[] — restrict which card styles may be used
--   music_track_url text — pin one exact bed, bypassing the mood pick

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS options jsonb NOT NULL DEFAULT '{}'::jsonb;
