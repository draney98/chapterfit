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


def test_top_asin_reads_saved_find_asin_result(tmp_path):
    audio = tmp_path / "Brave New World.mp3"
    audio.write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    (work / "Brave New World.mp3.asin.json").write_text(json.dumps({
        "candidates": [{"asin": "B00Y18C77Q", "score": 0.2},
                       {"asin": "B000000000"}]}))
    assert lib.top_asin(str(audio), str(work)) == "B00Y18C77Q"
    assert lib.top_asin(str(audio), str(tmp_path / "missing")) is None


def test_asin_candidates_rows_and_empty(tmp_path):
    from chaptertools.gui.commands import find_asin_argv

    audio = tmp_path / "Brave New World.mp3"
    audio.write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    (work / "Brave New World.mp3.asin.json").write_text(json.dumps({
        "candidates": [{
            "asin": "B00Y18C77Q",
            "title": "Brave New World",
            "authors": ["Aldous Huxley"],
            "narrators": ["James Adams", "Simon Jackson"],
            "runtime_min": 95,
            "score": 0.1956,
        }, {
            "asin": "B000000001",
            "title": "Other",
            "authors": ["Someone"],
        }, {"title": "no asin"}]}))
    rows = lib.asin_candidates(str(audio), str(work))
    assert rows == [{
        "asin": "B00Y18C77Q",
        "title": "Brave New World",
        "author": "Aldous Huxley",
        "narrator": "James Adams, Simon Jackson",
        "runtime": "95 min",
        "score": "0.20",
        "source": "find-asin",
    }, {
        "asin": "B000000001",
        "title": "Other",
        "author": "Someone",
        "narrator": "",
        "runtime": "",
        "score": "",
        "source": "find-asin",
    }]
    assert lib.asin_candidates(str(audio), str(tmp_path / "missing")) == []
    empty = work / "empty.mp3.asin.json"
    empty.write_text("{}")
    (tmp_path / "empty.mp3").write_bytes(b"")
    assert lib.asin_candidates(str(tmp_path / "empty.mp3"), str(work)) == []
    argv = find_asin_argv(
        str(audio), str(work / "out.json"),
        title="Brave New World", author="Aldous Huxley",
        keywords="huxley", region="uk", provider="abs:audible")
    assert argv[argv.index(str(audio)) + 1:] == [
        "--title", "Brave New World",
        "--author", "Aldous Huxley",
        "--keywords", "huxley",
        "--region", "uk",
        "--provider", "abs:audible",
        "--json", str(work / "out.json"),
    ]
    assert "--verify" not in argv
    bare = find_asin_argv(str(audio), str(work / "out.json"))
    assert bare[-5:] == [
        str(audio), "--provider", "find-asin", "--json",
        str(work / "out.json"),
    ]


def test_asin_query_uses_saved_guess_then_folders(tmp_path):
    audio = tmp_path / "Aldous Huxley" / "Brave New World" / "book.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    assert lib.asin_query(str(audio), str(work)) == {
        "title": "Brave New World",
        "author": "Aldous Huxley",
    }
    (work / "book.mp3.asin.json").write_text(json.dumps({
        "guessed_title": "16 - Brave New World",
        "guessed_author": "Top 100 Sci-Fi Books",
    }))
    assert lib.asin_query(str(audio), str(work)) == {
        "title": "16 - Brave New World",
        "author": "Top 100 Sci-Fi Books",
    }


def test_asin_query_prefers_tags_then_falls_back(tmp_path, monkeypatch):
    audio = tmp_path / "Aldous Huxley" / "Brave New World" / "book.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    (work / "book.mp3.asin.json").write_text(json.dumps({
        "guessed_title": "16 - Brave New World",
        "guessed_author": "Top 100 Sci-Fi Books",
    }))
    monkeypatch.setattr(lib, "probe_tags", lambda _path: {
        "title": "Brave New World",
        "artist": "Aldous Huxley",
    })
    assert lib.asin_query(str(audio), str(work)) == {
        "title": "Brave New World",
        "author": "Aldous Huxley",
    }
    monkeypatch.setattr(lib, "probe_tags", lambda _path: {"title": "Brave New World"})
    assert lib.asin_query(str(audio), str(work)) == {
        "title": "Brave New World",
        "author": "Top 100 Sci-Fi Books",
    }
    assert lib.asin_query(str(audio), str(work), prefer_tags=False) == {
        "title": "16 - Brave New World",
        "author": "Top 100 Sci-Fi Books",
    }


def test_asin_emit_defaults_from_identified_candidate(tmp_path):
    audio = tmp_path / "Aldous Huxley" / "Brave New World" / "book.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    (work / "book.mp3.asin.json").write_text(json.dumps({
        "candidates": [{
            "asin": "B002V1BVK4",
            "title": "Brave New World",
            "authors": ["Aldous Huxley"],
            "release": "2008-01-16",
        }, {"asin": "B00Y18C77Q", "title": "A Reader's Guide",
            "authors": ["Someone Else"], "release": "2015-01-01"}]}))
    assert lib.asin_emit_defaults(
        str(audio), str(work), "Audnexus B002V1BVK4") == {
            "title": "Brave New World",
            "album": "Brave New World",
            "author": "Aldous Huxley",
            "year": "2008",
        }
    assert lib.asin_emit_defaults(str(audio), str(work), "Audnexus B000") == {}
    assert lib.asin_emit_defaults(str(audio), str(work), "") == {}


def test_emit_embed_flag_does_not_consume_audio(tmp_path):
    audio = tmp_path / "Brave New World.mp3"
    audio.write_bytes(b"not-audio")
    work = tmp_path / "work"
    r = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "chapterfit.py"),
         "--work-dir", str(work), "emit",
         "--min-confidence", "0.5",
         "--chapters-txt", str(work / "chapters.txt"),
         "--abs-json", str(work / "chapters.json"),
         "--title", "Brave New World",
         "--author", "Aldous Huxley",
         "--year", "1932",
         "--embed", str(audio)],
        capture_output=True, text=True)
    err = r.stderr + r.stdout
    assert "required: audio" not in err
    assert r.returncode != 2
    assert "alignment" in err


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


