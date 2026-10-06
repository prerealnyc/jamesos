-- Keep what a caption-only re-render needs.
--
-- Captions are burned into the finished reel by Creatomate, so moving them
-- means drawing them again. Until now that was only possible by re-running the
-- WHOLE pipeline — re-cutting, re-transcribing, regenerating B-roll, and paying
-- for another assembly — because the two things a re-caption actually needs
-- were in-memory locals that the worker threw away:
--
--   clean_cut_url  the captionless cut the captions were drawn ON. It was
--                  already uploaded to media storage, but under a uuid4 name
--                  with no row pointing at it, so it was orphaned in the bucket
--                  and unrecoverable.
--   caption_cues   the word-pinned flashes ({start,end,text,raw_text}) built
--                  from the transcript of that cut. Re-deriving them from the
--                  source transcript does not work, because clip-tightening
--                  remaps the timeline and never saved its kept intervals.
--
-- With both on the row, moving/restyling/removing captions on an existing reel
-- is a local ffmpeg pass over the stored cut: seconds, no provider spend, and
-- the footage is the same pixels the owner already approved.
--
-- Rows rendered before this migration have neither, and cannot be re-captioned
-- cheaply — they need a full re-render, which may differ from what was approved.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS clean_cut_url text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS caption_cues jsonb NOT NULL DEFAULT '[]'::jsonb;
