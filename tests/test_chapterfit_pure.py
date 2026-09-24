"""Characterization tests for chapterfit pure math and artifacts."""

from __future__ import annotations

import array
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chapterfit as cf  # noqa: E402


def test_normalize_work_dir_strips_shell_escapes():
    assert cf.normalize_work_dir(r"Harry\ Harrison/Book") == "Harry Harrison/Book"
    assert cf.normalize_work_dir(r"path\ with\ spaces") == "path with spaces"


def test_parse_time_and_hms_roundtrip_shapes():
    assert cf.parse_time("1:02:03.5") == 3723.5
    assert cf.parse_time(12) == 12.0
    assert cf.hms(3661.5).endswith("01:01.500") or "1:01:01.500" in cf.hms(3661.5)
    assert cf.clock(3661) == "1:01:01"
    assert cf.hms(None) == "-"


def test_logsumexp_stable():
    assert math.isinf(cf.logsumexp([]))
    assert abs(cf.logsumexp([1.0, 1.0]) - (1.0 + math.log(2))) < 1e-9
    big = cf.logsumexp([1000.0, 1000.0])
    assert abs(big - (1000.0 + math.log(2))) < 1e-6


def test_find_gaps_synthetic_envelope():
    # 20 Hz: 50 ms frames. Quiet run of 2s below threshold, then speech.
    hz = 20
    env = [-20.0] * 40 + [-60.0] * 40 + [-20.0] * 40
    gaps = cf.find_gaps(env, hz, thr_db=-40.0, min_dur=1.0)
    assert len(gaps) == 1
    assert abs(gaps[0]["dur"] - 2.0) < 1e-9
    assert abs(gaps[0]["mid"] - 3.0) < 1e-9


def test_classify_gaps_few_still_tag_pop():
    gaps = [{"dur": 1.0}, {"dur": 1.05}]
    pops = cf.classify_gaps(gaps)
    assert pops[0]["k"] == 0 and pops[0]["n"] == 2
    assert all(g["pop"] == 0 for g in gaps)


def test_flatten_chapters_fixture(audnex_stainless):
    chapters, meta = cf.flatten_chapters(audnex_stainless)
    assert len(chapters) == 20
    assert chapters[0]["start"] == 0.0
    assert chapters[1]["title"] == "Chapter One"
    assert abs(chapters[1]["start"] - 20.524) < 1e-6
    assert abs(meta["brand_intro"] - 1.904) < 1e-6
    assert abs(meta["runtime"] - 17610.062) < 1e-3


def test_solve_recovers_offset_free_alignment():
    # Published chapter starts; observed gaps delayed by a constant offset.
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 220, 370, 550, 760])]
    offset = 42.0
    cands = [{"mid": s + offset} for s in [100, 220, 370, 550, 760]]
    assign, st = cf.solve(
        chapters, cands, pins={}, max_off=120.0, w_rate=8.0,
        skip_penalty=3.0)
    assert st["assigned"] >= 4
    for i, ch in enumerate(chapters):
        if i == 0 or i not in assign:
            continue
        assert abs(assign[i] - (ch["start"] + offset)) < 1.0


def test_solve_respects_pins_and_offset_hint():
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 220, 370, 550])]
    offset = 90.0
    cands = [{"mid": s + offset} for s in [100, 220, 370, 550]]
    # Without a hint, max_off=30 cannot see the true gaps.
    assign_miss, st_miss = cf.solve(
        chapters, cands, pins={}, max_off=30.0, w_rate=8.0,
        skip_penalty=3.0)
    assert st_miss["assigned"] < 3
    assign, st = cf.solve(
        chapters, cands, pins={1: 100.0 + offset}, max_off=30.0,
        w_rate=8.0, skip_penalty=3.0, offset_hint=offset, hint_window=20.0)
    assert st["assigned"] >= 3
    assert abs(assign[1] - (100.0 + offset)) < 1e-9


def test_score_population_prefers_consistent_rates():
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 200, 300, 400])]
    good = [{"mid": float(s)} for s in [100, 200, 300, 400]]
    noisy = [{"mid": float(s)} for s in [80, 160, 310, 500]]
    good_s = cf.score_population(
        chapters, good, dur=500.0, pins={}, max_off=40.0,
        w_rate=8.0, skip_penalty=3.0, tol=4.0)
    bad_s = cf.score_population(
        chapters, noisy, dur=500.0, pins={}, max_off=40.0,
        w_rate=8.0, skip_penalty=3.0, tol=4.0)
    assert good_s["score"] > bad_s["score"]


def test_posteriors_pin_and_unassigned():
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 200])]
    cands = [{"mid": 100.0}, {"mid": 200.0}]
    assign = {1: 100.0, 2: 200.0}
    posts = cf.posteriors(chapters, cands, assign, pins={1: 100.0},
                          window=10.0, w_rate=8.0)
    assert posts[0]["confidence"] == 1.0
    assert posts[1]["confidence"] == 1.0
    assert posts[2]["time"] == 200.0


def test_heading_parsers():
    assert cf.words_to_number("chapter twelve") == 12
    assert cf.words_to_number("twenty one") == 21
    heads = cf.parse_headings("and then Chapter Three begins")
    assert any(h.get("number") == 3 for h in heads)
    hit = cf.parse_heading("Chapter 7")
    assert hit is not None and hit.get("number") == 7


def test_artifact_roundtrip(tmp_path):
    audio = tmp_path / "book.mp3"
    audio.write_bytes(b"x" * 100)
    art = cf.Artifact(str(audio), work_dir=str(tmp_path / "work"))
    env = array.array("b", [-20, -60, -20, -60])
    art.write_envelope(env)
    got = art.read_envelope()
    assert list(got) == list(env)
    art.put("calibration", {"floor": -60.0}, {"sig": 1})
    art.save()
    art2 = cf.Artifact(str(audio), work_dir=str(tmp_path / "work"))
    assert art2.valid("calibration", {"sig": 1})
    assert art2.data["calibration"]["floor"] == -60.0
    # fingerprint change invalidates
    audio.write_bytes(b"y" * 120)
    art3 = cf.Artifact(str(audio), work_dir=str(tmp_path / "work"))
    assert "calibration" not in art3.data or art3.data.get("schema") == cf.SCHEMA
    # after size change, load clears derived data
    assert art3.data.get("calibration") is None


def test_coarse_fit_finds_offset():
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 220, 370, 550])]
    offset = 15.0
    cands = [{"mid": s + offset} for s in [100, 220, 370, 550]]
    fit = cf.coarse_fit(chapters, cands, dur=700.0, max_off=60.0, tol=4.0)
    assert fit is not None
    assert abs(fit["offset"] - offset) < 2.0
    assert fit["matched"] >= 3
