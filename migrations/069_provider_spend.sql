-- 069 — the provider spend ledger: one row per paid call to an outside model.
--
-- Until now nothing in this repo recorded what a brand COST. llm.py threw
-- result.usage away, thirteen direct AsyncOpenAI clients never went through
-- llm.py at all, and the one natural ceiling — Anthropic running out of credit —
-- was bypassed by FallbackLLM silently switching to OpenAI. The first anyone
-- heard of a runaway loop was the card statement.
--
-- This table is the number. Every chat completion, every drawn image, every
-- provider crossing writes a row here (spend.py), and the daily cap check
-- (spend.over_cap) reads it back before a scheduler job or an autopilot batch
-- is allowed to start.
--
-- est_usd is an ESTIMATE from a price table in spend.py, never a bill: the
-- providers' invoices are the truth and the prices drift. It is stored so the
-- cap can be enforced without a network call, and meta->>'estimate' says so on
-- every row.
--
-- Same RLS pattern as every other tenant table (060, 054): tenant_id defaults
-- from the app.current_tenant GUC so a write from any acquire() lands on the
-- right brand, FORCE so the table owner is bound too.

CREATE TABLE IF NOT EXISTS provider_spend (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  provider      text NOT NULL,                     -- openai | anthropic | fallback | …
  model         text NOT NULL DEFAULT '',
  units         numeric NOT NULL DEFAULT 0,        -- tokens, images, crossings …
  unit_kind     text NOT NULL DEFAULT '',          -- tokens | image | crossing
  est_usd       numeric(12, 6) NOT NULL DEFAULT 0, -- estimate, see spend.py
  agent_or_job  text NOT NULL DEFAULT '',          -- the job/agent that spent it
  meta          jsonb NOT NULL DEFAULT '{}',       -- tokens_in/out, size, estimate flag …
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- The two reads: today's total for the cap, and the last N days for the report.
CREATE INDEX IF NOT EXISTS provider_spend_tenant_time_idx
  ON provider_spend (tenant_id, created_at DESC);

ALTER TABLE provider_spend ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_spend FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_spend_tenant ON provider_spend;
CREATE POLICY provider_spend_tenant ON provider_spend USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- The app role writes and reads the ledger; it never deletes from it. A ledger
-- that can be emptied is not a ledger. Guarded like 053/060 so this is a no-op
-- on a local install where the role does not exist.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT SELECT, INSERT ON provider_spend TO james_app;
    REVOKE UPDATE, DELETE, TRUNCATE ON provider_spend FROM james_app;
  END IF;
END $$;
