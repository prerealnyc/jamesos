-- Name what each catalogue layout IS, so the pool is browsable and a curator can
-- see which KINDS of post are missing rather than scrolling 450 thumbnails.
--
-- The engine records two kinds ('photo_forward', 'graphic_card') and the whole
-- corpus collapses into them, which is why the library reads as an
-- undifferentiated pile. layout_type is the finer name, derived from the spec by
-- layout_types.classify() -- roles present x background treatment x photo
-- regions. Stored rather than computed on read because the curation screen
-- filters and groups on it.
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS layout_type text NOT NULL DEFAULT '';
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS label       text NOT NULL DEFAULT '';

-- Uploaded by a curator rather than read off a competitor. Already permitted by
-- the source_kind CHECK; this records who put it there and what they called it.
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS uploaded_by text NOT NULL DEFAULT '';
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS title       text NOT NULL DEFAULT '';

-- The curator's own niche tags: which brands/verticals this shape suits. Free
-- text, because the useful vocabulary is not knowable in advance -- 'golf',
-- 'jewellery', 'listing'. Empty means "any".
ALTER TABLE house_layouts ADD COLUMN IF NOT EXISTS niches text[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS house_layouts_type_idx
    ON house_layouts (layout_type, status) WHERE layout_type <> '';
CREATE INDEX IF NOT EXISTS house_layouts_niche_idx
    ON house_layouts USING gin (niches);
