-- Perception layer for the reference library. When a video is uploaded we
-- "watch" it: ffmpeg pulls audio + sample frames, Whisper transcribes, and
-- a vision model describes the structure/pacing/captions. The result is a
-- structured style fingerprint the scene-plan generator reads to replicate
-- the format (never the verbatim content).

ALTER TABLE media_assets
  ADD COLUMN IF NOT EXISTS analysis jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS analyzed boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS analysis_status text NOT NULL DEFAULT 'none';
  -- analysis_status: none | pending | done | failed | unsupported
