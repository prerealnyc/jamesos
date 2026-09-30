-- What each hero photo is OF, so a headline about Dubai stops landing over a
-- photo of Thailand.
--
-- Nothing in the system knew. The hero picker ranked photos by layout fit
-- (edge energy under the copy) and then rotated by least-recently-used; both
-- are questions about SHAPE. Measured 2026-09-28: "Unforgettable Dubai Awaits"
-- rendered over Phang Nga limestone karst, and no component involved was wrong
-- — none of them had any idea what the picture showed.
--
-- Two columns rather than one. The caption is what a human reads when asking
-- why a photo was chosen, and it feeds lexical search over the tags/notes this
-- table already has. The embedding is what actually ranks. Keeping both means a
-- bad match can be explained instead of merely observed.
--
-- vector(1024) matches the dimension every other embedding in this schema uses
-- (events.embedding, migration 001), so the same embedder serves both.
ALTER TABLE media_assets
  ADD COLUMN IF NOT EXISTS subject_caption text NOT NULL DEFAULT '';

ALTER TABLE media_assets
  ADD COLUMN IF NOT EXISTS subject_embedding vector(1024);

-- When it was read, so a backfill can find the photos it has not seen and a
-- re-read can be forced by nulling it. Deliberately not a boolean: "described
-- at some point" and "described by the current prompt" are different questions.
ALTER TABLE media_assets
  ADD COLUMN IF NOT EXISTS subject_read_at timestamptz;

-- No ANN index on purpose: a brand's library is hundreds of rows, not millions,
-- and an exact scan over 300 vectors is faster than an index probe. Revisit
-- only if a single tenant's library passes five figures.
CREATE INDEX IF NOT EXISTS media_assets_subject_todo_idx
  ON media_assets (tenant_id, role) WHERE subject_read_at IS NULL;
