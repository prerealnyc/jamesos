-- The nightly niche harvest: provenance for catalogue rows that arrived
-- automatically rather than through a curator's hand.
--
-- ADDITIVE ONLY. The source_kind CHECK is deliberately left alone: harvested
-- rows are source_kind 'niche', which 067 already permits and which adopts into
-- a brand's governed NICHE_SHARE lane unchanged. Provenance is told apart by
-- harvest_run_id <> '' (plus uploaded_by LIKE 'harvest:%'), not by a new kind.
--
-- source_key is the harvester's identity for the IMAGE it read ('onc:' + the
-- sha1 of the image URL). It is checked BEFORE the vision read, so an image the
-- catalogue already holds is never paid for twice. Unique where set; '' for
-- every row that did not come through the harvest.
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS source_key     text  NOT NULL DEFAULT '';
-- The BM2 run that wrote the row. What a revoke undoes, and what the curation
-- screen filters a night's intake on.
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS harvest_run_id text  NOT NULL DEFAULT '';
-- What the harvester knew about the post (author, media, reach, its own triage
-- verdict, the sha256 of the bytes). Stored for a human reading the row; never
-- trusted for any gate.
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS harvest_meta   jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS house_layouts_source_key_uq
    ON house_layouts (source_key) WHERE source_key <> '';

CREATE INDEX IF NOT EXISTS house_layouts_harvest_run_ix
    ON house_layouts (harvest_run_id) WHERE harvest_run_id <> '';
