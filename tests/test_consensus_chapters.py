#!/usr/bin/env python3
"""Deterministic synthetic tests for consensus_chapters.py."""

from __future__ import annotations

import math
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import chapterize
import consensus_chapters as cc


def make_timeline(source: str, identity: str, starts: list[float],
                  title: str = "Book") -> cc.SourceTimeline:
    chapters = []
    for i, start in enumerate(starts):
        length = (starts[i + 1] - start) if i + 1 < len(starts) else 300.0
        chapters.append(cc.SourceChapter(f"Chapter {i + 1}", start, length))
    return cc.SourceTimeline(
        source=source, identity=identity, title=title, author="Author",
        chapters=chapters, runtime=starts[-1] + 300.0)


def silences_at(times: list[float], dur: float = 3.0) -> list[chapterize.Silence]:
    half = dur / 2.0
    return [chapterize.Silence(max(0.0, t - half), t + half) for t in times]


def test_offset_and_scale():
    starts = [0, 100, 220, 370, 550, 760, 1000]
    tl = make_timeline("audible", "A1", starts)
    # Local file is delayed by 12s and runs 0.2% slow.
    scale, offset = 1.002, 12.0
    true = [0.0] + [s * scale + offset for s in starts[1:]]
    sil = silences_at(true[1:]) + silences_at([45, 300, 900], dur=1.2)
    prior = cc.fit_silence_prior(sil)
    result = cc.evaluate_timeline(
        tl, sil, prior, true[-1] + 300, 4.0, 120.0, True, None, 15.0)
    assert abs(result.model.offset - offset) < 2.5, result.model
    assert abs(result.model.scale - scale) < 0.01, result.model
    assert result.matched >= 5, result
    print("ok offset/scale")


def test_dense_sentence_pauses():
    starts = [0, 180, 360, 540, 720, 900]
    tl = make_timeline("audible", "A2", starts)
    offset = 8.0
    true = [0.0] + [s + offset for s in starts[1:]]
    # Real chapter gaps plus dense 1.2s sentence pauses.
    sil = silences_at(true[1:], dur=4.0)
    for t in range(20, 1100, 15):
        sil.append(chapterize.Silence(t, t + 1.2))
    prior = cc.fit_silence_prior(sil)
    result = cc.evaluate_timeline(
        tl, sil, prior, 1200, 4.0, 120.0, False, None, 15.0)
    assert result.matched >= 4, (result.matched, result.model)
    # Placed starts should land near the true long silences.
    for d in result.decisions[1:]:
        assert abs(d.placed - (d.published_start + offset)) < 8.0, d
    print("ok dense pauses")


def test_missing_chapter_silence():
    starts = [0, 120, 250, 400, 560]
    tl = make_timeline("audible", "A3", starts)
    offset = 5.0
    # Omit the silence for chapter 3; matcher should virtualize it.
    true = [s + offset for s in starts[1:]]
    sil = silences_at([true[0], true[2], true[3]], dur=3.5)
    prior = cc.fit_silence_prior(sil)
    result = cc.evaluate_timeline(
        tl, sil, prior, 900, 4.0, 120.0, False, None, 15.0)
    missing = result.decisions[2]
    assert missing.virtual or missing.confidence < 0.7, missing
    assert abs(result.decisions[1].placed - true[0]) < 3.0
    assert abs(result.decisions[3].placed - true[2]) < 3.0
    print("ok missing silence")


def test_misleading_long_gap():
    starts = [0, 200, 400, 600, 800]
    tl = make_timeline("audible", "A4", starts)
    offset = 10.0
    true = [s + offset for s in starts[1:]]
    sil = silences_at(true, dur=3.0)
    # A very long tape-side gap that is not a chapter break.
    sil.append(chapterize.Silence(505, 525))
    prior = cc.fit_silence_prior(sil)
    result = cc.evaluate_timeline(
        tl, sil, prior, 1100, 4.0, 120.0, False, None, 15.0)
    placed = [d.placed for d in result.decisions[1:]]
    assert all(abs(p - t) < 5.0 for p, t in zip(placed, true)), placed
    print("ok misleading long gap")


def test_source_selection_different_counts():
    # Ground-truth structure matches Audible (5 chapters).
    audible_starts = [0, 150, 320, 500, 700]
    # LibriVox has a different segmentation (more sections).
    lv_starts = [0, 100, 200, 350, 450, 600, 750]
    audible = make_timeline("audible", "GOOD", audible_starts, "Same Book")
    librivox = make_timeline("librivox", "BAD", lv_starts, "Same Book")

    offset = 7.0
    true = [s + offset for s in audible_starts[1:]]
    sil = silences_at(true, dur=3.5) + silences_at(
        [40, 260, 580], dur=1.3)

    prior = cc.fit_silence_prior(sil)
    ra = cc.evaluate_timeline(
        audible, sil, prior, 1000, 4.0, 120.0, False, None, 15.0)
    rl = cc.evaluate_timeline(
        librivox, sil, prior, 1000, 4.0, 120.0, False, None, 15.0,
        support_times=[d.placed for d in ra.decisions[1:]])
    # Re-score Audible with LibriVox soft support too.
    ra2 = cc.evaluate_timeline(
        audible, sil, prior, 1000, 4.0, 120.0, False, None, 15.0,
        support_times=[d.placed for d in rl.decisions[1:]])
    best = cc.select_best([ra2, rl], local_dur=1000)
    assert best.timeline.identity == "GOOD", (best.timeline.identity,
                                              ra2.score, rl.score)
    assert best.matched >= 3, best
    print("ok source selection")


def test_silence_prior_prefers_long():
    sil = silences_at([100, 200, 300], dur=4.0) + silences_at(
        list(range(10, 90, 8)), dur=1.1)
    prior = cc.fit_silence_prior(sil)
    assert prior.break_score(4.0) > prior.break_score(1.1)
    assert math.exp(prior.break_mean_log) > math.exp(prior.pause_mean_log)
    print("ok silence prior")


def test_report_shape():
    starts = [0, 100, 220, 360]
    tl = make_timeline("audible", "R1", starts)
    sil = silences_at([112, 232, 372], dur=3.0)
    prior = cc.fit_silence_prior(sil)
    result = cc.evaluate_timeline(
        tl, sil, prior, 700, 4.0, 120.0, False, None, 15.0)
    blob = cc.report_json(result, [result], "x.mp3", 700)
    assert blob["source"] == "consensus"
    assert blob["selected"]["identity"] == "R1"
    assert len(blob["chapters"]) == 4
    assert "score" in blob["align"]
    print("ok report shape")


if __name__ == "__main__":
    test_silence_prior_prefers_long()
    test_offset_and_scale()
    test_dense_sentence_pauses()
    test_missing_chapter_silence()
    test_misleading_long_gap()
    test_source_selection_different_counts()
    test_report_shape()
    print("\nall consensus tests passed")
