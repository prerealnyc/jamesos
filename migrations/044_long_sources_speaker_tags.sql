-- Per-source speaker assignment for on-screen name-tags: a list of
-- [{face_x, handle, subtitle}] the user confirmed via the "who is this?" step.
-- Applies to every reel cut from this source.
ALTER TABLE long_sources
  ADD COLUMN IF NOT EXISTS speaker_tags jsonb NOT NULL DEFAULT '[]'::jsonb;
