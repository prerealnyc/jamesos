-- Per-production review status, set from the Output Library Approve /
-- Reject buttons. The reason on rejection is what feeds the learning
-- loop (see src/james_os/video_feedback.py).
--
-- Nullable by design — an unreviewed production has review_status=NULL,
-- distinguished from approved/rejected so the Library can show the
-- right chip (no chip on unreviewed).

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS review_status text
    CHECK (review_status IS NULL OR review_status IN ('approved','rejected','approved_with_notes'));
ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS review_reason text;
ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;

CREATE INDEX IF NOT EXISTS video_productions_review_idx
  ON video_productions (review_status, reviewed_at DESC)
  WHERE review_status IS NOT NULL;
