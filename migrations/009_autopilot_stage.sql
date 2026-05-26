-- Live stage visibility for autopilot batches. The worker writes its current
-- phase here ('researching', 'ideating', 'drafting 2/3', 'saving') so the UI
-- can show what's happening in real time. Also lets us tell at a glance
-- which runs were interrupted mid-stage.

ALTER TABLE autopilot_runs
  ADD COLUMN IF NOT EXISTS stage text NOT NULL DEFAULT '';
