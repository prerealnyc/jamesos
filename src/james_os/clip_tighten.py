"""Intra-clip tightening — remove internal dead air from a reel window.

Like a human editor de-um-ing a take: we use the word-level timestamps we
already have (Whisper / AssemblyAI) to find SILENT GAPS between words, cut them
out on word boundaries, and remap every word's timestamp onto the compressed
timeline. The caller then re-encodes the video to match (audio_trim.tighten_clip)
and the EXISTING caption / B-roll builders run unchanged against the remapped
words, so everything stays in sync.

Pure functions only — no ffmpeg, no network, fully unit-testable. The actual
video re-encode lives in audio_trim.tighten_clip; the orchestration (gating,
fallback, re-upload) lives in story_video.build_engaging_avatar_assets.

Safety: gap-cutting NEVER bisects a word — cuts land at word.end / word.start —
so audio splices fall in the low-energy gaps. Removing dead air only.
"""

from __future__ import annotations

from .transcription import TranscribedWord, TranscriptionWithWords

# Small, conservative filler set. Only removed when clip_tighten_remove_fillers
# is on (off by default) — filler removal is the riskiest for naturalness.
_FILLERS = frozenset({
    "um", "umm", "uh", "uhh", "uhhh", "erm", "err", "uhm", "eh",
    "ah", "mm", "mmm", "hmm", "hm", "mhm",
})


def maybe_drop_fillers(words: list[TranscribedWord], enable: bool) -> list[TranscribedWord]:
    """Optionally drop isolated filler tokens and immediate duplicate restarts.
    Dropping a word turns its span into a gap that compute_kept_intervals may
    then excise (only if the resulting gap exceeds max_gap — short padded ums
    stay). No-op unless `enable`."""
    if not enable:
        return words
    out: list[TranscribedWord] = []
    for w in words:
        bare = "".join(c for c in (w.word or "").lower() if c.isalpha())
        if bare in _FILLERS:
            continue
        # Collapse an immediate stutter restart ("the— the"): same short word
        # repeated within 0.4s.
        if (out and (out[-1].word or "").strip().lower() == (w.word or "").strip().lower()
                and (w.start - out[-1].end) < 0.4):
            continue
        out.append(w)
    return out


def compute_kept_intervals(
    words: list[TranscribedWord], total_dur: float, *,
    max_gap: float = 0.55, head_pad: float = 0.04, tail_pad: float = 0.12,
    edge_keep: float = 0.35,
) -> list[tuple[float, float]]:
    """Walk consecutive words; when the inter-word silence exceeds `max_gap`,
    CLOSE the current kept interval (with a small tail_pad so the last word's
    fall-off survives) and OPEN the next at the following word (minus head_pad so
    its onset/in-breath survives). Leading/trailing silence is capped at
    `edge_keep` so James is on screen a beat before/after he speaks.

    Returns non-overlapping (start, end) intervals in order, every boundary on a
    word edge. A single interval == nothing to cut (caller treats as no-op)."""
    ws = sorted([w for w in words if w.end > w.start], key=lambda w: w.start)
    if not ws:
        return [(0.0, max(0.0, total_dur))]
    intervals: list[tuple[float, float]] = []
    cur_s = max(0.0, ws[0].start - edge_keep)
    cur_e = ws[0].end
    for a, b in zip(ws, ws[1:]):
        if (b.start - a.end) > max_gap:
            intervals.append((cur_s, min(total_dur, a.end + tail_pad)))
            cur_s = max(0.0, b.start - head_pad)
        cur_e = b.end
    intervals.append((cur_s, min(total_dur, min(cur_e + tail_pad, ws[-1].end + edge_keep))))
    # Re-merge any pad-induced overlaps; drop sub-frame slivers libx264 rejects.
    merged: list[tuple[float, float]] = []
    for s, e in intervals:
        if e - s < 0.05:
            continue
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged or [(0.0, max(0.0, total_dur))]


def remap_words(
    tr: TranscriptionWithWords, intervals: list[tuple[float, float]],
) -> TranscriptionWithWords:
    """Map every word's start/end from the OLD timeline onto the tightened one.
    A point t inside kept interval k (old [s,e], new offset prefix_k) maps to
    prefix_k + (t - s). Words that fell entirely inside an excised gap (only
    possible via filler removal) are dropped. Returns a new
    TranscriptionWithWords whose duration is the total kept length."""
    prefix = 0.0
    table: list[tuple[float, float, float]] = []   # (old_s, old_e, new_start)
    for s, e in intervals:
        table.append((s, e, prefix))
        prefix += (e - s)

    def _map(t: float) -> float | None:
        for s, e, ns in table:
            if s - 1e-6 <= t <= e + 1e-6:
                return ns + max(0.0, t - s)
        return None

    out: list[TranscribedWord] = []
    for w in tr.words:
        ns, ne = _map(w.start), _map(w.end)
        if ns is None or ne is None:
            continue                                # word lived in a removed gap
        out.append(TranscribedWord(
            word=w.word, start=round(ns, 3),
            end=round(max(ne, ns + 0.01), 3), speaker=w.speaker,
        ))
    return TranscriptionWithWords(text=tr.text, words=out, duration=round(prefix, 3))


__all__ = ["maybe_drop_fillers", "compute_kept_intervals", "remap_words", "_FILLERS"]
