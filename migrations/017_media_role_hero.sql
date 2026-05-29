-- Widen media_assets.role CHECK to include 'hero_photo' and 'hero_video'.
--
-- The hero roles back the /hero Hero Library page: hero_photo are
-- visual references describing the brand's hero (face / build / dress
-- / signature look) that GPT-4o vision summarises for the cinematic
-- image-prompt LLM; hero_video are clips of the hero on camera
-- (reserved for a future avatar-swap path). See src/james_os/media.py
-- ROLES tuple and src/james_os/hero_context.py for how they're used.

ALTER TABLE media_assets
  DROP CONSTRAINT IF EXISTS media_assets_role_check;

ALTER TABLE media_assets
  ADD CONSTRAINT media_assets_role_check
    CHECK (role IN (
      'style_reference', 'james_clip', 'broll', 'post_image',
      'hero_photo', 'hero_video'
    ));
