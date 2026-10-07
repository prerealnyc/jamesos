"""Rebuilding the captionless cut for a reel made before we kept one.

Every reel the owner already has predates the stash, so "move the captions" met
a wall on exactly the videos they wanted to fix. The cut is reconstructable for
reels cut from their own footage, because the source, the window and the
transcript were all persisted for other reasons — the only missing step,
clip-tightening, is a pure function of the same words.
"""

import json

import pytest

from james_os import caption_backfill as cb

pytestmark = pytest.mark.nodb

SOURCE = "https://x.supabase.co/storage/v1/object/public/media/t/src.mp4"


def _prod(**over) -> dict:
    p = {"mode": "long_form_reel", "status": "succeeded",
         "scenes": [{"source_url": SOURCE, "source_id": "s-1",
                     "start_s": 0.0, "end_s": 15.45}]}
    p.update(over)
    return p


# --- which reels can be rebuilt ---------------------------------------------

def test_a_reel_cut_from_the_brands_own_footage_can_be_rebuilt():
    ok, why = cb.can_rebuild(_prod())
    assert ok and why == ""


def test_a_mode_that_kept_no_source_is_refused_with_a_reason_in_plain_words():
    """18 engaging_avatar, 4 story_audio and the rest persist no source at all.
    The owner has to be told which of their videos this can never work on."""
    for mode in ("engaging_avatar", "story_audio", "avatar_story_mix",
                 "mixed", "timeline", "avatar_only", "split_horizontal"):
        ok, why = cb.can_rebuild(_prod(mode=mode))
        assert ok is False
        assert "own source videos" in why


def test_a_reel_that_never_finished_is_refused():
    ok, why = cb.can_rebuild(_prod(status="failed"))
    assert ok is False and "never finished" in why


def test_a_reel_whose_source_was_not_kept_is_refused():
    ok, why = cb.can_rebuild(_prod(scenes=[{"start_s": 0.0, "end_s": 10.0}]))
    assert ok is False and "source video" in why


def test_nonsense_in_and_out_points_are_refused_rather_than_downloaded():
    for scenes in ([{"source_url": SOURCE, "start_s": 9.0, "end_s": 2.0}],
                   [{"source_url": SOURCE, "start_s": 0.0, "end_s": 0.0}],
                   [{"source_url": SOURCE, "start_s": 0.0, "end_s": 99999.0}],
                   [{"source_url": SOURCE, "start_s": "x", "end_s": "y"}]):
        ok, why = cb.can_rebuild(_prod(scenes=scenes))
        assert ok is False, scenes
        assert "in and out points" in why


def test_scenes_stored_as_json_text_are_read_the_same_as_a_list():
    ok, _ = cb.can_rebuild(_prod(scenes=json.dumps(_prod()["scenes"])))
    assert ok is True


def test_a_production_with_no_scenes_at_all_does_not_crash():
    for scenes in (None, [], "", "not json", {}, [None], ["x"]):
        ok, why = cb.can_rebuild(_prod(scenes=scenes))
        assert ok is False and why


# --- the words that drive the rebuild ---------------------------------------

WORDS = [
    {"w": "Our", "t": 0.21, "e": 0.355, "sp": "A"},
    {"w": "leader", "t": 0.355, "e": 0.792, "sp": "A"},
    {"w": "Jackson", "t": 1.876, "e": 2.394, "sp": "A"},
    {"w": "outside", "t": 40.0, "e": 41.0, "sp": "A"},     # past the window
]


def test_the_stored_word_shape_is_read_correctly():
    """Stored as {w,t,e,sp} — NOT the TranscribedWord field names. Reading the
    wrong keys yields zero words and a silent no-op, which is how a first pass
    at this produced no captions at all."""
    out = cb.words_in_window(WORDS, 0.0, 15.45)
    assert [w.word for w in out] == ["Our", "leader", "Jackson"]
    assert out[0].start == pytest.approx(0.21)
    assert out[0].speaker == "A"


def test_words_are_shifted_so_the_cut_starts_at_zero():
    """Every caption time is measured from frame zero of the rebuilt cut, so a
    window that starts at 8.43s has to move to 0 or every caption is late."""
    out = cb.words_in_window(
        [{"w": "late", "t": 9.0, "e": 9.5}], 8.43, 20.0)
    assert out[0].start == pytest.approx(0.57)
    assert out[0].end == pytest.approx(1.07)


def test_words_outside_the_window_are_dropped():
    assert cb.words_in_window(WORDS, 0.0, 3.0) != []
    assert [w.word for w in cb.words_in_window(WORDS, 30.0, 45.0)] == ["outside"]
    assert cb.words_in_window(WORDS, 20.0, 30.0) == []


