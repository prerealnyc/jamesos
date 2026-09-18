"""Whisper invents speech when there is none, and reels were cut from it.

Fed a silent drone shot or a music-only promo, Whisper does not return nothing.
It returns something, and the something comes from what it was trained on. On
2026-09-18 three of one brand's videos transcribed as Khmer, one as Chinese
subscribe-spam ("请不吝点赞 订阅 转发"), and two as "Thanks for watching!" — and
the cutter chose moments from those words, captioned reels with them, and put
them in front of the owner as their own content.

Every number here is measured from those videos.
"""

import pytest

from james_os.long_form import MIN_WORDS_PER_SECOND, hallucinated

pytestmark = pytest.mark.nodb


# what the owner actually said, in the videos that really had speech
REAL = [
    ("Other than our stunning Joe Boyden custom homes, residents can expect to find many "
     "amenities, including our 18-hole PGA Championship golf course, along with our clubhouse "
     "featuring our restaurant and bar overlooking the lake", 37.0),      # 1.4 w/s, measured
    ("And we got Turtleback Mountain, a golf and resort community that we're trying to make "
     "sure people know about. With the golf tournament coming in September, the New Mexico "
     "Open, this place is going to be on the map", 40.0),                 # 1.6 w/s, measured
]

# what Whisper wrote over silence, verbatim from production
INVENTED = [
    ("ភ្រាំត្រាំត្អ្ម្ន្ហុដាន្ញូគ្ដ្ង់ដោ្រាំផុំត្រំត្រោទី្ផ្លា្រាំ។", 99.11),
    ("អូនិត្រាន្ល្តែរាន្ម្រោះ។ ស្រុងស្ក្នៅ។", 136.0),
    ("前头跨越千の湖付き 感谢您的明暗和祝愿 请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", 60.01),
    ("Thanks for watching!", 27.86),
    ("Thanks.", 34.7),
    ("you", 45.0),
]


@pytest.mark.parametrize("text, duration", REAL)
def test_real_speech_is_cut_as_before(text, duration):
    assert hallucinated(text, duration) == ""


@pytest.mark.parametrize("text, duration", INVENTED)
def test_invented_speech_is_refused_with_a_reason(text, duration):
    why = hallucinated(text, duration)
    assert why, f"this would have been cut into a reel: {text[:40]!r}"
    assert "speech" in why or "silence" in why


def test_the_threshold_sits_in_the_gap_between_them():
    """Real speech measured 1.4-2.1 words a second; every invented transcript
    measured 0.02-0.73. A threshold anywhere between is arbitrary; one OUTSIDE
    that gap would start throwing away real videos."""
    assert 0.73 < MIN_WORDS_PER_SECOND < 1.36


def test_a_very_short_clip_is_not_judged_on_its_rate():
    """Nine seconds of 'Are you serious?!' is a legitimate reel and a terrible
    sample for a words-per-second test."""
    assert hallucinated("Are you serious?!", 4.0) == ""


def test_an_empty_transcript_still_says_so():
    assert hallucinated("", 60.0)
    assert hallucinated("   ", 60.0)
