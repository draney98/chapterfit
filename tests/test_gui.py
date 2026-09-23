"""Smoke tests for the GUI package. Tk-dependent tests skip headless."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from chaptertools.gui import library as lib
from chaptertools.gui.runner import Job, Runner

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_scan_library_finds_audio(tmp_path):
    root = tmp_path / "library"
    book = root / "Isaac Asimov" / "Foundation"
    book.mkdir(parents=True)
    (book / "Foundation.mp3").touch()
    (book / "cover.jpg").touch()
    (book / "Foundation.chapters.mp3").touch()  # emit intermediate: hidden
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "x.mp3").touch()

    out = lib.scan_library(str(root))

    assert list(out) == ["Isaac Asimov"]
    assert out["Isaac Asimov"] == [str(book / "Foundation.mp3")]


def test_search_filters_library(tmp_path):
    app = _make_app(tmp_path)
    try:
        # add a second author/book to the cached library and re-render
        app._library["Greg Bear"] = [
            os.path.join(app.library_root, "Greg Bear", "Eon", "Eon.mp3")]

        app.search_var.set("book")  # 3+ chars, matches title
        authors = app.lib_tree.get_children("")
        assert len(authors) == 1
        leaves = [app.lib_tree.item(i, "text")
                  for i in app.lib_tree.get_children(authors[0])]
        assert leaves == ["Book.mp3"]

        app.search_var.set("bear")  # matches author -> all their books
        authors = app.lib_tree.get_children("")
        assert len(authors) == 1
        assert app.lib_tree.item(authors[0], "text") == "Greg Bear"
        assert len(app.lib_tree.get_children(authors[0])) == 1

        app.search_var.set("zz")  # 2 chars: below threshold, shows all
        assert len(app.lib_tree.get_children("")) == 2

        app.search_var.set("nothing-matches-this")
        assert not app.lib_tree.get_children("")

        app.search_var.set("")  # cleared: everything back
        assert len(app.lib_tree.get_children("")) == 2
    finally:
        app.destroy()


def test_library_root_prefers_explicit_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "from-env"))
    assert lib.library_root(str(tmp_path / "explicit")) == \
        str(tmp_path / "explicit")


def test_library_root_uses_env_when_not_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "from-env"))
    assert lib.library_root() == str(tmp_path / "from-env")


def test_library_root_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAPTERFIT_LIBRARY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert lib.library_root() == str(tmp_path)


def test_work_dir_for_mirrors_chapterbook(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAPTERFIT_WORK", raising=False)
    monkeypatch.setattr(lib, "work_root", lambda: str(tmp_path))
    audio = tmp_path / "library" / "Greg Bear" / "Eon" / "Eon.mp3"
    assert lib.work_dir_for(str(audio)) == \
        str(tmp_path / "Greg Bear" / "Eon")


def test_load_analysis_missing_is_empty(tmp_path):
    audio = tmp_path / "book.mp3"
    audio.touch()
    assert lib.load_analysis(str(audio)) == {}
    assert lib.analysis_path(str(audio)) == str(audio) + ".analysis.json"


def test_runner_streams_lines_and_exit_code(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("print('hello')\nprint('world')\n")
    runner = Runner()
    done = []
    runner.run(Job("test", ["python3", str(script)],
                   on_done=lambda j, code: done.append(code)))
    lines = []
    while not done:
        kind, job, payload = runner.events.get(timeout=10)
        if kind == "line":
            lines.append(payload)
        elif kind == "done":
            done.append(payload)
    assert lines == ["hello", "world"]
    assert done == [0]


def test_runner_reports_missing_binary():
    runner = Runner()
    done = []
    runner.run(Job("nope", ["definitely-not-a-real-binary-xyz"],
                   on_done=lambda j, code: done.append(code)))
    while not done:
        kind, job, payload = runner.events.get(timeout=10)
        if kind == "done":
            done.append(payload)
    assert done[0] is None


def _make_app(tmp_path):
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    import chaptertools.gui.workflow as wf
    from chaptertools.gui.app import App
    root = tmp_path / "library"
    book = root / "Author" / "Book"
    book.mkdir(parents=True)
    (book / "Book.mp3").touch()
    app = App(library_root=str(root))
    app.dialogs = []

    mp = pytest.MonkeyPatch()
    for name in ("showinfo", "showerror", "showwarning"):
        def recorder(title=None, message=None, _n=name, **kw):
            app.dialogs.append((_n, message))
        mp.setattr(wf.messagebox, name, recorder)

    real_destroy = app.destroy
    def destroy():
        mp.undo()
        real_destroy()
    app.destroy = destroy
    app.update_idletasks()
    return app


def test_app_builds_and_selects_book(tmp_path):
    app = _make_app(tmp_path)
    try:
        children = app.lib_tree.get_children("")
        assert len(children) == 1
        author = children[0]
        leaves = app.lib_tree.get_children(author)
        assert len(leaves) == 1
        app._on_select()
        assert app.workflow.audio is None  # selection fires, path resolves
        book_path = app._path_for(leaves[0])
        assert book_path is not None and book_path.endswith("Book.mp3")
        app.workflow.set_book(book_path)
        assert app.workflow.audio == book_path
        assert "Book.mp3" in app.workflow.book_var.get()
        assert app.workflow.work_dir.endswith(
            os.path.join("Author", "Book"))
    finally:
        app.destroy()


def test_workflow_refresh_renders_alignment(tmp_path):
    app = _make_app(tmp_path)
    try:
        book_path = str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")
        app.workflow.set_book(book_path)
        work = app.workflow.work_dir
        os.makedirs(work, exist_ok=True)
        art = {"schema": 3, "pins": {"1": 120.0}, "alignment": {"rate": 1.0,
               "results": [
            {"index": 0, "title": "Chapter 1", "published": 0.0,
             "time": 0.0, "confidence": 0.9, "pinned": False,
             "spread": None, "n_options": 2, "residual": None},
            {"index": 1, "title": "Chapter 2", "published": 3600.0,
             "time": None, "confidence": 0.0, "pinned": False,
             "spread": None, "n_options": 0, "residual": None},
        ]}}
        with open(os.path.join(work, "Book.mp3.analysis.json"), "w") as f:
            json.dump(art, f)
        app.workflow._refresh()
        rows = [app.workflow.tree.item(i, "values")
                for i in app.workflow.tree.get_children()]
        assert len(rows) == 2
        assert rows[0][1] == "Chapter 1"
        assert rows[0][4] == "0.90"
        assert rows[1][3] == "0:00:00.000"

        # Remove pin with an empty field falls back to the selected row,
        # and a successful remove chains into an automatic re-align.
        ran = []
        app.workflow.tree.selection_set(app.workflow.tree.get_children()[1])
        app.workflow.pin_ch.set("")
        import chaptertools.gui.workflow as wf
        monkey = pytest.MonkeyPatch()
        monkey.setattr(wf.WorkflowPanel, "_run",
                       lambda self, name, argv, then=None, stage=None, on_line=None:
                       ran.append((name, argv, then)))
        try:
            app.workflow.cmd_unpin()
            assert ran and ran[0][0] == "pin"
            assert "--remove" in ran[0][1] and "2" in ran[0][1]
            assert ran[0][2] is not None  # auto-align chained
            ran.clear()
            # a chapter without a pin is reported, not run
            app.workflow.pin_ch.set("2")
            app.workflow.analysis = {"pins": {"0": 5.0}}
            app.workflow.cmd_unpin()
            assert not ran
            assert any("has no pin" in (m or "") for _, m in app.dialogs)
        finally:
            monkey.undo()
    finally:
        app.destroy()


def test_withheld_shading_matches_emit(tmp_path):
    from chaptertools.gui.workflow import _withheld

    app = _make_app(tmp_path)
    try:
        book_path = str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")
        app.workflow.set_book(book_path)
        work = app.workflow.work_dir
        os.makedirs(work, exist_ok=True)

        def row(i, title, published, time, conf, pinned=False):
            return {"index": i, "title": title, "published": published,
                    "time": time, "confidence": conf, "pinned": pinned,
                    "spread": None, "n_options": 1, "residual": None}

        results = [
            row(0, "Opening", 0.0, 0.0, 1.00),
            row(1, "Low", 100.0, 110.0, 0.40),          # below 0.5 -> amber
            row(2, "Good", 200.0, 210.0, 0.90),
            row(3, "Unplaced", 300.0, None, 0.00),      # unplaced -> grey
            row(4, "PinnedLow", 400.0, 410.0, 0.10, pinned=True),  # kept
            row(5, "Shadowed", 401.0, 411.0, 0.90),     # <2s after PinnedLow
        ]
        results[4]["spread"] = 0.0  # healthy pin: posterior spread 0.0
        def dump(**extra):
            art = {"schema": 3, "alignment": {"rate": 1.0,
                   "results": results, **extra}}
            with open(os.path.join(work, "Book.mp3.analysis.json"), "w") as f:
                json.dump(art, f)

        dump()
        app.workflow._refresh()

        def tags_and_label(iid):
            return (set(app.workflow.tree.item(iid, "tags") or ()),
                    str(app.workflow.tree.item(iid, "values")[0]))

        def iids():
            return app.workflow.tree.get_children()

        assert tags_and_label(iids()[0]) == (set(), "1")
        assert tags_and_label(iids()[1]) == ({"lowconf"}, "?2")
        assert tags_and_label(iids()[2]) == (set(), "3")
        assert tags_and_label(iids()[3]) == ({"unplaced"}, "?4")
        assert tags_and_label(iids()[4]) == (set(), "5")       # pin survives
        assert tags_and_label(iids()[5]) == ({"lowconf"}, "?6")  # dedup shadow

        # conflicting pin (stored by the new alignment) -> red, wins over all
        dump(conflicts=[4])
        app.workflow._refresh()
        assert tags_and_label(iids()[4]) == ({"conflict"}, "5")

        # old-artifact fallback: pinned with spread None reads as conflict
        results[4]["spread"] = None
        dump()
        app.workflow._refresh()
        assert tags_and_label(iids()[4]) == ({"conflict"}, "5")
        results[4]["spread"] = 0.0

        # raising the spinner threshold re-shades immediately (rows rebuilt)
        app.workflow.min_conf.set(0.95)
        assert tags_and_label(iids()[2]) == ({"lowconf"}, "?3")
        assert _withheld(results, 0.95) == {2, 3, 4, 6}
    finally:
        app.destroy()


def test_table_shows_identified_chapters_before_align(tmp_path):
    app = _make_app(tmp_path)
    try:
        book_path = str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")
        app.workflow.set_book(book_path)
        work = app.workflow.work_dir
        os.makedirs(work, exist_ok=True)
        art = {"schema": 3, "identification": {"best_trial": {"score": 6.0},
               "chapters": [
                   {"title": "Published One", "start": 0.0},
                   {"title": "Published Two", "start": 90.0},
                   {"title": "Published Three", "start": 180.0}]}}

        def dump():
            with open(os.path.join(work, "Book.mp3.analysis.json"), "w") as f:
                json.dump(art, f)

        dump()
        app.workflow._refresh()

        rows = [app.workflow.tree.item(i, "values")
                for i in app.workflow.tree.get_children()]
        assert len(rows) == 3
        assert rows[1][0] == "2"            # no ?N markers before Align
        assert rows[1][1] == "Published Two"
        assert rows[1][2] == "0:01:30.000"
        assert rows[1][3] == "0:00:00.000"  # nothing placed yet
        assert rows[1][4] == ""
        assert all(set(app.workflow.tree.item(i, "tags") or ()) == {"unplaced"}
                   for i in app.workflow.tree.get_children())

        # after Align the rows gain placed times and marker logic returns
        art["alignment"] = {"rate": 1.0, "min_confidence": 0.5, "results": [
            {"index": 0, "title": "Published One", "published": 0.0,
             "time": 0.0, "confidence": 0.9, "pinned": False, "spread": 0.0,
             "n_options": 1, "residual": None},
            {"index": 1, "title": "Published Two", "published": 90.0,
             "time": 95.0, "confidence": 0.1, "pinned": False, "spread": None,
             "n_options": 1, "residual": None},
            {"index": 2, "title": "Published Three", "published": 180.0,
             "time": 185.0, "confidence": 0.8, "pinned": False, "spread": 0.0,
             "n_options": 1, "residual": None}]}
        dump()
        app.workflow._refresh()
        rows = [app.workflow.tree.item(i, "values")
                for i in app.workflow.tree.get_children()]
        assert rows[1][3] == "0:01:35.000"
        assert rows[1][0] == "?2"
        assert app.notebook.tab(app.workflow._chapters_tab, "text") == \
            "Chapters 2/3"
    finally:
        app.destroy()


def test_near_spec_accepts_bare_time(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(str(tmp_path / "library" / "Author" / "Book" / "Book.mp3"))
        # alignment present: nearest PLACED time wins
        wf.analysis = {"alignment": {"results": [
            {"index": 0, "time": 0.0, "confidence": 1.0, "pinned": False},
            {"index": 1, "time": 95.0, "confidence": 1.0, "pinned": False},
            {"index": 2, "time": 813.0, "confidence": 1.0, "pinned": False},
        ]}}
        assert wf._near_spec("2:40") == "2=0:02:40"     # nearest placed (95s)
        assert wf._near_spec("3=43:47") == "3=0:43:47"  # CH=TIME normalized
        assert wf._near_spec("12=2:40:05") == "12=2:40:05"

        # pre-align: nearest published start
        wf.analysis = {"identification": {"chapters": [
            {"title": "a", "start": 0.0}, {"title": "b", "start": 682.0}]}}
        assert wf._near_spec("11:30") == "2=0:11:30"

        # garbage is rejected with a ValueError (GUI shows a message box)
        with pytest.raises(ValueError):
            wf._near_spec("not a time")
    finally:
        app.destroy()


def test_table_shows_pins_before_align(tmp_path):
    """After a sweep/listen pin (alignment dropped), pins stay visible."""
    app = _make_app(tmp_path)
    try:
        book_path = str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")
        app.workflow.set_book(book_path)
        work = app.workflow.work_dir
        os.makedirs(work, exist_ok=True)
        art = {"schema": 3, "pins": {"1": 94.875, "3": 2071.4},
               "identification": {"best_trial": {"score": 6.0},
               "chapters": [
                   {"title": "One", "start": 0.0},
                   {"title": "Two", "start": 90.0},
                   {"title": "Three", "start": 200.0},
                   {"title": "Four", "start": 300.0}]}}
        with open(os.path.join(work, "Book.mp3.analysis.json"), "w") as f:
            json.dump(art, f)
        app.workflow._refresh()

        rows = [app.workflow.tree.item(i, "values")
                for i in app.workflow.tree.get_children()]
        assert len(rows) == 4
        # pins are 0-based: "1"=94.875 pins chapter 2, "3"=2071.4 chapter 4
        assert rows[1][3] == "0:01:34.875"
        assert rows[1][5] == "pin"
        assert set(app.workflow.tree.item(
            app.workflow.tree.get_children()[1], "tags") or ()) == set()
        # unpinned rows: grey unplaced, zero-clock placeholder
        assert rows[0][3] == "0:00:00.000"
        assert rows[0][5] == ""
        assert set(app.workflow.tree.item(
            app.workflow.tree.get_children()[0], "tags") or ()) == {"unplaced"}
        assert rows[3][3] == "0:34:31.400"
        assert rows[3][5] == "pin"
        # tab label counts pins as placed
        assert app.notebook.tab(app.workflow._chapters_tab, "text") == \
            "Chapters 2/4"
    finally:
        app.destroy()


def test_listen_and_sweep_chain_into_align(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None,
                            on_line=None: ran.append(then))
        monkeypatch.setattr(wf, "whisper_python",
                            lambda: "/home/oem/.venvs/whisper/bin/python")
        app.workflow._cf = ["/usr/bin/python3", "-u",
                            "/srv/media/books/chapterfit.py"]

        app.workflow.cmd_listen()
        app.workflow.cmd_sweep(start=100.0, end=700.0)

        assert ran == [app.workflow.cmd_align, app.workflow.cmd_align]
    finally:
        app.destroy()


def test_similar_gaps_cadence():
    from chaptertools.gui.workflow import similar_gaps

    # published starts 0, 100, 200, 300; pins hold chapters 0 and 3 at
    # 1x rate (times 0 and 300): chapters 1 and 2 predict to 100 and 200
    chapters = [{"title": f"c{i}", "start": float(i * 100)}
                for i in range(4)]
    pins = {0: 0.0, 3: 300.0}
    gaps = [{"mid": m, "dur": 2.0} for m in (95.0, 205.0, 900.0)]
    out = similar_gaps(gaps, pins, chapters, "cadence")
    mids = [o["gap"]["mid"] for o in out]
    assert 95.0 in mids and 205.0 in mids and 900.0 not in mids
    # predictions recorded for hover
    by_mid = {o["gap"]["mid"]: o for o in out}
    assert abs(by_mid[95.0]["pred"] - 100.0) < 1e-9
    assert by_mid[95.0]["chapter_title"] == "c1"

    # extrapolation: a chapter after the last pin extends the rate
    chapters2 = [{"title": f"c{i}", "start": float(i * 100)}
                 for i in range(5)]
    gaps2 = gaps + [{"mid": 402.0, "dur": 2.0}]
    out = similar_gaps(gaps2, pins, chapters2, "cadence")
    assert any(o["gap"]["mid"] == 402.0 for o in out)


def test_similar_gaps_duration_and_edges():
    from chaptertools.gui.workflow import similar_gaps

    chapters = [{"title": "c0", "start": 0.0}, {"title": "c1", "start": 100.0}]
    # pins sit on silences of ~2.0s and ~4.0s -> reference median 3.0s
    gaps = [{"mid": m, "dur": d} for m, d in
            ((0.0, 2.0), (100.0, 4.0), (500.0, 3.1), (900.0, 2.9),
             (700.0, 8.0))]
    pins = {0: 0.0, 1: 100.0}
    out = similar_gaps(gaps, pins, chapters, "duration")
    # closest to 3.0 first (0.1 off, a tie), pin silences excluded
    assert {o["gap"]["mid"] for o in out[:2]} == {900.0, 500.0}
    assert abs(out[0]["ref"] - 3.0) < 1e-9

    # cap honored (pin silences included so a reference exists)
    many = ([{"mid": 0.0, "dur": 2.0}, {"mid": 100.0, "dur": 4.0}]
            + [{"mid": float(m), "dur": 3.0}
               for m in range(1000, 3000, 10)])
    assert len(similar_gaps(many, pins, chapters, "duration",
                            limit=25)) == 25

    # no pins -> nothing
    assert similar_gaps(gaps, {}, chapters, "cadence") == []
    assert similar_gaps(gaps, {}, chapters, "duration") == []


def test_identify_prefill_from_label(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(str(tmp_path / "library" / "Author" / "Book" / "Book.mp3"))
        work = wf.work_dir
        os.makedirs(work, exist_ok=True)

        def dump_artifact(label):
            with open(os.path.join(work, "Book.mp3.analysis.json"), "w") as f:
                json.dump({"schema": 3, "identification": {
                    "label": label, "best_trial": {"score": 6.0},
                    "chapters": [{"title": "a", "start": 0.0}]}}, f)
            with open(os.path.join(work, "Book.mp3.analysis.json")) as f:
                return json.load(f)

        # Audnexus label -> ASIN radio + value, autofilled
        art = dump_artifact("Audnexus B0041M5MRE")
        wf.analysis = art
        wf.identify_value.set("")
        wf._prefill_identify()
        assert wf.identify_mode.get() == "asin"
        assert wf.identify_value.get() == "B0041M5MRE"
        wf._update_panel_results(wf._compute_states())
        assert "Audnexus B0041M5MRE" in wf.identify_result.get()

        # embedded dump path -> embedded radio
        art = dump_artifact(os.path.join(work, "chapters.embedded.json"))
        wf.analysis = art
        wf.identify_value.set("")
        wf._prefill_identify()
        assert wf.identify_mode.get() == "emb"

        # manual typing is never clobbered
        wf.identify_value.set("MYASIN01")
        wf._identify_autofilled = False
        art = dump_artifact("Audnexus B0041M5MRE")
        wf.analysis = art
        wf._prefill_identify()
        assert wf.identify_value.get() == "MYASIN01"
    finally:
        app.destroy()


def test_table_tint_and_font_pins(tmp_path):
    import tkinter.font as tkfont
    from tkinter import ttk

    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        # lighter grey for unplaced chapters
        assert wf.tree.tag_configure("unplaced").get("background") \
            == "#b0b0b0"
        # chapter table font is +2 over the UI default (resolved from the
        # named font the style stores)
        base = int(tkfont.nametofont("TkDefaultFont").cget("size"))
        style = ttk.Style(wf)
        got = style.configure("Chapter.Treeview", "font")
        named = tkfont.nametofont(str(got), root=app)
        assert int(named.cget("size")) == base + 2
    finally:
        app.destroy()


def test_play_from_time_button_and_call(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        # disabled with no book selected
        app.workflow._refresh()
        assert "disabled" in wf.playfrom_btn.state()
        # enabled once a book is loaded (no row selection required)
        wf.set_book(str(tmp_path / "library" / "Author" / "Book" / "Book.mp3"))
        assert "disabled" not in wf.playfrom_btn.state()

        # the call itself: parsed field time, uncapped playback
        played = []
        monkeypatch.setattr(wf.app, "play",
                            lambda path, start=None, length=None:
                            played.append((path, start, length)))
        wf.pin_time.set("2:40:05")
        wf.play_from_time()
        assert played == [(wf.audio, 2 * 3600 + 40 * 60 + 5, 0)]

        # garbage time -> message box, no playback
        played.clear()
        wf.pin_time.set("not a time")
        wf.play_from_time()
        assert played == []
        assert any("cannot read time" in (m or "")
                   for _, m in app.dialogs)
    finally:
        app.destroy()


def test_log_is_selectable_but_readonly(tmp_path):
    app = _make_app(tmp_path)
    try:
        app.log("hello log line")
        assert "hello log line" in app.log_text.get("1.0", "end-1c")
        assert str(app.log_text.cget("state")) == "normal"

        class FakeEvent:
            state = 0x0004  # ctrl held
            keysym = "c"

        assert app._log_key(FakeEvent()) is None  # ctrl+c passes through

        class PlainEvent:
            state = 0
            keysym = "x"

        assert app._log_key(PlainEvent()) == "break"  # typing blocked
    finally:
        app.destroy()


def test_cmd_listen_runs_whisper_python_on_the_script(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        monkeypatch.setattr(wf, "whisper_python",
                            lambda: "/home/oem/.venvs/whisper/bin/python")
        app.workflow._cf = ["/usr/bin/python3", "/srv/media/books/chapterfit.py"]

        app.workflow.cmd_listen()

        assert len(ran) == 1
        argv = ran[0]
        assert argv[0] == "/home/oem/.venvs/whisper/bin/python"
        assert argv[1] == "-u"  # unbuffered: true log ordering
        assert argv[2] == "/srv/media/books/chapterfit.py"
        assert "/usr/bin/python3" not in argv[:3]
        assert "listen" in argv and "--pin" in argv
    finally:
        app.destroy()


def test_cmd_listen_refuses_console_script(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        monkeypatch.setattr(wf, "whisper_python",
                            lambda: "/home/oem/.venvs/whisper/bin/python")
        app.workflow._cf = ["/usr/local/bin/chapterfit"]  # no .py path

        app.workflow.cmd_listen()

        assert ran == []  # refused rather than building a broken command
        assert any("whisper" in (m or "").lower() for _, m in app.dialogs)
    finally:
        app.destroy()


@pytest.mark.integration
def test_read_tags_round_trip(tmp_path):
    from chaptertools.output import read_tags

    mp3 = tmp_path / "t.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1", "-t", "1",
                    "-metadata", "title=Test Title",
                    "-metadata", "artist=Test Author",
                    "-metadata", "album=Test Album",
                    "-metadata", "date=1961",
                    "-metadata", "genre=Sci-Fi",
                    str(mp3), "-y"], check=True, capture_output=True)
    tags = read_tags(str(mp3))
    assert tags["title"] == "Test Title"
    assert tags["artist"] == "Test Author"
    assert tags["album"] == "Test Album"
    assert tags["date"] == "1961"
    assert tags["genre"] == "Sci-Fi"


@pytest.mark.integration
def test_emit_preserves_and_overrides_tags(tmp_path):
    """emit --embed must carry global tags through (the old wipe bug)."""
    work = tmp_path / "work"
    work.mkdir()
    src = tmp_path / "book.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=5", "-t", "5",
                    "-metadata", "title=Original Title",
                    "-metadata", "artist=Original Author",
                    "-metadata", "album=Original Album",
                    str(src), "-y"], check=True, capture_output=True)

    # minimal artifact with an alignment to emit
    art = {
        "schema": 3, "file": {"size": src.stat().st_size,
                              "mtime": int(src.stat().st_mtime)},
        "audio": str(src),
        "envelope": {"hz": 20, "frames": 100, "duration": 5.0},
        "alignment": {"rate": 1.0, "min_confidence": 0.5, "results": [
            {"index": 0, "title": "Part One", "published": 0.0, "time": 0.0,
             "confidence": 1.0, "pinned": False, "spread": None,
             "n_options": 1, "residual": None},
            {"index": 1, "title": "Part Two", "published": 2.0, "time": 2.0,
             "confidence": 0.9, "pinned": False, "spread": None,
             "n_options": 1, "residual": None},
        ]},
    }
    with open(work / "book.mp3.analysis.json", "w") as f:
        json.dump(art, f)

    subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "chapterfit.py"),
         "--work-dir", str(work), "emit", "--embed",
         "--author", "GUI Author",
         str(src)], check=True, capture_output=True)

    out = tmp_path / "book.chapters.mp3"
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters",
                        "-show_entries", "format_tags", "-of", "json",
                        str(out)], check=True, capture_output=True, text=True)
    j = json.loads(r.stdout)
    got = j["format"]["tags"]
    assert got["title"] == "Original Title"      # preserved
    assert got["album"] == "Original Album"      # preserved
    assert got["artist"] == "GUI Author"         # overridden
    assert len(j["chapters"]) == 2


def test_book_info_prefill_and_emit_flags(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        # no tags/nfo in the fixture -> fallback to folder names
        assert app.workflow.meta_vars["title"].get() == "Book"
        assert app.workflow.meta_vars["author"].get() == "Author"

        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        app.workflow.meta_vars["title"].set("My Title")
        app.workflow.meta_vars["year"].set("1961")
        app.workflow.meta_vars["album"].set("")  # empty = keep original
        app.workflow.meta_vars["author"].set("")  # cleared = no override
        app.workflow.cmd_emit()
        argv = ran[0]
        assert "--title" in argv and argv[argv.index("--title") + 1] == "My Title"
        assert "--year" in argv and argv[argv.index("--year") + 1] == "1961"
        assert "--album" not in argv  # empty field = no override
        assert "--author" not in argv

        # found summary reflects the empty harvest
        assert "no album.nfo" in app.workflow.found_var.get()
    finally:
        app.destroy()


def test_identify_embedded_dumps_and_runs(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        app.workflow.harvest = {
            "embedded_chapters": [{"start": 0.0, "title": "One"},
                                  {"start": 90.0, "title": "Two"}]}
        app.workflow.cmd_identify_embedded()

        assert len(ran) == 1
        argv = ran[0]
        dump = argv[argv.index("--chapters-json") + 1]
        with open(dump) as f:
            payload = json.load(f)
        assert payload["chapters"][0] == {"startOffsetMs": 0,
                                          "title": "One"}
        assert payload["chapters"][1]["startOffsetMs"] == 90000
        assert "identify" in argv

        # fewer than 2 chapters -> refused via dialog, no job
        ran.clear()
        app.workflow.harvest = {"embedded_chapters": [{"start": 0.0,
                                                       "title": "One"}]}
        app.workflow.cmd_identify_embedded()
        assert ran == []
        assert any("embedded" in (m or "").lower() for _, m in app.dialogs)
    finally:
        app.destroy()


def test_parse_reply_filters_events_and_garbage():
    from chaptertools.gui.mpv import parse_reply

    reply = parse_reply('{"request_id":3,"error":"success","data":12.5}')
    assert reply is not None and reply["data"] == 12.5
    assert parse_reply('{"event":"end-file"}') is None
    assert parse_reply("garbage") is None


@pytest.mark.integration
def test_player_reports_playback_time(tmp_path):
    from chaptertools.gui.mpv import Player

    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=10",
                    "-t", "10", str(wav)],
                   check=True, capture_output=True)
    p = Player()
    try:
        p.play(str(wav), length=3, extra=["--ao=null"])
        deadline = time.time() + 15
        t = None
        while time.time() < deadline:
            t = p.poll()
            if t is not None:
                break
            time.sleep(0.2)
        assert t is not None and t >= 0
        # uncapped playback (no --length) still runs and seeks
        p2 = Player()
        try:
            p2.play(str(wav), length=0, extra=["--ao=null"])
            started = poll_time(p2, 15)
            p2.seek(1.0)
            after = poll_time(p2, 15, min_wait=0.4)
            assert after is not None and started is not None
            assert after > started
        finally:
            p2.stop()
    finally:
        p.stop()
    assert not p.alive
    assert p.sock_path is None or not os.path.exists(p.sock_path)


def poll_time(player, timeout: float, min_wait: float = 0.0):
    time.sleep(min_wait)
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = player.poll()
        if t is not None:
            return t
        time.sleep(0.2)
    return None
