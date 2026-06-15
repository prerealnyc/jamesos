-- Autonomous-developer pipeline: a queued code-change can be picked up by
-- the hourly agent, which opens a PR for human approval. New 'proposed'
-- status + the PR link. (Flow: queued → proposed → [human merges] → done.)
ALTER TABLE feedback_changes DROP CONSTRAINT IF EXISTS feedback_changes_status_check;
ALTER TABLE feedback_changes
  ADD CONSTRAINT feedback_changes_status_check
  CHECK (status IN ('applied','queued','proposed','dismissed','done'));
ALTER TABLE feedback_changes ADD COLUMN IF NOT EXISTS pr_url text;
