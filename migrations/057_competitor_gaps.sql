-- 057: the content gap, kept.
--
-- content_gap() computed on demand and threw the answer away, which cost an
-- LLM pass on every read and — worse — made the gap unanswerable over time:
-- you could not see whether a gap you acted on actually closed. It is meant
-- to be a standing reference for the brand and for the strategiser, so it has
-- to persist like the profiles do.
--
-- One CURRENT row per tenant plus history: `computed_at` orders them, and the
-- newest row is the answer. Kept append-only rather than upserted so the
-- series is a record of how the brand's shape changed against its peers.
CREATE TABLE IF NOT EXISTS competitor_gaps (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  -- every number, computed from stored rows (peer mix, our mix, shortfall)
  facts         jsonb NOT NULL DEFAULT '{}',
  -- the narrative, written from those numbers and nothing else
  read          jsonb NOT NULL DEFAULT '{}',
  -- non-empty when either side was too thin to support a conclusion
  insufficient  jsonb NOT NULL DEFAULT '[]',
  -- denormalised for cheap reads and for charting the trend
  peer_posts_analysed int NOT NULL DEFAULT 0,
  our_posts_90d       int NOT NULL DEFAULT 0,
  gap_count           int NOT NULL DEFAULT 0,
  computed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS competitor_gaps_current_idx
  ON competitor_gaps (tenant_id, computed_at DESC);

ALTER TABLE competitor_gaps ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_gaps FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS competitor_gaps_tenant ON competitor_gaps;
CREATE POLICY competitor_gaps_tenant ON competitor_gaps USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
