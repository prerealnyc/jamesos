-- Widen video_productions.mode CHECK to allow 'timeline'.
--
-- 'timeline' is the freeform stitch produced by the /editor page: every
-- block already carries a real clip URL, so the planner and per-scene
-- renderer are no-ops and we go straight to Creatomate. Same end shape
-- (final_url, queued_action_id) as the other modes — just a different
-- way of getting there.
--
-- Postgres named CHECKs can't be re-declared, so drop-and-recreate.

ALTER TABLE video_productions
  DROP CONSTRAINT IF EXISTS video_productions_mode_check;

-- NOT VALID below: this list is historical — a later migration (030)
-- re-adds the final, validated list. Re-running this file against a DB
-- whose rows already use newer modes must not fail (migrate.py re-runs
-- every file).
ALTER TABLE video_productions
  ADD CONSTRAINT video_productions_mode_check
    CHECK (mode IN ('mixed','avatar_only','timeline')) NOT VALID;
