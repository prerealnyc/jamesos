"""A re-hosted file must say what it is.

/v1/media/rehost was built for the clip library and hardcoded `f"{safe}.mp4"`.
Correct while the only caller re-hosted OpusClip mp4s; wrong the moment anything
else used it. A brand's own post STILL now comes through here — the one durable
copy of the picture, made because Instagram and TikTok CDN links expire within
days — and it was being stored as a .mp4, announcing itself as a video to every
later reader, including the layout cloner that is supposed to read pixels out of
it.

The extension is taken from the best evidence available: the server's own
Content-Type, then the extension on the url, then the historical .mp4 assumption
so the clip library behaves exactly as it always did.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from james_os.api_v1 import _rehost_ext  # noqa: E402


def test_the_content_type_wins():
    assert _rehost_ext("image/jpeg", "https://cdn/x") == ".jpg"
    assert _rehost_ext("image/png", "https://cdn/x") == ".png"
    assert _rehost_ext("image/webp", "https://cdn/x") == ".webp"
    assert _rehost_ext("video/mp4", "https://cdn/x") == ".mp4"


def test_a_charset_suffix_does_not_defeat_it():
    assert _rehost_ext("image/png; charset=binary", "https://cdn/x") == ".png"
    assert _rehost_ext("  IMAGE/JPEG  ", "https://cdn/x") == ".jpg"


def test_the_url_answers_when_the_header_does_not():
    """Instagram CDN links carry the extension in the path and a query string
    after it, which is the shape that matters here."""
    assert _rehost_ext("", "https://scontent.cdninstagram.com/a/b.webp?ig_cache=1") == ".webp"
    assert _rehost_ext("application/octet-stream", "https://cdn/photo.JPG") == ".jpg"


def test_the_clip_library_is_unchanged():
    """An OpusClip signed url has no extension and no useful content type. It
    must still land as .mp4 — this endpoint's whole original job."""
    assert _rehost_ext("", "https://opusclip.example/signed?token=abc") == ".mp4"
    assert _rehost_ext("binary/octet-stream", "https://opusclip.example/dl") == ".mp4"


def test_a_path_that_is_not_an_extension_is_not_treated_as_one():
    assert _rehost_ext("", "https://cdn.example.com/a/b") == ".mp4"
    assert _rehost_ext("", "https://cdn/v1.2.3-build-longsuffix") == ".mp4"
