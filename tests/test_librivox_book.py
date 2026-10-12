"""LibriVox multi-file books on the normal Scan → Match → Place → Finish path."""
from __future__ import annotations

import json
from pathlib import Path

from chaptertools.gui import librivox_book as lb


def _folder(tmp_path, *, durations=(10.0, 20.0, 15.0)):
    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    files = []
    sections = []
    for i, dur in enumerate(durations):
        name = f"{i:02d} - Chapter {i}.mp3"
        path = folder / name
        path.write_bytes(b"x" * (100 + i))
        files.append(str(path))
        sections.append({
            "section_number": i, "title": f"Chapter {i}",
            "duration": dur, "file": name,
        })
    meta = {
        "id": 77, "title": "Book", "author": "Author",
        "sections": sections, "set": "128Kbps MP3",
    }
    (folder / "librivox.json").write_text(json.dumps(meta))
    nfo = folder / "album.nfo"
    t = 0.0
    tracks = []
    for i, dur in enumerate(durations):
        tracks.append(f'  <track>\n    <title>Chapter {i}</title>\n'
                      f'    <position>{i + 1}</position>\n'
                      f'    <duration>{int(dur)}</duration>\n  </track>')
        t += dur  # cumulative not used in this nfo shape
    nfo.write_text(
        "<album>\n<title>Book</title>\n"
        + "\n".join(tracks) + "\n</album>\n")
    return str(folder), files, meta


def test_write_ffconcat_lists_every_section(tmp_path):
    folder, files, _ = _folder(tmp_path)
    path = lb.write_ffconcat(files, str(tmp_path / "work" / "book.ffconcat"))
    text = Path(path).read_text()
    for f in files:
        assert f"file '{f}'" in text or f'file "{f}"' in text
    assert text.count("file ") == 3


def test_cumulative_starts_and_pin_specs():
    starts = lb.cumulative_starts([10.0, 20.0, 15.0])
    assert starts == [0.0, 10.0, 30.0]
    pins = lb.pin_specs(starts)
    assert pins == ["1=0:00:00", "2=0:00:10", "3=0:00:30"]


def test_book_time_maps_across_files():
    starts = [0.0, 10.0, 30.0]
    durs = [10.0, 20.0, 15.0]
    assert lb.book_to_file(0.0, starts, durs) == (0, 0.0)
    assert lb.book_to_file(10.5, starts, durs) == (1, 0.5)
    assert lb.book_to_file(44.0, starts, durs) == (2, 14.0)
    assert lb.file_to_book(1, 2.5, starts) == 12.5


def test_playlist_sig_changes_when_a_file_grows(tmp_path):
    _, files, _ = _folder(tmp_path)
    a = lb.playlist_sig(files)
    Path(files[1]).write_bytes(b"y" * 500)
    b = lb.playlist_sig(files)
    assert a != b


def test_nfo_chapters_from_probed_durations():
    chapters = lb.chapters_from_durations(
        ["Intro", "One", "Two"], [5.0, 10.0, 7.0])
    assert [c["title"] for c in chapters] == ["Intro", "One", "Two"]
    assert [c["start"] for c in chapters] == [0.0, 5.0, 15.0]
    assert chapters[0]["end"] == 5.0
    assert chapters[2]["end"] == 22.0


def test_measure_argv_includes_concat():
    from chaptertools.gui.commands import CHAPTERFIT_MODULE, measure_argv
    argv = measure_argv(
        ["python3", "-u", "-m", CHAPTERFIT_MODULE],
        "/work", "/a/01.mp3", concat="/work/book.ffconcat")
    assert "--concat" in argv
    assert argv[argv.index("--concat") + 1] == "/work/book.ffconcat"
    assert argv[-1] == "/a/01.mp3"


def test_set_book_librivox_keeps_the_step_rail(tmp_path, monkeypatch):
    import pytest
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display")
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "library"))
    folder, files, _ = _folder(tmp_path / "library")
    from chaptertools.gui.app import App
    app = App(library_root=str(tmp_path / "library"))
    try:
        wf = app.workflow
        wf.set_book(folder)
        assert wf._step != "multifile"
        assert wf.rail.winfo_manager()  # packed/gridded, not removed
        assert list(wf.section_files) == files
        assert wf.audio == files[0]
        assert not getattr(wf, "_multifile_mode", False)
        assert wf.embed_var.get() is False
        assert wf._concat_path and Path(wf._concat_path).is_file()
        assert wf.section_starts == [0.0, 10.0, 30.0]
    finally:
        app.destroy()


def test_pin_specs_match_cumulative_probed_durations():
    from chaptertools.gui.librivox_book import pin_specs
    assert pin_specs([0.0, 10.0, 30.0]) == [
        "1=0:00:00", "2=0:00:10", "3=0:00:30"]


def test_cmd_identify_nfo_chosen_when_album_has_tracks(tmp_path, monkeypatch):
    """After measure, a LibriVox album.nfo is identified without a prompt."""
    import pytest
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display")
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "library"))
    folder, files, _ = _folder(tmp_path / "library")
    from chaptertools.gui.app import App
    from chaptertools.nfo import read_nfo_chapters
    app = App(library_root=str(tmp_path / "library"))
    try:
        wf = app.workflow
        wf.set_book(folder)
        nfo = read_nfo_chapters(str(Path(folder) / "album.nfo"))
        assert len(nfo) >= 2
        ran = []
        wf.cmd_identify_nfo = lambda: ran.append("nfo") or True
        wf.harvest = {"nfo_chapters": nfo, "loading": False}
        wf.analysis = {
            "envelope": {"duration": 45.0},
            "calibration": {"x": 1},
            "gaps": {"gaps": [1]},
            "fingerprint": {"x": 1},
            "source": {"timeline_trustworthy": True},
        }
        wf._nfo_decided = False
        wf._maybe_identify_from_nfo()
        assert ran == ["nfo"]
        assert wf._nfo_decided
    finally:
        app.destroy()
