-- Widen video_productions.mode CHECK to allow 'engaging_avatar'.
--
-- engaging_avatar = HeyGen avatar plays continuously (full video + full
-- audio), with 2-5 cinematic B-roll stills cutting in for 1.5-2.5s
-- each at the moments the LLM picks as visually amplifiable. Different
-- from avatar_story_mix which splits time between avatar BEATS and
-- broll BEATS — engaging_avatar keeps James on camera most of the
-- time and uses B-roll as PUNCTUATION rather than alternation.

ALTER TABLE video_productions
  DROP CONSTRAINT IF EXISTS video_productions_mode_check;

-- NOT VALID below: this list is historical — a later migration (030)
-- re-adds the final, validated list. Re-running this file against a DB
-- whose rows already use newer modes must not fail (migrate.py re-runs
-- every file).
ALTER TABLE video_productions
  ADD CONSTRAINT video_productions_mode_check
    CHECK (mode IN (
      'mixed','avatar_only','timeline','story_audio',
      'avatar_story_mix','engaging_avatar'
    )) NOT VALID;