def test_book_marks_follow_the_artifact(tmp_path):
    audio = tmp_path / "Author" / "Book" / "Book.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"x")
    assert lib.book_marks(str(audio)) == ""
    work = lib.work_dir_for(str(audio))
    os.makedirs(work, exist_ok=True)
    art = lib.analysis_path(str(audio), work)
    with open(art, "w") as handle:
        json.dump({"envelope": {"duration": 1}}, handle)
    assert lib.book_marks(str(audio)) == "●"
    with open(art, "w") as handle:
        json.dump({"emitted": {"count": 3}}, handle)
    assert lib.book_marks(str(audio)) == "✓"
    stem, _ext = os.path.splitext(os.path.basename(audio))
    with open(os.path.join(work, f"{stem}.quality.mp3"), "wb") as handle:
        handle.write(b"x")
    assert lib.book_marks(str(audio)) == "✓♪"


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
        assert leaves == ["Book"]  # the book folder's title

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
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    audio = tmp_path / "library" / "Greg Bear" / "Eon" / "Eon.mp3"
    assert lib.work_dir_for(str(audio)) == \
        str(tmp_path / "work" / "Greg Bear" / "Eon")


def test_load_analysis_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    audio = tmp_path / "Author" / "Book" / "book.mp3"
    audio.parent.mkdir(parents=True)
    audio.touch()
    assert lib.load_analysis(str(audio)) == {}
    assert lib.analysis_path(str(audio)) == \
        str(tmp_path / "work" / "Author" / "Book" / "book.mp3.analysis.json")


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


def test_log_hold_restarts_the_same_command():
    import queue

    runner = Runner()
    job = Job("sleep", [sys.executable, "-c",
                        "import time; print('start', flush=True); time.sleep(30)"])
    runner.run(job)
    kind, _job, payload = runner.events.get(timeout=10)
    assert kind == "line" and payload == "start"
    runner.pause_for_log()
    time.sleep(0.3)
    assert runner.busy
    runner.release_log_hold(True)
    kind, _job, payload = runner.events.get(timeout=10)
    assert kind == "line" and payload == "start"
    runner.release_log_hold(False)
    runner.cancel()
    while True:
        try:
            kind, _job, payload = runner.events.get(timeout=10)
        except queue.Empty:
            break
        if kind == "done":
            break
    assert job.cancelled
    assert not runner.busy


def test_runner_cancel_marks_job_and_frees_runner(tmp_path):
    runner = Runner()
    job = Job("sleep", [sys.executable, "-c", "import time; time.sleep(30)"])
    runner.run(job)
    assert runner.busy
    deadline = time.time() + 5
    while runner._proc is None and time.time() < deadline:
        time.sleep(0.02)
    runner.cancel()
    while True:
        kind, _job, payload = runner.events.get(timeout=10)
        if kind == "done":
            break
    assert job.cancelled and payload != 0
    assert not runner.busy


@pytest.fixture
def fake_whisper(tmp_path, monkeypatch):
    """An executable stand-in for the whisper venv's python."""
    import chaptertools.gui.workflow as wf

    py = tmp_path / "whisper-venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    monkeypatch.setenv("WHISPER_PYTHON", str(py))
    assert wf.whisper_python() == str(py)
    return str(py)


def _make_app(tmp_path):
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    from chaptertools.gui.app import App
    root = tmp_path / "library"
    book = root / "Author" / "Book"
    book.mkdir(parents=True)
    (book / "Book.mp3").touch()
    mp = pytest.MonkeyPatch()
    mp.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    app = App(library_root=str(root), modal_busy=False)

    real_destroy = app.destroy
    def destroy():
        mp.undo()
        real_destroy()
    app.destroy = destroy
    app.update_idletasks()
    return app


def test_oversized_log_asks_without_blocking(tmp_path, dialogs):
    """A 60 MB log asks once and returns; it must not open a real dialog."""
    import time

    from chaptertools import file_log

    with open(file_log.LOG_PATH, "wb") as handle:
        handle.truncate(60 * 1024 * 1024)
    dialogs.answer(True)
    app = _make_app(tmp_path)
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            app.update()
            time.sleep(0.05)
        assert any(kind == "askyesno" and title == "Log file"
                   for kind, title, _message in dialogs.seen)
    finally:
        app.destroy()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def test_every_enabled_press_adds_a_log_line(tmp_path):
    import tkinter as tk

    app = _make_app(tmp_path)
    try:
        app.workflow.show_step("cleanup")
        quality = app.workflow.quality
        denoise = next(
            index for index, stage in enumerate(quality.chain.stages)
            if stage.name == "denoise")
        quality.select_stage(denoise)
        app.update_idletasks()
        from chaptertools.gui.theme import ctk_kind, widget_text

        widgets = list(_walk(app))
        presses = []
        values = []
        for widget in widgets:
            kind = ctk_kind(widget) or widget.winfo_class()
            if kind in (
                "TButton", "Button", "TCheckbutton", "Checkbutton",
                "TRadiobutton", "Radiobutton", "TMenubutton", "Menubutton",
                "CTkButton", "CTkCheckBox", "CTkSwitch", "CTkRadioButton",
                "CTkSegmentedButton",
            ):
                presses.append((widget, "<Button-1>"))
            elif kind in ("TSpinbox", "Spinbox", "TScale", "Scale", "CTkSlider"):
                values.append((widget, "<ButtonRelease-1>"))
            elif kind in ("TCombobox", "Combobox"):
                values.append((widget, "<<ComboboxSelected>>"))
        missed = []
        saw_chain = False
        saw_segment = False
        for widget, sequence in presses + values:
            try:
                if app._control_disabled(widget):
                    continue
            except tk.TclError:
                continue
            text = widget_text(widget)
            kind = ctk_kind(widget) or widget.winfo_class()
            before = app.log_text.index("end-1c")
            try:
                widget.event_generate(sequence)
                app.update_idletasks()
            except (tk.TclError, NotImplementedError):
                missed.append(f"{kind} {text}")
                continue
            if app.log_text.index("end-1c") == before:
                # Unmapped CustomTkinter widgets do not deliver bind_all from
                # event_generate; exercise the logger the way a real click would.
                event = type("E", (), {"widget": widget})()
                if sequence == "<Button-1>":
                    app._log_button_click(event)
                elif sequence == "<ButtonRelease-1>":
                    app._log_value_release(event)
                elif sequence == "<<ComboboxSelected>>":
                    app._log_combobox(event)
                app.update_idletasks()
            if app.log_text.index("end-1c") == before:
                missed.append(f"{kind} {text!r}")
                continue
            if kind in ("TCheckbutton", "CTkCheckBox") and not text.strip():
                saw_chain = True
            if "Re-estimate" in text:
                saw_segment = True
        assert missed == []
        assert saw_chain
        assert saw_segment
    finally:
        app.destroy()


