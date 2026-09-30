-- The house catalogue: layouts any brand may draw from, curated by hand.
--
-- WHY A NEW TABLE RATHER THAN A scope COLUMN ON design_templates.
-- Migration 057 gave style_templates exactly that treatment — a platform tenant
-- plus a split read policy — and porting it here was the obvious move. It does
-- not survive contact with this table. EVERY query in design_templates.py scopes
-- itself by RLS alone; not one names its tenant. Widening the read policy to
-- "own OR house" silently re-scopes all of them at once: count() starts counting
-- the house toward MIN_LIBRARY, learn_from_competitors starts reading house rows
-- as unlearned work, and the picker starts returning rows the brand cannot write
-- to. The isolation is doing real work, and a new table leaves it alone.
--
-- Two further things design_templates cannot give a curated pool:
--   * DELETE. 060 does not merely omit it, it REVOKEs it, because "don't throw
--     away any layouts" is a promise to a BRAND about its own library. A curated
--     catalogue is the opposite promise: discarding the bad ones is the product.
--   * Curation columns. A reviewer, a verdict, a quality note and a family key
--     are meaningless on a brand's private row.
--
-- WHAT LIVES HERE. A layout is a SPEC — roles, boxes as fractions, decorations —
-- with no colour, no wording, no logo. It is a shape. The brand's own voice,
-- palette, photo and logo are filled in at render, which is why one shape can
-- serve any brand without carrying anything of the brand it was learned from.
CREATE TABLE IF NOT EXISTS house_layouts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  kind          text NOT NULL DEFAULT '',        -- graphic_card | photo_forward | …
  spec          jsonb NOT NULL,
  fingerprint   text NOT NULL DEFAULT '',

  -- Near-duplicate grouping. NOT unique: exact hashing has nothing left to catch
  -- (measured 2026-09-29: 439 rows collapse to 436 fingerprints, and only 3
  -- layouts were ever learned twice), while the corpus is full of layouts that
  -- differ by a few pixels and are the same idea. This groups those families so
  -- a curator sees "these nine are one layout" instead of nine rows.
  family_key    text NOT NULL DEFAULT '',

  -- Where the shape came from. The image is kept so a human can JUDGE the
  -- layout — a spec is unreadable, a picture is not, and curation is the whole
  -- point of this table.
  source_kind   text NOT NULL DEFAULT 'competitor'
                CHECK (source_kind IN ('competitor', 'niche', 'curated')),
  source_url    text NOT NULL DEFAULT '',
  source_image_uri text NOT NULL DEFAULT '',
  -- The tenant this was promoted FROM, for audit only. Deliberately not a FK:
  -- the catalogue must outlive the brand that contributed it, which is the
  -- entire reason this table exists.
  promoted_from uuid,

  -- Curation. 'candidate' is what a promotion lands as; nothing reaches a brand
  -- until a human has said yes, because "we add a lot of good-looking images so
  -- the output is really good" is a claim about judgement, not volume.
  status        text NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('candidate', 'approved', 'rejected')),
  review_note   text NOT NULL DEFAULT '',
  reviewed_by   text NOT NULL DEFAULT '',
  reviewed_at   timestamptz,

  -- How the catalogue is earning its keep, across every brand that took it.
  adopted_count int NOT NULL DEFAULT 0,
  approvals     int NOT NULL DEFAULT 0,
  rejections    int NOT NULL DEFAULT 0,
  qa_passes     int NOT NULL DEFAULT 0,
  qa_fails      int NOT NULL DEFAULT 0,

  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- One row per distinct shape. A second brand promoting the same competitor post
-- updates the first row rather than adding a twin.
CREATE UNIQUE INDEX IF NOT EXISTS house_layouts_fingerprint_uniq
  ON house_layouts (fingerprint) WHERE fingerprint <> '';

-- What a brand asks for: approved layouts, best-earning first.
CREATE INDEX IF NOT EXISTS house_layouts_serve_idx
  ON house_layouts (status, (approvals - rejections) DESC, adopted_count DESC)
  WHERE status = 'approved';

-- What a curator asks for: the queue, oldest first.
CREATE INDEX IF NOT EXISTS house_layouts_queue_idx
  ON house_layouts (status, created_at) WHERE status = 'candidate';

CREATE INDEX IF NOT EXISTS house_layouts_family_idx
  ON house_layouts (family_key) WHERE family_key <> '';

-- NO ROW LEVEL SECURITY, and that is the point of the table: it belongs to the
-- platform, not to a tenant. There is no tenant_id to scope by. Access is gated
-- above the database — reads through the engine's service key, writes only
-- through admin-gated routes.
--
-- DELETE IS GRANTED HERE, unlike design_templates. The two tables make opposite
-- promises on purpose: a brand's library never forgets, a curated catalogue must.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON house_layouts TO james_app;
  END IF;
END $$;

-- The link back: which house layout a brand's copy came from. Lives on
-- design_templates because that is the row that gets used, but adds NOTHING to
-- its RLS or its policy — the column is inert to every existing query.
ALTER TABLE design_templates
  ADD COLUMN IF NOT EXISTS house_layout_id uuid;

-- A brand adopts each house layout at most once. This is the idempotency key
-- that lets the top-up run on every pick without duplicating.
CREATE UNIQUE INDEX IF NOT EXISTS design_templates_house_uniq
  ON design_templates (tenant_id, house_layout_id) WHERE house_layout_id IS NOT NULL;
