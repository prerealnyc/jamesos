-- Widen media_assets.role to include 'post_image' for AI-generated
-- hero images attached to LinkedIn/Twitter/IG posts. Same storage path
-- and lifecycle as uploaded media — just a different role tag so the
-- /images page can filter to them without mixing with james_clip or
-- style_reference.

ALTER TABLE media_assets
  DROP CONSTRAINT IF EXISTS media_assets_role_check;

ALTER TABLE media_assets
  ADD CONSTRAINT media_assets_role_check
    CHECK (role IN ('style_reference','james_clip','broll','post_image'));