def test_click_log_ignores_a_tcl_only_widget(tmp_path):
    app = _make_app(tmp_path)
    try:
        event = type("E", (), {})()
        event.widget = ".popdown.f.l"
        app._log_button_click(event)
        assert "clicked" not in app.log_text.get("1.0", "end")
    finally:
        app.destroy()


def _click(app, widget):
    event = type("E", (), {})()
    event.widget = widget
    app._log_button_click(event)


def test_click_focus_leaves_menus_alone():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    from chaptertools.gui.theme import focus_clicked_widget
    try:
        entry = tkinter.Entry(root)
        entry.pack()
        entry.focus_set()
        root.update_idletasks()
        menu = tkinter.Menu(root, tearoff=0)
        menu.add_command(label="Quit")
        focused = []
        menu.focus_set = lambda: focused.append("menu")
        event = type("E", (), {"widget": menu})()
        focus_clicked_widget(event)
        assert focused == []
        entry.focus_set = lambda: focused.append("entry")
        event.widget = entry
        focus_clicked_widget(event)
        assert focused == ["entry"]
    finally:
        root.destroy()


def test_menu_grab_name_does_not_break_the_click(monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    from chaptertools.gui import theme
    try:
        assert theme._resolve_grab(root, ".__tk__messagebox") == ".__tk__messagebox"
        monkeypatch.setattr(theme, "_grabbed", lambda _root: ".__tk__messagebox")
        event = type("E", (), {"widget": root, "x_root": 0, "y_root": 0})()
        theme._trace_pointer(root, "all-press", event)
        theme.focus_clicked_widget(event)
    finally:
        root.destroy()


def test_menu_class_bindings_keep_tk_post_and_invoke(tmp_path):
    app = _make_app(tmp_path)
    try:
        assert "tk::MenuButtonDown" in app.bind_class("Menu", "<Button>")
        assert "tk::MenuInvoke" in app.bind_class("Menu", "<ButtonRelease>")
        assert not app.bind_class("Menu", "<Button-1>")
        assert not app.bind_class("Menu", "<ButtonRelease-1>")
    finally:
        app.destroy()


def test_menu_trace_records_pointer_state(tmp_path):
    app = _make_app(tmp_path)
    try:
        import chaptertools.file_log as file_log
        from chaptertools.gui.theme import trace_menu_event
        got = []
        original = file_log.write

        def capture(text):
            got.append(text)
            original(text)

        file_log.write = capture
        try:
            trace_menu_event(app, "class-press")
        finally:
            file_log.write = original
        assert any(
            "menu-trace class-press" in line and "grab=" in line and "focus=" in line
            for line in got)
    finally:
        app.destroy()


def test_app_click_focus_skips_menus(tmp_path):
    app = _make_app(tmp_path)
    try:
        import customtkinter as ctk
        extra = ctk.CTkToplevel(app)
        try:
            script = app.bind_all("<Button-1>")
            assert "focus_clicked_widget" in script
            assert "set_focus" not in script
        finally:
            extra.destroy()
    finally:
        app.destroy()


def test_click_log_names_the_action_and_the_preset(tmp_path):
    import tkinter as tk
    from tkinter import ttk

    app = _make_app(tmp_path)
    try:
        app.session.book_info.title = "Revelation Space"
        app.workflow._step = "cleanup"
        quality = app.workflow.quality
        quality._preset_name = "Audiobook"
        quality._baseline = quality.chain.snapshot()
        quality.chain.stage("denoise").params["strength"] = 0.1
        app.session.playhead = 12 * 60 + 4
        app.player_bar.play_btn.configure(text="▶")
        _click(app, app.player_bar.play_btn)
        logged = app.log_text.get("1.0", "end")
        assert (
            "Play a 30 s preview from 0:12:04, preset Audiobook, edited, "
            "modified: denoise — Revelation Space — Audio cleanup"
        ) in logged
        assert "clicked" not in logged

        app.player_bar.play_btn.configure(text="❚❚")
        _click(app, app.player_bar.play_btn)
        assert "Stop the preview — Revelation Space — Audio cleanup" in (
            app.log_text.get("1.0", "end"))

        app.workflow._step = "place"
        app.player_bar.play_btn.configure(text="▶")
        _click(app, app.player_bar.play_btn)
        assert "Play from 0:12:04 — Revelation Space — Place" in (
            app.log_text.get("1.0", "end"))

        button = ttk.Button(app, text="Identify")
        _click(app, button)
        assert "Identify: search — Revelation Space — Place" in app.log_text.get("1.0", "end")

        chosen = tk.BooleanVar(value=False)
        check = ttk.Checkbutton(app, text="Embed chapters", variable=chosen)
        _click(app, check)
        assert "Embed chapters: on — Revelation Space — Place" in (
            app.log_text.get("1.0", "end"))

        menu = tk.Menu(app, tearoff=0)
        menu.add_command(label="Play from 5 s before", command=lambda: None)
        menu.activate(0)
        event = type("E", (), {})()
        event.widget = menu
        app._log_menu_click(event)
        assert "Play from 5 s before — Revelation Space — Place" in (
            app.log_text.get("1.0", "end"))

        button.configure(state="disabled")
        before = app.log_text.get("1.0", "end")
        _click(app, button)
        assert app.log_text.get("1.0", "end") == before
    finally:
        app.destroy()


def test_source_radios_switch_bypass_and_log_the_stream(tmp_path):
    from tkinter import ttk

    app = _make_app(tmp_path)
    try:
        app.session.book_info.title = "Revelation Space"
        app.workflow._step = "cleanup"
        quality = app.workflow.quality
        bar = app.player_bar
        assert bar.source_var.get() == "edited"
        assert app._control_disabled(bar.original_btn)
        before = app.log_text.get("1.0", "end")
        _click(app, bar.original_btn)
        assert app.log_text.get("1.0", "end") == before

        stuck = ttk.Checkbutton(app, text="Stuck", state="disabled")
        stuck.state(["!disabled"])
        assert "disabled" in str(stuck.cget("state"))
        assert not app._control_disabled(stuck)
        stuck.destroy()

        quality._preview = {"ready": True}
        bar._show_hear()
        assert not app._control_disabled(bar._source_seg)
        assert bar.source_var.get() == "edited"

        played = []
        quality._player.play = lambda *args, **kwargs: played.append("play")
        bar._source_seg.set("Original")
        bar._hear_seg("Original")
        _click(app, bar._source_seg)
        assert quality.hear_var.get()
        assert quality._player.bypass is True
        assert played == []
        logged = app.log_text.get("1.0", "end")
        assert "Hear original — Revelation Space — Audio cleanup" in logged

        bar._source_seg.set("Edited")
        bar._hear_seg("Edited")
        _click(app, bar._source_seg)
        assert not quality.hear_var.get()
        assert quality._player.bypass is False
        assert "Hear edited — Revelation Space — Audio cleanup" in (
            app.log_text.get("1.0", "end"))

        quality._finish("preview", {"dry": None, "wet": None, "sr": 44100})
        assert played == ["play"]
        assert "Playing edited audio" in app.log_text.get("1.0", "end")
        quality.hear_var.set(True)
        quality._finish("preview", {"dry": None, "wet": None, "sr": 44100})
        assert "Playing original" in app.log_text.get("1.0", "end")
        assert played == ["play", "play"]
    finally:
        app.destroy()


def test_hear_radios_sit_beside_the_play_group(tmp_path):
    app = _make_app(tmp_path)
    try:
        app.session.cleanup["stages_on"] = 1
        app.player_bar._show_hear()
        info = app.player_bar.edited_btn.master.grid_info()
        assert int(info["column"]) == 5
    finally:
        app.destroy()


def test_finished_preview_clears_the_play_icon(tmp_path):
    app = _make_app(tmp_path)
    try:
        player = app.workflow.quality._player
        app.workflow._step = "cleanup"

        class _Stream:
            def __init__(self, active):
                self.active = active

            def stop(self):
                return None

            def close(self):
                return None

        player._stream = _Stream(True)
        assert player.alive
        app.player_bar._sync_button()
        assert str(app.player_bar.play_btn.cget("text")) == "❚❚"

        player._stream = _Stream(False)
        assert not player.alive
        assert player._stream is None
        app.player_bar._sync_button()
        assert str(app.player_bar.play_btn.cget("text")) == "▶"
    finally:
        app.destroy()


def test_cleanup_dropdown_stops_playback(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    app = _make_app(tmp_path)
    try:
        quality = app.workflow.quality
        stopped = []
        quality._player.stop = lambda: stopped.append("preview")

        class _Library:
            alive = True

            def stop(self):
                stopped.append("library")
                self.alive = False

        app.player = _Library()
        quality.detail._pick_mode(quality.chain.stage("denoise"), "Spectral")
        assert stopped == ["preview", "library"]
        assert str(app.player_bar.play_btn.cget("text")) == "▶"
    finally:
        app.destroy()


def test_preset_change_prompts_only_when_asked(tmp_path, monkeypatch, dialogs):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("CHAPTERFIT_CONFIRM_PRESET", raising=False)
    app = _make_app(tmp_path)
    try:
        quality = app.workflow.quality
        quality._baseline = quality.chain.snapshot()
        quality._preset_name = "Gentle cleanup"
        quality.chain.stage("compressor").params["ratio"] = 4.0
        quality._on_preset_picked("Audiobook master")
        assert not any(kind == "askyesno" for kind, _t, _m in dialogs.seen)
        assert quality._preset_name == "Audiobook master"

        quality.chain.stage("compressor").params["ratio"] = 9.0
        monkeypatch.setenv("CHAPTERFIT_CONFIRM_PRESET", "1")
        dialogs.seen.clear()
        dialogs.answer(False)
        quality._on_preset_picked("Gentle cleanup")
        assert [(kind, title) for kind, title, _m in dialogs.seen] == [
            ("askyesno", "Apply preset")]
        assert quality._preset_name == "Audiobook master"
    finally:
        app.destroy()


def test_busy_logs_when_it_starts_and_when_it_ends(tmp_path):
    app = _make_app(tmp_path)
    try:
        app.session.set_job(True, "preview", "Preparing a 30 s preview", False)
        app.session.set_job_detail("Decoding 40%")
        text = app.log_text.get("1.0", "end")
        assert text.count("busy: Preparing a 30 s preview") == 1
        assert "Decoding" not in text
        assert app.player_bar.status_var.get() == "Preparing a 30 s preview"
        app.session.set_job(True, "process", "Rendering cleaned audio", True)
        text = app.log_text.get("1.0", "end")
        assert "idle: Preparing a 30 s preview" in text
        assert "busy: Rendering cleaned audio" in text
        app.session.set_job(False)
        text = app.log_text.get("1.0", "end")
        assert "idle: Rendering cleaned audio" in text
        assert app.player_bar.status_var.get() == "Idle"
    finally:
        app.destroy()


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


def test_workflow_refresh_renders_alignment(tmp_path, dialogs):
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
        assert rows[0][5] == "0.90"
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
            assert any("has no pin" in (m or "")
                       for _k, _t, m in dialogs.seen)
        finally:
            monkey.undo()
    finally:
        app.destroy()


def test_withheld_shading_matches_emit(tmp_path):
    from chaptertools.envelope.emit import select_chapters

    def _withheld(results, thr):
        return select_chapters(results, thr)[1]

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

        # old artifact (no stored conflicts): no guessing, a note instead
        results[4]["spread"] = None
        dump()
        app.workflow._refresh()
        assert tags_and_label(iids()[4]) == (set(), "5")
        assert "re-run Align" in app.workflow.conflict_note.get()
        dump(conflicts=[])
        app.workflow._refresh()
        assert app.workflow.conflict_note.get() == ""
        results[4]["spread"] = 0.0

        # raising the spinner threshold re-shades once it settles (debounced)
        app.workflow.min_conf.set(0.95)
        assert app.workflow._refresh_after is not None
        app.workflow.flush_refresh()
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
        assert rows[1][5] == ""
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
        assert app.workflow.chapters_var.get() == "Chapters 2/3"
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
        assert rows[1][6] == "pin"
        assert set(app.workflow.tree.item(
            app.workflow.tree.get_children()[1], "tags") or ()) == set()
        # unpinned rows: grey unplaced, zero-clock placeholder
        assert rows[0][3] == "0:00:00.000"
        assert rows[0][6] == ""
        assert set(app.workflow.tree.item(
            app.workflow.tree.get_children()[0], "tags") or ()) == {"unplaced"}
        assert rows[3][3] == "0:34:31.400"
        assert rows[3][6] == "pin"
        # tab label counts pins as placed
        assert app.workflow.chapters_var.get() == "Chapters 2/4"
    finally:
        app.destroy()


def test_listen_and_sweep_chain_into_align(tmp_path, monkeypatch, fake_whisper):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None,
                            on_line=None: ran.append(then))

        app.workflow.cmd_listen()
        app.workflow.cmd_sweep(start=100.0, end=700.0)

        assert ran == [app.workflow.cmd_align, app.workflow.cmd_align]
    finally:
        app.destroy()


def _book(tmp_path):
    return str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")


def _write_artifact(wf, art):
    os.makedirs(wf.work_dir, exist_ok=True)
    with open(os.path.join(wf.work_dir, "Book.mp3.analysis.json"), "w") as f:
        json.dump({"schema": 3, **art}, f)


def _write_kept_artifact(wf, art):
    """Write an artifact Artifact.load will keep (matching file size)."""
    st = os.stat(wf.audio)
    _write_artifact(wf, {
        "file": {"size": st.st_size, "mtime": int(st.st_mtime)},
        **art,
    })


def test_blocked_step_hover_says_what_to_do(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, {
            "envelope": {"duration": 3600.0}, "calibration": {"x": 1},
            "gaps": {"gaps": []}, "fingerprint": {"x": 1},
        })
        wf._refresh()
        app.update()
        place = "On Match, Search for the book, then choose an edition."
        assert all(tip.text == place for tip in wf.rail._tips["place"])
        assert wf.rail._subs["place"].get() == "waiting on Match"
        wf.show_step("match")
        app.update()
        assert wf.asin_search_btn.winfo_viewable()
        _write_artifact(wf, WEAK_ART)
        wf._refresh()
        # A low-confidence row keeps Place unfinished, so Finish waits.
        assert wf.rail._subs["finish"].get() == "waiting on Place"
        assert wf.rail._tips["place"][0].text == ""
    finally:
        app.destroy()


WEAK_ART = {
    "envelope": {"duration": 3600.0}, "calibration": {"x": 1},
    "gaps": {"gaps": [{"mid": 600.0, "dur": 2.0}]}, "fingerprint": {"x": 1},
    "identification": {"best_trial": {"score": 6.0}, "chapters": [
        {"title": "One", "start": 0.0}, {"title": "Two", "start": 600.0},
        {"title": "Three", "start": 1200.0}]},
    "alignment": {"rate": 1.0, "conflicts": [], "results": [
        {"index": 0, "title": "One", "published": 0.0, "time": 0.0,
         "confidence": 1.0, "pinned": False, "spread": 0.0},
        {"index": 1, "title": "Two", "published": 600.0, "time": 600.0,
         "confidence": 0.9, "pinned": False, "spread": 0.0},
        {"index": 2, "title": "Three", "published": 1200.0, "time": 1210.0,
         "confidence": 0.1, "pinned": False, "spread": 30.0}]},
}


def test_resolve_summary_keeps_next_label(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf.set_book(_book(tmp_path))
        assert wf._stage == "resolve"  # weak chapters open Resolve
        wf._refresh()
        wf._refresh()
        assert wf.next_label.winfo_exists()
        assert wf.next_var.get() == "Next: Listen"
        assert wf.next_label.cget("text") == "Next:"
        assert wf._next_jump.cget("text") == "Listen"
        links = [w.cget("text") for w in wf.unresolved_frame.winfo_children()]
        assert "3" in links
        from chaptertools.gui.theme import pair
        fg = wf.listen_btn.cget("fg_color")
        # The accent action stores both modes, so a switch does not leave
        # the light fill behind.
        wanted = tuple(part.lower() for part in pair("accent"))
        got = fg if isinstance(fg, (tuple, list)) else (fg,)
        assert tuple(str(part).lower() for part in got) == wanted
    finally:
        app.destroy()


def test_auto_advance_follows_next_stage(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        assert wf._stage == "measure"
        measured = {k: v for k, v in WEAK_ART.items()
                    if k not in ("identification", "alignment")}
        _write_artifact(wf, measured)
        wf._on_done(0, stage="measure", name="measure")
        assert wf._stage == "identify"
        identified = {**measured,
                      "identification": WEAK_ART["identification"]}
        _write_artifact(wf, identified)
        wf._on_done(0, stage="identify", name="identify")
        assert wf._step == "match"  # stay put so an edition can be chosen
        assert not wf._auto_stage
        _write_artifact(wf, WEAK_ART)
        wf._on_done(0, stage="align", name="align")
        assert wf._stage == "resolve"  # weak chapters skip past Align
    finally:
        app.destroy()


def test_dry_run_stays_enabled_after_align(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf._refresh()
        app.update()
        from chaptertools.gui.theme import is_disabled
        assert not is_disabled(wf.finalize_btn)
        assert is_disabled(wf.apply_btn)
    finally:
        app.destroy()


def test_search_is_on_screen_at_default_size(tmp_path):
    from test_gui_legibility import _settle

    app = _make_app(tmp_path)
    try:
        _settle(app, "1600x960+0+0")
        app.workflow.set_book(_book(tmp_path))
        app.workflow.show_step("match")
        app.update()
        button = app.workflow.asin_search_btn
        assert button.winfo_viewable()
        right = app.winfo_rootx() + app.winfo_width()
        bottom = app.winfo_rooty() + app.winfo_height()
        assert button.winfo_rootx() + button.winfo_width() <= right
        assert button.winfo_rooty() + button.winfo_height() <= bottom
    finally:
        app.destroy()


def test_spawn_failure_is_reported(tmp_path, dialogs):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        wf._on_done(None, name="measure")
        assert any("measure could not start" in (m or "")
                   for _k, _t, m in dialogs.seen)
        dialogs.seen.clear()
        wf._on_done(-15, name="sweep", cancelled=True)
        assert dialogs.seen == []  # a cancel is not an error dialog
    finally:
        app.destroy()


def test_busy_locks_job_controls(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf._refresh()
        wf.tree.selection_set(wf.tree.get_children()[2])
        wf._fill_from_selection()
        from chaptertools.gui.theme import is_disabled
        assert not is_disabled(wf.hint_btn)
        wf.set_busy(True)
        for w in (wf.measure_btn, wf.hint_btn, wf.sweep_start_entry,
                  wf.reread_btn):
            assert is_disabled(w)
        assert "job is running" in wf._tips[wf.hint_btn].reason
        wf.set_busy(False)
        assert not is_disabled(wf.hint_btn)
    finally:
        app.destroy()


def test_sweep_range_keeps_manual_edits(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf._refresh()
        rows = wf.tree.get_children()
        wf.tree.selection_set(rows[2])
        wf._fill_from_selection()
        assert wf.sweep_start.get() == "0:15:10"
        wf.sweep_start.set("0:20:00")
        wf._sweep_autofilled = False  # what typing in the field does
        wf.tree.selection_set(rows[1])
        wf._fill_from_selection()
        assert wf.sweep_start.get() == "0:20:00"
        wf.set_book(_book(tmp_path))  # a new book fills again
        wf.tree.selection_set(wf.tree.get_children()[1])
        wf._fill_from_selection()
        assert wf.sweep_start.get() == "0:05:00"
    finally:
        app.destroy()


def test_finalize_gate_survives_reselect(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf._on_done(0, stage="finalize-dry", name="finalize")
        assert wf._finalize_dry_ok
        wf.embed_var.set(not wf.embed_var.get())
        assert not wf._finalize_dry_ok
        wf.embed_var.set(not wf.embed_var.get())
        assert wf._finalize_dry_ok
        wf.set_book(_book(tmp_path))
        assert wf._finalize_dry_ok
        time.sleep(0.01)
        _write_artifact(wf, {**WEAK_ART, "pins": {"2": 1200.0}})
        os.utime(os.path.join(wf.work_dir, "Book.mp3.analysis.json"),
                 (time.time() + 5, time.time() + 5))
        assert not wf._finalize_dry_ok  # artifact changed: dry run again
    finally:
        app.destroy()


def test_measure_result_shows_populations_and_header_fix(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        art = dict(WEAK_ART)
        art["gaps"] = {"gaps": [{"mid": 600.0, "dur": 2.0}],
                       "populations": [{"k": 0, "n": 900, "centre": 0.6},
                                       {"k": 1, "n": 41, "centre": 3.2}]}
        art["source"] = {"timeline_trustworthy": False,
                         "declared_duration": 3500.0,
                         "decoded_duration": 3600.0,
                         "header_error_sec": 100.0,
                         "header_error_ratio": 0.028}
        _write_artifact(wf, art)
        wf._refresh()
        text = wf.measure_result.get()
        assert "1 pause found" in text
        assert "The longest 41 are about 3.2 s" in text
        assert "-write_xing 1" in text and "Book.fixed.mp3" in text
        assert wf.copy_fix_btn.winfo_manager() == "grid"
        art["source"]["timeline_trustworthy"] = True
        _write_artifact(wf, art)
        wf._refresh()
        assert "-write_xing" not in wf.measure_result.get()
        assert wf.copy_fix_btn.winfo_manager() == ""
    finally:
        app.destroy()


def test_rejected_identify_offers_another_edition(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        art = dict(WEAK_ART)
        art["identification"] = {
            **WEAK_ART["identification"], "label": "Audnexus B000000001",
            "duration_ratio": 0.71,
            "blocked": ["file is 71% of the published runtime."],
            "best_trial": {"score": 1.2, "rate_spread": 0.09}}
        _write_artifact(wf, art)
        wf._refresh()
        assert wf.identify_btn.cget("text") == "Check"
        assert wf.verdict_headline_var.get() == "Doesn't fit this audio"
        assert "different" in wf.verdict_reason_var.get().lower() \
            or "%" in wf.verdict_reason_var.get()
        ran = []
        wf._run = lambda *a, **k: ran.append(a)
        wf.identify_mode.set("asin")
        wf.identify_value.set("B000000001")
        wf.cmd_identify_primary()  # same source again: ask, do not rerun
        assert ran == []
        wf.identify_value.set("B000000002")
        wf.cmd_identify_primary()
        assert ran and "B000000002" in ran[0][1]
    finally:
        app.destroy()


def test_similar_gaps_cadence():
    from chaptertools.gui.table import similar_gaps

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
    from chaptertools.gui.table import similar_gaps

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


def test_gap_marks_chapters_without_pins_and_longest():
    from types import SimpleNamespace

    from chaptertools.gui.table import gap_marks

    def row(i, title, published, placed):
        return SimpleNamespace(index=i, title=title,
                               published=published, placed=placed)

    # one placed chapter, no pins: its bar, plus at most 10 extras
    rows = [row(0, "c0", 0.0, 0.0), row(1, "c1", 100.0, None)]
    gaps = [{"mid": 0.0, "dur": 2.0}] + [
        {"mid": float(m), "dur": 2.0} for m in range(50, 400, 10)]
    bars, extras = gap_marks(gaps, rows, "cadence")
    assert [b["time"] for b in bars] == [0.0]
    assert bars[0]["title"] == "c0"
    assert len(extras) == 10
    assert all(abs(e["gap"]["mid"]) > 0.06 for e in extras)

    # nothing placed: the 10 longest silences
    bare = [row(0, "c0", 0.0, None)]
    longs = [{"mid": float(i), "dur": float(i)} for i in range(15)]
    bars, extras = gap_marks(longs, bare, "cadence")
    assert bars == []
    assert [e["gap"]["dur"] for e in extras] == [
        float(i) for i in range(14, 4, -1)]
    assert gap_marks([], rows, "cadence") == ([], [])


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
        from chaptertools.gui import palette
        # Unplaced rows use the palette colour for the mode on screen.
        got = wf.tree.tag_configure("unplaced").get("background")
        assert str(got).lower() == palette.current("ROW_UNPLACED").lower()
        from chaptertools.gui.ui_kit import BODY, ROW_H
        # Chapter rows use the same body size as every other table.
        # The old +2 pt / 30 px row is cancelled.
        base = int(tkfont.nametofont("TkDefaultFont", root=app).cget("size"))
        style = ttk.Style(wf)
        got = style.lookup("Treeview", "font")
        named = tkfont.nametofont(str(got), root=app) if isinstance(got, str) \
            else got
        assert int(named.cget("size")) == -BODY
        assert base == -BODY
        assert int(style.lookup("Chapter.Treeview", "rowheight")) == ROW_H
        assert int(style.lookup("Treeview", "rowheight")) == ROW_H
    finally:
        app.destroy()


def test_busy_sentence_is_a_hover_unless_the_job_is_modal(tmp_path):
    app = _make_app(tmp_path)
    try:
        app.session.set_job(True, "measure", "Measuring the audio", False)
        assert app.job_window.win is None
        assert app.player_bar._busy_tip.text == "Measuring the audio"
        app.session.set_job(True, "align", "Placing chapters", True)
        # Tests pass modal_busy=False, so the Working window never opens.
        # The sentence stays on the status bar.
        assert app.job_window.win is None
        assert app.player_bar.status_var.get() == "Placing chapters"
        app.session.set_job(False)
        assert app.job_window.win is None
        assert app.player_bar.status_var.get() == "Idle"
    finally:
        app.destroy()


def test_open_problem_row_shows_use_this(tmp_path):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(_book(tmp_path))
        _write_artifact(wf, WEAK_ART)
        wf._refresh()
        from chaptertools.gui.theme import ctk_kind, widget_text
        labels = [
            widget_text(w) for w in wf.problem_bar.winfo_children()
            if (ctk_kind(w) or w.winfo_class()) in (
                "TLabel", "TButton", "TMenubutton",
                "CTkLabel", "CTkButton")
        ]
        assert any("withheld" in text for text in labels)
        assert "Use this" in labels
        menu = wf._problem_menu
        entries = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
        assert "Play from 5 s before" in entries
        for iid in wf.tree.get_children():
            assert not wf.tree.get_children(iid)
    finally:
        app.destroy()


def test_play_from_five_seconds_before(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    try:
        wf = app.workflow
        wf.set_book(str(tmp_path / "library" / "Author" / "Book" / "Book.mp3"))
        played = []
        monkeypatch.setattr(wf.app, "play",
                            lambda path, start=None, length=None:
                            played.append((path, start, length)))
        wf._play_before(2 * 3600 + 40 * 60 + 5)
        assert played == [(wf.audio, 2 * 3600 + 40 * 60, 0)]
    finally:
        app.destroy()


def test_log_is_selectable_but_readonly(tmp_path):
    app = _make_app(tmp_path)
    try:
        app.log("hello log line")
        logged = app.log_text.get("1.0", "end-1c")
        assert "hello log line" in logged
        assert logged.split()[0].count(":") == 2
        assert app.player_bar.status_var.get() == "Idle"
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


def test_cmd_listen_runs_whisper_python_on_the_module(tmp_path, monkeypatch,
                                                      fake_whisper):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))

        app.workflow.cmd_listen()

        assert len(ran) == 1
        argv = ran[0]
        # the venv interpreter runs the package module (no script needed)
        assert argv[:4] == [fake_whisper, "-u", "-m", "chaptertools.cli.chapterfit"]
        assert "listen" in argv and "--pin" in argv
    finally:
        app.destroy()


def test_module_launch_works_outside_the_project(tmp_path):
    """What whisper jobs rely on: any interpreter, any cwd, package_env()."""
    from chaptertools.gui.workflow import resolve_chapterfit
    from chaptertools.util import package_env

    env = package_env({"PATH": os.environ.get("PATH", "")})
    out = subprocess.run([*resolve_chapterfit(sys.executable), "version"],
                         cwd=tmp_path, env=env, capture_output=True, text=True)
    from chaptertools import __version__
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"chapterfit {__version__}"


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


@pytest.mark.integration
def test_emit_uses_library_alignment_on_cleaned_audio(tmp_path):
    """Chapters belong to the analyzed book, even when the file is cleaned."""
    work = tmp_path / "work"
    work.mkdir()
    src = tmp_path / "book.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=5", "-t", "5",
                    str(src), "-y"], check=True, capture_output=True)
    cleaned = work / "book.quality.mp3"
    cleaned.write_bytes(src.read_bytes())
    art = {
        "schema": 3, "file": {"size": src.stat().st_size,
                              "mtime": int(src.stat().st_mtime)},
        "audio": str(src),
        "envelope": {"hz": 20, "frames": 100, "duration": 5.0},
        "alignment": {"rate": 1.0, "min_confidence": 0.5, "results": [
            {"index": 0, "title": "Part One", "published": 0.0, "time": 0.0,
             "confidence": 1.0, "pinned": False, "spread": None,
             "n_options": 1, "residual": None},
        ]},
    }
    with open(work / "book.mp3.analysis.json", "w") as f:
        json.dump(art, f)
    subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "chapterfit.py"),
         "--work-dir", str(work), "emit", "--embed",
         "--source", str(src), str(cleaned)],
        check=True, capture_output=True, text=True)
    out = work / "book.quality.chapters.mp3"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(out)],
        check=True, capture_output=True, text=True)
    chapters = json.loads(probe.stdout)["chapters"]
    assert chapters[0]["tags"]["title"] == "Part One"


def test_cleanup_apply_emits_the_cleaned_file_from_the_library(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        audio = str(tmp_path / "library" / "Author" / "Book" / "Book.mp3")
        app.workflow.set_book(audio)
        app.workflow.include_cleanup.set(True)
        app.workflow._chapters_placed = lambda: True
        _write_kept_artifact(app.workflow, {
            "alignment": {"results": [
                {"index": 0, "title": "One", "published": 0.0, "time": 0.0,
                 "confidence": 1.0, "pinned": False}]},
        })
        ran = []
        monkeypatch.setattr(
            wf.WorkflowPanel, "_run",
            lambda self, name, argv, then=None, stage=None, on_line=None:
            ran.append((name, argv)))
        app.workflow._emit_then_finalize()
        name, argv = ran[0]
        assert name == "emit"
        assert argv[-1].endswith("Book.quality.mp3")
        assert argv[argv.index("--source") + 1] == audio
    finally:
        app.destroy()


def test_book_info_prefill_and_emit_flags(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        # folder names show at once; the file is read off the Tk thread
        assert app.workflow.meta_vars["title"].get() == "Book"
        assert "Reading" in app.workflow.found_var.get()
        app.workflow.meta_vars["year"].set("1950")  # typed while loading
        app.workflow.wait_for_facts()
        # no tags/nfo in the fixture -> fallback to folder names
        assert app.workflow.meta_vars["title"].get() == "Book"
        assert app.workflow.meta_vars["author"].get() == "Author"
        assert app.workflow.meta_vars["year"].get() == "1950"
        assert app.workflow._disk is not None

        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        app.workflow.meta_vars["title"].set("My Title")
        app.workflow.meta_vars["year"].set("1961")
        app.workflow.meta_vars["album"].set("")  # empty = keep original
        app.workflow.meta_vars["author"].set("")  # cleared = no override
        _write_kept_artifact(app.workflow, {
            "alignment": {"results": [
                {"index": 0, "title": "One", "published": 0.0, "time": 0.0,
                 "confidence": 1.0, "pinned": False}]},
        })
        app.workflow.cmd_emit()
        argv = ran[0]
        assert "--title" in argv and argv[argv.index("--title") + 1] == "My Title"
        assert "--year" in argv and argv[argv.index("--year") + 1] == "1961"
        assert "--album" not in argv  # empty field = no override
        assert "--author" not in argv

        # found summary reflects the empty harvest
        assert "no chapters" in app.workflow.found_var.get()
    finally:
        app.destroy()


def test_identify_embedded_dumps_and_runs(tmp_path, monkeypatch, dialogs):
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
        assert any("embedded" in (m or "").lower()
                   for _k, _t, m in dialogs.seen)
    finally:
        app.destroy()


def test_identify_nfo_dumps_and_runs(tmp_path, monkeypatch):
    import chaptertools.gui.workflow as wf

    app = _make_app(tmp_path)
    try:
        app.workflow.set_book(str(tmp_path / "library" / "Author" / "Book"
                                  / "Book.mp3"))
        ran = []
        monkeypatch.setattr(wf.WorkflowPanel, "_run",
                            lambda self, name, argv, then=None, stage=None, on_line=None:
                            ran.append(argv))
        app.workflow.harvest = {
            "nfo_chapters": [{"start": 0.0, "title": "Opening", "end": 25.0},
                             {"start": 25.0, "title": "One", "end": 90.0}]}
        assert app.workflow.cmd_identify_nfo() is True
        dump = ran[0][ran[0].index("--chapters-json") + 1]
        with open(dump) as f:
            payload = json.load(f)
        assert payload["chapters"][0]["title"] == "Opening"
        assert payload["chapters"][1]["startOffsetMs"] == 25000
        assert os.path.basename(dump) == "chapters.nfo.json"
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
    import shutil
    if shutil.which("mpv") is None:
        pytest.skip("mpv is not installed")
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


def test_gui_opens_without_numpy(tmp_path, monkeypatch):
    """The window builds when the optional quality extra is missing."""
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    import sys
    for name in list(sys.modules):
        if name == "chaptertools.audio_quality.worker" or name.startswith(
                "chaptertools.audio_quality.worker."):
            del sys.modules[name]
    monkeypatch.setitem(sys.modules, "numpy", None)
    from chaptertools.gui.app import App
    root = tmp_path / "library" / "Author" / "Book"
    root.mkdir(parents=True)
    (root / "Book.mp3").touch()
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    app = App(library_root=str(tmp_path / "library"), modal_busy=False)
    try:
        assert app.workflow.quality.available is False
    finally:
        app.destroy()


def test_disabled_buttons_are_not_filled_blue(tmp_path):
    """A disabled control keeps its kind. It is never the shared blue fill."""
    from chaptertools.gui.theme import pair, set_enabled
    from chaptertools.gui.ui_kit import primary, secondary, text_button

    app = _make_app(tmp_path)
    try:
        host = app.workflow
        buttons = (
            primary(host, "Run"),
            secondary(host, "Check"),
            text_button(host, "Close"),
        )
        banned = {tuple(str(c).lower() for c in pair("disabled_fill")),
                  tuple(str(c).lower() for c in pair("accent"))}

        def flat(color):
            if isinstance(color, (tuple, list)):
                return tuple(str(part).lower() for part in color)
            return (str(color).lower(),)

        for button in buttons:
            set_enabled(button, False)
            assert flat(button.cget("fg_color")) not in banned
    finally:
        app.destroy()
