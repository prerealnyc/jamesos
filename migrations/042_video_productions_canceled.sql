-- Add 'canceled' as a terminal status for video_productions so a user can
-- cancel an in-flight render and it reads cleanly (not a fake 'failed').
ALTER TABLE video_productions DROP CONSTRAINT IF EXISTS video_productions_status_check;
ALTER TABLE video_productions ADD CONSTRAINT video_productions_status_check
  CHECK (status = ANY (ARRAY[
    'queued', 'planning', 'rendering_clips', 'assembling',
    'succeeded', 'failed', 'canceled'
  ]));
