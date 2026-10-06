-- Keep the reel's HEADLINE so it can be moved too.
--
-- Migration 062 kept the captionless cut and the caption flashes, which made
-- moving the CAPTIONS on a finished reel a local ffmpeg pass. The big boxed
-- headline over the first few seconds — "WHY WEEKENDS FEEL ENDLESS AT
-- TURTLEBACK" — is a separate element with its own hard-coded position, and it
-- was thrown away exactly like the captions were.
--
--   caption_hook  {"text": "<the headline as rendered>", "hold": <seconds>}
--
-- `hold` is how long it stays up before it clears; the caption track is held
-- back until then so the two never share the screen. A re-draw needs it for
-- both reasons: to know when to stop drawing the headline, and to know which
-- caption flashes the assembler suppressed underneath it.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS caption_hook jsonb NOT NULL DEFAULT '{}'::jsonb;
