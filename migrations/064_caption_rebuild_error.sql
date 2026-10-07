-- Remember why a reel's captionless cut could not be rebuilt.
--
-- The caption editor offers to rebuild a reel made before the cut was kept.
-- Whether that will WORK, though, is only knowable by doing it: the source
-- transcript may be broken, or the reconstruction may not line up with the
-- finished file. Without somewhere to record that, the editor promises a
-- rebuild, the owner waits a minute, and it fails — every time they open it.
--
-- Written by caption_backfill.rebuild on a refusal and cleared on success, so
-- a reel that becomes rebuildable later (its source re-transcribed, say) is
-- picked up again rather than being written off for good.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS caption_rebuild_error text NOT NULL DEFAULT '';
