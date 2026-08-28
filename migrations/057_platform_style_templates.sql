-- Platform (house) style templates — the starter reel library every brand
-- inherits on day one, plus the provenance to tell where a template came from.
--
-- The problem this closes: style_templates is tenant-scoped and RLS-FORCEd, so
-- a template built on one brand is invisible to every other brand. A brand-new
-- signup's library was EMPTY until somebody uploaded reference reels into that
-- tenant — there was no starter set, no seeding, no way to share a proven reel
-- format across brands.
--
-- The model: house templates are owned by a dedicated PLATFORM TENANT. They are
-- readable by every brand and writable by none — curating the house library
-- means connecting AS the platform tenant, which is gated in Python
-- (templates.py::_platform_conn). Replication is unchanged: a brand replicates a
-- house template through the ordinary path, and the render fills in THAT
-- brand's voice, hero, logo and colours. Nothing about a house template is
-- brand-specific — it is a FORMAT, not content.
--
-- Read own + house; write own only. Split into two policies because the single
-- FOR ALL policy could not express that asymmetry.

-- (1) The platform tenant. Owns the house library; never a real brand, never
-- logged into. Fixed id so app code and RLS agree on one constant.
INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-0000000000f0', 'JAMES OS Platform')
ON CONFLICT (id) DO NOTHING;

-- (2) Provenance. scope = who owns it; origin = how it was made;
-- source_template_id = what it was forked/published from.
ALTER TABLE style_templates
  ADD COLUMN IF NOT EXISTS scope  text NOT NULL DEFAULT 'brand',
  ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'inspector',
  ADD COLUMN IF NOT EXISTS source_template_id uuid
    REFERENCES style_templates(id) ON DELETE SET NULL;

ALTER TABLE style_templates DROP CONSTRAINT IF EXISTS style_templates_scope_check;
ALTER TABLE style_templates ADD CONSTRAINT style_templates_scope_check
  CHECK (scope IN ('brand', 'platform'));

-- 'inspector' — reverse-engineered from a reference video (the original path);
-- 'authored'  — hand-built in the template builder;
-- 'forked'    — copied from another template (house → brand, or a saved edit).
ALTER TABLE style_templates DROP CONSTRAINT IF EXISTS style_templates_origin_check;
ALTER TABLE style_templates ADD CONSTRAINT style_templates_origin_check
  CHECK (origin IN ('inspector', 'authored', 'forked'));

-- Keep any row that somehow landed in the platform tenant consistent BEFORE
-- the invariant below is added (no-op on a fresh install).
UPDATE style_templates
   SET scope = 'platform'
 WHERE tenant_id = '00000000-0000-0000-0000-0000000000f0'::uuid
   AND scope <> 'platform';

-- The invariant, in the schema rather than in a comment: a house template is
-- exactly a template owned by the platform tenant. Literal (not a function) so
-- the CHECK stays immutable.
ALTER TABLE style_templates DROP CONSTRAINT IF EXISTS style_templates_scope_tenant_check;
ALTER TABLE style_templates ADD CONSTRAINT style_templates_scope_tenant_check
  CHECK ((scope = 'platform')
         = (tenant_id = '00000000-0000-0000-0000-0000000000f0'::uuid));

-- (3) RLS — the asymmetry. READ sees your own rows plus the house library;
-- WRITE stays strictly your own tenant, so a brand session can never edit,
-- rename or delete a house template. Publishing to the house library is an
-- explicit connection as the platform tenant, gated in Python.
--
-- Note both policies are PERMISSIVE and therefore OR'd for SELECT; the write
-- policy's USING adds nothing the read policy doesn't already allow. UPDATE and
-- DELETE are governed by the write policy alone, which is the point.
--
-- Defense in depth: a brand session inserting scope='platform' fails the
-- scope/tenant CHECK above AND the write policy's WITH CHECK.
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS style_templates_tenant ON style_templates';
  EXECUTE 'DROP POLICY IF EXISTS style_templates_read   ON style_templates';
  EXECUTE 'DROP POLICY IF EXISTS style_templates_write  ON style_templates';

  EXECUTE 'CREATE POLICY style_templates_read ON style_templates FOR SELECT USING ('
        || '  tenant_id = current_setting(''app.current_tenant'', true)::uuid'
        || '  OR scope = ''platform'')';

  EXECUTE 'CREATE POLICY style_templates_write ON style_templates FOR ALL '
        || 'USING      (tenant_id = current_setting(''app.current_tenant'', true)::uuid) '
        || 'WITH CHECK (tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;

-- FORCE ROW LEVEL SECURITY was applied in 034 and still stands; re-assert so
-- this migration is safe to run against a database that predates it.
ALTER TABLE style_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE style_templates FORCE  ROW LEVEL SECURITY;

-- (4) The house library is read by every brand on every library load, so give
-- it its own small index rather than riding the (tenant_id, created_at) one.
CREATE INDEX IF NOT EXISTS style_templates_platform_idx
  ON style_templates (trending_score DESC, created_at DESC)
  WHERE scope = 'platform';

-- (5) A forked/published copy must NOT inherit the source's reference video —
-- style_templates_ref_uniq is a GLOBAL unique index on reference_media_id, so
-- carrying it over would collide across tenants. Copies carry
-- source_template_id instead; this index makes that lineage cheap to walk.
CREATE INDEX IF NOT EXISTS style_templates_source_idx
  ON style_templates (source_template_id)
  WHERE source_template_id IS NOT NULL;
