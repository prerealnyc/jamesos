-- What each catalogue layout EARNED, per niche: owners' verdicts on posts drawn
-- from it, and measured engagement against the brand's baseline.
--
-- WHY. A verdict on a forked layout moved only the brand's own fork
-- (design_templates.approvals), so what one golf brand learned about a layout
-- taught no other golf brand anything, and the picker ranked the catalogue on
-- fit and freshness alone. Kept PER NICHE because "golf brands approve this"
-- says nothing about a law firm. `niche` is a niche_vocab label, or '' for a
-- brand whose niche maps to no label.
--
-- Counts, not events: the score (house_layouts.outcome_score) needs only sums,
-- and an upsert per verdict keeps this one row per (layout, niche).
CREATE TABLE IF NOT EXISTS house_layout_outcomes (
  house_layout_id uuid NOT NULL REFERENCES house_layouts(id) ON DELETE CASCADE,
  niche           text NOT NULL,
  approvals       int NOT NULL DEFAULT 0,
  rejections      int NOT NULL DEFAULT 0,
  measured        int NOT NULL DEFAULT 0,
  lift_sum        numeric NOT NULL DEFAULT 0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (house_layout_id, niche)
);

-- NO ROW LEVEL SECURITY, like house_layouts itself (067): this belongs to the
-- platform, not to a tenant. A row aggregates the verdicts of EVERY brand in a
-- niche, so there is no tenant_id to scope by, and no brand's identity is in
-- it — only counts. Access is gated above the database: written by the
-- engine's verdict hook and the tenant-bound /outcome route, read by the
-- picker.
--
-- No DELETE for the app: results are history. A discarded catalogue row takes
-- its results with it through the CASCADE, which runs as the table owner.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT SELECT, INSERT, UPDATE ON house_layout_outcomes TO james_app;
    -- Supabase's default privileges grant arwd on every new public table, so
    -- leaving DELETE out of the GRANT withholds nothing (found on 060). Revoked
    -- explicitly; the CASCADE still runs, as the referencing table's owner.
    REVOKE DELETE, TRUNCATE ON house_layout_outcomes FROM james_app;
  END IF;
END $$;
