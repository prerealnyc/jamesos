-- Per-render video-engine override. Lets a single production pick its B-roll
-- engine (e.g. higgsfield) without changing the global VIDEO_PROVIDER default
-- (runway). Blank = use the configured/global provider. Read by run_production
-- and threaded into the B-roll renderer.
ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS video_engine text NOT NULL DEFAULT '';
