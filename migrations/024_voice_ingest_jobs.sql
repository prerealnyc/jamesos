-- Voice Studio ingest jobs — durable progress for "drop a Drive folder /
-- links → transcribe + extract → ingest as voice_corpus → feeds generation."
--
-- A dedicated row per run so the UI can show live progress AND so a server
-- restart never leaves a fake 'running' status sitting around (it's reaped
-- to 'interrupted' on startup). This is the frustration-ledger lesson:
-- background work must be observable and honest, never performative.

CREATE TABLE IF NOT EXISTS voice_ingest_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  source        text NOT NULL DEFAULT '',          -- folder url / "N links"
  category      text NOT NULL DEFAULT 'voice_corpus',
  status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','interrupted')),
  stage         text NOT NULL DEFAULT 'starting',
  total         int  NOT NULL DEFAULT 0,            -- files to process
  processed     int  NOT NULL DEFAULT 0,            -- files done
  chunks        int  NOT NULL DEFAULT 0,            -- voice_corpus chunks created
  files         jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{name, chunks, type}]
  errors        jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{source, error}]
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE INDEX IF NOT EXISTS voice_ingest_jobs_tenant_created_idx
  ON voice_ingest_jobs (tenant_id, created_at DESC);
