-- Autopilot is research-first: it gathers live market research + trends
-- BEFORE inventing ideas, so generated content rides what's actually working
-- (virality-aligned), never blind voice-riffing. Each run records the intel
-- it used here.

ALTER TABLE autopilot_runs
  ADD COLUMN IF NOT EXISTS research jsonb NOT NULL DEFAULT '{}'::jsonb;
