-- 061: a layout can be learned from the NICHE, not only from a competitor.
--
-- Media monitoring surfaces high-engagement posts in a brand's niche whose
-- account nobody tracks — and which Instagram and LinkedIn never name a poster
-- for. They are kept on their own shelf (competitors.status = 'reference', see
-- niche_reference.py) and read into layouts like any other still. Calling that
-- provenance 'competitor' would credit an account we never identified, so the
-- layout records source_kind = 'niche'.
--
-- The old CHECK list did not know the word. Replaced rather than added to,
-- because a CHECK constraint is one expression; NOT VALID keeps the rewrite off
-- the existing rows (every one of them already satisfies the new list, and a
-- full validation scan on a growing table buys nothing).

ALTER TABLE design_templates
  DROP CONSTRAINT IF EXISTS design_templates_source_kind_check;

ALTER TABLE design_templates
  ADD CONSTRAINT design_templates_source_kind_check
  CHECK (source_kind IN ('competitor', 'reference', 'niche', 'own', 'sample'))
  NOT VALID;
