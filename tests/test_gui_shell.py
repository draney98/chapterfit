"""Window shell: one session, one player, log drawer."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("tkinter")


def _button_labels(widget):
    import tkinter as tk

    from chaptertools.gui.theme import ctk_kind, widget_text

    labels = []
    for child in widget.winfo_children():
        if not isinstance(child, tk.Misc):
            continue
        kind = ctk_kind(child) or child.winfo_class()
        if kind in ("TButton", "Button", "CTkButton"):
            labels.append(widget_text(child))
        labels.extend(_button_labels(child))
    return labels


def test_session_player_and_log_drawer(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    if not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from chaptertools.gui.app import App

    app = App(library_root=str(tmp_path))
    try:
        assert app.session.book is None
        assert app.player_bar is not None
        assert app.log_text is app.log_drawer.text
        assert not app.log_drawer.visible
        assert not hasattr(app, "notebook")
        assert app.workflow.rail is not None
        app.log("hello")
        assert "hello" in app.log_text.get("1.0", "end")
        app.toggle_log()
        assert app.log_drawer.visible
        app.toggle_log()
        assert not app.log_drawer.visible
        assert not hasattr(app.workflow, "notebook")
        assert app.workflow.timeline is not None
        assert not hasattr(app.workflow.quality.analysis, "plot")
        app.workflow.apply_timeline_selection((12.0, 40.0))
        assert app.session.selection == (12.0, 40.0)
        assert app.workflow.sweep_start.get()
        audio = tmp_path / "Author" / "Book"
        audio.mkdir(parents=True)
        book = audio / "Book.mp3"
        book.write_bytes(b"x" * 5000)
        called = []
        app.workflow.cmd_measure = lambda: called.append(book.name)
        app.workflow.set_book(str(book))
        assert called == ["Book.mp3"]
        labels = _button_labels(app)
        assert "Scan file" not in labels
        for gone in ("Emit", "Finalize", "Process full file", "Replace library file"):
            assert gone not in labels
        assert "Apply to library" in labels
        assert "Dry run" in labels
        app._library_open = False
        app.workflow.set_book(str(book))
        app.update_idletasks()
        assert app._library_collapsed
        app.toggle_library()
        assert not app._library_collapsed
        app._fill_library()
        app.library_filter.set("done")
        assert not app.lib_tree.get_children("")
        app.library_filter.set("work")
        assert app.lib_tree.get_children("")
    finally:
        app.destroy()
