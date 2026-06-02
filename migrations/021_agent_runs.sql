-- Agent runs — every "Do" prompt becomes one row, every tool the
-- agent calls becomes one entry in tool_calls. Lets the user see
-- exactly what got initiated, what completed, what failed.
--
-- Why a dedicated table (not `actions`):
--   * `actions` is the approval queue — items waiting for human yes/no.
--   * `agent_runs` is the agent's own execution log — durable record
--     of which tools fired, with what args, and what came back.
--   Combined, the user can ask "what did the agent do today" without
--   conflating that with "what's pending approval."

CREATE TABLE IF NOT EXISTS agent_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  prompt        text NOT NULL,
  status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','cancelled')),
  error         text,
  -- Each entry: {name, args, result, started_at, completed_at,
  --              ok, error}. JSONB so adding fields later doesn't
  --              need another migration.
  tool_calls    jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- Plain-text summary the agent writes at the end of the loop —
  -- "I added @prerealcapital, kicked a refresh, and 2 new posts
  --  landed in the queue." So the user gets a one-glance read.
  summary       text NOT NULL DEFAULT '',
  -- Optional pointer to the final response if it ended with a Q&A
  -- style answer instead of (or alongside) tool calls.
  answer        text NOT NULL DEFAULT '',
  citations     jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx
  ON agent_runs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS agent_runs_tenant_created_idx
  ON agent_runs (tenant_id, created_at DESC);
