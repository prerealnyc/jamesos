-- B-roll library: pipeline-rendered clips are filed as media_assets rows with
-- source_type='generated' (vs user 'upload' / imported 'url'). Widen the CHECK
-- so registration doesn't violate it. (Same constraint-drift trap as the
-- video_productions mode check in 029/030 — caught before it 500'd this time.)
ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS media_assets_source_type_check;
ALTER TABLE media_assets ADD CONSTRAINT media_assets_source_type_check
  CHECK (source_type = ANY (ARRAY['upload', 'url', 'generated']));