def test_words_are_returned_in_time_order():
    out = cb.words_in_window(
        [{"w": "b", "t": 2.0, "e": 2.5}, {"w": "a", "t": 1.0, "e": 1.5}], 0.0, 10.0)
    assert [w.word for w in out] == ["a", "b"]


def test_a_broken_transcript_yields_nothing_rather_than_junk_captions():
    """A garbage transcript must not become garbage captions burned onto video."""
    assert cb.words_in_window(None, 0, 10) == []
    assert cb.words_in_window("not json", 0, 10) == []
    assert cb.words_in_window([{"w": "x"}], 0, 10) == []          # no timings
    assert cb.words_in_window([{"w": "x", "t": 5, "e": 1}], 0, 10) == []  # ends before it starts
    assert cb.words_in_window(["nope", 7, None], 0, 10) == []


def test_a_transcript_stored_as_json_text_is_read_too():
    assert len(cb.words_in_window(json.dumps(WORDS), 0.0, 15.45)) == 3


# --- the safety rail --------------------------------------------------------

def test_a_rebuilt_cut_is_only_trusted_when_it_matches_the_finished_reel():
    """This is a RECONSTRUCTION, not a recording. If it comes out at a different
    length the timeline is shifted, and every caption would sit on the wrong
    words — which would look like our bug, on the owner's video."""
    assert 0 < cb.MAX_DRIFT_S <= 1.0
    # Measured on Turtleback 52039bd4: rebuilt 12.14s vs finished 12.22s.
    assert abs(12.14 - 12.22) < cb.MAX_DRIFT_S


def test_the_rebuilt_cut_is_scaled_to_the_finished_reels_frame():
    """The assembly upscaled the source on its way out — a Turtleback source is
    360x640 and its reel is 1080x1920. Rebuilding at the source's size would
    hand the owner a reel a third the resolution of the one they had, as the
    price of moving a caption. First run of this really did return 360x640."""
    import inspect

    src = inspect.getsource(cb._cut_window)
    assert "scale=" in src and "setsar=1" in src
    assert "size" in inspect.signature(cb._cut_window).parameters
    # ...and the caller passes the FINISHED reel's dimensions, not the source's.
    rebuild_src = inspect.getsource(cb.rebuild)
    assert "_probe(str(prod.get(\"final_url\")" in rebuild_src
    assert "(tw, th)" in rebuild_src


def test_a_rebuild_restores_the_headline_rather_than_dropping_it():
    """The original headline was written at render time and never stored, so it
    cannot be recovered exactly. Leaving it out would mean that moving a caption
    silently DELETES the reel's headline — a worse answer than a paraphrase from
    the same function the pipeline used. The editor shows the text either way."""
    import inspect

    src = inspect.getsource(cb.rebuild)
    assert "gen_video_hook" in src
    assert "caption_hook=$4" in src          # and it is actually persisted
    assert '"rebuilt": True' in src          # marked, so the UI can say so


def test_a_headline_that_cannot_be_rewritten_does_not_fail_the_rebuild():
    """A reel with its captions movable and no headline beats a reel with
    neither, so the LLM call is best-effort."""
    import inspect

    src = inspect.getsource(cb.rebuild)
    hook_block = src[src.index("hook: dict = {}"):src.index("async with acquire(tenant_id) as conn:\n        await conn.execute(\n            \"UPDATE")]
    assert "except Exception" in hook_block


# --- a broken transcript must not become broken captions --------------------

def test_a_transcript_too_sparse_to_be_speech_is_refused():
    """A Turtleback source in this library is 99 seconds transcribed as ONE word
    of Khmer. The drift check cannot catch that — the cut comes out the right
    LENGTH carrying one piece of nonsense — so the rate is checked instead.
    Normal delivery runs 2-3 words a second."""
    assert 0 < cb.MIN_WORDS_PER_S < 1.0
    assert 1 / 99 < cb.MIN_WORDS_PER_S         # the Khmer case is refused
    assert 33 / 15.45 > cb.MIN_WORDS_PER_S     # the real Turtleback reel is kept


def test_the_rate_check_is_actually_wired_into_the_rebuild():
    """Checks placement, not prose: an earlier version of this test matched a
    message string and broke the moment the f-string was re-wrapped, while the
    code it was guarding was fine."""
    import inspect

    src = inspect.getsource(cb.rebuild)
    assert "MIN_WORDS_PER_S" in src
    # It runs BEFORE anything is downloaded or re-encoded — the point of the
    # check is to refuse a broken transcript without paying for the work.
    assert src.index("MIN_WORDS_PER_S") < src.index("_cut_window")
    assert src.index("MIN_WORDS_PER_S") < src.index("fetch_drive_file_to_path")


# --- the batch --------------------------------------------------------------

