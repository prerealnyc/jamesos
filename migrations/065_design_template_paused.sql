-- An owner switching a layout off is not the same event as design QA giving up
-- on it, and the schema could not tell them apart: `status` allowed only
-- 'active' and 'retired', and 'retired' already means "QA failed this three
-- times" (see design_templates.mark_qa). Reusing it for a human's choice would
-- make the QA record unreadable — you could no longer ask how many layouts the
-- renderer actually rejected — and an owner re-enabling one would look like a
-- QA reprieve.
--
-- 'paused' is therefore the owner's lane. Nothing else changes: `pick()` and
-- `count()` both ask for status = 'active', so a paused layout is simply not
-- drawn, and — as with everything in this table — nothing is deleted.
ALTER TABLE design_templates
  DROP CONSTRAINT IF EXISTS design_templates_status_check;

ALTER TABLE design_templates
  ADD CONSTRAINT design_templates_status_check
  CHECK (status IN ('active', 'retired', 'paused'));
