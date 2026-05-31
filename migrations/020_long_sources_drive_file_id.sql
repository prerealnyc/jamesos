-- Long-form sources from Drive: keep Drive as the canonical store
-- instead of copying 4 GB videos into Supabase Storage just to read
-- them again later.
--
-- When drive_file_id is set, the ingest and cut paths re-download
-- from Drive (fast for the service account, no Supabase size cap).
-- source_url stays nullable so non-Drive uploads still go through
-- the Storage upload path unchanged.

ALTER TABLE long_sources
  ADD COLUMN IF NOT EXISTS drive_file_id text;

CREATE INDEX IF NOT EXISTS long_sources_drive_file_id_idx
  ON long_sources (drive_file_id)
  WHERE drive_file_id IS NOT NULL;
