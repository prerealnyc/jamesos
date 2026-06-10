"""Tests for the Autopilot distinct-style-template selector (pure logic)."""

from james_os.autopilot_templates import assign_distinct


def _lib(n):
    return [{"id": f"t{i}", "name": f"T{i}", "template": {"x": 1}} for i in range(n)]


def test_all_distinct_when_n_equals_library():
    out = assign_distinct(_lib(5), 5)
    assert len(out) == 5
    assert len({t["id"] for t in out}) == 5  # 5 videos → 5 different styles


def test_distinct_prefix_when_fewer_videos_than_library():
    out = assign_distinct(_lib(5), 3)
    assert [t["id"] for t in out] == ["t0", "t1", "t2"]


def test_cycles_in_order_when_more_videos_than_library():
    out = assign_distinct(_lib(3), 7)
    assert [t["id"] for t in out] == ["t0", "t1", "t2", "t0", "t1", "t2", "t0"]
    assert {t["id"] for t in out} == {"t0", "t1", "t2"}  # whole library used


def test_empty_library_returns_empty():
    assert assign_distinct([], 5) == []  # caller falls back to normal generation


def test_zero_videos_returns_empty():
    assert assign_distinct(_lib(3), 0) == []
