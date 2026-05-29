-- Per-production caption preset.
--
-- Blank/NULL = "let the AI pick" (the worker calls pick_caption_style()
-- before assembly). A specific value bypasses that LLM call. Examples:
-- 'tiktok_yellow', 'clean_white', 'bold_pop', 'subtle_minimal',
-- 'branded_red' — see src/james_os/caption_styles.py for the full list.

ALTER TABLE video_productions
  ADD COLUMN IF NOT EXISTS caption_style text NOT NULL DEFAULT '';
