-- Phase 2: style replication. A production can now be driven BY a stored
-- style template (the "trending video styles" library). We persist the
-- template id (provenance) plus the render parameters the template resolved
-- to — so the worker reads the template's music mood / logo / structure
-- instead of the hardcoded defaults, and the UI can show what a video was
-- replicated from.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS template_id    uuid REFERENCES style_templates(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS music_mood     text  NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS logo_position  text  NOT NULL DEFAULT '',   -- '' = no logo overlay
  ADD COLUMN IF NOT EXISTS structure      jsonb NOT NULL DEFAULT '[]'::jsonb;  -- mixed-mode scene structure override
