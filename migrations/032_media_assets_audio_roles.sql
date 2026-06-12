-- Audio library: background music (tagged by mood) + SFX (whoosh/hit/riser)
-- as media_assets roles, so renders can pull audio from the library instead
-- of only the 4 static settings URLs.
ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS media_assets_role_check;
ALTER TABLE media_assets ADD CONSTRAINT media_assets_role_check
  CHECK (role = ANY (ARRAY[
    'style_reference', 'james_clip', 'broll', 'post_image',
    'hero_photo', 'hero_video', 'music', 'sfx'
  ]));
