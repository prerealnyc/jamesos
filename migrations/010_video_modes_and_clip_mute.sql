-- Two production-quality knobs:
--
-- 1) video_productions.mode — 'mixed' (current: per-scene B-roll+avatar+
--    james_clip then Creatomate assembly) or 'avatar_only' (one HeyGen
--    render of the full script — same voice end-to-end, no silence gaps).
--
-- 2) media_assets.mute_audio — flagged on a james_clip when its native
--    voice shouldn't play (e.g., narrator track carries the audio). Lets
--    the user reuse clips as visual-only without re-uploading silent versions.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'mixed'
    CHECK (mode IN ('mixed','avatar_only'));

ALTER TABLE media_assets
  ADD COLUMN IF NOT EXISTS mute_audio boolean NOT NULL DEFAULT false;
