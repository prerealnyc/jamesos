-- 041_video_productions_broll_style.sql
--
-- Per-reel B-roll art direction, selectable like caption_style:
--   ''/'literal'  → literal real-world scenes (the default)
--   'cinematic'   → dramatic conceptual film-metaphor b-roll (the Agent-Opus
--                   look): moody grade, data-as-prop, one cohesive world.
-- Read by _run_long_form_reel → build_engaging_avatar_assets → pick_insert_points
-- → _insert_pick_system, which swaps the art-direction block of the b-roll
-- director prompt. Empty string is treated as 'literal'.
ALTER TABLE public.video_productions
  ADD COLUMN IF NOT EXISTS broll_style text NOT NULL DEFAULT '';