def test_the_batch_only_picks_up_work_that_is_still_outstanding():
    """Re-running it must not rebuild everything again: a reel that already has
    its cut is finished, and re-cutting would spend time and storage to produce
    the same file."""
    import inspect

    src = inspect.getsource(cb.candidates)
    assert "coalesce(clean_cut_url, '') = ''" in src
    assert "can_rebuild" in src           # and the mode/window rules still apply


def test_one_bad_reel_does_not_stop_the_rest():
    """A library of forty is exactly where a single broken transcript must not
    take the other thirty-nine with it."""
    import inspect

    src = inspect.getsource(cb.rebuild_all)
    assert "except Exception" in src
    assert "asyncio.gather" in src


def test_the_batch_is_rate_limited():
    """Each rebuild re-cuts a video. Forty at once would open forty downloads
    and forty ffmpeg processes on one box."""
    import inspect

    src = inspect.getsource(cb.rebuild_all)
    assert "Semaphore" in src
    assert inspect.signature(cb.rebuild_all).parameters["concurrency"].default <= 5


# --- where the footage actually lives ---------------------------------------

def test_a_drive_backed_reel_counts_as_having_a_source():
    """The Drive path sets source_url to the literal "pending://". Testing that
    field for non-emptiness accepts a placeholder and then dies at ffmpeg with
    "Protocol not found" — which is exactly how 43 of James's reels first looked
    like they were backfillable."""
    ok, why = cb.can_rebuild(_prod(scenes=[{
        "source_url": "pending://", "drive_file_id": "1AbC", "source_id": "s-1",
        "start_s": 0.0, "end_s": 15.0}]))
    assert ok is True, why


def test_a_placeholder_source_with_no_drive_id_is_refused_before_ffmpeg():
    ok, why = cb.can_rebuild(_prod(scenes=[{
        "source_url": "pending://", "start_s": 0.0, "end_s": 15.0}]))
    assert ok is False
    assert "wasn't kept" in why


def test_only_a_real_url_counts_on_the_upload_path():
    for bad in ("pending://", "file:///etc/passwd", "s3://bucket/x.mp4", "   ", "nope"):
        ok, _ = cb.can_rebuild(_prod(scenes=[{
            "source_url": bad, "start_s": 0.0, "end_s": 15.0}]))
        assert ok is False, bad
    for good in ("http://x/y.mp4", "https://x/y.mp4"):
        ok, _ = cb.can_rebuild(_prod(scenes=[{
            "source_url": good, "start_s": 0.0, "end_s": 15.0}]))
        assert ok is True, good


def test_drive_is_fetched_to_disk_because_ffmpeg_cannot_read_a_drive_id():
    import inspect

    src = inspect.getsource(cb.rebuild)
    assert "fetch_drive_file_to_path" in src
    assert src.index("fetch_drive_file_to_path") < src.index("_cut_window(cut_from")


def test_the_batch_fetches_each_source_once_not_once_per_reel():
    """A library is many reels cut from a few videos — 42 of James's come from
    12 files, one serving 8 reels. The source is a whole YouTube video from
    Drive, minutes to fetch, so per-reel downloads make the batch take an
    afternoon instead of half an hour."""
    import inspect

    src = inspect.getsource(cb.rebuild_all)
    assert "groups.setdefault" in src
    assert "fetch_drive_file_to_path" in src
    # fetched OUTSIDE the per-reel loop
    assert src.index("fetch_drive_file_to_path") < src.index("for item in items:\n                    try:")
    assert "source_path=src_path" in src


def test_a_prefetched_source_skips_the_download():
    import inspect

    assert "source_path" in inspect.signature(cb.rebuild).parameters
    src = inspect.getsource(cb.rebuild)
    assert "cut_from = source_path or source_url" in src


def test_a_source_that_cannot_be_fetched_fails_only_its_own_group():
    import inspect

    src = inspect.getsource(cb.rebuild_all)
    block = src[src.index("except Exception as exc:"):]
    assert "for item in items:" in block      # every reel in THAT group is recorded
    assert "return" in block                  # and the other groups carry on


def test_a_refusal_is_remembered_so_it_is_not_offered_again():
    """Whether a rebuild will work is only knowable by doing it. Without a note,
    the editor offers the rebuild, the owner waits a minute, and it fails — every
    single time they open that reel."""
    import inspect

    src = inspect.getsource(cb.rebuild)
    assert src.count("_remember(") >= 4          # every refusal path after the row loads
    assert "caption_rebuild_error=''" in src     # ...and success clears it


def test_the_note_never_fails_the_rebuild():
    import inspect

    assert "except Exception" in inspect.getsource(cb._remember)


def test_the_editor_reads_the_note_instead_of_promising_a_rebuild():
    import inspect

    from james_os import api_v1

    src = inspect.getsource(api_v1._recaption_readiness)
    assert "caption_rebuild_error" in src
    assert src.index("caption_rebuild_error") < src.index("can_rebuild(row)")
