-- Widen video_productions.mode CHECK to allow 'story_audio'.
--
-- story_audio = HeyGen voice → Whisper word-timestamps → LLM beat
-- segmentation → gpt-image-1 still per beat → Creatomate stitches
-- audio + stills (Ken Burns) + word-pinned captions. Same end shape
-- (final_url, queued_action_id) as the other modes — just a different
-- pipeline.

ALTER TABLE video_productions
  DROP CONSTRAINT IF EXISTS video_productions_mode_check;

ALTER TABLE video_productions
  ADD CONSTRAINT video_productions_mode_check
    CHECK (mode IN ('mixed','avatar_only','timeline','story_audio'));
