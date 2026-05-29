-- Per-production image style.
--
-- Blank/NULL = "story modes default to cinematic, everything else
-- defaults to photoreal" (the worker resolves at render time). A
-- specific value pins the style. Examples: 'cinematic', 'photoreal',
-- 'editorial', 'minimal', 'bw_photo'. See src/james_os/imagegen.py
-- POST_STYLES for the full list.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS image_style text NOT NULL DEFAULT '';
