"""Heard-column mid matching, and the transcript window when a display exists."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _host(positions, pattern=""):
    return SimpleNamespace(
        analysis={"heard": {"positions": positions}},
        grep_var=SimpleNamespace(get=lambda: pattern),
    )


def test_heard_of_matches_mid_not_time():
    from chaptertools.gui.workflow import WorkflowPanel

    row = SimpleNamespace(placed=100.0)
    host = _host([
        {"time": 100.0, "mid": 10.0, "text": "mid says far"},
        {"mid": 104.0, "text": "nearby speech"},
        {"mid": 200.0, "text": "too far"},
    ])
    assert WorkflowPanel._heard_of(host, row) == "nearby speech"


def test_heard_of_falls_back_to_time():
    from chaptertools.gui.workflow import WorkflowPanel

    row = SimpleNamespace(placed=50.0)
    host = _host([{"time": 52.0, "text": "from time"}])
    assert WorkflowPanel._heard_of(host, row) == "from time"


def test_heard_of_keeps_chapter_filter():
    from chaptertools.gui.workflow import WorkflowPanel

    row = SimpleNamespace(placed=100.0)
    host = _host([{"mid": 100.0, "text": "nearby speech"}], pattern="chapter")
    assert WorkflowPanel._heard_of(host, row) == ""


def _display_app(tmp_path, monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "library"))
    (tmp_path / "library").mkdir()
    from chaptertools.gui.app import App
    return App(library_root=str(tmp_path / "library"), modal_busy=False)


_CLIPS = {"heard": {"clips": {
    "12.5|3|base": '[{"s": 0.0, "t": "chapter two begins"}]',
    "80.0|2|base": '[{"s": 0.0, "t": "the road goes on"}]',
}}}


def test_transcript_window_shows_speech_and_plays_the_line(tmp_path, monkeypatch):
    app = _display_app(tmp_path, monkeypatch)
    played = []
    try:
        app.update()
        wf = app.workflow
        wf.show_step("place")
        app.update()
        assert wf.transcript_btn.cget("text") == "View transcripts"
        wf.analysis = _CLIPS
        wf.play_at_book_time = lambda t: played.append(t)
        wf.transcript_btn.invoke()
        view = wf.transcripts
        app.update()
        view.win.update()
        body = view.text.get("1.0", "end")
        assert "chapter two begins" in body
        assert "the road goes on" in body
        assert view.count.get() == "2 lines from 2 clips"
        assert wf._step == "place"

        box = view.text._textbox
        idx = box.search("the road goes on", "1.0")
        bbox = box.bbox(idx)
        assert bbox, "transcript line is not on screen"
        box.event_generate("<Button-1>", x=bbox[0] + 2, y=bbox[1] + 2)
        assert played == [80.0]

        view.pattern.set("road")
        filtered = view.text.get("1.0", "end")
        assert "the road goes on" in filtered
        assert "chapter two begins" not in filtered
        assert view.count.get() == "1 of 2 lines from 2 clips"

        view.pattern.set("chapt[")
        assert view.count.get().startswith("bad regex:")

        refreshed = {"heard": {"clips": {
            "3|1|base": '[{"s": 0.0, "t": "after refresh"}]'}}}
        monkeypatch.setattr(
            "chaptertools.gui.workflow.load_analysis",
            lambda _audio, _work: refreshed)
        audio = tmp_path / "library" / "Book.mp3"
        audio.write_bytes(b"")
        work = tmp_path / "work"
        work.mkdir()
        wf.audio = str(audio)
        wf.work_dir = str(work)
        view.pattern.set("")
        wf._refresh()
        assert "after refresh" in view.text.get("1.0", "end")
        assert "chapter two begins" not in view.text.get("1.0", "end")
    finally:
        app.destroy()


def test_popup_menu_releases_grab():
    from chaptertools.gui.transcript_view import TranscriptView

    released = []
    view = TranscriptView.__new__(TranscriptView)
    view._menu = SimpleNamespace(
        tk_popup=lambda x, y: None,
        grab_release=lambda: released.append(True),
    )
    view._popup_menu(SimpleNamespace(x_root=10, y_root=20))
    assert released == [True]
