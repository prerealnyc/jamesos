-- Long Form Cutter — takes a 50-60 min podcast/long-form video and
-- cuts it into 30-45s standalone Reels at the engaging moments the
-- LLM picks.
--
-- Two tables: a source per upload, many candidates per source.

CREATE TABLE IF NOT EXISTS long_sources (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) DEFAULT
                 current_setting('app.current_tenant', true)::uuid,
  -- Where the long video lives on our storage. Original uploads get
  -- ffmpeg-extracted to a low-bitrate mp3 alongside so chunked Whisper
  -- can run without redoing the extract.
  title        text NOT NULL DEFAULT '',
  source_url   text NOT NULL,             -- Supabase Storage URL for the mp4
  audio_url    text NOT NULL DEFAULT '',  -- low-bitrate mp3 derived from it
  duration_s   double precision NOT NULL DEFAULT 0,
  -- Whisper output. words is [{w,t,end}, ...]; full_text is the joined
  -- text for the LLM's candidate-finder prompt.
  full_text    text NOT NULL DEFAULT '',
  words        jsonb NOT NULL DEFAULT '[]'::jsonb,
  status       text NOT NULL DEFAULT 'uploading' CHECK (
                 status IN ('uploading','transcribing','analyzing',
                            'ready','failed')),
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS long_sources_tenant_status_idx
  ON long_sources (tenant_id, status, created_at DESC);

ALTER TABLE long_sources ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY long_sources_tenant ON long_sources USING (
    tenant_id = current_setting('app.current_tenant', true)::uuid);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


CREATE TABLE IF NOT EXISTS reel_candidates (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) DEFAULT
                 current_setting('app.current_tenant', true)::uuid,
  source_id    uuid NOT NULL REFERENCES long_sources(id) ON DELETE CASCADE,
  start_s      double precision NOT NULL,
  end_s        double precision NOT NULL,
  hook_quote   text NOT NULL DEFAULT '',   -- the engaging opener
  summary      text NOT NULL DEFAULT '',   -- one-liner about what's in this reel
  score        integer NOT NULL DEFAULT 0, -- 1-10 LLM confidence
  -- When the user clicks Render, we kick a video_productions row in
  -- long_form_reel mode. production_id links back.
  production_id uuid,
  dismissed    boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reel_candidates_source_idx
  ON reel_candidates (source_id, dismissed, score DESC);

ALTER TABLE reel_candidates ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY reel_candidates_tenant ON reel_candidates USING (
    tenant_id = current_setting('app.current_tenant', true)::uuid);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- New production mode for the per-candidate reel render. Same pipeline
-- as engaging_avatar except the "avatar" track is a CUT of real
-- footage from a long_source instead of a HeyGen render.
ALTER TABLE video_productions
  DROP CONSTRAINT IF EXISTS video_productions_mode_check;

ALTER TABLE video_productions
  ADD CONSTRAINT video_productions_mode_check
    CHECK (mode IN (
      'mixed','avatar_only','timeline','story_audio',
      'avatar_story_mix','engaging_avatar','long_form_reel'
    ));
