-- Brand kit: the uploaded brand logo lives as a media_assets row so the
-- watermark/end-card layers have a durable URL.
ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS media_assets_role_check;
ALTER TABLE media_assets ADD CONSTRAINT media_assets_role_check
  CHECK (role = ANY (ARRAY[
    'style_reference', 'james_clip', 'broll', 'post_image',
    'hero_photo', 'hero_video', 'music', 'sfx', 'brand_logo'
  ]));
