"""Characterization tests for chapterize silence alignment and formats."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chapterize as cz  # noqa: E402


def _book(starts: list[float], brand_intro: float = 0.0) -> cz.AudnexBook:
    chapters = []
    for i, start in enumerate(starts):
        length = (starts[i + 1] - start) if i + 1 < len(starts) else 300.0
        chapters.append(cz.Chapter(f"Chapter {i + 1}", start, length))
    return cz.AudnexBook(
        asin="TEST",
        chapters=chapters,
        brand_intro=brand_intro,
        runtime=starts[-1] + 300.0,
        title="Test Book",
        author="Author",
    )


def _silences(mids: list[float], dur: float = 3.0) -> list[cz.Silence]:
    half = dur / 2.0
    return [cz.Silence(max(0.0, m - half), m + half) for m in mids]


def test_linfit_known_line():
    pairs = [(100.0, 112.0), (200.0, 212.5), (300.0, 313.0),
             (400.0, 413.5), (500.0, 514.0)]
    fit = cz._linfit(pairs)
    assert fit is not None
    scale, offset = fit
    assert abs(scale - 1.005) < 1e-3
    assert abs(offset - 11.5) < 1.0


def test_score_counts_within_tol():
    boundaries = [100.0, 200.0, 300.0]
    sil_mids = [112.0, 212.0, 400.0]
    matched, mean_err, pairs = cz._score(boundaries, sil_mids, 12.0, 1.0, 4.0)
    assert matched == 2
    assert len(pairs) == 2
    assert mean_err < 0.1


def test_solve_alignment_recovers_offset():
    starts = [0.0, 100.0, 220.0, 370.0, 550.0, 760.0]
    offset = 12.0
    book = _book(starts)
    true = [s + offset for s in starts[1:]]
    sil = _silences(true) + _silences([40.0, 300.0, 900.0], dur=1.2)
    al = cz.solve_alignment(
        book, sil, local_dur=1100.0, mode="auto", tol=4.0,
        max_offset=120.0, allow_scale=False, verbose=False)
    assert abs(al.offset - offset) < 2.0
    assert al.matched >= 4


def test_solve_alignment_scale_fit():
    starts = [0.0, 100.0, 220.0, 370.0, 550.0, 760.0, 1000.0]
    scale, offset = 1.002, 12.0
    book = _book(starts)
    true = [s * scale + offset for s in starts[1:]]
    sil = _silences(true)
    al = cz.solve_alignment(
        book, sil, local_dur=true[-1] + 300.0, mode="auto", tol=4.0,
        max_offset=120.0, allow_scale=True, verbose=False)
    assert abs(al.offset - offset) < 2.5
    assert abs(al.scale - scale) < 0.01
    assert al.matched >= 5


def test_place_chapters_monotonic_and_ch0_zero():
    starts = [0.0, 100.0, 220.0, 370.0]
    book = _book(starts)
    al = cz.Alignment(offset=10.0, scale=1.0, matched=3, total=3)
    sil = _silences([110.0, 230.0, 380.0])
    placed = cz.place_chapters(
        book, sil, al, local_dur=700.0, snap=True, snap_window=5.0, min_len=1.0)
    assert placed[0].start == 0.0
    assert all(placed[i].start <= placed[i + 1].start
               for i in range(len(placed) - 1))


def test_parse_audnex_fixture(audnex_stainless):
    book = cz.parse_audnex(audnex_stainless, "B0041M5MRE")
    assert book.asin == "B0041M5MRE"
    assert len(book.chapters) == 20
    assert book.chapters[0].title == "Opening Credits"
    assert book.chapters[1].start == pytest_approx(20.524)
    assert book.brand_intro == pytest_approx(1.904)
    assert book.runtime == pytest_approx(17610.062)


def pytest_approx(value):
    # tiny local helper so this file stays importable without pytest.approx
    class A:
        def __eq__(self, other):
            return abs(float(other) - value) < 1e-6
    return A()


def test_ffmetadata_and_abs_json_shape():
    book = _book([0.0, 100.0, 200.0])
    book.title = "T"
    book.author = "A"
    placed = [
        cz.Placed("Opening", 0.0, False, 0.0),
        cz.Placed("One", 110.0, True, 0.1),
        cz.Placed("Two", 210.0, True, 0.0),
    ]
    meta = cz.ffmetadata(placed, 400.0, book)
    assert meta.startswith(";FFMETADATA1")
    assert "title=T" in meta
    assert "artist=A" in meta
    assert "START=110000" in meta
    blob = json.loads(cz.abs_json(placed, 400.0))
    assert blob[0] == {"id": 0, "start": 0.0, "end": 110.0, "title": "Opening"}
    assert blob[-1]["end"] == 400.0


def test_detect_silences_parses_stderr(monkeypatch, tmp_path):
    stderr_text = (
        "silence_start: 10.0\n"
        "silence_end: 13.5 | silence_duration: 3.5\n"
        "silence_start: 50.25\n"
        "silence_end: 53.0 | silence_duration: 2.75\n"
    )

    class Fake:
        returncode = 0
        stdout = ""
        stderr = stderr_text

    def fake_run(cmd, **kwargs):
        return Fake()

    monkeypatch.setattr(
        "chaptertools.silence.detection.subprocess.run", fake_run)
    audio = tmp_path / "book.mp3"
    audio.write_bytes(b"fake")
    sil = cz.detect_silences(
        str(audio), noise_db=-40.0, min_dur=1.5,
        verbose=False, cache_dir=False)
    assert len(sil) == 2
    assert abs(sil[0].start - 10.0) < 1e-9
    assert abs(sil[0].end - 13.5) < 1e-9
    assert abs(sil[1].mid - (50.25 + 53.0) / 2) < 1e-9
